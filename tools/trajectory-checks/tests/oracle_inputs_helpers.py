"""Scenario loader and defaults for the oracle-inputs tests (layer 4, P0).

Oracle-input scenarios (``tests/oracle_inputs_scenarios/*.yaml``) are
written as layer-1 output records and run-folder contents (provenance spec
``trajectory-provenance-spec.md`` §0.6). ``oracle_inputs.derive`` is called
directly; ``match.link`` is never run.

This module imports nothing from ``trajectory_checks.oracle_inputs``: it
is loaded by ``test_oracle_inputs_rules.py``, which must pass before the
module exists (decision D44).

Scenario file layout::

    name: OS01_null_manifest        # equal to the file stem
    description: one line or a folded block
    xfail: "reason"                 # optional: test_scenario is marked
                                    # xfail(strict=True) with this reason
    rules: [OI1, OI2]               # rule ids of spec §0.4 exercised here
    run: {...}                      # optional RunInfo overrides; its
                                    # capabilities are merged into the
                                    # default (all false)
    paths: [...]                    # partial paths.jsonl rows
    coverage: {dotted.key: value}   # optional coverage.json overrides
    graded_manifest: [...]          # partial MC-02 records; absent -> not
    null_manifest: [...]            # passed
    null_score: {phases: {phase: status}, commit, case_image_digest}
    run_score: {phases: {phase: status}}
    baseline: {commit, files: {path: {sha256}}}
    final_files: [{path, status, sha256}, ...]
    run_commit: "..."               # optional; absent -> None
    config: {...}                   # optional; absent -> {}
    expect:
      oracle_inputs: [{...}, ...]   # partial rows, each matching exactly
                                    # one produced row
      oracle_inputs_exact: true     # optional: no other row (D58)
      absent: [path, ...]           # paths with no row
      block: {dotted.key: value}    # keys of oracle_inputs.json; a key led
                                    # by M3.1 or M3.5 is looked up under
                                    # "metrics"
      warnings: [substring, ...]    # each in exactly one warning
      warnings_exact: true          # optional: no other warning

Defaults (spec §0.6):

- ``run``: ``conftest.DEFAULT_RUN`` (workspace ``/repo``, every capability
  false), ``run_key`` ``art__agent__1``, ``family`` ``claude``, ``commit``
  ``c0``; every other universal field None.
- ``paths``: ``in_workspace`` from ``match.relative_path``, ``tracked``
  true when the baseline lists the path, ``final_state`` ``changed`` when
  tracked else ``untracked``, ``events`` [], ``final_writer`` None,
  ``final_writer_under`` false, ``unattributed_candidates`` [].
- ``coverage``: ``capabilities`` = ``run.capabilities``; snapshot scope
  ``tracked``, ``rates.untracked_visible`` false; the rest as
  ``facts_helpers``' coverage default.
- manifest records: ``check_id`` ``check<i>`` (list position), ``phase``
  ``experiment_runs``, ``method`` ``read_file_text``,
  ``resolved_workspace_path`` the relative path when ``path`` is under the
  workspace root, every other key None.
- ``null_score``, ``run_score``: the shorthand is expanded to
  ``oracle_result.json``'s schema in the order given (``score`` = number of
  ``success``); ``null_score.commit`` defaults to ``c0`` and
  ``case_image_digest`` to None; ``run_score`` has neither. Any other key
  (``oracle_digest``) is copied.
- ``baseline``: ``commit`` ``c0``; a file without ``sha256`` gets ``"00"``.
- ``final_files``: ``status`` ``M``, ``size`` None, ``in_diff`` true,
  ``reason_omitted`` None, ``sha256`` None.
"""

import ast
import copy
import pathlib
import re
import types
from collections.abc import Mapping, Sequence
from typing import Any

import conftest
import facts_helpers
import pytest
import yaml

from trajectory_checks import match

SCENARIOS_DIR = conftest.TESTS_DIR / "oracle_inputs_scenarios"

# Every rule id of spec §0.4. The rule test fails on an id no scenario
# cites, or a scenario citing an id not listed here.
RULE_IDS = (
    *("OI1", "OI2", "OI3", "OI4", "OI5", "OI6", "OI7", "OI8", "OI9"),
    "OI10",
    *("NS1", "NS2", "NS3", "NS4"),
)

