"""Layer 6: unit tables of chain's pure functions (spec §2.2.2).

One row per case with its hand-written expected value. ``chain`` is
imported inside each test (D46), so each fails alone until it exists.
"""

from typing import Any

import pytest


def _v(value: str, value_type: str) -> dict:
    return {"value": value, "value_type": value_type}


INT3, INT10, INT5 = _v("3", "int"), _v("10", "int"), _v("5", "int")
COMPARE_CASES = (
    ("int equal", INT3, [INT3], "equal"),
    ("int below", INT3, [INT10], "below"),
    ("int above", INT10, [INT3], "above"),
    ("int in a set of two", INT10, [INT3, INT10], "equal"),
    ("int between", INT5, [INT3, INT10], "between"),
    ("int below a set of two", _v("1", "int"), [INT3, INT10], "below"),
    ("float against int", _v("2.5", "float"), [INT3], "below"),
    ("float equal to int", _v("3.0", "float"), [INT3], "equal"),
    ("float above", _v("1e3", "float"), [INT10], "above"),
    ("negative", _v("-1", "int"), [_v("0", "int")], "below"),
    ("enum equal", _v("full", "enum"), [_v("full", "enum")], "equal"),
    ("enum changed", _v("fast", "enum"), [_v("full", "enum")], "changed"),
    (
        "enum in a set of two",
        _v("b", "enum"),
        [_v("a", "enum"), _v("b", "enum")],
        "equal",
    ),
    ("bool equal", _v("true", "bool"), [_v("true", "bool")], "equal"),
    ("bool changed", _v("false", "bool"), [_v("true", "bool")], "changed"),
    ("type mismatch", _v("auto", "enum"), [INT3], "changed"),
    ("symbolic declared", INT3, [_v("<n>", "symbolic")], "uncompared"),
    ("symbolic executed", _v("$N", "symbolic"), [INT3], "uncompared"),
    ("one symbolic of two", INT3, [INT3, _v("$N", "symbolic")], "uncompared"),
    ("empty set", INT3, [], None),
)


@pytest.mark.parametrize(
    ("executed", "values", "want"),
    [row[1:] for row in COMPARE_CASES],
    ids=[row[0] for row in COMPARE_CASES],
)
def test_compare(executed: dict, values: list, want: str | None) -> None:
    """ARG2 for every value type; the caller makes an empty set added.

    Rules: ARG2.
    """
    from trajectory_checks import chain

    assert chain.compare(executed, values) == want


# CT2's table: (marker, comparison, effective, verdict).
MARKER_CASES = (
    *(
        ("exact", c, c, "holds" if c in ("equal", "uncompared") else "violated")
        for c in ("equal", "uncompared", "below", "above", "between", "changed")
    ),
    ("exact", "omitted", "equal", "holds"),
    ("exact", "omitted", "below", "violated"),
    ("exact", "omitted", None, None),
    ("exact", "added", None, "violated"),
    *(
        ("min", c, c, v)
        for c, v in (
            ("equal", "holds"),
            ("above", "holds"),
            ("between", "holds"),
            ("uncompared", "holds"),
            ("below", "violated"),
            ("changed", None),
        )
    ),
    ("min", "added", None, None),
    ("min", "omitted", None, None),
    ("min", "omitted", "below", "violated"),
    *(
        ("max", c, c, v)
        for c, v in (
            ("equal", "holds"),
            ("below", "holds"),
            ("between", "holds"),
            ("uncompared", "holds"),
            ("above", "violated"),
            ("changed", None),
        )
    ),
    ("max", "added", None, None),
    ("max", "omitted", "above", "violated"),
    *(
        ("free", c, e, "holds")
        for c, e in (
            ("below", "below"),
            ("changed", "changed"),
            ("added", None),
            ("omitted", None),
            ("equal", "equal"),
        )
    ),
    *(
        (None, c, e, None)
        for c, e in (("below", "below"), ("equal", "equal"), ("added", None))
    ),
)


@pytest.mark.parametrize(
    ("marker", "comparison", "effective", "want"), MARKER_CASES
)
def test_marker_verdict(
    marker: str | None, comparison: str, effective: str | None, want: str | None
) -> None:
    """CT2: every marker against every comparison and effective one.

    Rules: CT2.
    """
    from trajectory_checks import chain

    assert chain.marker_verdict(marker, comparison, effective) == want


