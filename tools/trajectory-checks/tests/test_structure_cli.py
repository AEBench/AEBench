"""Layer 3 end to end: the files cli.py writes (spec §1a.3, §3.4, §5).

The run folder is the one ``test_cli.py`` assembles from two fixtures of a
real run (one shell call and its trace record); nothing here depends on
the corpus.
"""

import copy
import csv
import json
import logging
import pathlib
from typing import Any

import pytest
import test_cli

from trajectory_checks import cli, parse, structure

STRUCTURE_FILES = ("call_structure.jsonl", "reruns.jsonl", "file_classes.jsonl")
TEXT_PATCH = (
    "diff --git a/src/a.py b/src/a.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -1 +1 @@\n"
    "-x = 1\n"
    "+x = 2\n"
)


def test_analyse_derives_the_structure(tmp_path: pathlib.Path) -> None:
    """cli.analyse runs structure.derive on the same in-memory records."""
    result = cli.analyse(test_cli._make_run(tmp_path))
    for key in ("call_structure", "reruns", "file_classes", "structure"):
        assert key in result, key
    shell = [c for c in result["calls"] if c["tool_type"] == "shell"]
    assert [r["call_id"] for r in result["call_structure"]] == [
        c["call_id"] for c in shell
    ]
    structure = result["structure"]
    assert structure["run_key"] == result["coverage"]["run_key"]
    assert structure["capabilities"] == result["coverage"]["capabilities"]
    assert set(structure["metrics"]) == {"M2.1", "M2.2", "M2.3", "M4.2"}


def test_write_outputs_writes_structure_files(tmp_path: pathlib.Path) -> None:
    """write_outputs writes the structure files deterministically.

    The three per-item files and structure.json, byte-identical when
    written twice, one sorted-key object per line (§1a.3, OUTF1).
    """
    folders = []
    for name in ("one", "two"):
        result = cli.analyse(test_cli._make_run(tmp_path / f"in_{name}"))
        folders.append(cli.write_outputs(result, tmp_path / f"out_{name}"))
    for name in (*STRUCTURE_FILES, "structure.json"):
        first = (folders[0] / name).read_bytes()
        assert first == (folders[1] / name).read_bytes(), name
    for name in STRUCTURE_FILES:
        for line in (folders[0] / name).read_text().splitlines():
            record = json.loads(line)
            assert list(record) == sorted(record)
    structure = json.loads((folders[0] / "structure.json").read_text())
    assert structure["schema_version"] == "1"


def test_main_writes_structure_csv(tmp_path: pathlib.Path) -> None:
    """cli.main writes structure.csv with peer ratios.

    cli.main writes structure.csv: one row per run with peer ratios,
    byte-identical across two runs of the command (§5.5, T20).
    """
    written = []
    for name in ("one", "two"):
        out = tmp_path / f"out_{name}"
        run_dir = test_cli._make_run(tmp_path / name)
        assert cli.main([str(run_dir), "--out", str(out)]) == 0
        assert any(out.rglob("structure.json"))
        written.append((out / "structure.csv").read_bytes())
    assert written[0] == written[1]
    assert written[0].count(b"\n") == 2  # the header and one run
    path = tmp_path / "out_one" / "structure.csv"
    with path.open(encoding="utf-8") as handle:
        (row,) = list(csv.DictReader(handle))
    assert row["run_key"]
    assert "capabilities.final_content" in row
    assert "bases.unchanged_under" in row
    assert "M2.1.repeat_max_act" in row
    assert "M2.1.repeat_max_act_peer_ratio" in row
    assert "M4.2.changed_by_class.code" in row


