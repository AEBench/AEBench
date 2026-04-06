from __future__ import annotations

import dataclasses

from pathlib import Path

from . import utils


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class FailingCheck(utils.BaseCheck):

    message: str = "requirement intentionally fails"

    def check(self) -> utils.CheckResult:
        return utils.CheckResult.failure(self.message)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TextFileEqualityCheck(utils.BaseCheck):
    """Check file text matches a reference file exactly."""

    observed_path: Path
    reference_path: Path

    def check(self) -> utils.CheckResult:
        if not self.observed_path.is_file():
            return utils.CheckResult.failure(f"observed file missing: {self.observed_path}")
        if not self.reference_path.is_file():
            return utils.CheckResult.failure(f"reference file missing: {self.reference_path}")

        observed = self.observed_path.read_text(encoding="utf-8")
        expected = self.reference_path.read_text(encoding="utf-8")
        if observed != expected:
            max_preview = 200
            expected_preview = expected[:max_preview] + ("..." if len(expected) > max_preview else "")
            observed_preview = observed[:max_preview] + ("..." if len(observed) > max_preview else "")
            return utils.CheckResult.failure(
                f"content mismatch ({len(expected)} vs {len(observed)} chars): "
                f"expected={expected_preview!r} observed={observed_preview!r}"
            )
        return utils.CheckResult.success("file contents match reference")