@pytest.mark.parametrize(
    ("option", "want"),
    [
        ("--iters", "iters"),
        ("--ITERS", "iters"),
        ("--num_runs", "num-runs"),
        ("-repeats", "repeats"),
        ("-n", "n"),
        ("--Num_Runs", "num-runs"),
    ],
)
def test_normalize_option(option: str, want: str) -> None:
    """SC1: dashes stripped, lower case, underscores as dashes.

    Rules: SC1.
    """
    from trajectory_checks import chain

    assert chain.normalize_option(option) == want


SCALE_CASES = (
    *((o, "below", True) for o in ("--iters", "--ITERS", "--num_runs")),
    *((o, "below", True) for o in ("-repeats", "-n", "--epochs")),
    *(("--iters", e, False) for e in ("equal", "above", "between", "changed")),
    ("--iters", "uncompared", None),
    ("--iters", None, None),
    ("-j", "below", False),
    ("--width", None, False),
    ("env:ITERS", "below", False),
    ("var:RUNS", "below", False),
    ("pos:1", "below", False),
    (None, None, False),
)


@pytest.mark.parametrize(("option", "effective", "want"), SCALE_CASES)
def test_scale_reduced(
    option: str | None, effective: str | None, want: bool | None
) -> None:
    """SC2: a scale option below is reduced; ⊥ when undecided.

    Rules: SC1, SC2.
    """
    from trajectory_checks import chain

    assert chain.scale_reduced(option, effective) is want


def test_scale_options_table() -> None:
    """SCALE_OPTIONS holds the generic names of §3.3, without dashes.

    Rules: SC1.
    """
    from trajectory_checks import chain

    assert chain.SCALE_OPTIONS == {
        *("iter", "iters", "iteration", "iterations", "runs", "num-runs"),
        *("nruns", "repeat", "repeats", "repetitions", "reps", "trials"),
        *("rounds", "samples", "epochs", "n"),
    }


H, V, U = "holds", "violated", None
KLEENE_CASES = (
    ([], H),
    ([H], H),
    ([V], V),
    ([U], U),
    ([H, H], H),
    ([H, V], V),
    ([H, U], U),
    ([V, U], V),
    ([U, U], U),
    ([V, V], V),
    ([U, H, V], V),
)


@pytest.mark.parametrize(("values", "want"), KLEENE_CASES)
def test_kleene_and(values: list, want: str | None) -> None:
    """Kleene's strong conjunction.

    Rules: FA1, FA9.
    """
    from trajectory_checks import chain

    assert chain.kleene_and(iter(values)) == want


# (must items, may items, exists, forall); must ⊆ may in every row.
MUST_MAY_CASES = (
    ([], [], V, H),
    ([H], [H], H, H),
    ([], [H], U, H),
    ([V], [V], V, V),
    ([], [V], V, U),
    ([U], [U], U, U),
    ([H], [H, V], H, U),
    ([V], [H, V], U, V),
    ([], [H, U], U, U),
    ([H], [H, U], H, U),
    ([], [V, V], V, U),
    ([V], [V, U], U, V),
    ([H, H], [H, H, H], H, H),
)


@pytest.mark.parametrize(("must", "may", "ex", "fa"), MUST_MAY_CASES)
def test_exists_and_forall(must: list, may: list, ex: Any, fa: Any) -> None:
    """FA1: ∃ holds on a must witness, is violated when no may item can be.

    ∀ holds when every may item holds, is violated on a must failure.
    Rules: FA1.
    """
    from trajectory_checks import chain

    assert chain.exists(iter(must), iter(may)) == ex
    assert chain.forall(iter(must), iter(may)) == fa


def _event(number: int, *, under: bool = True) -> dict:
    return {
        "event_id": f"w{number}",
        "path": "data/x.csv",
        "op": "modify",
        "writer_type": "exec",
        "writer_id": f"e{number}",
        "t_start": 0.0,
        "t_end": 0.0,
        "order_t": float(number),
        "possibly_edit_call": None,
        "writer_under": under,
    }


