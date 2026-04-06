"""Oracle check primitives and subprocess helpers."""

from __future__ import annotations

import abc
import codecs
import dataclasses
import enum
import locale
import logging
import pathlib
import selectors
import subprocess
import time

from collections.abc import Callable, Mapping, Sequence

from ..constants import SUBPROCESS_WAIT_TIMEOUT


DEFAULT_MAX_CAPTURE_CHARS: int = 16_384
DEFAULT_MAX_TRUNCATED_MESSAGE_CHARS: int = 2_048
_DEFAULT_TRUNCATION_SUFFIX = "\n... [output truncated]"


def decode_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [output truncated]"


class CheckOutcome(enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"


@dataclasses.dataclass(frozen=True, slots=True)
class CheckResult:

    ok: bool
    message: str
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    timed_out: bool = False
    cwd: pathlib.Path | None = None

    @classmethod
    def success(
        cls,
        message: str = "",
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int | None = None,
        timed_out: bool = False,
        cwd: pathlib.Path | None = None,
    ) -> "CheckResult":
        return cls(
            ok=True,
            message=message,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
            cwd=cwd,
        )

    @classmethod
    def failure(
        cls,
        message: str,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int | None = None,
        timed_out: bool = False,
        cwd: pathlib.Path | None = None,
    ) -> "CheckResult":
        return cls(
            ok=False,
            message=message,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
            cwd=cwd,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class CheckEntry:

    name: str
    outcome: CheckOutcome
    message: str
    optional: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class OracleReport:

    results: tuple[CheckEntry, ...]

    @property
    def ok(self) -> bool:
        return all(
            r.outcome != CheckOutcome.FAILED
            for r in self.results
            if not r.optional
        )

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == CheckOutcome.PASSED)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == CheckOutcome.FAILED)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BaseCheck(abc.ABC):

    name: str
    optional: bool = False

    @abc.abstractmethod
    def check(self) -> CheckResult:
        raise NotImplementedError


@dataclasses.dataclass(frozen=True, slots=True)
class ProcResult:

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool


def stream_subprocess(
    proc: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    on_chunk: Callable[[str, bytes], None],
    drain_after_kill: bool = False,
) -> tuple[int | None, bool]:
    """Drive proc to completion, calling on_chunk per chunk, handle timeout/kill."""
    assert proc.stdout is not None
    assert proc.stderr is not None

    sel = selectors.DefaultSelector()
    try:
        sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
        sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")

        deadline = time.monotonic() + timeout_seconds
        timed_out = False

        def _read_chunk(stream) -> bytes:
            if hasattr(stream, "read1"):
                return stream.read1(8192)
            return stream.read(8192)

        def _close_stream(stream) -> None:
            try:
                sel.unregister(stream)
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        def _drain(drain_seconds: float) -> None:
            drain_deadline = time.monotonic() + drain_seconds
            while sel.get_map() and time.monotonic() < drain_deadline:
                for key, _mask in sel.select(timeout=0.1):
                    stream = key.fileobj
                    chunk = _read_chunk(stream)
                    if not chunk:
                        _close_stream(stream)
                        continue
                    on_chunk(key.data, chunk)

        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _mask in sel.select(timeout=min(0.25, remaining)):
                stream = key.fileobj
                chunk = _read_chunk(stream)
                if not chunk:
                    _close_stream(stream)
                    continue
                on_chunk(key.data, chunk)

        if timed_out:
            try:
                proc.kill()
            except Exception:
                pass
            if drain_after_kill:
                _drain(1.0)
            try:
                proc.wait(timeout=SUBPROCESS_WAIT_TIMEOUT)
            except Exception:
                pass
            return None, True

        try:
            rc = proc.wait(timeout=SUBPROCESS_WAIT_TIMEOUT)
        except Exception:
            rc = proc.returncode
        return rc, False
    finally:
        sel.close()