# The capability pairs of spec §0.6 (marked †) and §6.9's §0 rows:
# (condition, rule). A condition is decided from the YAML alone
# (condition_holds), so the rule test needs no layer-4 code.
CAPABILITY_RULES = (
    ("oracle_reads", "OI1"),
    ("created_visible", "OI5"),
    ("exact_writes", "OI10"),
    ("null_manifest", "OI3"),
)

METRIC_IDS = ("M3.1", "M3.5")
DEFAULT_RUN_EXTRA = types.MappingProxyType(
    {"run_key": "art__agent__1", "family": "claude", "commit": "c0"}
)
RECORD_KEYS = (
    *("check_id", "phase", "method", "path", "resolved_workspace_path"),
    *("exists", "sha256", "size", "mtime_ns", "glob_pattern"),
    *("glob_matches", "argv", "exists_before", "sha256_before"),
    "tracked_before",
)
FINAL_FILE_KEYS = (
    *("path", "status", "size", "sha256", "in_diff", "reason_omitted"),
)
# Spec §0.3: the fields of one oracle_inputs.jsonl row.
ROW_FIELDS = frozenset(
    {
        *("input_id", "path", "in_workspace", "input_scope", "pattern"),
        *("match_type", "listed_by", "in_under", "access_types", "checks"),
        *("phases", "source_type", "at_baseline", "baseline_sha256"),
        *("tracked_at_baseline", "null_sha256", "oracle_writes"),
        *("oracle_writes_basis", "grading_exists", "grading_sha256"),
        "grading_mtime_ns",
    }
)
BLOCK_FIELDS = frozenset(
    {
        *("schema_version", "run_key", "family", "capabilities", "bases"),
        *("preconditions", "metrics", "warnings"),
    }
)
M31_KEYS = frozenset(
    {
        *("source_type", "inputs_under", "inputs_over", "inputs_read_under"),
        *("inputs_stat_only_under", "inputs_outside_workspace"),
        *("inputs_reference", "inputs_present_at_baseline"),
        *("inputs_absent_at_baseline", "inputs_oracle_written", "patterns"),
        *("pattern_matches_complete", "command_checks"),
        *("command_reads_recorded", "phases_complete", "run_commit"),
        *("null_commit", "run_case_image_digest", "null_case_image_digest"),
        "null_image_matches",
    }
)
M35_KEYS = frozenset(
    {
        *("null_score", "null_score_max", "null_phases_passed", "run_score"),
        *("run_score_max", "score_gain", "phases_passed_by_null"),
        *("phases_gained", "phases_lost"),
    }
)
PRECONDITION_LABELS = (
    "oracle_inputs",
    "null_commit",
    "null_score",
    "run_score",
)
BASES_KEYS = frozenset({"source", "pattern_matches", "oracle_writes"})
ACCESS_TYPES = types.MappingProxyType(
    {
        "read_file_text": "read",
        "path_exists": "stat",
        "path_is_file": "stat",
        "path_is_dir": "stat",
        "glob": "list",
        "command": "command",
    }
)

TOP_KEYS = frozenset(
    {
        *("name", "description", "xfail", "rules", "run", "paths"),
        *("coverage", "graded_manifest", "null_manifest", "null_score"),
        *("run_score", "baseline", "final_files", "run_commit", "config"),
        "expect",
    }
)
EXPECT_KEYS = frozenset(
    {
        *("oracle_inputs", "oracle_inputs_exact", "absent", "block"),
        *("warnings", "warnings_exact"),
    }
)


def scenario_paths() -> list[pathlib.Path]:
    """Lists the oracle-input scenario files in name order.

    Returns:
        Sorted paths of ``oracle_inputs_scenarios/*.yaml``.
    """
    return sorted(SCENARIOS_DIR.glob("*.yaml"))


