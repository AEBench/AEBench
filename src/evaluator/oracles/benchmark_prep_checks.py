"""Benchmark prep oracle checks: path and command validation."""

from __future__ import annotations

import abc
import dataclasses
import os
import pathlib
import shlex
import types

from collections.abc import Mapping, Sequence

from ..constants import DEFAULT_ORACLE_CHECK_TIMEOUT
from . import utils
from .env_setup_checks import FilesystemPathCheck


_CommandT = str | Sequence[str]


def _format_command(cmd: _CommandT) -> str:
    if isinstance(cmd, str):
        return cmd
    return " ".join(shlex.quote(str(arg)) for arg in cmd)


def _cwd_suffix(cwd: pathlib.Path | None) -> str:
    if cwd is None:
        return ""
    return f" [cwd = {cwd}]"


def _require_directory(path: pathlib.Path | None) -> str | None:
    if path is None:
        return None
    if not path.exists():
        return f"working directory missing: {path}"
    if not path.is_dir():
        return f"working directory is not a directory: {path}"
    return None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkCommandCheck(utils.BaseCheck):

    cwd: pathlib.Path | str | os.PathLike[str] | None = None
    cmd: _CommandT
    signature: str | None = None
    timeout_seconds: float = DEFAULT_ORACLE_CHECK_TIMEOUT
    env_overrides: Mapping[str, str] = dataclasses.field(default_factory=dict)
    use_shell: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("BenchmarkCommandCheck.name must be non-empty")

        if self.cwd is not None and not isinstance(self.cwd, pathlib.Path):
            object.__setattr__(self, "cwd", pathlib.Path(self.cwd))

        if isinstance(self.cmd, (list, tuple)):
            if not self.cmd:
                raise ValueError(f"{self.name}: cmd must be non-empty")
            bad = [arg for arg in self.cmd if not isinstance(arg, str) or arg == ""]
            if bad:
                raise TypeError(
                    f"{self.name}: all command argv entries must be non-empty str; bad entries: {bad!r}"
                )
            object.__setattr__(self, "cmd", tuple(self.cmd))
        elif isinstance(self.cmd, str):
            if not self.cmd.strip():
                raise ValueError(f"{self.name}: cmd must be non-empty")
            if not self.use_shell:
                raise ValueError(
                    f"{self.name}: string cmd requires use_shell = True (prefer argv tokens)"
                )
        else:
            raise TypeError(f"{self.name}: cmd must be a string or a sequence of args")

        if self.timeout_seconds <= 0:
            raise ValueError(f"{self.name}: timeout_seconds must be > 0")

        if self.signature is not None and not self.signature.strip():
            object.__setattr__(self, "signature", None)

        env_dict: dict[str, str] = {}
        for key, value in dict(self.env_overrides).items():
            if key is None or str(key) == "":
                raise TypeError(
                    f"{self.name}: env_overrides contains an empty env var name: {key!r}"
                )
            env_dict[str(key)] = str(value)
        object.__setattr__(self, "env_overrides", types.MappingProxyType(env_dict))

    def check(self) -> utils.CheckResult:
        cwd_path = self.cwd if isinstance(self.cwd, pathlib.Path) else None
        error = _require_directory(cwd_path)
        if error is not None:
            return utils.CheckResult.failure(error, cwd=cwd_path)

        env = os.environ.copy()
        if self.env_overrides:
            env.update(self.env_overrides)

        cmd_display = _format_command(self.cmd)
        cwd_note = _cwd_suffix(cwd_path)

        sig = self.signature if (self.signature is not None and self.signature.strip()) else None
        sig_found_stdout = sig is None
        sig_found_stderr = sig is None
        tail_len = 0 if sig is None else max(len(sig) - 1, 0)
        stdout_tail = ""
        stderr_tail = ""

        def _on_chunk(stream_name: str, text: str) -> None:
            nonlocal sig_found_stdout, sig_found_stderr, stdout_tail, stderr_tail
            if sig is None:
                return

            if stream_name == "stdout" and not sig_found_stdout:
                hay = stdout_tail + text
                if sig in hay:
                    sig_found_stdout = True
                stdout_tail = hay[-tail_len:] if tail_len else ""
            elif stream_name == "stderr" and not sig_found_stderr:
                hay = stderr_tail + text
                if sig in hay:
                    sig_found_stderr = True
                stderr_tail = hay[-tail_len:] if tail_len else ""

        cmd_run: _CommandT
        if self.use_shell and not isinstance(self.cmd, str):
            cmd_run = _format_command(self.cmd)
        else:
            cmd_run = self.cmd

        try:
            run = utils.run_subprocess_capture(
                cmd=cmd_run,
                cwd=cwd_path,
                env=env,
                timeout_seconds=float(self.timeout_seconds),
                use_shell=self.use_shell,
                capture_limit_chars=utils.DEFAULT_MAX_CAPTURE_CHARS,
                drain_after_kill=False,
                on_chunk=_on_chunk,
            )
        except OSError as exc:
            return utils.CheckResult.failure(
                f"failed to run command: {cmd_display}{cwd_note}: {exc}",
                stdout="",
                stderr=str(exc),
                returncode=None,
                timed_out=False,
                cwd=cwd_path,
            )

        if run.timed_out:
            return utils.CheckResult.failure(
                f"command timed out after {self.timeout_seconds}s: {cmd_display}{cwd_note}",
                stdout=run.stdout,
                stderr=run.stderr,
                returncode=None,
                timed_out=True,
                cwd=cwd_path,
            )

        if run.returncode != 0:
            return utils.CheckResult.failure(
                f"command failed (rc = {run.returncode}): {cmd_display}{cwd_note}",
                stdout=run.stdout,
                stderr=run.stderr,
                returncode=run.returncode,
                timed_out=False,
                cwd=cwd_path,
            )

        if sig is not None and not (sig_found_stdout or sig_found_stderr):
            return utils.CheckResult.failure(
                f"signature not found: {sig!r}: {cmd_display}{cwd_note}",
                stdout=run.stdout,
                stderr=run.stderr,
                returncode=run.returncode,
                timed_out=False,
                cwd=cwd_path,
            )

        return utils.CheckResult.success(
            stdout=run.stdout,
            stderr=run.stderr,
            returncode=run.returncode,
            cwd=cwd_path,
        )


class OracleBenchmarkPrepBase(utils._OraclePhaseBase):
    """Base for benchmark prep oracle phases."""

    phase_label = "BenchmarkPrep"

    @abc.abstractmethod
    def requirements(self) -> Sequence[utils.BaseCheck]:
        raise NotImplementedError