"""Reading one run directory, writing its output files, the CLI.

Usage:
    python -m trajectory_checks RUN_DIR_OR_ROOT ... --out DIR
        [--runs runs.txt] [--config overrides.toml] [--oracle-runs DIR]
"""

import argparse
import csv
import json
import logging
import pathlib
import sys
import types
import typing

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from trajectory_checks import (
    facts,
    input_state,
    match,
    oracle_inputs,
    parse,
    structure,
    writer,
)

logger = logging.getLogger(__name__)
OUTPUT_FILES = ("calls", "execs", "links", "dispositions", "paths")
FACTS_FILES = ("call_facts", "exec_facts", "path_facts")
STRUCTURE_FILES = ("call_structure", "reruns", "file_classes")
LAYER4_FILES = ("oracle_inputs", "input_states")
WRITER_FILES = ("last_writers", "input_rewrites", "hunks")
# The list-valued metrics left out of the layer-4 CSVs, by name (D38).
LISTS = frozenset(
    "null_phases_passed phases_passed_by_null phases_gained phases_lost "
    "final_writer_roots_under".split()
)
# Run-level blocks: (file name, key of analyse's result).
BLOCKS = (
    ("coverage", "coverage"),
    ("facts", "facts"),
    ("structure", "structure"),
    ("oracle_inputs", "oracle_inputs_run"),
    ("state_counts", "state_counts"),
    ("writer", "writer"),
)
# Who owns each --config key; a layer also gets the keys of the layers it
# lists (match.link rejects any other key, facts.derive too).
_OWNERS = types.MappingProxyType(
    {
        "match": (match.DEFAULTS,),
        "facts": (match.DEFAULTS, facts.DEFAULTS),
        "structure": (match.DEFAULTS, structure.DEFAULTS),
        "oracle_inputs": (oracle_inputs.DEFAULTS,),
        "input_state": (input_state.DEFAULTS,),
        "writer": (match.DEFAULTS,),  # layer 5 owns no key (its D36)
    }
)


def _read_json(path: pathlib.Path, value_type: type = dict) -> typing.Any:
    """A JSON file's content of that type, or None when unreadable."""
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, value_type) else None