def run_subprocess_capture(
    *,
    cmd: str | Sequence[str],
    cwd: pathlib.Path | None,
    env: Mapping[str, str] | None,
    timeout_seconds: float,
    use_shell: bool = False,
    capture_limit_chars: int = DEFAULT_MAX_CAPTURE_CHARS,
    drain_after_kill: bool = False,
    encoding: str | None = None,
    on_chunk: Callable[[str, str], None] | None = None,
) -> ProcResult:
    """Run a subprocess with bounded stdout/stderr capture."""
    if capture_limit_chars <= 0:
        raise ValueError("capture_limit_chars must be > 0")

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=use_shell,
        text=False,
    )

    codec_name = encoding or locale.getpreferredencoding(False) or "utf-8"
    stdout_decoder = codecs.getincrementaldecoder(codec_name)(errors="replace")
    stderr_decoder = codecs.getincrementaldecoder(codec_name)(errors="replace")

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stdout_len = 0
    stderr_len = 0
    stdout_overflow = False
    stderr_overflow = False

    def _append_bounded(parts: list[str], current_len: int, text: str) -> tuple[int, bool]:
        if current_len >= capture_limit_chars:
            return current_len, True
        remaining = capture_limit_chars - current_len
        if len(text) <= remaining:
            parts.append(text)
            return current_len + len(text), False
        parts.append(text[:remaining])
        return capture_limit_chars, True

    def _handle_text(stream_name: str, text: str) -> None:
        nonlocal stdout_len, stderr_len, stdout_overflow, stderr_overflow
        if not text:
            return
        if on_chunk is not None:
            on_chunk(stream_name, text)

        if stream_name == "stdout":
            stdout_len, overflowed = _append_bounded(stdout_parts, stdout_len, text)
            stdout_overflow = stdout_overflow or overflowed
        else:
            stderr_len, overflowed = _append_bounded(stderr_parts, stderr_len, text)
            stderr_overflow = stderr_overflow or overflowed

    def _on_raw_chunk(stream_name: str, raw: bytes) -> None:
        decoder = stdout_decoder if stream_name == "stdout" else stderr_decoder
        _handle_text(stream_name, decoder.decode(raw))

    returncode, timed_out = stream_subprocess(
        proc,
        timeout_seconds=float(timeout_seconds),
        on_chunk=_on_raw_chunk,
        drain_after_kill=drain_after_kill,
    )

    _handle_text("stdout", stdout_decoder.decode(b"", final=True))
    _handle_text("stderr", stderr_decoder.decode(b"", final=True))

    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    if stdout_overflow:
        stdout += _DEFAULT_TRUNCATION_SUFFIX
    if stderr_overflow:
        stderr += _DEFAULT_TRUNCATION_SUFFIX

    return ProcResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )


def build_oracle_report(
    *,
    logger: logging.Logger,
    requirements: Callable[[], Sequence[BaseCheck]],
) -> OracleReport:
    """Run each check and build a report."""
    try:
        reqs = requirements()
    except Exception as exc:
        msg = f"failed to enumerate requirements: {type(exc).__name__}: {exc}"
        logger.error(msg)
        return OracleReport(
            results=(
                CheckEntry(
                    name="<requirements>",
                    outcome=CheckOutcome.FAILED,
                    message=msg,
                    optional=False,
                ),
            )
        )

    results: list[CheckEntry] = []
    for req in reqs:
        try:
            result = req.check()
            if result.ok:
                outcome = CheckOutcome.PASSED
                msg = result.message or "ok"
            else:
                outcome = CheckOutcome.WARNING if req.optional else CheckOutcome.FAILED
                msg = result.message or "failed"
        except Exception as exc:
            outcome = CheckOutcome.WARNING if req.optional else CheckOutcome.FAILED
            msg = f"unexpected error: {type(exc).__name__}: {exc}"

        results.append(
            CheckEntry(
                name=req.name,
                outcome=outcome,
                message=msg,
                optional=req.optional,
            )
        )
    return OracleReport(results=tuple(results))


def log_oracle_report(
    logger: logging.Logger,
    *,
    label: str,
    report: OracleReport,
    verbose: bool = False,
) -> bool:
    for result in report.results:
        if result.outcome == CheckOutcome.PASSED:
            if verbose:
                logger.info("[%s] %s: PASSED — %s", label, result.name, result.message)
        elif result.outcome == CheckOutcome.WARNING:
            logger.warning("[%s] %s: WARNING — %s", label, result.name, result.message)
        else:
            logger.error("[%s] %s: FAILED — %s", label, result.name, result.message)
    return report.ok


class _OraclePhaseBase(abc.ABC):
    """Shared base for all four oracle phase types."""

    phase_label = "OraclePhase"

    def __init__(self, *, logger: logging.Logger, **_kwargs: object) -> None:
        super().__init__()
        self._logger = logger

    @abc.abstractmethod
    def requirements(self) -> Sequence[BaseCheck]:
        raise NotImplementedError

    def report(self) -> OracleReport:
        return build_oracle_report(
            logger=self._logger,
            requirements=self.requirements,
        )

    def run(self, *, verbose: bool = False) -> bool:
        rep = self.report()
        return log_oracle_report(
            self._logger,
            label=self.phase_label,
            report=rep,
            verbose=verbose,
        )