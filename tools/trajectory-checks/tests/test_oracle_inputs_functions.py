"""Layer 4, P0: table-driven tests of the pure functions (spec §0.6).

Every expected value is written by hand from the rules of spec §0.4.
Each test names the rules it exercises on a ``Rules:`` docstring line,
which ``test_oracle_inputs_rules.py`` reads without importing this file.
The module under test is imported inside each test (D44), so until it
exists each test fails on its own and the rest of the suite still runs.
"""

import builtins
import copy
import pathlib
from typing import Any

import conftest
import oracle_inputs_helpers as helpers
import pytest

ROOT = "/repo"


def _module() -> Any:
    from trajectory_checks import oracle_inputs

    return oracle_inputs


def _records(*raw: dict[str, Any]) -> list[dict[str, Any]]:
    return [helpers.make_record(r, i, ROOT) for i, r in enumerate(raw)]


def _row(**fields: Any) -> dict[str, Any]:
    """A full oracle_inputs.jsonl row: a literal null-agent input."""
    row: dict[str, Any] = dict.fromkeys(helpers.ROW_FIELDS)
    row.update(
        input_id="i0",
        path="data/a.csv",
        in_workspace=True,
        input_scope="workspace",
        match_type="literal",
        listed_by=[],
        in_under=True,
        access_types=["read"],
        checks=["c"],
        phases=["experiment_runs"],
        source_type="null_agent",
    )
    row.update(fields)
    return row


def _normalize(
    records: list[dict[str, Any]],
    source_type: str = "null_agent",
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], dict, list[str]]:
    args = {"baseline": None, "reference_prefixes": (), **kwargs}
    return _module().normalize_manifest(
        records, source_type, workspace_root=ROOT, **args
    )