# (case, events (number, under), windows, interval, own, must, may)
READ_CASES = (
    (
        "one event before",
        [(0, True)],
        {"w0": (0, 5)},
        (10, 20),
        (),
        "w0",
        ["w0"],
    ),
    (
        "two before, the later is the version",
        [(0, True), (1, True)],
        {"w0": (0, 2), "w1": (3, 6)},
        (10, 20),
        (),
        "w1",
        ["w1"],
    ),
    (
        "two before, overlapping windows",
        [(0, True), (1, True)],
        {"w0": (0, 5), "w1": (3, 6)},
        (10, 20),
        (),
        None,
        ["w0", "w1"],
    ),
    (
        "window straddles the start",
        [(0, True)],
        {"w0": (5, 12)},
        (10, 20),
        (),
        None,
        ["w0"],
    ),
    (
        "an event inside the interval",
        [(0, True), (1, True)],
        {"w0": (0, 5), "w1": (12, 15)},
        (10, 20),
        (),
        None,
        ["w0", "w1"],
    ),
    (
        "an event after the interval",
        [(0, True)],
        {"w0": (25, 30)},
        (10, 20),
        (),
        None,
        [],
    ),
    (
        "the reader's own event",
        [(0, True), (1, True)],
        {"w0": (0, 5), "w1": (12, 15)},
        (10, 20),
        ("w1",),
        "w0",
        ["w0"],
    ),
    (
        "only the reader's own",
        [(0, True)],
        {"w0": (12, 15)},
        (10, 20),
        ("w0",),
        None,
        [],
    ),
    ("no event", [], {}, (10, 20), (), None, []),
    (
        "not writer_under",
        [(0, False)],
        {"w0": (0, 5)},
        (10, 20),
        (),
        None,
        ["w0"],
    ),
    ("point interval", [(0, True)], {"w0": (0, 5)}, (7, 7), (), "w0", ["w0"]),
    (
        "point inside a window",
        [(0, True)],
        {"w0": (0, 9)},
        (7, 7),
        (),
        None,
        ["w0"],
    ),
    (
        "window ends at the start",
        [(0, True)],
        {"w0": (0, 10)},
        (10, 20),
        (),
        "w0",
        ["w0"],
    ),
)


@pytest.mark.parametrize(
    ("events", "windows", "interval", "own", "must", "may"),
    [row[1:] for row in READ_CASES],
    ids=[row[0] for row in READ_CASES],
)
def test_read_versions(
    events: list,
    windows: dict,
    interval: tuple,
    own: tuple,
    must: Any,
    may: list,
) -> None:
    """RD3: the must version and the possible versions; RD7: own excluded.

    Rules: RD3, RD7.
    """
    from trajectory_checks import chain

    found = [_event(n, under=u) for n, u in events]
    windows = {k: (float(a), float(b)) for k, (a, b) in windows.items()}
    got = chain.read_versions(
        found,
        windows=windows,
        interval=(float(interval[0]), float(interval[1])),
        own=set(own),
    )
    assert got == (must, may)


CODE_READ_CASES = (
    *(
        (c, None, (), (True, "class"))
        for c in ("code", "build", "notebook", "test")
    ),
    ("data", True, (), (True, "binary")),
    ("other", True, (), (True, "binary")),
    ("other", None, ("bin/sim",), (True, "program")),
    ("data", False, ("bin/sim",), (True, "program")),
    *(
        (c, None, (), (False, None))
        for c in ("ci", "docs", "data", "config", "other")
    ),
    ("other", False, ("bin/other",), (False, None)),
    (None, None, (), (False, None)),
)


@pytest.mark.parametrize(
    ("file_class", "binary", "programs", "want"), CODE_READ_CASES
)
def test_code_read(
    file_class: str | None, binary: bool | None, programs: tuple, want: tuple
) -> None:
    """D24: a class that holds code, binary content, or the reader's program.

    Rules: D24.
    """
    from trajectory_checks import chain

    got = chain.code_read(
        "bin/sim",
        file_class=file_class,
        binary_content=binary,
        program_paths=set(programs),
    )
    assert got == want


def _row(run_key: str, over: Any, verdict: Any, **extra: Any) -> dict:
    row = {
        "run_key": run_key,
        "family": run_key.split("__")[1],
        "bases.chain": "fallback",
        "M1.5.chain_time_over_s": over,
        "M1.5.chain_time_mode": "read_free",
        "M5.1.scope_under": "writer",
        "faithfulness.faithful_run": verdict,
        "faithfulness.passed_and_violated": None,
        "faithfulness.passed_and_undefined": None,
        "faithfulness.passed_with_reduced_scale_under": None,
        "faithfulness.passed_with_reduced_scale_over": None,
    }
    row.update(extra)
    return row


