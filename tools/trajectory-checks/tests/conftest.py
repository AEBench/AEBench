"""Shared helpers for the matching-layer tests.

Scenario files (``tests/scenarios/*.yaml``) list only what they test. Every
omitted field takes the default documented here, so a scenario stays short
and agent-neutral.

Scenario file layout::

    name: S01_exact                 # equal to the file stem
    description: one line, which rule is tested
    run: {...}                      # optional, overrides DEFAULT_RUN
    calls: [...]                    # Call records (spec §5.1), partial
    execs: [...]                    # Exec records (spec §5.2), partial
    baseline: {...}                 # optional, tracked_files.json content
    final_diff: {...}               # optional, tracked_files_diff.json content
    config: {...}                   # optional, passed to match.link
    expect:
      links: [...]                  # each must match one actual link
      links_exact: true             # optional: per link_type listed above,
                                    # no other links of that type
      links_absent: [...]           # optional: no actual link matches any
      dispositions: {id: value}     # value: a disposition string, or a
                                    # partial dispositions row
      paths: [...]                  # partial paths rows, matched by "path"
      coverage: {dotted.key: value} # e.g. rates.call_match_rate_under

Call defaults (``DEFAULT_CALL``): ``tool_type`` "shell", ``raw_name`` "Bash"
for shell calls and the tool type otherwise, ``family`` "claude", ``actor``
"main", ``t_end`` = ``t_start`` + 1, ``t_precision_s`` 1.0,
``command_norm`` = ``command``, ``background`` False for shell calls and None
otherwise, ``timeout_s`` None, ``paths`` [], ``edit_content`` True for edit
and write calls, ``log_ok`` True, ``log_exit_code`` None. ``seq`` is the
position in the list.

Exec defaults (``DEFAULT_EXEC``): ``duration_s`` 1.0 when neither it nor
``t_end`` is given; ``t_end`` = ``t_start`` + ``duration_s`` (or
``duration_s`` = ``t_end`` - ``t_start``); ``exit_code`` 0, ``signal`` None,
``complete`` True, ``cwd`` the run's workspace root, ``pid`` 1000 + list
index, ``argv0`` "/bin/bash", ``command_norm`` = ``command``, ``wrapper``
None, ``noise`` None, ``stdout_bytes`` and ``stderr_bytes`` 0, and every
field decision D26 adds (``env_keys``, ``schema_version``,
``parent_command_id`` and the others of ``NEW_EXEC_FIELDS``) None.
``snapshot`` defaults to status "ok", scope "tracked", empty lists,
``skip_reason`` None, the D26 fields (``hashes`` and the three
``*_untracked`` lists) None, and ``totals`` equal to the list lengths; a
partial ``snapshot`` is merged into that default. ``seq`` is the position after
sorting by (``t_start``, ``command_id``); the list order of the YAML file is
kept, so it may differ from ``seq`` (spec §5.2).

Run defaults (``DEFAULT_RUN``, a ``RunInfo``): ``t_start`` 0, ``t_end``
10000, ``workspace_root`` "/repo", ``trace_ok`` True, every other
``RunInfo`` field None (``log_ok`` None: ``inputs.log`` is then whether
calls were given) and every capability False. A scenario's
``run.capabilities`` is merged into the default capabilities.
``baseline``, ``final_diff`` and ``config`` default to None.
"""

import copy
import json
import math
import pathlib
import types
from collections.abc import Mapping, Sequence
from typing import Any

import yaml

TESTS_DIR = pathlib.Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
PARSE_FIXTURES_DIR = FIXTURES_DIR / "parse"
TRACE_FIXTURES_DIR = FIXTURES_DIR / "trace"
SCENARIOS_DIR = TESTS_DIR / "scenarios"

# Field names that a future spec rename may change, kept in one place.
TOOL_TYPE_FIELD = "tool_type"
LINK_TYPE_FIELD = "link_type"
WRITER_TYPE_FIELD = "writer_type"
ITEM_TYPE_FIELD = "item_type"

# Sentinel used in expected fixture files: the value must be a non-null str.
ANY_NON_NULL = "<any non-null>"

FAMILIES = ("claude", "codex", "deepseek", "zcode")