def _by_key(rows: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    return {(row["path"], row["pattern"]): row for row in rows}


# ---------------------------------------------------------------- glob_match

GLOBS = (
    # `*` within one component.
    ("results/*.csv", "results/a.csv", True),
    ("results/*.csv", "results/x/a.csv", False),
    ("results/*", "results/a.csv", True),
    # `**` across components, zero or more of them, as a whole component.
    ("results/**/*.csv", "results/a.csv", True),
    ("results/**/*.csv", "results/x/y/a.csv", True),
    ("results/**", "results/x/y/a.csv", True),
    ("results/x**", "results/x/y", False),
    # `?`: one character of one component.
    ("results/run?.csv", "results/run1.csv", True),
    ("results/run?.csv", "results/run12.csv", False),
    ("results/?", "results/x/y", False),
    # Classes.
    ("results/run[12].csv", "results/run2.csv", True),
    ("results/run[12].csv", "results/run3.csv", False),
    ("results/run[!12].csv", "results/run3.csv", True),
    ("results/run[!12].csv", "results/run1.csv", False),
    # A pattern relative to the listed folder, and a path outside it.
    ("out/data/*.json", "out/data/r.json", True),
    ("results/*.csv", "other/a.csv", False),
    ("results/*.csv", "results.csv", False),
    # A dot-file is matched only by a pattern starting with a dot.
    ("results/*", "results/.hidden", False),
    ("results/*.csv", "results/.a.csv", False),
    ("results/.*", "results/.hidden", True),
    ("results/?hidden", "results/.hidden", False),
    # S2 guards: bash globstar without dotglob, not Python 3.12's pathlib
    # (which matches dot-files): `**` crosses no hidden folder, and a
    # class never matches a leading dot.
    ("results/**/*.csv", "results/.cache/a.csv", False),
    ("results/**/*.csv", "results/x/.y/a.csv", False),
    ("results/**", "results/.git", False),
    ("results/.cache/*.csv", "results/.cache/a.csv", True),
    ("results/**/.*", "results/x/.keep", True),
    ("results/[.]hidden", "results/.hidden", False),
)


@pytest.mark.parametrize(("pattern", "path", "want"), GLOBS)
def test_glob_match(pattern: str, path: str, want: bool) -> None:
    """Bash globstar semantics: a leading dot only explicitly (Q6, S2).

    Rules: OI5.
    """
    assert _module().glob_match(pattern, path) is want


# ---------------------------------------------------------- normalize_manifest


def test_normalize_merges_records_of_one_path() -> None:
    """Records of one path merge; lists are sorted unions.

    A boolean is true when any record says true, false when all say false,
    null otherwise. Rules: OI3, OI7.
    """
    records = _records(
        {
            "check_id": "b",
            "phase": "experiment_runs",
            "path": "/repo/data/a.csv",
            "exists": True,
            "sha256": "aa",
            "exists_before": False,
            "sha256_before": "aa",
            "tracked_before": None,
        },
        {
            "check_id": "a",
            "phase": "benchmark_prep",
            "method": "path_exists",
            "path": "/repo/data/a.csv",
            "exists": True,
            "exists_before": None,
            "tracked_before": True,
        },
        {
            "check_id": "a",
            "method": "path_is_file",
            "path": "/repo/data/b.csv",
            "exists_before": False,
            "tracked_before": False,
        },
        {
            "check_id": "a",
            "method": "path_is_file",
            "path": "/repo/data/b.csv",
            "exists_before": False,
            "tracked_before": False,
        },
    )
    rows, first, warnings = _normalize(records)
    assert [row["path"] for row in rows] == ["data/a.csv", "data/b.csv"]
    a, b = rows
    assert a["access_types"] == ["read", "stat"]
    assert a["checks"] == ["a", "b"]
    assert a["phases"] == ["benchmark_prep", "experiment_runs"]
    assert a["at_baseline"] is None  # false and null
    assert a["tracked_at_baseline"] is True  # null and true
    assert a["baseline_sha256"] == "aa"
    assert a["null_sha256"] == "aa"
    assert b["at_baseline"] is False  # false and false
    assert b["tracked_at_baseline"] is False
    assert b["access_types"] == ["stat"]
    assert warnings == []
    assert first[("data/a.csv", None)] is True


@pytest.mark.parametrize(
    ("method", "access"),
    [
        ("read_file_text", "read"),
        ("path_exists", "stat"),
        ("path_is_file", "stat"),
        ("path_is_dir", "stat"),
        ("command", "command"),
    ],
)
def test_normalize_access_types(method: str, access: str) -> None:
    """Each executor method maps to its access type (ACCESS_TYPES).

    Rules: OI3.
    """
    rows, _, _ = _normalize(_records({"method": method, "path": "/repo/f"}))
    assert [row["access_types"] for row in rows] == [[access]]
    assert _module().ACCESS_TYPES[method] == access


def test_access_types_table() -> None:
    """ACCESS_TYPES is the method table of §0.8; run_process has none.

    Rules: OI3, OI6.
    """
    table = dict(_module().ACCESS_TYPES)
    assert table == dict(helpers.ACCESS_TYPES)
    assert "run_process" not in table


def test_normalize_expands_a_glob() -> None:
    """A glob record gives a pattern row and a literal row per match.

    Each literal row lists the pattern's id; a match merges with another
    record of its path; the match contributes no existence (D47).
    Rules: OI4, OI3, OI7.
    """
    records = _records(
        {
            "check_id": "list",
            "method": "glob",
            "path": "/repo/results",
            "glob_pattern": "run*.csv",
            "glob_matches": ["results/run1.csv", "results/run2.csv"],
            "exists": True,
            "exists_before": True,
        },
        {
            "check_id": "read",
            "path": "/repo/results/run1.csv",
            "exists": True,
            "sha256": "11",
            "exists_before": True,
            "sha256_before": "11",
            "tracked_before": True,
        },
    )
    rows, first, _ = _normalize(records)
    assert [(r["input_id"], r["path"], r["pattern"]) for r in rows] == [
        ("i0", "results", "run*.csv"),
        ("i1", "results/run1.csv", None),
        ("i2", "results/run2.csv", None),
    ]
    pattern, run1, run2 = rows
    assert pattern["match_type"] == "pattern"
    assert pattern["access_types"] == ["list"]
    assert pattern["in_under"] is False
    assert pattern["listed_by"] == []
    assert pattern["at_baseline"] is True
    assert run1["match_type"] == "literal"
    assert run1["listed_by"] == ["i0"]
    assert run1["access_types"] == ["list", "read"]
    assert run1["checks"] == ["list", "read"]
    assert run1["at_baseline"] is True
    assert run2["listed_by"] == ["i0"]
    assert run2["access_types"] == ["list"]
    assert run2["in_under"] is True
    assert run2["at_baseline"] is None
    assert run2["null_sha256"] is None
    assert first[("results/run1.csv", None)] is True
    assert first[("results/run2.csv", None)] is None
    assert first[("results", "run*.csv")] is True


def test_normalize_outside_workspace() -> None:
    """A path outside the workspace stays absolute, normalized.

    Rules: OI2.
    """
    records = _records(
        {"path": "/home/agent/./x/../log.txt", "exists": False},
        {"path": "/repo/a", "resolved_workspace_path": "./b/../a"},
    )
    rows, _, _ = _normalize(records)
    inside, outside = rows
    assert (inside["path"], inside["in_workspace"]) == ("a", True)
    assert inside["input_scope"] == "workspace"
    assert outside["path"] == "/home/agent/log.txt"
    assert outside["in_workspace"] is False
    assert outside["input_scope"] == "outside_workspace"


def test_normalize_reference_prefix() -> None:
    """A path under a reference prefix has input_scope reference.

    Rules: OI2.
    """
    records = _records({"path": "/refs/exp.json"}, {"path": "/refsx/y"})
    rows, _, _ = _normalize(records, reference_prefixes=("/refs",))
    scopes = {row["path"]: row["input_scope"] for row in rows}
    assert scopes == {
        "/refs/exp.json": "reference",
        "/refsx/y": "outside_workspace",
    }


def test_normalize_two_hashes() -> None:
    """Two different hashes of one path give null and one warning.

    Rules: OI3.
    """
    records = _records(
        {"path": "/repo/a", "sha256": "aa", "exists": True},
        {"path": "/repo/a", "sha256": "ab", "exists": True},
        {"path": "/repo/b", "sha256": "bb", "exists": True},
        {"path": "/repo/b", "sha256": "bb", "exists": True},
    )
    rows, _, warnings = _normalize(records)
    hashes = {row["path"]: row["null_sha256"] for row in rows}
    assert hashes == {"a": None, "b": "bb"}
    assert warnings == ["oracle input a read with two hashes"]


def test_normalize_null_sha256_from_reads_only() -> None:
    """null_sha256 is the hash of read_file_text records only.

    A stat record that hashed gives no null_sha256 on a null-agent row; on
    a graded row any method that hashed gives grading_sha256.
    Rules: OI3.
    """
    records = _records(
        {"method": "path_is_file", "path": "/repo/a", "sha256": "aa"},
        {"path": "/repo/b", "sha256": "bb"},
        {"method": "path_is_file", "path": "/repo/b", "sha256": "bb"},
    )
    rows, _, _ = _normalize(records)
    assert {r["path"]: r["null_sha256"] for r in rows} == {"a": None, "b": "bb"}
    rows, _, _ = _normalize(records, "graded")
    assert {r["path"]: r["grading_sha256"] for r in rows} == {
        "a": "aa",
        "b": "bb",
    }


def test_normalize_skips_run_process() -> None:
    """run_process records have no path and give no row.

    Rules: OI2, OI6.
    """
    records = _records(
        {"method": "run_process", "path": None, "argv": ["x"]},
        {"path": "/repo/a"},
    )
    rows, first, _ = _normalize(records)
    assert [row["path"] for row in rows] == ["a"]
    assert set(first) == {("a", None)}


def test_normalize_command_record() -> None:
    """A command record (MC-16) gives a row with access type command.

    Rules: OI3, OI6.
    """
    records = _records(
        {"method": "command", "path": "/repo/src/x.c", "check_id": "build"}
    )
    rows, _, _ = _normalize(records)
    assert rows[0]["access_types"] == ["command"]
    assert rows[0]["checks"] == ["build"]


def test_normalize_graded_from_baseline() -> None:
    """A graded row's grading fields and its baseline facts.

    The grading fields come from its own records (any method that hashed);
    at_baseline, tracked_at_baseline and baseline_sha256 from the baseline.
    Rules: OI3.
    """
    records = _records(
        {
            "method": "path_is_file",
            "path": "/repo/data/a.bin",
            "exists": True,
            "sha256": "d1",
            "mtime_ns": 42,
        },
        {"path": "/repo/data/a.bin", "exists": True, "mtime_ns": 50},
    )
    baseline = helpers.make_baseline(
        {"files": {"data/a.bin": {"sha256": "d0"}}}
    )
    rows, _, _ = _normalize(records, "graded", baseline=baseline)
    (row,) = rows
    assert row["source_type"] == "graded"
    assert row["at_baseline"] is True
    assert row["tracked_at_baseline"] is True
    assert row["baseline_sha256"] == "d0"
    assert row["grading_exists"] is True
    assert row["grading_sha256"] == "d1"
    assert row["grading_mtime_ns"] == 42
    assert row["null_sha256"] is None


def test_normalize_graded_borrows_pre_oracle() -> None:
    """A graded untracked row borrows the pre-oracle pass (OS19, OS20).

    Without pre_oracle its baseline existence and hash are null and
    tracked_at_baseline is false (a baseline was given) or null (none).
    Rules: OI3.
    """
    records = _records({"path": "/repo/out/new.csv", "exists": True})
    baseline = helpers.make_baseline({"files": {"x": {}}})
    pre = {
        ("out/new.csv", None): _row(
            path="out/new.csv",
            at_baseline=False,
            baseline_sha256=None,
            tracked_at_baseline=False,
        )
    }
    rows, _, _ = _normalize(
        records, "graded", baseline=baseline, pre_oracle=pre
    )
    assert (
        rows[0]["at_baseline"],
        rows[0]["baseline_sha256"],
        rows[0]["tracked_at_baseline"],
    ) == (False, None, False)
    pre[("out/new.csv", None)].update(at_baseline=True, baseline_sha256="nn")
    rows, _, _ = _normalize(
        records, "graded", baseline=baseline, pre_oracle=pre
    )
    assert (rows[0]["at_baseline"], rows[0]["baseline_sha256"]) == (True, "nn")
    rows, _, _ = _normalize(records, "graded", baseline=baseline)
    assert (
        rows[0]["at_baseline"],
        rows[0]["baseline_sha256"],
        rows[0]["tracked_at_baseline"],
    ) == (None, None, False)
    rows, _, _ = _normalize(records, "graded", baseline=None)
    assert rows[0]["tracked_at_baseline"] is None


def test_normalize_first_record_exists() -> None:
    """The returned first-record exists when a later record disagrees.

    Rules: OI3, OI10.
    """
    records = _records(
        {"path": "/repo/a", "exists": False},
        {"path": "/repo/a", "exists": True, "sha256": "aa"},
    )
    rows, first, _ = _normalize(records, "graded")
    assert first == {("a", None): False}
    assert rows[0]["grading_exists"] is True


def test_normalize_leaves_oracle_writes_null() -> None:
    """Every returned row has oracle_writes and its basis null.

    Rules: OI10.
    """
    records = _records(
        {"path": "/repo/a", "exists": True, "exists_before": False},
        {"method": "glob", "path": "/repo/d", "glob_pattern": "*"},
    )
    rows, _, _ = _normalize(records)
    assert rows
    for row in rows:
        assert row["oracle_writes"] is None
        assert row["oracle_writes_basis"] is None
        assert set(row) == helpers.ROW_FIELDS


# --------------------------------------------------------------- null_value

NULL_VALUES = (
    # (at_baseline, first exists, baseline_sha256, null_sha256) -> value
    ((False, True, None, "bb"), True),
    ((True, True, "aa", "aa"), False),
    ((True, True, "aa", "AA"), False),
    ((True, True, "aa", "ab"), True),
    ((False, False, None, None), False),
    ((None, True, None, "aa"), None),
    ((True, None, "aa", None), None),
    ((True, True, "aa", None), None),
    ((True, True, None, "aa"), None),
    ((True, False, "aa", None), True),
)


@pytest.mark.parametrize(("args", "want"), NULL_VALUES)
def test_null_value(args: tuple, want: bool | None) -> None:
    """The null-agent value of oracle_writes for each combination.

    Rules: OI3.
    """
    assert _module().null_value(*args) is want


# ------------------------------------------------------------- end_of_agent

FINAL = {
    "a.csv": {"path": "a.csv", "status": "A", "sha256": "a1"},
    "m.csv": {"path": "m.csv", "status": "M", "sha256": None},
    "d.csv": {"path": "d.csv", "status": "D", "sha256": None},
}
END_CASES = (
    ("a.csv", None, None, (True, "a1")),
    ("m.csv", True, "m0", (True, None)),
    ("d.csv", True, "d0", (False, None)),
    ("t.csv", True, "t0", (True, "t0")),
    ("u.csv", False, None, (None, None)),
    ("u.csv", None, None, (None, None)),
)


@pytest.mark.parametrize(("path", "tracked", "sha", "want"), END_CASES)
def test_end_of_agent(
    path: str, tracked: bool | None, sha: str | None, want: tuple
) -> None:
    """End-of-agent existence and hash from final_files (D36).

    Listed A or M: exists with its hash; D: absent; not listed and tracked
    at the baseline: the baseline state; otherwise unknown.
    Rules: OI10.
    """
    files = {key: helpers.make_final_files([v])[0] for key, v in FINAL.items()}
    got = _module().end_of_agent(
        files, path, tracked_at_baseline=tracked, baseline_sha256=sha
    )
    assert tuple(got) == want


def test_end_of_agent_without_final_files() -> None:
    """With no final_files.json the state is unknown.

    Rules: OI10.
    """
    got = _module().end_of_agent(
        None, "t.csv", tracked_at_baseline=True, baseline_sha256="t0"
    )
    assert tuple(got) == (None, None)


def test_derive_normalizes_final_files_paths() -> None:
    """A final_files entry written as an absolute path is normalized.

    end_of_agent takes an index by workspace-relative path; derive builds
    it with match.relative_path (D55).
    Rules: OI10, OI2.
    """
    raw = helpers.read_raw(
        helpers.SCENARIOS_DIR / "OS17_exact_oracle_writes.yaml"
    )
    for entry in raw["final_files"]:
        entry["path"] = "/repo/" + entry["path"]
    result = helpers.run_derive(helpers.build_scenario(raw))
    writes = {
        r["path"]: (r["oracle_writes"], r["oracle_writes_basis"])
        for r in result["oracle_inputs"]
    }
    assert writes["data/a.csv"] == (True, "exact")
    assert writes["data/b.csv"] == (False, "exact")


# ------------------------------------------------------------- oracle_writes

EXACT_CASES = (
    # (end, first exists, grading_sha256) -> exact value or None
    ((True, "h1"), True, "h1", False),
    ((True, "h1"), True, "h2", True),
    ((True, "h1"), False, None, True),
    ((True, "h1"), None, None, None),
    ((True, "h1"), True, None, None),
    ((False, None), True, "h2", True),
    ((False, None), False, None, False),
    ((False, None), None, None, None),
    ((None, None), True, "h2", None),
    ((None, None), False, None, None),
    ((True, None), True, "h2", None),
)


@pytest.mark.parametrize(("end", "first", "grading", "want"), EXACT_CASES)
def test_oracle_writes_exact(
    end: tuple, first: bool | None, grading: str | None, want: bool | None
) -> None:
    """OI10's exact form from each combination; no value falls back.

    With the fallback null, a side unknown gives (null, null).
    Rules: OI10.
    """
    row = _row(source_type="graded", grading_sha256=grading)
    got = _module().oracle_writes(row, first, end=end, null_value=None)
    assert tuple(got) == ((want, "exact") if want is not None else (None, None))


@pytest.mark.parametrize("fallback", [True, False, None])
def test_oracle_writes_fallback(fallback: bool | None) -> None:
    """The fallback when the exact form gives no value or does not apply.

    Rules: OI10.
    """
    row = _row(source_type="graded", grading_sha256="h1")
    want = (fallback, "fallback") if fallback is not None else (None, None)
    module = _module()
    assert tuple(
        module.oracle_writes(row, True, end=None, null_value=fallback)
    ) == (want)
    unknown = module.oracle_writes(
        row, True, end=(None, None), null_value=fallback
    )
    assert tuple(unknown) == want
    exact = module.oracle_writes(
        row, True, end=(True, "h1"), null_value=fallback
    )
    assert tuple(exact) == (False, "exact")


@pytest.mark.parametrize("match_type", ["pattern", "pattern_match"])
def test_oracle_writes_not_on_listings(match_type: str) -> None:
    """Pattern and pattern_match rows have no oracle_writes.

    Rules: OI10.
    """
    row = _row(
        match_type=match_type, pattern="*" if match_type == "pattern" else None
    )
    got = _module().oracle_writes(row, True, end=(False, None), null_value=True)
    assert tuple(got) == (None, None)


# --------------------------------------------------------------- instantiate


def _summary(path: str, final_state: str = "untracked", **extra: Any) -> dict:
    row = {
        "path": path,
        "in_workspace": True,
        "tracked": final_state != "untracked",
        "events": [],
        "final_writer": None,
        "final_writer_under": False,
        "final_state": final_state,
        "unattributed_candidates": [],
    }
    row.update(extra)
    return row


def _pattern_rows() -> list[dict[str, Any]]:
    return [
        _row(
            input_id="i0",
            path="results",
            pattern="*.csv",
            match_type="pattern",
            in_under=False,
            access_types=["list"],
            checks=["csv"],
            phases=["experiment_runs"],
            at_baseline=True,
        ),
        _row(
            input_id="i1",
            path="results",
            pattern="run*",
            match_type="pattern",
            in_under=False,
            access_types=["list"],
            checks=["runs"],
            phases=["benchmark_prep"],
            at_baseline=True,
        ),
        _row(
            input_id="i2",
            path="results/a.csv",
            listed_by=["i0"],
            access_types=["list"],
            checks=["csv"],
        ),
    ]


def test_instantiate_adds_pattern_matches() -> None:
    """Run paths matching a pattern become pattern_match rows.

    A deleted path, a literal row's path, an outside path and a
    non-matching path give no new row; a path matched by two patterns is
    listed by both; ids are renumbered by OI7 with listed_by remapped.
    Rules: OI5, OI7.
    """
    paths = [
        _summary("results/run2.csv"),
        _summary("results/b.csv"),
        _summary("results/gone.csv", "deleted"),
        _summary("results/a.csv", "changed"),
        _summary("results/x/c.csv"),
        _summary("/tmp/results/d.csv", in_workspace=False),
        _summary("aaa.csv"),
    ]
    rows = _module().instantiate(_pattern_rows(), paths, source_type="graded")
    got = [
        (r["input_id"], r["path"], r["pattern"], r["match_type"]) for r in rows
    ]
    assert got == [
        ("i0", "results", "*.csv", "pattern"),
        ("i1", "results", "run*", "pattern"),
        ("i2", "results/a.csv", None, "literal"),
        ("i3", "results/b.csv", None, "pattern_match"),
        ("i4", "results/run2.csv", None, "pattern_match"),
    ]
    a, b, run2 = rows[2], rows[3], rows[4]
    assert a["listed_by"] == ["i0"]
    assert b["listed_by"] == ["i0"]
    assert run2["listed_by"] == ["i0", "i1"]
    assert run2["checks"] == ["csv", "runs"]
    assert run2["phases"] == ["benchmark_prep", "experiment_runs"]
    assert run2["access_types"] == ["list"]
    for row in (b, run2):
        assert row["in_under"] is False
        assert row["at_baseline"] is False
        assert row["source_type"] == "graded"
        assert row["in_workspace"] is True
        assert row["input_scope"] == "workspace"
        for field in (
            *("baseline_sha256", "tracked_at_baseline", "null_sha256"),
            *("oracle_writes", "oracle_writes_basis", "grading_exists"),
            *("grading_sha256", "grading_mtime_ns"),
        ):
            assert row[field] is None, field


def test_instantiate_remaps_listed_by() -> None:
    """A new row sorting before a pattern row renumbers it and its links.

    Rules: OI5, OI7.
    """
    rows = [
        _row(
            input_id="i0",
            path="out",
            pattern="*",
            match_type="pattern",
            in_under=False,
            access_types=["list"],
        ),
        _row(
            input_id="i1",
            path="out/sub",
            pattern="*.csv",
            match_type="pattern",
            in_under=False,
            access_types=["list"],
        ),
        _row(input_id="i2", path="out/sub/z.csv", listed_by=["i1"]),
    ]
    got = _module().instantiate(
        rows, [_summary("out/a.txt")], source_type="null_agent"
    )
    assert [(r["input_id"], r["path"]) for r in got] == [
        ("i0", "out"),
        ("i1", "out/a.txt"),
        ("i2", "out/sub"),
        ("i3", "out/sub/z.csv"),
    ]
    assert got[1]["listed_by"] == ["i0"]
    assert got[1]["source_type"] == "null_agent"
    assert got[3]["listed_by"] == ["i2"]


# --------------------------------------------------------------- writes_basis

BASES = (
    (["exact", "exact"], True, "exact"),
    (["fallback"], True, "fallback"),
    (["exact", "fallback"], False, "mixed"),
    ([None], True, "exact"),
    ([None], False, "fallback"),
    ([], False, "fallback"),
)


@pytest.mark.parametrize(("bases", "capability", "want"), BASES)
def test_writes_basis(bases: list, capability: bool, want: str) -> None:
    """bases.oracle_writes from the rows' bases, else the capability form.

    Rules: OI10.
    """
    rows = [
        _row(
            input_id=f"i{n}",
            oracle_writes=None if basis is None else False,
            oracle_writes_basis=basis,
        )
        for n, basis in enumerate(bases)
    ]
    assert _module().writes_basis(rows, capability) == want


# -------------------------------------------------------------------- scores


def _score(**phases: str) -> dict[str, Any]:
    return helpers.expand_score({"phases": phases}, null=False)


def test_scores_four_phases() -> None:
    """NS1 to NS3 on four-phase results.

    Rules: NS1, NS2, NS3.
    """
    got = _module().scores(
        _score(a="error", b="success", c="success", d="failure"),
        _score(a="success", b="success", c="failure", d="success"),
    )
    assert got == {
        "null_score": 2,
        "null_score_max": 4,
        "null_phases_passed": ["b", "c"],
        "run_score": 3,
        "run_score_max": 4,
        "score_gain": 1,
        "phases_passed_by_null": ["b"],
        "phases_gained": ["a", "d"],
        "phases_lost": ["c"],
    }


def test_scores_two_and_three_phases() -> None:
    """The maximum is the number of phases, never a constant.

    A run result with an extra phase counts it.
    Rules: NS1, NS2, NS3.
    """
    got = _module().scores(
        _score(b="success", a="success"),
        _score(a="success", b="error", c="success"),
    )
    assert got["null_score_max"] == 2
    assert got["null_phases_passed"] == ["a", "b"]
    assert got["run_score_max"] == 3
    assert got["score_gain"] == 0
    assert got["phases_gained"] == ["c"]
    assert got["phases_lost"] == ["b"]
    assert got["phases_passed_by_null"] == ["a"]


def test_scores_read_not_recomputed() -> None:
    """The score is read from the result, never recomputed.

    Rules: NS2.
    """
    null, run = _score(a="success"), _score(a="success", b="success")
    run["score"] = 7
    got = _module().scores(null, run)
    assert (got["run_score"], got["score_gain"]) == (7, 6)


def test_scores_missing_parts() -> None:
    """NS4: the parts of M3.5 a missing result voids.

    Without the null result every key is null; without the run result the
    run fields and the phase comparison are.

    Rules: NS4.
    """
    module = _module()
    none = module.scores(None, _score(a="success"))
    assert set(none) == helpers.M35_KEYS
    assert all(value is None for value in none.values())
    half = module.scores(_score(a="success", b="error"), None)
    assert (half["null_score"], half["null_score_max"]) == (1, 2)
    assert half["null_phases_passed"] == ["a"]
    for key in (
        *("run_score", "run_score_max", "score_gain"),
        *("phases_passed_by_null", "phases_gained", "phases_lost"),
    ):
        assert half[key] is None, key


# ------------------------------------------------------------------- derive


def test_derive_rejects_unknown_config() -> None:
    """A config key not in DEFAULTS raises ValueError.

    Rules: OI2.
    """
    scenario = helpers.load_scenario(
        helpers.SCENARIOS_DIR / "OS01_null_manifest_read_stat_glob.yaml"
    )
    module = _module()
    assert dict(module.DEFAULTS) == {"reference_prefixes": ()}
    with pytest.raises(ValueError):
        helpers.run_derive(scenario, config={"no_such_key": 1})


def test_derive_reads_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module reads no file (D32): open is never called.

    Rules: OI1, OI10.
    """
    scenario = helpers.load_scenario(
        helpers.SCENARIOS_DIR / "OS17_exact_oracle_writes.yaml"
    )
    module = _module()

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"file opened: {args}")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(pathlib.Path, "open", refuse)
    monkeypatch.setattr(pathlib.Path, "read_text", refuse)
    result = module.derive(
        scenario["linked"],
        **{
            key: copy.deepcopy(scenario[key])
            for key in ("run", *helpers.DERIVE_ARGS)
        },
    )
    assert result["oracle_inputs"]


def test_derive_preconditions_table() -> None:
    """PRECONDITIONS holds the four labels of §0.5; BASES the three keys.

    Rules: OI1, OI5, OI9.
    """
    module = _module()
    labels = [row[0] for row in module.PRECONDITIONS]
    assert labels == list(helpers.PRECONDITION_LABELS)
    assert {row[0] for row in module.BASES} == helpers.BASES_KEYS


def test_derive_metric_values_are_rounded() -> None:
    """Rows and block pass match.round_floats unchanged (outputs rounded).

    Rules: OI7.
    """
    from trajectory_checks import match

    scenario = helpers.load_scenario(
        helpers.SCENARIOS_DIR / "OS21_synthetic_example.yaml"
    )
    result = helpers.run_derive(scenario)
    assert match.round_floats(result) == result
    assert conftest.canonical(result) == conftest.canonical(
        helpers.run_derive(scenario)
    )


def test_derive_phases_complete_per_check() -> None:
    """A phase is complete only when each of its command checks has reads.

    A command record of another check does not complete it (OI6).
    Rules: OI6.
    """
    raw = {
        "null_manifest": [
            *(
                {
                    "check_id": c,
                    "phase": "env_setup",
                    "method": "run_process",
                    "path": None,
                }
                for c in ("a", "b")
            ),
            {
                "check_id": "b",
                "phase": "env_setup",
                "method": "command",
                "path": "/repo/x",
            },
            {
                "check_id": "c",
                "phase": "build",
                "method": "run_process",
                "path": None,
            },
            {
                "check_id": "c",
                "phase": "build",
                "method": "command",
                "path": "/repo/y",
            },
        ],
        "null_score": {"phases": {"env_setup": "success"}},
    }
    result = helpers.run_derive(helpers.build_scenario(raw))
    m31 = result["oracle_inputs_run"]["metrics"]["M3.1"]
    assert m31["command_checks"] == 3
    assert m31["command_reads_recorded"] is True
    assert m31["phases_complete"] == {"build": True, "env_setup": False}
    warnings = result["oracle_inputs_run"]["warnings"]
    assert not any("MC-16" in w for w in warnings)


# ------------------------------------------------ cycle-2 critic: A7, S1

_GRADED = {"check_id": "summary", "path": "/repo/out/new.csv", "exists": True}
_PRE_ORACLE = {**_GRADED, "exists": False, "exists_before": False}


def test_derive_borrows_past_pathless_null_records() -> None:
    """A null manifest's run_process and pathless records hold no row.

    On a graded run the borrow set reads only the null rows that hold
    their own record (D71); a record with no path is not one, and the
    untracked row still borrows the pre-oracle pass (OS19's values).
    Rules: OI3, OI6.
    """
    raw = {
        "run": {"capabilities": {"oracle_reads": True}},
        "baseline": {"files": {"data/a.csv": {"sha256": "aa"}}},
        "graded_manifest": [_GRADED],
        "null_manifest": [
            {"check_id": "make", "method": "run_process", "path": None},
            {"check_id": "make", "method": "command", "path": None},
            {**_PRE_ORACLE, "tracked_before": False},
        ],
        "null_score": {"phases": {"experiment_runs": "success"}},
    }
    result = helpers.run_derive(helpers.build_scenario(raw))
    (row,) = result["oracle_inputs"]
    assert (row["path"], row["source_type"]) == ("out/new.csv", "graded")
    assert (row["at_baseline"], row["tracked_at_baseline"]) == (False, False)


def _two_hashes(graded: bool, commit: str = "c0") -> list[str]:
    """The warnings of derive when the null manifest has two hashes."""
    raw: dict[str, Any] = {
        "null_manifest": [
            {"check_id": "a", "path": "/repo/data/a.csv", "sha256": "aa"},
            {"check_id": "b", "path": "/repo/data/a.csv", "sha256": "bb"},
        ],
        "null_score": {"phases": {"experiment_runs": "success"}},
        "baseline": {"files": {"data/a.csv": {"sha256": "aa"}}},
    }
    raw["null_score"]["commit"] = commit
    if graded:
        raw["run"] = {"capabilities": {"oracle_reads": True}}
        raw["graded_manifest"] = [{"path": "/repo/data/a.csv", "sha256": "aa"}]
    result = helpers.run_derive(helpers.build_scenario(raw))
    return result["oracle_inputs_run"]["warnings"]


def test_null_manifest_warnings_follow_the_source() -> None:
    """The null manifest's warnings are the run's only when it is the source.

    On a graded run it is used only for borrowed facts, with no warning
    (OI1); on a null-agent run its OI3 warning is given, also when the
    null commit differs and no row is kept (OI9).
    Rules: OI1, OI3, OI9.
    """
    warning = "oracle input data/a.csv read with two hashes"
    assert warning not in _two_hashes(graded=True)
    assert _two_hashes(graded=False).count(warning) == 1
    assert _two_hashes(graded=False, commit="c1").count(warning) == 1
