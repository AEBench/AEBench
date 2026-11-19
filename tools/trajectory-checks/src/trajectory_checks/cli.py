"""Reading one run directory, writing its output files, the CLI.

Usage:
    python -m trajectory_checks RUN_DIR_OR_ROOT ... --out DIR
        [--runs runs.txt] [--config overrides.toml]
"""

import argparse
import csv
import json
import logging
import pathlib
import sys
import typing

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from trajectory_checks import facts, match, parse, structure

logger = logging.getLogger(__name__)
OUTPUT_FILES = ("calls", "execs", "links", "dispositions", "paths")
FACTS_FILES = ("call_facts", "exec_facts", "path_facts")
STRUCTURE_FILES = ("call_structure", "reruns", "file_classes")


def _read_json(path: pathlib.Path) -> dict | None:
    """Returns a JSON object file's content, or None when unreadable."""
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


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
) -> dict[str, typing.Any]:
    """Reads one run directory, links it and derives its facts.

    Args:
        run_dir: A folder holding `run/` and `monitor/`.
        config: Overrides of match.DEFAULTS and facts.DEFAULTS.

    Returns:
        `calls`, `execs`, `links`, `dispositions`, `paths`, `coverage`,
        `call_facts`, `exec_facts`, `path_facts`, `facts`,
        `call_structure`, `reruns`, `file_classes` and `structure`.

    Raises:
        parse.UnsupportedFormatError: The agent format is unknown.
    """
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
    config = config or {}
    linked = match.link(
        calls,
        execs,
        run=run,
        baseline=baseline,
        final_diff=_read_json(folder / "tracked_files_diff.json"),
        config={k: v for k, v in config.items() if k not in facts.DEFAULTS},
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
        config=config,  # join_rate_warn too: one threshold (D51)
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
        config=config,  # snapshot_slop_s and join_rate_warn are layer 1's
    )
    return {
        "calls": match.round_floats(calls),
        "execs": match.round_floats(execs),
        **linked,
        **derived,
        **shaped,
    }


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


def _write_csv(rows: list[dict[str, typing.Any]], path: pathlib.Path) -> None:
    """Writes rows as CSV; columns of the first row first, then the rest."""
    columns = list(rows[0])
    columns += sorted({k for r in rows for k in r} - set(columns))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


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
    for name in OUTPUT_FILES + FACTS_FILES + STRUCTURE_FILES:
        with (folder / f"{name}.jsonl").open("w", encoding="utf-8") as out:
            for record in result[name]:
                out.write(json.dumps(record, sort_keys=True) + "\n")
    for name in ("coverage", "facts", "structure"):
        with (folder / f"{name}.json").open("w", encoding="utf-8") as out:
            json.dump(result[name], out, sort_keys=True, indent=1)
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
    args = parser.parse_args(argv)
    config = {}
    if args.config:
        with open(args.config, "rb") as handle:
            config = tomllib.load(handle)
    rows, fact_rows, structure_rows, failed = [], [], [], []
    for run_dir in _run_dirs(args.paths, args.runs):
        try:  # one run's error, of any kind, fails only that run (D56)
            result = analyse(run_dir, config)
            built = (
                csv_row(result["coverage"]),
                facts_row(result["facts"]),
                facts_row(result["structure"], structure.DICT_FIELDS),
            )
            key = str(result["coverage"]["run_key"])
            write_outputs(result, pathlib.Path(args.out) / key)
        except Exception:
            failed.append(run_dir)
            logger.exception("run %s failed", run_dir)
            continue
        tables = (rows, fact_rows, structure_rows)
        for table, row in zip(tables, built, strict=True):
            table.append(row)
    if rows:
        peers = {k: v for k, v in config.items() if k in facts.DEFAULTS}
        _write_csv(rows, pathlib.Path(args.out) / "coverage.csv")
        for name, table in (
            ("facts", fact_rows),
            ("structure", structure_rows),
        ):
            _write_csv(
                facts.peer_ratios(table, peers),
                pathlib.Path(args.out) / f"{name}.csv",
            )
    for run_dir in failed:
        print(f"failed: {run_dir}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
