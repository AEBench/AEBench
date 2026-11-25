from __future__ import annotations

import errno
import fcntl
import os
import struct
from pathlib import Path

# linux/btrfs.h
_IOCTL_MAGIC = 0x94
_SUBVOL_RDONLY = 1 << 1

# struct btrfs_ioctl_vol_args { __s64 fd; char name[4088]; }
_VOL_ARGS = struct.Struct("=q4088s")
# struct btrfs_ioctl_vol_args_v2 { __s64 fd; __u64 transid; __u64 flags;
#     union { ...; __u64 unused[4]; }; union { char name[4040]; ... }; }
_VOL_ARGS_V2 = struct.Struct("=qQQ32x4040s")


def _iow(nr: int, size: int) -> int:
	return (1 << 30) | (size << 16) | (_IOCTL_MAGIC << 8) | nr


_IOC_SNAP_CREATE_V2 = _iow(23, _VOL_ARGS_V2.size)
_IOC_SUBVOL_CREATE = _iow(14, _VOL_ARGS.size)


class BtrfsError(RuntimeError):
	"""A btrfs operation failed; the message says which and why."""


def create_subvolume(path: Path | str) -> Path:
	"""Creates an empty subvolume at `path`, whose parent must exist on btrfs."""
	target = Path(path)
	with _open_dir(target.parent) as parent:
		_ioctl(
			parent,
			_IOC_SUBVOL_CREATE,
			_VOL_ARGS.pack(0, os.fsencode(target.name)),
			f"create subvolume {target}",
		)
	return target


def snapshot(source: Path | str, target: Path | str) -> Path:
	"""Takes a read-only snapshot of the subvolume `source` at the new path `target`, atomically."""
	target = Path(target)
	with _open_dir(source) as source_fd, _open_dir(target.parent) as parent:
		_ioctl(
			parent,
			_IOC_SNAP_CREATE_V2,
			_VOL_ARGS_V2.pack(source_fd, 0, _SUBVOL_RDONLY, os.fsencode(target.name)),
			f"snapshot {source} to {target}",
		)
	return target


class _open_dir:
	def __init__(self, path: Path | str) -> None:
		self.path = path
		self.fd = -1

	def __enter__(self) -> int:
		try:
			self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
		except OSError as exc:
			raise BtrfsError(f"cannot open {self.path}: {exc.strerror}") from exc
		return self.fd

	def __exit__(self, *_: object) -> None:
		os.close(self.fd)


def _ioctl(fd: int, request: int, payload: bytes, what: str) -> None:
	try:
		fcntl.ioctl(fd, request, bytearray(payload))
	except OSError as exc:
		hint = (
			" (not on btrfs; see scripts/setup_btrfs_workspace_root.sh)"
			if exc.errno == errno.ENOTTY
			else ""
		)
		raise BtrfsError(f"failed to {what}: {exc.strerror}{hint}") from exc


__all__ = ["BtrfsError", "create_subvolume", "snapshot"]
