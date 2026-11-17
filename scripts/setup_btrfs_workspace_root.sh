#!/bin/bash
# One-time setup, as root, of the btrfs filesystem that `aebench case monitor
# --workdir-snapshots` keeps its workspaces and snapshots on.
#
# Creates a sparse image file, formats it as btrfs, and loop-mounts it with
# user_subvol_rm_allowed, so the invoking user can create, snapshot and delete
# subvolumes without root afterwards. Quotas stay off: they make thousands of
# snapshots slow.
#
#   sudo scripts/setup_btrfs_workspace_root.sh <image> <size> <mount-point> [owner]
#   sudo scripts/setup_btrfs_workspace_root.sh /data/aebench-btrfs.img 100G /mnt/aebench-btrfs ark
#
# Then point AEBench at it:
#   export AEBENCH_BTRFS_WORKSPACE_ROOT=<mount-point>/workspaces
#
# Needs btrfs-progs for mkfs.btrfs (apt install btrfs-progs); AEBench itself
# does not. The mount does not survive a reboot; the script prints an fstab line.
set -euo pipefail

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
	sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//' >&2
	exit 2
fi

image=$1
size=$2
mount_point=$3
owner=${4:-${SUDO_USER:-}}
options="loop,noatime,user_subvol_rm_allowed"

if [ "$(id -u)" -ne 0 ]; then
	echo "run as root (sudo)" >&2
	exit 1
fi
if [ -z "$owner" ]; then
	echo "name the owning user as the fourth argument" >&2
	exit 1
fi
if ! command -v mkfs.btrfs >/dev/null; then
	echo "mkfs.btrfs not found; install btrfs-progs" >&2
	exit 1
fi
if mountpoint -q "$mount_point"; then
	echo "$mount_point is already a mount point" >&2
	exit 1
fi

if [ -e "$image" ]; then
	echo "reusing the existing image $image"
else
	truncate --size "$size" "$image"
	mkfs.btrfs --quiet --label aebench "$image"
fi

mkdir -p "$mount_point"
mount -o "$options" "$image" "$mount_point"
mkdir -p "$mount_point/workspaces"
chown "$owner:" "$mount_point" "$mount_point/workspaces"

echo "mounted $image at $mount_point ($options)"
echo "export AEBENCH_BTRFS_WORKSPACE_ROOT=$mount_point/workspaces"
echo "to mount at boot, add to /etc/fstab:"
echo "$image $mount_point btrfs $options 0 0"