def _read_jsonl(path: pathlib.Path, notes: list[str]) -> list[dict] | None:
    """The JSON objects of a JSON Lines file; None when unreadable (D67).

    A non-blank line that does not decode to an object is skipped with a
    note; a file with such lines and no object is unreadable.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            lines = [line for line in handle.read().split("\n") if line.strip()]
    except OSError:
        return None
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append(None)
    found = [r for r in records if isinstance(r, dict)]
    if skipped := len(records) - len(found):
        notes.append(f"{path.name}: {skipped} undecodable lines skipped")
    return found if found or not records else None


def _read_result(path: pathlib.Path, notes: list[str]) -> dict | None:
    """A null score or oracle_result.json; malformed parts are ⊥ (D68)."""
    value = _read_json(path)
    if value is None:
        return None
    phases, score = value.get("phases"), value.get("score")
    good = [
        p
        for p in (phases if isinstance(phases, list) else ())
        if isinstance(p, dict) and isinstance(p.get("phase"), str)
    ]
    number = isinstance(score, int | float) and not isinstance(score, bool)
    if good != (phases or []) or not (number or score is None):
        notes.append(f"{path.name}: malformed phases or score skipped")
    return {**value, "phases": good, "score": score if number else None}


def _upstream_ref(run_dir: pathlib.Path) -> str | None:
    """The `upstream_ref=` value of run/run_meta.txt, if any (OI9)."""
    meta = run_dir / "run" / "run_meta.txt"
    if not meta.is_file():
        return None
    for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("upstream_ref="):
            return line.split("=", 1)[1].strip() or None
    return None


def route_config(
    config: typing.Mapping[str, typing.Any] | None,
) -> dict[str, dict[str, typing.Any]]:
    """Splits --config keys by the layer that owns them (D42, §3.8 L2).

    Args:
        config: Overrides of any layer's DEFAULTS.

    Returns:
        Layer name (the keys of _OWNERS) to the overrides it receives.

    Raises:
        ValueError: A key is in no layer's DEFAULTS.
    """
    config = config or {}
    known = {k for tables in _OWNERS.values() for t in tables for k in t}
    unknown = sorted(set(config) - known)
    if unknown:
        raise ValueError(f"config keys no layer knows: {unknown}")
    return {
        layer: {k: v for k, v in config.items() if any(k in t for t in tables)}
        for layer, tables in _OWNERS.items()
    }


def _trace_path(run_dir: pathlib.Path, recorded: typing.Any) -> pathlib.Path:
    """Finds commands.jsonl wherever the run directory was copied to.

    The run folder's copy wins over the recorded absolute path.
    """
    name = pathlib.Path(str(recorded or "commands.jsonl"))
    local = run_dir / "monitor" / name.name
    nested = run_dir / "monitor" / name.parent.name / name.name
    found = [p for p in (local, nested, name) if recorded and p.is_file()]
    return found[0] if found else local


def _trace_ok(trace: pathlib.Path, token: str | None) -> bool:
    """C0.1: every record's run_id is the run's token (decision F1)."""
    if not token:
        return True
    with trace.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                run_id = json.loads(line).get("run_id")
            except (json.JSONDecodeError, AttributeError):
                continue
            if run_id != token:
                return False
    return True


def _run_key(run_dir: pathlib.Path) -> str:
    """`<artifact>__<agent>__<n>` for an `artifact/agent/n` folder."""
    parts = run_dir.resolve().parts
    return "__".join(parts[-3:]) if parts[-1].isdigit() else parts[-1]


def _file(path: pathlib.Path) -> str | None:
    """The path as text when the file exists, else None."""
    return str(path) if path.is_file() else None


def capabilities(
    execs: list[parse.Exec],
    run: typing.Mapping[str, typing.Any],
    monitor_dir: pathlib.Path,
) -> dict[str, bool]:
    """The capabilities of a run: which new monitor data it holds (§5.6).

    Each is true only when the data is present, so all are false on runs
    of a monitor that predates the to-change.md items.

    Args:
        execs: The run's Exec records.
        run: Its RunInfo, without `capabilities`.
        monitor_dir: Its `monitor/` folder.

    Returns:
        One bool per name of match.CAPABILITIES.
    """
    snapshots = [e["snapshot"] for e in execs]
    return {
        "parent_links": any(e["depth"] is not None for e in execs),
        "start_events": any(e["record_type"] is not None for e in execs),
        "exact_timing": any(e["ended_at"] is not None for e in execs),
        "content_hashes": any(s["hashes"] is not None for s in snapshots),
        "untracked_visible": any(
            s["created_untracked"] is not None for s in snapshots
        ),
        "read_tracing": any(e["reads"] is not None for e in execs),
        "final_content": run["final_diff_content"] is not None,
        "oracle_reads": run["oracle_reads"] is not None,
        "egress_log": (monitor_dir / "egress.jsonl").is_file(),
    }


def analyse(
    run_dir: pathlib.Path,
    config: typing.Mapping[str, typing.Any] | None = None,
    oracle_runs: pathlib.Path | str | None = None,
) -> dict[str, typing.Any]:
    """Reads one run directory, links it and derives its facts.

    Args:
        run_dir: A folder holding `run/` and `monitor/`.
        config: Overrides of any layer's DEFAULTS, routed by route_config.
        oracle_runs: The --oracle-runs folder, holding one null-agent
            oracle run per artifact (`<artifact>/oracle_inputs.jsonl` and
            `null_agent_score.json`); None when there is none.

    Returns:
        `calls`, `execs`, `links`, `dispositions`, `paths`, `coverage`,
        `call_facts`, `exec_facts`, `path_facts`, `facts`,
        `call_structure`, `reruns`, `file_classes`, `structure`,
        `oracle_inputs`, `oracle_inputs_run`, `input_states`,
        `state_counts`, `last_writers`, `input_rewrites`, `hunks` and
        `writer`.

    Raises:
        parse.UnsupportedFormatError: The agent format is unknown.
        ValueError: A config key is in no layer's DEFAULTS.
    """
    routed = route_config(config)
    run_dir = pathlib.Path(run_dir)
    result = parse.last_json_line(run_dir / "run" / "result.jsonl")
    monitor = result.get("command_monitor") or {}
    family = parse.detect_family(run_dir)
    log = run_dir / "run" / "runner_output.log"
    calls = parse.load_calls(log, family) if log.is_file() else []
    log_ok = log.is_file() and next(parse.log_events(log), None) is not None
    recorded = monitor.get("trace_path")
    trace = _trace_path(run_dir, recorded)
    token = (
        pathlib.Path(recorded).parent.name
        if recorded
        else monitor.get("run_token")
    )
    has_trace = trace.is_file()
    folder = run_dir / "monitor"
    events = folder / "file_events.jsonl"
    events = events if events.is_file() else None
    execs = parse.load_execs(trace, family, events) if has_trace else []
    runtime = result.get("runtime") or {}
    baseline = _read_json(folder / "tracked_files.json")
    meta = _read_json(folder / "trace_meta.json")
    versions = [e["schema_version"] for e in execs]
    run = match.RunInfo(
        t_start=parse.parse_time(result.get("started_at")),
        t_end=parse.parse_time(result.get("finished_at")),
        workspace_root=str(runtime.get("workspace_mount") or "/"),
        trace_ok=has_trace and _trace_ok(trace, token),
        run_key=_run_key(run_dir),
        family=family,
        log_ok=log_ok,
        monitor_version=(meta or {}).get("broker_version"),
        schema_version=(meta or {}).get("schema_version")
        or next((v for v in versions if v is not None), None),
        trace_meta=meta,
        final_diff_content=_file(folder / "final.diff"),
        final_workspace=_file(folder / "final_workspace.tar.zst"),
        oracle_reads=_file(run_dir / "run" / "oracle_inputs.jsonl")
        or _file(folder / "oracle_inputs.jsonl"),
        case_image_digest=runtime.get("image_digest"),
        commit=(baseline or {}).get("commit"),
        shim_intact=monitor.get("shim_intact"),
        unattributed_reads=parse.read_events(events)[1] if events else None,
        capabilities={},
    )
    run["capabilities"] = capabilities(execs, run, folder)
    linked = match.link(
        calls,
        execs,
        run=run,
        baseline=baseline,
        final_diff=_read_json(folder / "tracked_files_diff.json"),
        config=routed["match"],
    )
    diff_path = (monitor.get("system_files") or {}).get("diff_path")
    derived = facts.derive(
        calls,
        execs,
        linked,
        run=run,
        system_diff=_read_json(_trace_path(run_dir, diff_path))
        if diff_path
        else None,
        config=routed["facts"],  # join_rate_warn too: one threshold (D51)
    )
    final_diff_text = None
    if run["capabilities"]["final_content"]:  # BIN1: MC-03's final.diff
        final_diff_text = pathlib.Path(run["final_diff_content"]).read_text(
            encoding="utf-8", errors="replace"
        )
    shaped = structure.derive(
        calls,
        execs,
        linked,
        derived,
        run_info=run,
        baseline=baseline,
        final_diff_text=final_diff_text,
        config=routed["structure"],  # snapshot_slop_s is layer 1's
    )
    notes: list[str] = []  # for oracle_inputs.json, after the scores' (D67)
    final_files = _final_files(folder, notes)  # read once (layer 5's D37)
    layer4 = _layer4(
        run_dir,
        run,
        calls,
        execs,
        linked,
        baseline,
        routed,
        oracle_runs,
        final_files=final_files,
        final_notes=notes,
    )
    written = writer.derive(
        calls,
        execs,
        linked,
        derived,
        shaped,
        run=run,
        oracle_inputs={
            "oracle_inputs": layer4["oracle_inputs"],
            "oracle_inputs_run": layer4["oracle_inputs_run"],
        },
        input_states={
            "input_states": layer4["input_states"],
            "state_counts": layer4["state_counts"],
        },
        final_diff_text=final_diff_text,
        final_files=final_files,
        config=routed["writer"],
    )
    return {
        "calls": match.round_floats(calls),
        "execs": match.round_floats(execs),
        **linked,
        **derived,
        **shaped,
        **layer4,
        **written,
    }


def _final_files(monitor: pathlib.Path, notes: list[str]) -> list[dict] | None:
    """monitor/final_files.json's entries with a path, for layers 4 and 5.

    Malformed entries are skipped with a note.
    """
    final_files = _read_json(monitor / "final_files.json", list)
    if final_files is not None:
        found = [
            e
            for e in final_files
            if isinstance(e, dict) and isinstance(e.get("path"), str)
        ]
        if len(found) < len(final_files):
            notes.append("final_files.json: malformed entries skipped")
        final_files = found
    return final_files


def _layer4(
    run_dir: pathlib.Path,
    run: match.RunInfo,
    calls: list[parse.Call],
    execs: list[parse.Exec],
    linked: match.LinkResult,
    baseline: dict | None,
    routed: dict[str, dict[str, typing.Any]],
    oracle_runs: pathlib.Path | str | None = None,
    *,
    final_files: list[dict] | None,
    final_notes: list[str],
) -> dict[str, typing.Any]:
    """Reads layer 4's files and runs oracle_inputs, then input_state.

    What a file holds that cannot be used is skipped, noted in
    oracle_inputs.json's warnings (D67, D68); final_files comes from
    _final_files, its notes after the scores' as before layer 5.
    """
    notes: list[str] = []
    null_manifest = null_score = None
    if oracle_runs is not None:
        folder = pathlib.Path(oracle_runs) / str(run["run_key"]).split("__")[0]
        null_manifest = _read_jsonl(folder / "oracle_inputs.jsonl", notes)
        null_score = _read_result(folder / "null_agent_score.json", notes)
    run_score = _read_result(run_dir / "run" / "oracle_result.json", notes)
    notes += final_notes
    monitor = run_dir / "monitor"
    given = oracle_inputs.derive(
        linked,
        run=run,
        graded_manifest=_read_jsonl(pathlib.Path(run["oracle_reads"]), notes)
        if run["oracle_reads"]
        else None,
        null_manifest=null_manifest,
        null_score=null_score,
        run_score=run_score,
        baseline=baseline,
        final_files=final_files,
        run_commit=_upstream_ref(run_dir) if run["commit"] is None else None,
        config=routed["oracle_inputs"],
    )
    given["oracle_inputs_run"]["warnings"] += notes
    states = input_state.classify(
        calls,
        execs,
        linked,
        run=run,
        oracle_inputs=given,
        baseline=baseline,
        final_diff=_read_json(monitor / "tracked_files_diff.json"),
        final_files=final_files,
        phase_status=None
        if run_score is None
        else {p["phase"]: p.get("status") for p in run_score["phases"]},
        config=routed["input_state"],
    )
    return {**given, **states}


def _flat(value: typing.Any, prefix: str = "") -> dict[str, typing.Any]:
    """Flattens nested dicts to `a.b` keys for coverage.csv."""
    if not isinstance(value, dict):
        return {prefix: value}
    flat: dict[str, typing.Any] = {}
    for key, item in value.items():
        flat.update(_flat(item, f"{prefix}.{key}" if prefix else key))
    return flat


def csv_row(coverage: match.Coverage) -> dict[str, typing.Any]:
    """One coverage.csv row: identity, the coverage blocks, warnings.

    Args:
        coverage: A run's coverage report.

    Returns:
        Column name to value, in the fixed column order.
    """
    row = {"run_key": coverage["run_key"], "family": coverage["family"]}
    blocks = ("inputs", "integrity", "counts", "rates", "preconditions")
    for block in (*blocks, "capabilities"):
        row.update(_flat(coverage[block], block))
    row["warnings"] = ";".join(coverage["warnings"])
    return row


def facts_row(
    run_facts: facts.RunFacts | structure.RunStructure,
    dict_fields: typing.Mapping[str, typing.Sequence[str]] = facts.DICT_FIELDS,
) -> dict[str, typing.Any]:
    """One facts.csv or structure.csv row (§5.5 of each spec).

    Identity, preconditions, capabilities, bases, scalar metrics, warnings.

    Args:
        run_facts: A run's facts.json or structure.json value.
        dict_fields: facts.DICT_FIELDS, or structure.DICT_FIELDS for a
            structure.json value.

    Returns:
        Column name to value, before the peer ratios, in the fixed column
        order: a dict-valued metric is flattened by `dict_fields` (a
        dotted sub-key walks nested dicts, an absent one is 0), also when
        PRE4 voided it to null; the M5.2 name lists are left out.
    """
    row = {"run_key": run_facts["run_key"], "family": run_facts["family"]}
    for block in ("preconditions", "capabilities", "bases"):
        row.update(_flat(run_facts[block], block))
    for block, fields in run_facts["metrics"].items():
        for field, value in fields.items():
            if field.startswith("names_"):
                continue
            for key in dict_fields.get(field) or [None]:
                name = f"{block}.{field}" + (f".{key}" if key else "")
                cell = value
                for part in key.split(".") if key else ():
                    cell = cell.get(part, 0) if isinstance(cell, dict) else cell
                row[name] = cell
    row["warnings"] = ";".join(run_facts["warnings"])
    return row


def layer4_rows(result: dict[str, typing.Any]) -> dict[str, dict]:
    """The oracle_inputs.csv and state_counts.csv rows of a run (D38, D69).

    cli.facts_row with input_state.PHASES as the fixed phase sub-keys (a
    phase the run lacks is empty, never facts_row's 0), then the list
    metrics of LISTS dropped by name.
    """

    def row(value: dict, metric: str, key: str, fields: dict) -> dict:
        found = value["metrics"][metric]
        if found[key] is not None:
            found = {
                **found,
                key: dict.fromkeys(input_state.PHASES) | found[key],
            }
        value = {**value, "metrics": {**value["metrics"], metric: found}}
        cells = facts_row(value, fields).items()
        return {k: v for k, v in cells if k.split(".")[-1] not in LISTS}

    flags = [
        f"{p}.{f}" for p in input_state.PHASES for f in input_state.PHASE_FLAGS
    ]
    return {
        "oracle_inputs": row(
            result["oracle_inputs_run"],
            "M3.1",
            "phases_complete",
            {"phases_complete": input_state.PHASES},
        ),
        "state_counts": row(
            result["state_counts"],
            "M3.2",
            "phases",
            {**input_state.DICT_FIELDS, "phases": flags},
        ),
    }


def writer_rows(result: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """One writer.csv row: facts_row with writer.DICT_FIELDS (D40).

    No metric of layer 5 is a list, so none is dropped as LISTS are.
    """
    return facts_row(result["writer"], writer.DICT_FIELDS)


def writer_input_rows(
    result: dict[str, typing.Any],
) -> list[dict[str, typing.Any]]:
    """The writer_inputs.csv rows of one run, with INPUT_COLUMNS keys."""
    rewrites = {r["input_id"]: r for r in result["input_rewrites"]}
    return [
        {
            "run_key": result["writer"]["run_key"],
            **{k: row[k] for k in writer.INPUT_COLUMNS[1:7]},  # input_id …
            "M3.3.writer_duration_s": row["writer_duration_s"],
            "M3.3.root_span_over_s": row["root_span_over_s"],
            "M2.4.rewrites_over": rewrites[row["input_id"]]["rewrites_over"],
        }
        for row in result["last_writers"]
    ]


def _write_csv(
    rows: list[dict[str, typing.Any]],
    path: pathlib.Path,
    columns: typing.Sequence[str] | None = None,
) -> None:
    """Writes rows as CSV; columns of the first row first, then the rest.

    `columns`, when given, is the header, so rows may be empty.
    """
    columns = list(rows[0] if columns is None else columns)
    columns += sorted({k for r in rows for k in r} - set(columns))
    with path.open("w", encoding="utf-8", newline="") as handle:
        table = csv.DictWriter(handle, fieldnames=columns)
        table.writeheader()
        table.writerows(rows)


def write_outputs(
    result: dict[str, typing.Any], out_dir: pathlib.Path
) -> pathlib.Path:
    """Writes the output files of one run into `out_dir`.

    Args:
        result: The value of analyse.
        out_dir: The run's output folder (main uses `<out>/<run_key>`).

    Returns:
        The run's output folder.
    """
    folder = pathlib.Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    names = OUTPUT_FILES + FACTS_FILES + STRUCTURE_FILES + LAYER4_FILES
    for name in names + WRITER_FILES:
        with (folder / f"{name}.jsonl").open("w", encoding="utf-8") as out:
            for record in result[name]:
                out.write(json.dumps(record, sort_keys=True) + "\n")
    for name, key in BLOCKS:
        with (folder / f"{name}.json").open("w", encoding="utf-8") as out:
            json.dump(result[key], out, sort_keys=True, indent=1)
            out.write("\n")
    return folder


def _run_dirs(paths: list[str], runs_file: str | None) -> list[pathlib.Path]:
    """Lists the run folders under `paths`.

    Only folders holding run/result.jsonl count; `runs_file`, when given,
    restricts them to the `artifact/agent/n` names it lists.
    """
    found: set[pathlib.Path] = set()
    for path in map(pathlib.Path, paths):
        if (path / "run" / "result.jsonl").is_file():
            found.add(path)
        found.update(p.parent.parent for p in path.rglob("run/result.jsonl"))
    if runs_file:
        with open(runs_file, encoding="utf-8") as handle:
            wanted = {
                line.strip().strip("/") for line in handle if line.strip()
            }
        found = {p for p in found if "/".join(p.parts[-3:]) in wanted}
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    """The command line (spec §7.4).

    Args:
        argv: Arguments without the program name; sys.argv when None.

    Returns:
        0 when every run was processed, 1 when a run failed.
    """
    parser = argparse.ArgumentParser(prog="trajectory_checks")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--runs")
    parser.add_argument("--config")
    parser.add_argument("--oracle-runs")
    args = parser.parse_args(argv)
    config = {}
    if args.config:
        with open(args.config, "rb") as handle:
            config = tomllib.load(handle)
    try:  # an unknown key is reported once, not once per run
        route_config(config)
    except ValueError as error:
        parser.error(f"--config {args.config}: {error}")
    names = ("facts", "structure", "oracle_inputs", "state_counts", "writer")
    rows, failed, inputs = [], [], []
    tables: dict[str, list] = {name: [] for name in names}
    for run_dir in _run_dirs(args.paths, args.runs):
        try:  # one run's error, of any kind, fails only that run (D56)
            result = analyse(run_dir, config, args.oracle_runs)
            built = {
                "facts": facts_row(result["facts"]),
                "structure": facts_row(
                    result["structure"], structure.DICT_FIELDS
                ),
                **layer4_rows(result),
                "writer": writer_rows(result),
            }
            input_rows = writer_input_rows(result)
            coverage = csv_row(result["coverage"])
            key = str(result["coverage"]["run_key"])
            write_outputs(result, pathlib.Path(args.out) / key)
        except Exception:
            failed.append(run_dir)
            logger.exception("run %s failed", run_dir)
            continue
        rows.append(coverage)
        for name in names:
            tables[name].append(built[name])
        inputs += input_rows
    if rows:
        peers = {k: v for k, v in config.items() if k in facts.DEFAULTS}
        _write_csv(rows, pathlib.Path(args.out) / "coverage.csv")
        for name in names:
            _write_csv(
                facts.peer_ratios(tables[name], peers),
                pathlib.Path(args.out) / f"{name}.csv",
            )
        ratios = [c for c in writer.INPUT_COLUMNS if c.startswith("M")]
        _write_csv(  # PEER2, with its header even with no row (D40)
            writer.input_peer_ratios(inputs, peers),
            pathlib.Path(args.out) / "writer_inputs.csv",
            [*writer.INPUT_COLUMNS, *(f"{c}_peer_ratio" for c in ratios)],
        )
    for run_dir in failed:
        print(f"failed: {run_dir}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