def test_final_diff_read_when_present(tmp_path: pathlib.Path) -> None:
    """cli.py reads monitor/final.diff when present.

    cli.py reads monitor/final.diff when the run has final content and
    passes its text to derive (PRE3, BIN1).
    """
    run_dir = test_cli._make_run(tmp_path / "with")
    (run_dir / "monitor" / "final.diff").write_text(TEXT_PATCH)
    result = cli.analyse(run_dir)
    assert result["structure"]["capabilities"]["final_content"] is True
    assert result["structure"]["preconditions"]["final_content"] == "holds"
    plain = cli.analyse(test_cli._make_run(tmp_path / "without"))
    assert plain["structure"]["preconditions"]["final_content"] == "fails"
    assert (
        plain["structure"]["metrics"]["M4.2"]["binary_changed_by_class"] is None
    )


def _two_runs(tmp_path: pathlib.Path) -> list[pathlib.Path]:
    """Two run folders, ``bad__claude__1`` and ``good__claude__1``."""
    out = []
    for name in ("bad", "good"):
        made = test_cli._make_run(tmp_path / name)
        out.append(made.rename(made.parent / f"{name}__claude__1"))
    return out


def _structure_rows(out: pathlib.Path) -> list[dict[str, str]]:
    with (out / "structure.csv").open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_main_handles_an_untimed_call(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2, S5: a run whose call has no time and no root exec is analysed.

    The bad run gets two reruns of ./run.sh, the first with t_start None
    (parse.Call allows it) and no exec; derive gives the edge an unknown
    gap instead of raising, so both runs are written.
    """
    real = parse.load_calls

    def load_calls(log: pathlib.Path, family: str) -> list[Any]:
        calls = real(log, family)
        if "bad__" not in str(log):
            return calls
        last = max(c["seq"] for c in calls)
        for i, start in enumerate((None, calls[0]["t_start"] + 100)):
            extra = copy.deepcopy(calls[0])
            extra.update(
                call_id=f"untimed{i}",
                seq=last + 1 + i,
                command="./run.sh",
                command_norm="./run.sh",
                t_start=start,
                t_end=None if start is None else start + 1,
            )
            calls.append(extra)
        return calls

    monkeypatch.setattr(parse, "load_calls", load_calls)
    bad, good = _two_runs(tmp_path)
    out = tmp_path / "out"
    assert cli.main([str(bad), str(good), "--out", str(out)]) == 0
    assert {r["run_key"] for r in _structure_rows(out)} == {
        "bad__claude__1",
        "good__claude__1",
    }
    reruns = (out / "bad__claude__1" / "reruns.jsonl").read_text()
    edges = [json.loads(line) for line in reruns.splitlines()]
    assert edges, "no rerun edge between the two ./run.sh calls"
    assert all(e["gap_observed"] is False for e in edges)


def test_main_continues_past_a_failing_run(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A2: one run's unexpected error does not abort the run set.

    An error of any kind in one run's analysis (cycle 1 caught only
    OSError and ValueError) must report that run as failed (exit 1), log
    it with its traceback (D56) and still write the other runs.
    """
    real = structure.derive

    def derive(*args: Any, **kwargs: Any) -> Any:
        if kwargs["run_info"]["run_key"] == "bad__claude__1":
            raise TypeError("injected")
        return real(*args, **kwargs)

    monkeypatch.setattr(structure, "derive", derive)
    bad, good = _two_runs(tmp_path)
    out = tmp_path / "out"
    with caplog.at_level(logging.ERROR, logger=cli.logger.name):
        assert cli.main([str(bad), str(good), "--out", str(out)]) == 1
    assert [r["run_key"] for r in _structure_rows(out)] == ["good__claude__1"]
    assert (out / "good__claude__1" / "structure.json").is_file()
    (record,) = caplog.records
    assert record.levelno == logging.ERROR
    assert "bad__claude__1" in record.getMessage()
    assert record.exc_info and record.exc_info[0] is TypeError


def _csv_keys(out: pathlib.Path) -> dict[str, list[str]]:
    """The run_key column of each of the three cross-run CSV files."""
    keys = {}
    for name in ("coverage", "facts", "structure"):
        with (out / f"{name}.csv").open(encoding="utf-8") as handle:
            keys[name] = [r["run_key"] for r in csv.DictReader(handle)]
    return keys


def test_main_isolates_a_failing_write(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D56: an OSError writing one run's files fails only that run.

    The run is reported failed and has no row in any of the three CSV
    files; the other run is written in full.
    """
    real = cli.write_outputs

    def write_outputs(result: Any, out_dir: pathlib.Path) -> Any:
        if result["coverage"]["run_key"] == "bad__claude__1":
            raise OSError("disk full")
        return real(result, out_dir)

    monkeypatch.setattr(cli, "write_outputs", write_outputs)
    bad, good = _two_runs(tmp_path)
    out = tmp_path / "out"
    assert cli.main([str(bad), str(good), "--out", str(out)]) == 1
    assert _csv_keys(out) == dict.fromkeys(
        ("coverage", "facts", "structure"), ["good__claude__1"]
    )
    assert (out / "good__claude__1" / "structure.json").is_file()


def test_main_isolates_a_failing_row(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D56: an error building one run's structure.csv row fails only it.

    The rows of a run are built before any is kept and before its files
    are written, so the failed run has no row in any CSV file and no
    output folder; the other run is written in full.
    """
    real = cli.facts_row

    def facts_row(value: Any, *args: Any) -> Any:
        if args and value["run_key"] == "bad__claude__1":
            raise KeyError("injected")
        return real(value, *args)

    monkeypatch.setattr(cli, "facts_row", facts_row)
    bad, good = _two_runs(tmp_path)
    out = tmp_path / "out"
    assert cli.main([str(bad), str(good), "--out", str(out)]) == 1
    assert _csv_keys(out) == dict.fromkeys(
        ("coverage", "facts", "structure"), ["good__claude__1"]
    )
    assert not (out / "bad__claude__1").exists()
    assert (out / "good__claude__1" / "structure.json").is_file()


# §5.5 and §7: the nine classes, the three roles and the eight labels, in
# the spec's order; capabilities and bases are written sorted.
CLASSES = (
    *("ci", "build", "notebook", "test", "code"),
    *("docs", "data", "config", "other"),
)
ROLES = ("edit_call", "nested_exec", "root_exec")
LABELS = (
    *("log", "C1_under", "final_diff", "C6", "C7_under"),
    *("untracked_visible", "tracked_files", "final_content"),
)
BLOCKS = (
    *("run_key", "family", "preconditions", "capabilities", "bases"),
    *("M2.1", "M2.2", "M2.3", "M4.2", "warnings"),
)


def test_structure_csv_columns(tmp_path: pathlib.Path) -> None:
    """§5.5: the column order of structure.csv (test gap 11).

    Identity, preconditions, capabilities, bases, M2.1 to M4.2 and
    warnings, then the peer ratios; a class dict class by class, a role
    split class-major with the roles of each class together.
    """
    out = tmp_path / "out"
    run_dir = test_cli._make_run(tmp_path / "run")
    assert cli.main([str(run_dir), "--out", str(out)]) == 0
    with (out / "structure.csv").open(encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    own = header[: header.index("warnings") + 1]
    assert all(c.endswith("_peer_ratio") for c in header[len(own) :])
    blocks = []
    for column in own:
        parts = column.split(".")
        block = ".".join(parts[:2]) if column.startswith("M") else parts[0]
        if block not in blocks:
            blocks.append(block)
    assert tuple(blocks) == BLOCKS

    def under(prefix: str) -> list[str]:
        return [c[len(prefix) :] for c in own if c.startswith(prefix)]

    assert under("preconditions.") == list(LABELS)
    names = under("capabilities.")
    assert names == sorted(names) and len(names) == 10
    assert under("bases.") == ["gap", "unchanged_under", "writes_between"]
    assert under("M4.2.changed_by_class.") == list(CLASSES)
    assert under("M4.2.baseline_by_class.") == list(CLASSES)
    for form in ("under", "over"):
        assert under(f"M4.2.changed_by_class_and_role_{form}.") == [
            f"{c}.{r}" for c in CLASSES for r in ROLES
        ]
