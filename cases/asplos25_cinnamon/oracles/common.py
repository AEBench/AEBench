from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, CheckResult

# Files the README says the runs produce in the mounted outputs folder.
EXPECTED_OUTPUTS = (
    "keyswitch_comparison.pdf",
    "bootstrap_comparison.pdf",
    "performance.pdf",
    "performance_per_dollar.pdf",
    "performance_table.txt",
)


def find_outputs_dir(
    workspace: Path, *, executor: RuntimeCheckExecutor
) -> Path | None:
    """Locates the outputs folder by its contents, falling back to its name.

    The README's `mkdir -p asplos25_cinnamon_artifact/outputs` lands in a
    different place depending on where the agent runs it, so search the
    workspace for the expected result files instead. The folder holding the
    most of them wins; ties prefer a folder named `outputs`, then the
    shallowest path. Before any run has written a file, fall back to the
    shallowest directory named `outputs`. Returns a path under `workspace`,
    or None.
    """
    try:
        root = executor.resolve_path(workspace)
    except (OSError, ValueError):
        return None

    def _relative_matches(pattern: str) -> list[Path]:
        try:
            matches = executor.glob(workspace, pattern)
        except (OSError, NotImplementedError):
            return []
        rels = []
        for match in matches:
            # Glob results are executor-native; map them back under `workspace`.
            try:
                rel = Path(match).relative_to(root)
            except ValueError:
                continue
            if ".git" not in rel.parts:
                rels.append(rel)
        return rels

    found: dict[Path, set[str]] = {}
    for name in EXPECTED_OUTPUTS:
        for rel in _relative_matches(f"**/{name}"):
            found.setdefault(rel.parent, set()).add(name)

    if found:
        best = min(
            found,
            key=lambda d: (-len(found[d]), d.name != "outputs", len(d.parts), str(d)),
        )
        return workspace / best

    named = [
        rel for rel in _relative_matches("**/outputs")
        if executor.path_is_dir(workspace / rel)
    ]
    if not named:
        return None
    return workspace / min(named, key=lambda d: (len(d.parts), str(d)))


# PDF date format: D:YYYYMMDDHHmmSS followed by Z, +HH'mm', -HH'mm' or nothing
_PDF_CREATION_DATE = re.compile(
    r"/CreationDate\s*\(D:(\d{14})(Z|[+-]\d{2}'?\d{2}'?)?"
)
# PDF dates have one-second resolution
_CLOCK_SLACK = timedelta(seconds=60)


def _pdf_creation_date(
    pdf_path: Path, *, executor: RuntimeCheckExecutor
) -> datetime | None:
    """Returns the CreationDate embedded in a PDF's info dictionary.

    The PDF is binary, so grep extracts the ASCII date instead of decoding
    the whole file as text.
    """
    result = executor.run_process_capture(
        cmd=("grep", "-a", "-o", "-m", "1", "/CreationDate *(D:[0-9Z+'-]*",
             str(executor.resolve_path(pdf_path))),
        cwd=None,
        env=None,
        timeout_seconds=10.0,
    )
    match = _PDF_CREATION_DATE.search(result.stdout or "")
    if result.returncode != 0 or match is None:
        return None

    stamp, offset = match.groups()
    created = datetime.strptime(stamp, "%Y%m%d%H%M%S")
    if offset in (None, "Z"):
        return created.replace(tzinfo=timezone.utc)
    sign = 1 if offset[0] == "+" else -1
    digits = offset[1:].replace("'", "")
    delta = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return created.replace(tzinfo=timezone(sign * delta))


@dataclass(frozen=True, slots=True, kw_only=True)
class PdfCreatedDuringRunCheck(BaseCheck):
    """Checks that a PDF was generated while the agent was running.

    Outputs plotted earlier, or copied from another run, carry a
    CreationDate outside this run's window. Unlike the file's mtime, the
    embedded date survives copies of the workspace.
    """

    pdf_path: Path
    started_at: datetime | None
    finished_at: datetime | None

    def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
        if self.started_at is None or self.finished_at is None:
            return CheckResult.failure("agent run start/end times are unavailable")
        try:
            created = _pdf_creation_date(self.pdf_path, executor=executor)
        except (OSError, ValueError) as exc:
            return CheckResult.failure(f"failed to read {self.pdf_path}: {exc}")
        if created is None:
            return CheckResult.failure(f"no CreationDate found in {self.pdf_path}")

        # AEBench records run times in UTC
        started, finished = (
            t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            for t in (self.started_at, self.finished_at)
        )
        if not (started - _CLOCK_SLACK <= created <= finished + _CLOCK_SLACK):
            return CheckResult.failure(
                f"{self.pdf_path.name} created at {created.isoformat()}, outside the "
                f"agent run ({started.isoformat()} to {finished.isoformat()})"
            )
        return CheckResult.success(
            f"{self.pdf_path.name} created at {created.isoformat()}"
        )