def read_raw(path: pathlib.Path) -> dict[str, Any]:
    """Reads one scenario file as written.

    Args:
        path: A scenario file.

    Returns:
        The decoded YAML mapping.
    """
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def scenario_params() -> list[Any]:
    """The scenario files as pytest params, xfail-marked when they say so.

    Returns:
        One ``pytest.param`` per scenario file, id the stem.
    """
    params = []
    for path in scenario_paths():
        reason = read_raw(path).get("xfail")
        marks = (
            [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        )
        params.append(pytest.param(path, id=path.stem, marks=marks))
    return params


def condition_holds(raw: Mapping[str, Any], condition: str) -> bool:
    """Whether a CAPABILITY_RULES condition is true in a raw scenario.

    Args:
        raw: A scenario as written.
        condition: ``oracle_reads`` (the capability), ``created_visible``
            (capability ``untracked_visible`` or coverage
            ``rates.untracked_visible``, D37), ``exact_writes``
            (``oracle_reads`` and ``final_content`` and ``final_files``
            given) or ``null_manifest`` (a null-agent manifest given).

    Returns:
        The condition's value.
    """
    capabilities = (raw.get("run") or {}).get("capabilities") or {}
    coverage = raw.get("coverage") or {}
    if condition == "oracle_reads":
        return bool(capabilities.get("oracle_reads"))
    if condition == "created_visible":
        return bool(
            capabilities.get("untracked_visible")
            or coverage.get("rates.untracked_visible")
        )
    if condition == "exact_writes":
        return bool(
            capabilities.get("oracle_reads")
            and capabilities.get("final_content")
            and raw.get("final_files") is not None
        )
    if condition == "null_manifest":
        return raw.get("null_manifest") is not None
    raise ValueError(condition)


def make_record(
    raw: Mapping[str, Any], index: int, workspace_root: str
) -> dict[str, Any]:
    """Completes a partial MC-02 record with the §0.6 defaults.

    Args:
        raw: The record as written.
        index: Its position in its manifest.
        workspace_root: The run's workspace root.

    Returns:
        A record with every key of RECORD_KEYS (extra keys kept).
    """
    record: dict[str, Any] = dict.fromkeys(RECORD_KEYS)
    record.update(
        check_id=f"check{index}",
        phase="experiment_runs",
        method="read_file_text",
    )
    given = copy.deepcopy(dict(raw))
    record.update(given)
    if "resolved_workspace_path" not in given and record["path"] is not None:
        relative, inside = match.relative_path(record["path"], workspace_root)
        record["resolved_workspace_path"] = relative if inside else None
    return record


def expand_score(
    raw: Mapping[str, Any] | None, *, null: bool
) -> dict[str, Any] | None:
    """Expands the score shorthand to oracle_result.json's schema.

    Args:
        raw: ``{phases: {phase: status}, ...}`` or None.
        null: Whether this is the null-agent score (commit and digest).

    Returns:
        The score object, or None when not given.
    """
    if raw is None:
        return None
    given = copy.deepcopy(dict(raw))
    phases = given.pop("phases", None) or {}
    passed = sum(1 for status in phases.values() if status == "success")
    score: dict[str, Any] = {
        "status": "success",
        "score": passed,
        "summary": f"Passed {passed}/{len(phases)} phases.",
        "phases": [
            {"phase": p, "status": s, "summary": "", "error": None}
            for p, s in phases.items()
        ],
        "artifacts": {},
        "error": None,
    }
    if null:
        score["commit"] = "c0"
        score["case_image_digest"] = None
    score.update(given)
    return score


def make_baseline(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Completes a baseline (tracked_files.json content).

    Args:
        raw: ``{commit, files: {path: {sha256}}}`` or None.

    Returns:
        The baseline, or None when not given.
    """
    if raw is None:
        return None
    baseline = {"commit": "c0", "source": "git", "files": {}}
    given = copy.deepcopy(dict(raw))
    files = given.pop("files", None) or {}
    baseline.update(given)
    for path, entry in files.items():
        baseline["files"][path] = {"sha256": "00", **(entry or {})}
    return baseline


def make_final_files(
    raw: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Completes partial final_files.json entries (MC-03).

    Args:
        raw: Partial entries, or None.

    Returns:
        Full entries, or None when not given.
    """
    if raw is None:
        return None
    entries = []
    for item in raw:
        entry: dict[str, Any] = dict.fromkeys(FINAL_FILE_KEYS)
        entry.update(status="M", in_diff=True)
        entry.update(copy.deepcopy(dict(item)))
        entries.append(entry)
    return entries


def make_run(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The RunInfo of a raw scenario.

    Args:
        raw: A scenario as written.

    Returns:
        A full RunInfo.
    """
    run = copy.deepcopy(dict(conftest.DEFAULT_RUN))
    run.update(DEFAULT_RUN_EXTRA)
    given = copy.deepcopy(dict(raw.get("run") or {}))
    run["capabilities"].update(given.pop("capabilities", None) or {})
    run.update(given)
    if run["capabilities"]["oracle_reads"] and run["oracle_reads"] is None:
        run["oracle_reads"] = "run/oracle_inputs.jsonl"  # layer 1 sets both
    for key in ("t_start", "t_end"):
        if isinstance(run.get(key), int):
            run[key] = float(run[key])
    return run


def make_paths(
    raw: list[Mapping[str, Any]],
    baseline: Mapping[str, Any] | None,
    workspace_root: str,
) -> list[dict[str, Any]]:
    """Completes partial paths.jsonl rows.

    Args:
        raw: Partial rows.
        baseline: The completed baseline, or None.
        workspace_root: The run's workspace root.

    Returns:
        Full PathSummary rows.
    """
    listed = set((baseline or {}).get("files") or {})
    rows = []
    for item in raw:
        path = item["path"]
        tracked = path in listed
        row = {
            "path": path,
            "in_workspace": match.relative_path(path, workspace_root)[1],
            "tracked": tracked,
            "events": [],
            "final_writer": None,
            "final_writer_under": False,
            "final_state": "changed" if tracked else "untracked",
            "unattributed_candidates": [],
        }
        row.update(copy.deepcopy(dict(item)))
        rows.append(row)
    return rows


def build_scenario(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Completes a raw scenario mapping with every default.

    Args:
        raw: A scenario as written (``read_raw``), possibly transformed.

    Returns:
        A dict with ``name``, ``description``, ``rules``, ``raw``, ``run``,
        ``linked``, the run-folder inputs (``graded_manifest``,
        ``null_manifest``, ``null_score``, ``run_score``, ``baseline``,
        ``final_files``, ``run_commit``, ``config``) and ``expect``.
    """
    raw = copy.deepcopy(dict(raw))
    run = make_run(raw)
    root = run["workspace_root"]
    baseline = make_baseline(raw.get("baseline"))
    paths = make_paths(raw.get("paths") or [], baseline, root)
    coverage = facts_helpers._coverage(
        [], [], [], [], paths, run, dict(raw.get("coverage") or {})
    )
    manifests = {}
    for key in ("graded_manifest", "null_manifest"):
        given = raw.get(key)
        manifests[key] = (
            None
            if given is None
            else [make_record(r, i, root) for i, r in enumerate(given)]
        )
    return {
        "name": raw.get("name"),
        "description": raw.get("description"),
        "rules": list(raw.get("rules") or []),
        "raw": raw,
        "run": run,
        "linked": {
            "links": [],
            "dispositions": [],
            "paths": paths,
            "coverage": coverage,
        },
        **manifests,
        "null_score": expand_score(raw.get("null_score"), null=True),
        "run_score": expand_score(raw.get("run_score"), null=False),
        "baseline": baseline,
        "final_files": make_final_files(raw.get("final_files")),
        "run_commit": raw.get("run_commit"),
        "config": copy.deepcopy(raw.get("config") or {}),
        "expect": raw.get("expect") or {},
    }


def load_scenario(path: pathlib.Path) -> dict[str, Any]:
    """Loads one scenario file and fills in every default.

    Args:
        path: A scenario file.

    Returns:
        The value of ``build_scenario`` on the file's content.
    """
    return build_scenario(read_raw(path))


DERIVE_ARGS = (
    *("graded_manifest", "null_manifest", "null_score", "run_score"),
    *("baseline", "final_files", "run_commit", "config"),
)


def run_derive(scenario: Mapping[str, Any], **overrides: Any) -> Any:
    """Calls ``oracle_inputs.derive`` on a loaded scenario.

    Args:
        scenario: A dict from ``build_scenario``.
        **overrides: Replacements for ``linked``, ``run`` or any keyword
            argument of ``derive``.

    Returns:
        The result of ``oracle_inputs.derive``.
    """
    from trajectory_checks import oracle_inputs  # late: D44

    args = {
        key: copy.deepcopy(scenario[key])
        for key in ("linked", "run", *DERIVE_ARGS)
        if key not in overrides
    }
    args.update(overrides)
    linked = args.pop("linked")
    return oracle_inputs.derive(linked, **args)


def block_value(block: Mapping[str, Any], key: str) -> Any:
    """Looks up an ``expect.block`` key in an oracle_inputs.json value.

    Args:
        block: The ``oracle_inputs_run`` part of a ``derive`` result.
        key: A dotted key; a leading metric id means ``metrics.<id>``.

    Returns:
        The value found.

    Raises:
        KeyError: When a component is missing.
    """
    for metric in METRIC_IDS:
        if key == metric or key.startswith(metric + "."):
            value = block["metrics"][metric]
            rest = key[len(metric) + 1 :]
            return conftest.dotted(value, rest) if rest else value
    return conftest.dotted(block, key)


def validate(scenario: Mapping[str, Any], stem: str) -> list[str]:
    """Checks that a loaded scenario is internally consistent.

    Args:
        scenario: A dict from ``load_scenario``.
        stem: The file stem the scenario was read from.

    Returns:
        One message per problem; empty when consistent.
    """
    problems = []
    raw = scenario["raw"]
    if scenario["name"] != stem:
        problems.append(f"name {scenario['name']!r} != file stem {stem!r}")
    if not re.fullmatch(r"OS\d{2}b?_[a-z0-9_]+", stem):
        problems.append(f"file stem {stem!r} is not OS<nn>[b]_<snake_case>")
    if not scenario["description"]:
        problems.append("no description")
    unknown = set(raw) - TOP_KEYS
    if unknown:
        problems.append(f"unknown top-level keys {sorted(unknown)}")
    unknown = set(scenario["expect"]) - EXPECT_KEYS
    if unknown:
        problems.append(f"unknown expect keys {sorted(unknown)}")
    rules = scenario["rules"]
    if not rules:
        problems.append("no rules cited")
    if set(rules) - set(RULE_IDS):
        problems.append(
            f"unknown rule ids {sorted(set(rules) - set(RULE_IDS))}"
        )
    if len(set(rules)) != len(rules):
        problems.append("a rule id is cited twice")
    capabilities = scenario["run"]["capabilities"]
    if set(capabilities) != set(conftest.CAPABILITIES):
        problems.append("run capabilities are not the nine of layer 1")
    has_reads = scenario["run"]["oracle_reads"] is not None
    if capabilities["oracle_reads"] != has_reads:
        problems.append("oracle_reads disagrees with RunInfo.oracle_reads")
    for key in ("graded_manifest", "null_manifest"):
        for record in scenario[key] or []:
            extra = set(record) - set(RECORD_KEYS)
            if extra:
                problems.append(f"{key}: unknown keys {sorted(extra)}")
            if record["method"] not in (*ACCESS_TYPES, "run_process"):
                problems.append(f"{key}: unknown method {record['method']}")
            if (record["method"] == "run_process") != (record["path"] is None):
                problems.append(f"{key}: a path exactly when not run_process")
    graded = scenario["graded_manifest"] is not None
    if graded and not capabilities["oracle_reads"]:
        problems.append("graded_manifest without capability oracle_reads")
    return problems


RULES_LINE = re.compile(
    r"^\s*Rules:(.+?)(?:\n\s*\n|\Z)", re.MULTILINE | re.DOTALL
)


def rule_citations(
    files: Sequence[str],
) -> dict[str, set[str]]:
    """The rule ids each test function cites in its docstring.

    A test cites rules in a docstring paragraph ``Rules: OI3, OI10.``. The
    files are parsed, not imported, so the citation check runs before the
    layer-4 modules exist (D44, D45).

    Args:
        files: Test file names under ``tests/``.

    Returns:
        Rule id -> the names of the tests citing it.
    """
    cited: dict[str, set[str]] = {}
    for name in files:
        tree = ast.parse((conftest.TESTS_DIR / name).read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            for line in RULES_LINE.findall(ast.get_docstring(node) or ""):
                for rule in re.split(r"[,.\s]+", line.strip()):
                    if rule:
                        cited.setdefault(rule, set()).add(
                            f"{name}::{node.name}"
                        )
    return cited