# Spec §5.1 fields. ``timeout_s`` is deferred in this layer, so it is the only
# field allowed to be missing from a parsed call.
CALL_FIELDS = frozenset(
    {
        "call_id",
        "seq",
        "family",
        "raw_name",
        TOOL_TYPE_FIELD,
        "actor",
        "t_start",
        "t_end",
        "t_precision_s",
        "command",
        "command_norm",
        "background",
        "paths",
        "edit_content",
        "log_ok",
        "log_exit_code",
    }
)
OPTIONAL_CALL_FIELDS = frozenset({"timeout_s"})

# Spec §5.2 fields.
EXEC_FIELDS = frozenset(
    {
        "command_id",
        "seq",
        "t_start",
        "t_end",
        "duration_s",
        "exit_code",
        "signal",
        "complete",
        "cwd",
        "pid",
        "argv0",
        "command",
        "command_norm",
        "wrapper",
        "noise",
        "snapshot",
        "stdout_bytes",
        "stderr_bytes",
    }
)
# Decision D26: present in every Exec, None when the monitor did not record
# them (``env_keys`` is in every existing trace).
NEW_EXEC_FIELDS = (
    *("env_keys", "schema_version", "parent_command_id", "root_command_id"),
    *("depth", "ppid", "pgid", "sid", "parent_link", "record_type"),
    *("released_at", "ended_at", "snapshot_before_s", "snapshot_after_s"),
    *("reads", "orphans", "stdio"),
)
EXEC_FIELDS = EXEC_FIELDS | frozenset(NEW_EXEC_FIELDS)
NEW_SNAPSHOT_FIELDS = (
    *("hashes", "created_untracked", "modified_untracked"),
    "deleted_untracked",
)
SNAPSHOT_FIELDS = frozenset(
    {
        "status",
        "scope",
        "created",
        "modified",
        "deleted",
        "totals",
        "skip_reason",
        *NEW_SNAPSHOT_FIELDS,
    }
)
RECORD_TYPES = frozenset({"closed", "flushed_at_stop", "start_only"})
PARENT_LINKS = frozenset({"live", "closed", "adopted", "none"})
# Spec §5.6: the capabilities of a RunInfo, all False on old runs.
CAPABILITIES = (
    *("parent_links", "start_events", "exact_timing", "content_hashes"),
    *("untracked_visible", "read_tracing", "final_content", "oracle_reads"),
    *("egress_log", "stdio"),
)
RUN_INFO_FIELDS = frozenset(
    {
        *("t_start", "t_end", "workspace_root", "trace_ok", "run_key"),
        *("family", "log_ok", "monitor_version", "schema_version"),
        *("trace_meta", "final_diff_content", "final_workspace"),
        *("oracle_reads", "case_image_digest", "commit", "shim_intact"),
        *("unattributed_reads", "capabilities"),
    }
)
PRECONDITION_LABELS = frozenset({"C1_under", "C2_under", "C6", "C7_under"})

TOOL_TYPES = frozenset(
    {
        "shell",
        "read",
        "edit",
        "write",
        "web",
        "wait",
        "control",
        "meta",
        "other",
    }
)
RESULT_KEYS = frozenset({"links", "dispositions", "paths", "coverage"})

DEFAULT_RUN = types.MappingProxyType(
    {
        **dict.fromkeys(RUN_INFO_FIELDS),
        "t_start": 0.0,
        "t_end": 10000.0,
        "workspace_root": "/repo",
        "trace_ok": True,
        "capabilities": dict.fromkeys(CAPABILITIES, False),
    }
)
DEFAULT_CALL = types.MappingProxyType(
    {
        TOOL_TYPE_FIELD: "shell",
        "family": "claude",
        "actor": "main",
        "t_precision_s": 1.0,
        "timeout_s": None,
        "paths": (),
        "log_ok": True,
        "log_exit_code": None,
    }
)
DEFAULT_EXEC = types.MappingProxyType(
    {
        "exit_code": 0,
        "signal": None,
        "complete": True,
        "argv0": "/bin/bash",
        "wrapper": None,
        "noise": None,
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        **dict.fromkeys(NEW_EXEC_FIELDS),
    }
)
DEFAULT_SNAPSHOT = types.MappingProxyType(
    {
        **dict.fromkeys(NEW_SNAPSHOT_FIELDS),
        "status": "ok",
        "scope": "tracked",
        "created": (),
        "modified": (),
        "deleted": (),
        "skip_reason": None,
    }
)


