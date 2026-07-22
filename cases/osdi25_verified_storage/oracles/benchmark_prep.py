from __future__ import annotations

from collections.abc import Sequence
from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase

class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            # CapybaraKV Verification (Correctness & Speed)
            # - Run verify-ae.sh and parse the output to enforce an exact match for the string: "verification results:: 707 verified, 0 errors".
            # - Parse the verification time from the logs and assert that it verifies "quickly" (< 180 seconds) to explicitly validate Claim 1.
            # Using timeout_seconds=180.0 acts as our time bound assertion for Claim 1.
            self.command_check(
                name="capybarakv_verification_and_speed",
                cwd=self.workspace_path("osdi25", "capybaraKV", "capybarakv", "src"),
                cmd=("bash", "-c", "./verify-ae.sh --time --num-threads 8"),
                signature="verification results:: 707 verified, 0 errors",
                timeout_seconds=180.0,
            ),

            # CapybaraNS Verification
            # - Run scons with the Dafny path. 'scons: done building targets.' confirms a successful build/verification exit.
            self.command_check(
                name="capybarans_verification_build_succeeded",
                cwd=self.workspace_path("osdi25", "capybaraNS"),
                cmd=("bash", "-c", "dafny_dir=$(dirname $(find ~ -name Dafny.dll -type f 2>/dev/null | grep -v 'Trash' | head -n 1)); scons --dafny-path=$dafny_dir"),
                signature="scons: done building targets.",
                timeout_seconds=300.0,
            ),
            # - After ensuring it builds, print the actual generated Dafny logs (.vdfy) to genuinely check for the "verified, 0 errors" phrase.
            self.command_check(
                name="capybarans_verification_0_errors",
                cwd=self.workspace_path("osdi25", "capybaraNS"),
                cmd=("bash", "-c", "dafny_dir=$(dirname $(find ~ -name Dafny.dll -type f 2>/dev/null | grep -v 'Trash' | head -n 1)); scons --dafny-path=$dafny_dir > /dev/null && find src -name '*.vdfy' -exec cat {} +"),
                signature="verified, 0 errors",
                timeout_seconds=300.0,
            ),

            # Line-counting (Table 2)
            # - Run count_capybarakv_lines.py and dafny-line-count.py. Capture the outputs and do an exact numerical match against the Table 2 metrics in the paper.
            self.command_check(
                name="capybarakv_line_counts_exact",
                cwd=self.workspace_path("osdi25", "capybaraKV", "capybarakv", "src"),
                cmd=("bash", "-c", f"(./verify-ae.sh --emit=dep-info || true) && /usr/bin/python3 count_capybarakv_lines.py lib.d ../../pmcopy {self.workspace_path().parent / 'verus'} | awk '/Total/ {{ if ($4==5244 && $6==14255 && $8==5531) {{ printf \"%.1f\\n\", $6/$8 }} }}'"),
                signature="2.6",
                timeout_seconds=60.0,
            ),
            self.command_check(
                name="capybarans_line_counts_exact",
                cwd=self.workspace_path("osdi25", "capybaraNS"),
                cmd=("bash", "-c", "trap 'rm -rf /tmp/linecounts' EXIT; rm -rf /tmp/linecounts && python3 dafny-line-count.py | awk '/Total/ { if ($3==414 && $5==673 && $7==278) { printf \"%.1f\\n\", $5/$7 } }'"),
                signature="2.4",
                timeout_seconds=120.0,
            ),
        )