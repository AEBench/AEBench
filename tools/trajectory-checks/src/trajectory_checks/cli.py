"""Reading one run directory, writing the six output files, the CLI.

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

from trajectory_checks import match, parse

logger = logging.getLogger(__name__)
OUTPUT_FILES = ("calls", "execs", "links", "dispositions", "paths")


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
    """Reads one run directory and links it.

    Args:
        run_dir: A folder holding `run/` and `monitor/`.
        config: Overrides of match.DEFAULTS.

    Returns:
        `calls`, `execs`, `links`, `dispositions`, `paths` and `coverage`.

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
    linked = match.link(
        calls,
        execs,
        run=run,
        baseline=baseline,
        final_diff=_read_json(folder / "tracked_files_diff.json"),
        config=config,
    )
    return {
        "calls": match.round_floats(calls),
        "execs": match.round_floats(execs),
        **linked,
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


def write_outputs(
    result: dict[str, typing.Any], out_dir: pathlib.Path
) -> pathlib.Path:
    """Writes the six files of one run into `out_dir`.

    Args:
        result: The value of analyse.
        out_dir: The run's output folder (main uses `<out>/<run_key>`).

    Returns:
        The run's output folder.
    """
    folder = pathlib.Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_FILES:
        with (folder / f"{name}.jsonl").open("w", encoding="utf-8") as out:
            for record in result[name]:
                out.write(json.dumps(record, sort_keys=True) + "\n")
    with (folder / "coverage.json").open("w", encoding="utf-8") as out:
        json.dump(result["coverage"], out, sort_keys=True, indent=1)
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
    config = None
    if args.config:
        with open(args.config, "rb") as handle:
            config = tomllib.load(handle)
    rows, failed = [], []
    for run_dir in _run_dirs(args.paths, args.runs):
        try:
            result = analyse(run_dir, config)
        except (OSError, ValueError) as error:
            failed.append(run_dir)
            logger.error("run %s failed: %s", run_dir, error)
            continue
        write_outputs(
            result, pathlib.Path(args.out) / str(result["coverage"]["run_key"])
        )
        rows.append(csv_row(result["coverage"]))
    if rows:
        columns = list(rows[0])
        columns += sorted({k for r in rows for k in r} - set(columns))
        with (pathlib.Path(args.out) / "coverage.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
    for run_dir in failed:
        print(f"failed: {run_dir}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