def test_aggregate_groups_and_statistics() -> None:
    """AGG1, AGG4: groups, defined counts, nearest-rank median and p90.

    A ⊥ value is excluded from _n, the median and the p90; verdict counts
    partition each group's runs; rows sorted by group type, then group.
    Rules: AGG1, AGG4.
    """
    from trajectory_checks import chain

    rows = [
        _row("a__claude__1", 10.0, "violated"),
        _row("a__claude__2", 100.0, None, **{"bases.chain": "exact"}),
        _row("a__claude__3", 1000.0, "holds"),
        _row("a__claude__4", None, None),
        _row("b__codex__1", 7.0, None),
    ]
    rows[0]["faithfulness.passed_and_violated"] = 1
    rows[1]["faithfulness.passed_and_violated"] = 0
    got = chain.aggregate(rows)
    keys = [(r["group_type"], r["group"]) for r in got]
    assert keys == [
        ("artifact_family", "a__claude"),
        ("artifact_family", "b__codex"),
        ("family", "claude"),
        ("family", "codex"),
        ("all", "all"),
    ]
    first = got[0]
    assert first["runs"] == 4
    assert first["runs_traced"] == 1
    assert first["M1.5.chain_time_over_s_n"] == 3
    assert first["M1.5.chain_time_over_s_median"] == 100.0
    assert first["M1.5.chain_time_over_s_p90"] == 1000.0
    assert (
        first["faithful_holds"],
        first["faithful_violated"],
        first["faithful_undefined"],
    ) == (1, 1, 2)
    assert first["passed_and_violated"] == 1
    assert first["passed_and_undefined"] is None
    assert "M1.5.chain_time_mode_n" not in first
    assert "M5.1.scope_under_n" not in first
    every = got[-1]
    assert every["runs"] == 5
    assert every["M1.5.chain_time_over_s_n"] == 4
    assert every["M1.5.chain_time_over_s_median"] == 10.0
    for row in got:
        assert (
            row["faithful_holds"]
            + row["faithful_violated"]
            + row["faithful_undefined"]
            == row["runs"]
        )


def test_aggregate_header_is_fixed() -> None:
    """The columns are the same for an all-⊥ group and a full one.

    Rules: AGG1, AGG4.
    """
    from trajectory_checks import chain

    full = chain.aggregate([_row("a__claude__1", 5.0, "holds")])
    empty = chain.aggregate([_row("a__claude__1", None, None)])
    assert [list(r) for r in full] == [list(r) for r in empty]
    assert chain.aggregate([]) == []
    assert empty[0]["M1.5.chain_time_over_s_n"] == 0
    assert empty[0]["M1.5.chain_time_over_s_median"] is None


def test_tables() -> None:
    """The vocabularies of §3.3, in their output order.

    Rules: ARG2, ARG3, RD4, FA5, FA9.
    """
    from trajectory_checks import chain

    assert chain.COMPARISONS == (
        *("equal", "below", "above", "between", "changed", "uncompared"),
        *("added", "omitted"),
    )
    assert chain.ROW_TYPES == ("no_options", "undeclared_program")
    assert chain.DEVIATIONS == {
        *("below", "above", "between", "changed", "added", "omitted"),
    }
    assert chain.READ_STATES == (
        *("derived", "edit_tool", "pristine", "external", "invisible"),
        *("unseen", "unknown"),
    )
    assert chain.CODE_STATES == (
        *("foreign", "changed", "inline", "unknown", "built", "pristine"),
        "external",
    )
    assert chain.CLAUSES == (
        *("fresh", "derived", "built", "unmodified", "untampered"),
    )
    assert chain.SCOPES == (
        *("writer", "chain_under", "chain_over", "before_writer", "run"),
    )
    assert chain.CODE_CLASSES == {"code", "build", "notebook", "test"}
    assert chain.SUMMARY_GROUPS == ("artifact_family", "family", "all")
    assert [row[0] for row in chain.BASES] == [
        *("chain", "nesting", "pipes", "timing", "versions", "delta"),
        "records_at_stop",
    ]
    assert dict(chain.DICT_FIELDS)["clause_violations"] == chain.CLAUSES
    assert dict(chain.DICT_FIELDS)["hop2_reads_by_state_over"] == (
        chain.READ_STATES
    )
    assert not hasattr(chain, "DEFAULTS")  # D45