def load_json(path: pathlib.Path) -> Any:
    """Reads a JSON file.

    Args:
        path: File to read.

    Returns:
        The decoded JSON value.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def make_call(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    """Completes a partial scenario call with the documented defaults.

    Args:
        raw: The call as written in the scenario.
        index: Its position in the scenario's call list.

    Returns:
        A full Call record (spec §5.1).
    """
    call = {key: copy.deepcopy(value) for key, value in DEFAULT_CALL.items()}
    call["paths"] = []
    call.update(copy.deepcopy(dict(raw)))
    tool_type = call[TOOL_TYPE_FIELD]
    is_shell = tool_type == "shell"
    call.setdefault("seq", index)
    call.setdefault("raw_name", "Bash" if is_shell else tool_type)
    if "t_end" not in call:  # lazily: an untimed call may give its end
        call["t_end"] = call["t_start"] + 1.0
    call.setdefault("command", None)
    call.setdefault("command_norm", call["command"])
    call.setdefault("background", False if is_shell else None)
    call.setdefault("edit_content", tool_type in ("edit", "write"))
    _floats(call, ("t_start", "t_end", "t_precision_s"))
    return call


def make_exec(
    raw: Mapping[str, Any], index: int, workspace_root: str
) -> dict[str, Any]:
    """Completes a partial scenario exec with the documented defaults.

    Args:
        raw: The exec as written in the scenario.
        index: Its position in the scenario's exec list.
        workspace_root: Default working directory.

    Returns:
        A full Exec record (spec §5.2) without ``seq``.
    """
    record = dict(DEFAULT_EXEC)
    record["cwd"] = workspace_root
    record["pid"] = 1000 + index
    record.update(copy.deepcopy(dict(raw)))
    if "t_end" in record and "duration_s" not in record:
        record["duration_s"] = record["t_end"] - record["t_start"]
    record.setdefault("duration_s", 1.0)
    record.setdefault("t_end", record["t_start"] + record["duration_s"])
    record.setdefault("command_norm", record["command"])
    snapshot = {
        key: list(v) if isinstance(v, tuple) else v
        for key, v in DEFAULT_SNAPSHOT.items()
    }
    snapshot.update(record.get("snapshot") or {})
    snapshot.setdefault(
        "totals",
        {k: len(snapshot[k]) for k in ("created", "modified", "deleted")},
    )
    record["snapshot"] = snapshot
    _floats(record, ("t_start", "t_end", "duration_s"))
    return record


def assign_exec_seq(execs: list[dict[str, Any]]) -> None:
    """Sets ``seq`` from the (``t_start``, ``command_id``) order, in place.

    Args:
        execs: Exec records; their list order is left unchanged.
    """
    order = sorted(execs, key=lambda e: (e["t_start"], e["command_id"]))
    for seq, record in enumerate(order):
        record["seq"] = seq


def load_scenario(path: pathlib.Path) -> dict[str, Any]:
    """Loads one scenario file and fills in every default.

    Args:
        path: A ``scenarios/*.yaml`` file.

    Returns:
        A dict with ``name``, ``description``, ``run``, ``calls``, ``execs``,
        ``baseline``, ``final_diff``, ``config`` and ``expect``.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    run = copy.deepcopy(dict(DEFAULT_RUN))
    given = dict(raw.get("run") or {})
    run["capabilities"].update(given.pop("capabilities", None) or {})
    run.update(given)
    _floats(run, ("t_start", "t_end"))
    calls = [make_call(c, i) for i, c in enumerate(raw.get("calls") or [])]
    execs = [
        make_exec(e, i, run["workspace_root"])
        for i, e in enumerate(raw.get("execs") or [])
    ]
    assign_exec_seq(execs)
    return {
        "name": raw["name"],
        "description": raw["description"],
        "run": run,
        "calls": calls,
        "execs": execs,
        "baseline": raw.get("baseline"),
        "final_diff": raw.get("final_diff"),
        "config": raw.get("config"),
        "expect": raw.get("expect") or {},
    }


def scenario_paths() -> list[pathlib.Path]:
    """Lists the scenario files in name order.

    Returns:
        Sorted paths of ``scenarios/*.yaml``.
    """
    return sorted(SCENARIOS_DIR.glob("*.yaml"))


def run_link(scenario: Mapping[str, Any], **overrides: Any) -> dict[str, Any]:
    """Calls ``match.link`` on a loaded scenario.

    Args:
        scenario: A dict from ``load_scenario``.
        **overrides: Replacements for ``calls``, ``execs``, ``run``,
            ``baseline``, ``final_diff`` or ``config``.

    Returns:
        The result of ``match.link``.
    """
    from trajectory_checks import match  # imported late: may not exist yet

    args = {
        key: copy.deepcopy(scenario[key])
        for key in ("calls", "execs", "run", "baseline", "final_diff", "config")
    }
    args.update(overrides)
    return match.link(
        args["calls"],
        args["execs"],
        run=args["run"],
        baseline=args["baseline"],
        final_diff=args["final_diff"],
        config=args["config"],
    )


def close(actual: Any, expected: Any, tolerance: float = 1e-6) -> bool:
    """Compares two values, numbers within a tolerance.

    Args:
        actual: Actual value.
        expected: Expected value.
        tolerance: Absolute tolerance for floats.

    Returns:
        True when equal (recursively for lists and dicts).
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance)
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        return set(actual) == set(expected) and all(
            close(actual[k], expected[k], tolerance) for k in expected
        )
    if isinstance(expected, Sequence) and not isinstance(expected, str):
        return (
            isinstance(actual, Sequence)
            and not isinstance(actual, str)
            and len(actual) == len(expected)
            and all(
                close(a, e, tolerance)
                for a, e in zip(actual, expected, strict=True)
            )
        )
    return actual == expected


def partial_mismatches(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    tolerance: float = 1e-6,
    prefix: str = "",
) -> list[str]:
    """Compares only the keys present in ``expected``.

    Nested dicts in ``expected`` are compared partially too. The sentinel
    ``ANY_NON_NULL`` accepts any non-null string.

    Args:
        actual: Actual record.
        expected: Expected (partial) record.
        tolerance: Absolute tolerance for numbers.
        prefix: Key path for messages.

    Returns:
        One message per mismatching key; empty when everything matches.
    """
    problems = []
    for key, want in expected.items():
        where = prefix + str(key)
        if key not in actual:
            problems.append(f"{where}: missing")
            continue
        got = actual[key]
        if want == ANY_NON_NULL:
            if not isinstance(got, str) or not got:
                problems.append(f"{where}: {got!r}, expected a non-null str")
        elif isinstance(want, Mapping) and isinstance(got, Mapping):
            problems += partial_mismatches(got, want, tolerance, where + ".")
        elif not close(got, want, tolerance):
            problems.append(f"{where}: {got!r} != expected {want!r}")
    return problems


def dotted(value: Mapping[str, Any], key: str) -> Any:
    """Looks up a dotted key such as ``rates.call_match_rate_under``.

    Args:
        value: Nested dict.
        key: Dot-separated key path.

    Returns:
        The value found.

    Raises:
        KeyError: When a component is missing.
    """
    parts = key.split(".")
    while parts:
        # Keys may contain dots themselves (e.g. "C0.1"): take the longest
        # prefix of the remaining parts that is a key at this level.
        for size in range(len(parts), 0, -1):
            candidate = ".".join(parts[:size])
            if isinstance(value, Mapping) and candidate in value:
                value = value[candidate]
                parts = parts[size:]
                break
        else:
            raise KeyError(key)
    return value


def canonical(result: Any) -> str:
    """Serialises a result for byte-level comparison.

    Args:
        result: Any JSON-compatible value.

    Returns:
        JSON with sorted keys.
    """
    return json.dumps(result, sort_keys=True)


def _floats(record: dict[str, Any], keys: Sequence[str]) -> None:
    for key in keys:
        if isinstance(record.get(key), int) and not isinstance(
            record.get(key), bool
        ):
            record[key] = float(record[key])
