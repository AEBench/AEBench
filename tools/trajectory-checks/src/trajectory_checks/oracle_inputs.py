"""Layer 4, step P0: the oracle inputs of a run and the null score.

Builds M3.1 (the files and folders the oracle reads, stats or lists, with
their state in the untouched workspace and, for a graded run, at grading)
and M3.5 (the null-agent score and the run's gain over it), as
`trajectory-provenance-spec.md` §0 specifies. The source is this run's
own grading reads when capability `oracle_reads` holds (exact), else the
manifest of one null-agent oracle run of the artifact (fallback); the two
are never merged (OI1). Every function is pure: `cli.analyse` reads the
files and passes their content (D32).
"""

import collections
import fnmatch
import types
import typing

from trajectory_checks import match


class ManifestRecord(typing.TypedDict):
    """One MC-02 record (spec §0.2); missing keys read as None."""

    check_id: str
    phase: str
    method: str
    path: str | None
    resolved_workspace_path: str | None
    exists: bool | None
    sha256: str | None
    size: int | None
    mtime_ns: int | None
    glob_pattern: str | None
    glob_matches: list[str] | None
    argv: list[str] | None
    exists_before: bool | None
    sha256_before: str | None
    tracked_before: bool | None


class FinalFile(typing.TypedDict):
    """One entry of monitor/final_files.json (MC-03); missing keys None."""

    path: str
    status: str | None  # A, M, D
    size: int | None
    sha256: str | None
    in_diff: bool | None
    reason_omitted: str | None


class OracleInput(typing.TypedDict):
    """One row of oracle_inputs.jsonl (spec §0.3)."""

    input_id: str
    path: str
    in_workspace: bool
    input_scope: str  # workspace, outside_workspace, reference
    pattern: str | None
    match_type: str  # literal, pattern, pattern_match
    listed_by: list[str]
    in_under: bool
    access_types: list[str]
    checks: list[str]
    phases: list[str]
    source_type: str  # graded, null_agent
    at_baseline: bool | None
    baseline_sha256: str | None
    tracked_at_baseline: bool | None
    null_sha256: str | None
    oracle_writes: bool | None
    oracle_writes_basis: str | None  # exact, fallback
    grading_exists: bool | None
    grading_sha256: str | None
    grading_mtime_ns: int | None


class OracleInputsBlock(typing.TypedDict):
    """oracle_inputs.json (spec §0.3)."""

    run_key: str | None
    family: str | None
    capabilities: dict[str, bool]
    bases: dict[str, str]  # source, pattern_matches, oracle_writes
    preconditions: dict[str, str]  # label -> holds, vacuous, fails
    metrics: dict[str, dict[str, typing.Any]]  # "M3.1", "M3.5" -> fields
    warnings: list[str]


class OracleInputsResult(typing.TypedDict):
    """Everything P0 produces for one run."""

    oracle_inputs: list[OracleInput]
    oracle_inputs_run: OracleInputsBlock  # written as oracle_inputs.json


Key = tuple[str, str | None]  # (path, pattern): the cross-run key

# §0.8: the check executor's methods (oracle_checks_runtime.py, MC-02,
# MC-16). run_process has no path and no access type: a command check.
ACCESS_TYPES = types.MappingProxyType(
    dict.fromkeys(("path_exists", "path_is_file", "path_is_dir"), "stat")
    | {"read_file_text": "read", "glob": "list", "command": "command"}
)
# Deployment configuration: evaluator-owned locations, none by default.
DEFAULTS = types.MappingProxyType({"reference_prefixes": ()})
# §0.5: (label, the metrics it guards).
PRECONDITIONS = (
    ("oracle_inputs", ("M3.1",)),
    ("null_commit", ("M3.1", "M3.5")),
    ("null_score", ("M3.5",)),
    ("run_score", ("M3.5",)),
)
# §0.3, §6.9: (basis key, what selects its exact form).
BASES = (
    ("source", "oracle_reads"),
    ("pattern_matches", "created files visible"),
    ("oracle_writes", "writes_basis"),
)
_ROW_FIELDS = tuple(OracleInput.__annotations__)
_NAMES = ("phase", "check_id")  # D68: the fields a record must have
_TEXT = "path resolved_workspace_path glob_pattern sha256 sha256_before".split()
# OI8: M3.1 key -> (field, value): the literal rows whose field has it.
_COUNTS = (
    ("inputs_stat_only_under", "access_types", ["stat"]),
    ("inputs_outside_workspace", "input_scope", "outside_workspace"),
    ("inputs_reference", "input_scope", "reference"),
    ("inputs_present_at_baseline", "at_baseline", True),
    ("inputs_absent_at_baseline", "at_baseline", False),
    ("inputs_oracle_written", "oracle_writes", True),
)
_M35 = (
    "null_score null_score_max null_phases_passed run_score run_score_max "
    "score_gain phases_passed_by_null phases_gained phases_lost"
).split()


def _lower(sha: str | None) -> str | None:
    """Hashes are compared as lowercase hex (§6.8)."""
    return sha.lower() if isinstance(sha, str) else sha


def _merge_bool(values: typing.Iterable[bool | None]) -> bool | None:
    """OI3: true when any is true, false when all are false, else None."""
    values = list(values)
    if True in values:
        return True
    if values and all(v is False for v in values):
        return False
    return None


def _merge_hash(
    values: typing.Iterable[str | None], path: str, warnings: list[str]
) -> str | None:
    """OI3: the single known hash; None and a warning when two differ."""
    known = {_lower(v) for v in values if v is not None}
    if len(known) > 1:
        text = f"oracle input {path} read with two hashes"
        if text not in warnings:
            warnings.append(text)
        return None
    return next(iter(known), None)


def _differs(
    exists: tuple[bool | None, bool | None],
    hashes: tuple[str | None, str | None],
) -> bool | None:
    """Whether two (existence, hash) states differ (OI3, OI10).

    True when both existences are known and differ, or both hashes are
    known and differ; false when the existences are known and equal and
    either the file is absent on both sides or both hashes are known
    (and so equal); None otherwise.
    """
    first, second = exists
    one, two = map(_lower, hashes)
    known = first is not None and second is not None
    if known and first != second:
        return True
    if one is not None and two is not None and one != two:
        return True
    if known and (not first or None not in (one, two)):
        return False
    return None


def _scope(path: str, inside: bool, prefixes: tuple[str, ...]) -> str:
    """OI2: reference under a prefix (whole components, D50), else by side."""
    for prefix in prefixes:
        prefix = prefix.rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return "reference"
    return "workspace" if inside else "outside_workspace"


def _numbered(rows: list[OracleInput]) -> list[OracleInput]:
    """OI7: sorts the rows, numbers them i<n> and remaps listed_by."""
    rows.sort(
        key=lambda r: (
            not r["in_workspace"],
            r["path"],
            r["pattern"] is not None,
            r["pattern"] or "",
        )
    )
    renamed = {r["input_id"]: f"i{n}" for n, r in enumerate(rows)}
    for row in rows:
        row["input_id"] = renamed[row["input_id"]]
        ids = {renamed.get(i, i) for i in row["listed_by"]}
        row["listed_by"] = sorted(ids, key=lambda i: int(i[1:]))
    return rows


def _wellformed(
    records: list[ManifestRecord] | None, warnings: list[str]
) -> list[ManifestRecord] | None:
    """D68: the usable records; the malformed ones are skipped, warned.

    A record needs a known method, a string phase and check id, and a
    string or null in every other string field; a glob_matches that is
    not a list of strings is dropped. Records with none usable read as
    no manifest (D67).
    """
    if records is None:
        return None
    out, skipped, dropped = [], 0, 0
    for record in records:
        if not (
            record.get("method") in (*ACCESS_TYPES, "run_process")
            and all(isinstance(record.get(k), str) for k in _NAMES)
            and all(isinstance(record.get(k), str | None) for k in _TEXT)
        ):
            skipped += 1
            continue
        matches = record.get("glob_matches")
        if matches is not None and not (
            isinstance(matches, list)
            and all(isinstance(m, str) for m in matches)
        ):
            dropped += 1
            record = typing.cast(
                ManifestRecord, {**record, "glob_matches": None}
            )
        out.append(record)
    if skipped or dropped:
        warnings.append(
            f"malformed manifest records: {skipped} skipped, {dropped}"
            " glob_matches dropped"
        )
    return out if out or not records else None


def _input_records(records: list[ManifestRecord]) -> list[ManifestRecord]:
    """OI2, OI6: the records with a path; run_process is a command check."""
    return [
        r
        for r in records
        if r.get("path") is not None and r.get("method") != "run_process"
    ]


def _key(record: ManifestRecord, root: str) -> tuple[Key, bool]:
    """A record's own (path, pattern) key, and whether it is inside."""
    raw = record.get("resolved_workspace_path") or record["path"]
    path, inside = match.relative_path(raw, root)
    glob = record.get("method") == "glob"
    return (path, record.get("glob_pattern") if glob else None), inside


def _row(path: str, inside: bool, source_type: str, **fields) -> OracleInput:
    """A row with every field of OracleInput, None unless given."""
    row = dict.fromkeys(_ROW_FIELDS)
    row.update(path=path, in_workspace=inside, source_type=source_type)
    row.update(fields)
    return typing.cast(OracleInput, row)


def normalize_manifest(
    records: list[ManifestRecord],
    source_type: str,
    *,
    workspace_root: str,
    baseline: typing.Mapping[str, typing.Any] | None,
    reference_prefixes: tuple[str, ...],
    pre_oracle: typing.Mapping[Key, OracleInput] | None = None,
) -> tuple[list[OracleInput], dict[Key, bool | None], list[str]]:
    """Merges manifest records into literal and pattern rows (OI2-OI4).

    pre_oracle holds the null-agent rows by key, for the baseline facts a
    graded row borrows (OI3): only the rows that hold a record of their
    own (D71, A7), so no row a glob only matched; None when the null
    manifest is absent or its commit does not match. Rows are ordered
    and numbered by OI7, with oracle_writes and oracle_writes_basis None.

    Returns:
        The rows, the exists of each row's first record by key, and the
        warnings.
    """
    groups: dict[Key, list[ManifestRecord]] = {}
    touched: dict[Key, dict[str, typing.Any]] = {}

    def touch(
        key: Key, inside: bool, access: str, record: ManifestRecord
    ) -> None:
        entry = touched.setdefault(
            key,
            {"in": inside, "access": set(), "checks": set(), "phases": set()},
        )
        groups.setdefault(key, [])
        entry["access"].add(access)
        entry["checks"].add(record.get("check_id"))
        entry["phases"].add(record.get("phase"))

    for record in _input_records(records):
        method = record.get("method")
        key, inside = _key(record, workspace_root)
        touch(key, inside, ACCESS_TYPES[method], record)
        groups[key].append(record)
        matches = record.get("glob_matches") if method == "glob" else None
        for item in matches or []:  # OI4, D47: list, check, phase only
            found, found_inside = match.relative_path(item, workspace_root)
            touch((found, None), found_inside, "list", record)
            touched[found, None].setdefault("listed_by", set()).add(key)
    ids = {key: f"t{n}" for n, key in enumerate(groups)}
    files = (baseline or {}).get("files") or {}
    warnings: list[str] = []
    rows = []
    for key, group in groups.items():
        path, pattern = key
        entry = touched[key]
        row = _row(
            path,
            entry["in"],
            source_type,
            input_id=ids[key],
            input_scope=_scope(path, entry["in"], reference_prefixes),
            pattern=pattern,
            match_type="literal" if pattern is None else "pattern",
            listed_by=[ids[k] for k in entry.get("listed_by", ())],
            in_under=pattern is None,
            access_types=sorted(entry["access"]),
            checks=sorted(entry["checks"]),
            phases=sorted(entry["phases"]),
        )
        if source_type == "null_agent":
            reads = [r for r in group if r.get("method") == "read_file_text"]
            row.update(
                at_baseline=_merge_bool(r.get("exists_before") for r in group),
                baseline_sha256=_merge_hash(
                    [r.get("sha256_before") for r in group], path, warnings
                ),
                tracked_at_baseline=_merge_bool(
                    r.get("tracked_before") for r in group
                ),
                null_sha256=_merge_hash(
                    [r.get("sha256") for r in reads], path, warnings
                ),
            )
        else:
            _graded_fields(row, group, files, baseline, pre_oracle, warnings)
        rows.append(row)
    first = {k: g[0].get("exists") if g else None for k, g in groups.items()}
    return _numbered(rows), first, warnings


def _graded_fields(
    row: OracleInput,
    group: list[ManifestRecord],
    files: typing.Mapping[str, typing.Any],
    baseline: typing.Mapping[str, typing.Any] | None,
    pre_oracle: typing.Mapping[Key, OracleInput] | None,
    warnings: list[str],
) -> None:
    """OI3, graded source: baseline facts, borrowed ones, grading fields."""
    key = (row["path"], row["pattern"])
    if row["pattern"] is None and row["path"] in files:
        row.update(
            at_baseline=True,
            tracked_at_baseline=True,
            baseline_sha256=_lower(files[row["path"]].get("sha256")),
        )
    elif pre_oracle is not None and key in pre_oracle:
        borrowed = pre_oracle[key]
        for field in ("at_baseline", "tracked_at_baseline", "baseline_sha256"):
            row[field] = borrowed[field]
    else:
        row["tracked_at_baseline"] = False if baseline is not None else None
    sha = _merge_hash([r.get("sha256") for r in group], row["path"], warnings)
    row["grading_exists"] = _merge_bool(r.get("exists") for r in group)
    row["grading_sha256"] = sha
    if sha is not None:  # D59: from the first record that gave the hash
        row["grading_mtime_ns"] = next(
            r.get("mtime_ns") for r in group if _lower(r.get("sha256")) == sha
        )


def null_value(
    at_baseline: bool | None,
    first_exists: bool | None,
    baseline_sha256: str | None,
    null_sha256: str | None,
) -> bool | None:
    """The null-agent value of oracle_writes for one row (OI3)."""
    return _differs((at_baseline, first_exists), (baseline_sha256, null_sha256))


def final_index(
    final_files: list[FinalFile] | None, root: str
) -> dict[str, FinalFile] | None:
    """D55: final_files by workspace-relative path, inside entries only."""
    if final_files is None:
        return None
    found = ((*match.relative_path(e["path"], root), e) for e in final_files)
    return {path: entry for path, inside, entry in found if inside}


def end_of_agent(
    final_files: typing.Mapping[str, FinalFile] | None,
    path: str,
    *,
    tracked_at_baseline: bool | None,
    baseline_sha256: str | None,
) -> tuple[bool | None, str | None]:
    """Existence and hash of a path at the end of the agent's run (D36).

    final_files is indexed by workspace-relative path; None without
    monitor/final_files.json.
    """
    entry = (final_files or {}).get(path) or {}
    status = entry.get("status")
    if final_files is not None and not entry and tracked_at_baseline is True:
        return True, _lower(baseline_sha256)
    if status in ("A", "M"):
        return True, _lower(entry.get("sha256"))
    return (False, None) if status == "D" else (None, None)


def oracle_writes(
    row: OracleInput,
    first_exists: bool | None,
    *,
    end: tuple[bool | None, str | None] | None,
    null_value: bool | None,
) -> tuple[bool | None, str | None]:
    """Returns oracle_writes and its basis for one row (OI10).

    end is end_of_agent's answer, or None when the exact form does not
    apply (oracle_reads or final_content false, no final_files, a row
    outside the workspace); null_value is the null-agent value of the
    same key, already None when the null run's commit or image does not
    match.
    """
    if row["match_type"] != "literal":
        return None, None
    if end is not None:
        exact = _differs(
            (end[0], first_exists), (end[1], row["grading_sha256"])
        )
        if exact is not None:
            return exact, "exact"
    if null_value is not None:
        return null_value, "fallback"
    return None, None


def writes_basis(rows: list[OracleInput], capability_exact: bool) -> str:
    """bases.oracle_writes: exact, fallback or mixed (D34)."""
    found = {
        r["oracle_writes_basis"] for r in rows if r["oracle_writes"] is not None
    }
    if len(found) > 1:
        return "mixed"
    if found:
        return found.pop()
    return "exact" if capability_exact else "fallback"


def _glob_parts(pattern: tuple[str, ...], parts: tuple[str, ...]) -> bool:
    """glob_match on components; a hidden name only matched explicitly."""
    if not pattern:
        return not parts
    head, hidden = pattern[0], bool(parts) and parts[0].startswith(".")
    if head == "**":  # zero or more whole components
        return _glob_parts(pattern[1:], parts) or (
            bool(parts) and not hidden and _glob_parts(pattern, parts[1:])
        )
    return (
        bool(parts)
        and (head.startswith(".") or not hidden)
        and fnmatch.fnmatchcase(parts[0], head)
        and _glob_parts(pattern[1:], parts[1:])
    )


def glob_match(pattern: str, path: str) -> bool:
    """Whether a path matches a pathlib-style glob pattern (OI5).

    pattern is the listed folder joined with the row's pattern.
    """
    return _glob_parts(tuple(pattern.split("/")), tuple(path.split("/")))


def instantiate(
    rows: list[OracleInput],
    paths: list[match.PathSummary],
    *,
    source_type: str,
    reference_prefixes: tuple[str, ...] = (),
) -> list[OracleInput]:
    """Adds the pattern_match rows of OI5 and renumbers by OI7."""
    rows = [dict(r) for r in rows]
    literal = {r["path"] for r in rows if r["match_type"] != "pattern"}
    patterns = [
        (r, r["pattern"] if r["path"] == "." else f"{r['path']}/{r['pattern']}")
        for r in rows
        if r["match_type"] == "pattern"
    ]
    added = {}
    for summary in sorted(paths, key=lambda p: p["path"]):
        path = summary["path"]
        if (
            not summary["in_workspace"]
            or summary["final_state"] == "deleted"
            or path in literal
        ):
            continue
        hits = [r for r, glob in patterns if glob_match(glob, path)]
        if hits:
            added[path] = _row(
                path,
                True,
                source_type,
                input_id=f"n{path}",
                input_scope=_scope(path, True, reference_prefixes),  # D71
                match_type="pattern_match",
                listed_by=[r["input_id"] for r in hits],
                in_under=False,
                access_types=["list"],
                checks=sorted({c for r in hits for c in r["checks"]}),
                phases=sorted({p for r in hits for p in r["phases"]}),
                at_baseline=False,
            )
    return _numbered(rows + list(added.values()))


def scores(
    null_score: typing.Mapping[str, typing.Any] | None,
    run_score: typing.Mapping[str, typing.Any] | None,
) -> dict[str, typing.Any]:
    """The M3.5 block by NS1-NS4; every key present, None when unknown."""

    def passed(result: typing.Mapping[str, typing.Any]) -> set[str]:
        phases = result.get("phases") or []
        return {p["phase"] for p in phases if p.get("status") == "success"}

    out = dict.fromkeys(_M35)
    if null_score is None:
        return out
    by_null = passed(null_score)
    out.update(
        null_score=null_score.get("score"),
        null_score_max=len(null_score.get("phases") or []),
        null_phases_passed=sorted(by_null),
    )
    if run_score is None:
        return out
    by_run = passed(run_score)
    out.update(
        run_score=run_score.get("score"),
        run_score_max=len(run_score.get("phases") or []),
        phases_passed_by_null=sorted(by_null & by_run),
        phases_gained=sorted(by_run - by_null),
        phases_lost=sorted(by_null - by_run),
    )
    if out["run_score"] is not None and out["null_score"] is not None:
        out["score_gain"] = out["run_score"] - out["null_score"]
    return out


def _commit_check(
    run: match.RunInfo,
    run_commit: str | None,
    null_manifest: list[ManifestRecord] | None,
    null_score: typing.Mapping[str, typing.Any] | None,
) -> dict[str, typing.Any]:
    """OI9: the null_commit label, the image comparison and warnings."""
    artifact = str(run["run_key"]).split("__")[0]
    found = {
        "run_commit": run.get("commit") or run_commit,
        "null_commit": (null_score or {}).get("commit"),
        "run_case_image_digest": run.get("case_image_digest"),
        "null_case_image_digest": (null_score or {}).get("case_image_digest"),
        "null_image_matches": None,
    }
    warnings = []
    ours, theirs = found["run_commit"], found["null_commit"]
    if null_manifest is None and null_score is None:
        label = "vacuous"  # D50: no null run was passed
    elif ours is not None and theirs is not None:
        same = str(ours).lower() == str(theirs).lower()  # D71, as §6.8
        label = "holds" if same else "fails"
    else:
        label = "holds"  # D24, D41: unknown commits count as matching
        what = "" if theirs is None else f"graded {theirs}, run "
        warnings.append(
            f"null_commit unverified: {artifact} null run {what}commit unknown"
        )
    images = found["run_case_image_digest"], found["null_case_image_digest"]
    if None not in images:
        found["null_image_matches"] = images[0] == images[1]
        if not found["null_image_matches"]:
            warnings.append(f"null run image differs: {artifact}")
    return {"label": label, "found": found, "warnings": warnings}


def _manifest_label(manifest: list[ManifestRecord] | None) -> str:
    """§0.5's oracle_inputs label for the manifest OI1 chose."""
    if manifest is None:
        return "fails"
    return "holds" if _input_records(manifest) else "vacuous"


def _m31(
    source_type: str,
    rows: list[OracleInput],
    manifest: list[ManifestRecord],
    found: dict[str, typing.Any],
    visible: bool,
    warnings: list[str],
) -> dict[str, typing.Any]:
    """The M3.1 block: OI6's command checks, OI8's counts, OI9's values."""
    literal = [r for r in rows if r["match_type"] == "literal"]
    procs = [r for r in manifest if r.get("method") == "run_process"]
    commands = {
        r.get("check_id") for r in manifest if r.get("method") == "command"
    }
    if procs and not commands:
        warnings.append(
            "oracle command checks ran without read recording (MC-16): "
            "M3.1 inputs may be missing files"
        )
    complete = {r.get("phase"): True for r in manifest}
    for record in procs:
        if record.get("check_id") not in commands:
            complete[record.get("phase")] = False

    types_ = collections.Counter(r["match_type"] for r in rows)
    return {
        "source_type": source_type,
        "inputs_under": len(literal),
        "inputs_over": len(literal) + types_["pattern_match"],
        "inputs_read_under": sum(
            bool({"read", "command"} & set(r["access_types"])) for r in literal
        ),
        **{
            key: sum(r[field] == value for r in literal)
            for key, field, value in _COUNTS
        },
        "patterns": types_["pattern"],
        "pattern_matches_complete": visible,
        "command_checks": len(procs),
        "command_reads_recorded": bool(commands) if commands or procs else None,
        "phases_complete": dict(sorted(complete.items())),
        **found,
    }


def merge_config(
    defaults: typing.Mapping[str, typing.Any],
    config: typing.Mapping[str, typing.Any] | None,
) -> dict:
    """The defaults with the overrides; unknown keys raise ValueError."""
    unknown = set(config or {}) - set(defaults)
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    return {**defaults, **(config or {})}


def derive(
    linked: match.LinkResult,
    *,
    run: match.RunInfo,
    graded_manifest: list[ManifestRecord] | None = None,
    null_manifest: list[ManifestRecord] | None = None,
    null_score: typing.Mapping[str, typing.Any] | None = None,
    run_score: typing.Mapping[str, typing.Any] | None = None,
    baseline: typing.Mapping[str, typing.Any] | None = None,
    final_files: list[FinalFile] | None = None,
    run_commit: str | None = None,
    config: typing.Mapping[str, typing.Any] | None = None,
) -> OracleInputsResult:
    """Builds the oracle inputs and the null score of one run (P0).

    Args:
        linked: Layer 1's result; paths (OI5) and coverage (capabilities,
            rates.untracked_visible) are read.
        run: The run's RunInfo; capabilities, workspace_root, run_key,
            family, commit and case_image_digest are read.
        graded_manifest: The records of the file run["oracle_reads"]
            names, read by cli.analyse; used when
            run["capabilities"]["oracle_reads"] holds (OI1).
        null_manifest: <oracle_runs>/<artifact>/oracle_inputs.jsonl.
        null_score: <oracle_runs>/<artifact>/null_agent_score.json.
        run_score: run/oracle_result.json.
        baseline: monitor/tracked_files.json.
        final_files: monitor/final_files.json (MC-03), for OI10's exact
            form.
        run_commit: The run's commit when run["commit"] is None
            (run/run_meta.txt's upstream_ref=), else None.
        config: Overrides of DEFAULTS.

    Returns:
        The rows of oracle_inputs.jsonl and the oracle_inputs.json block.

    Raises:
        ValueError: A config key is not in DEFAULTS.
    """
    cfg = merge_config(DEFAULTS, config)
    skipped: list[str] = []
    graded_manifest = _wellformed(graded_manifest, skipped)
    null_manifest = _wellformed(null_manifest, skipped)
    caps = run["capabilities"]
    coverage = linked["coverage"]
    graded = bool(caps.get("oracle_reads"))
    source = "graded" if graded else "null_agent"
    manifest = graded_manifest if graded else null_manifest
    common = {
        "workspace_root": run["workspace_root"],
        "baseline": baseline,
        "reference_prefixes": tuple(cfg["reference_prefixes"]),
    }
    check = _commit_check(run, run_commit, null_manifest, null_score)
    commit_ok = check["label"] != "fails"
    warnings = check["warnings"] + skipped
    null: tuple[list[OracleInput], dict, list[str]] = ([], {}, [])
    if null_manifest is not None:  # normalized once, also as the source
        null = normalize_manifest(null_manifest, "null_agent", **common)
        if not graded:  # OI1: on a graded run, borrowed with no warning
            warnings += [w for w in null[2] if w not in warnings]
    null_rows = {(r["path"], r["pattern"]): r for r in null[0]}
    null_first = null[1]
    labels = {
        "oracle_inputs": _manifest_label(manifest),
        "null_commit": check["label"],
        "null_score": "holds" if null_score is not None else "fails",
        "run_score": "holds" if run_score is not None else "fails",
    }
    if labels["oracle_inputs"] == "fails":
        warnings.append("input oracle_inputs missing: M3.1 null")
    visible = bool(
        caps.get("untracked_visible")
        or coverage["rates"].get("untracked_visible")
    )
    exact = bool(
        graded and caps.get("final_content") and final_files is not None
    )
    rows: list[OracleInput] = []
    m31 = dict.fromkeys(_m31(source, [], [], check["found"], visible, []))
    if labels["oracle_inputs"] != "fails" and (graded or commit_ok):
        rows, first, found = null
        if graded:
            held = {  # D71: OI3 borrows from a row holding its own record
                _key(r, run["workspace_root"])[0]
                for r in _input_records(null_manifest or [])
            }
            pre = {k: null_rows[k] for k in held} if commit_ok else None
            rows, first, found = normalize_manifest(
                manifest, source, pre_oracle=pre, **common
            )
        warnings += [w for w in found if w not in warnings]
        index = final_index(final_files, run["workspace_root"])
        fallback_ok = (
            commit_ok and check["found"]["null_image_matches"] is not False
        )
        for row in rows:
            key = (row["path"], row["pattern"])
            end = end_of_agent(
                index if exact and row["in_workspace"] else None,
                row["path"],
                tracked_at_baseline=row["tracked_at_baseline"],
                baseline_sha256=row["baseline_sha256"],
            )
            value = None
            twin = null_rows.get(key) if graded else row
            if fallback_ok and twin is not None:
                value = null_value(
                    twin["at_baseline"],
                    null_first.get(key),
                    twin["baseline_sha256"],
                    twin["null_sha256"],
                )
            row["oracle_writes"], row["oracle_writes_basis"] = oracle_writes(
                row, first.get(key), end=end, null_value=value
            )
        rows = instantiate(
            rows,
            linked["paths"],
            source_type=source,
            reference_prefixes=common["reference_prefixes"],
        )
        if not visible and any(r["match_type"] == "pattern" for r in rows):
            warnings.append("pattern inputs instantiated without created files")
        m31 = _m31(source, rows, manifest, check["found"], visible, warnings)
    if not commit_ok:
        found = check["found"]
        warnings.append(
            f"precondition null_commit failed ({found['null_commit']} != "
            f"{found['run_commit']}): "
            + ("M3.5" if graded else "M3.1, M3.5")
            + " null"
        )
    if null_score is None:
        warnings.append("input null_score missing: M3.5 null")
    elif run_score is None:
        warnings.append("input run_score missing: M3.5 run fields null")
    block = {
        "run_key": run.get("run_key"),
        "family": run.get("family"),
        "capabilities": dict(sorted(coverage["capabilities"].items())),
        "bases": {
            "oracle_writes": writes_basis(rows, exact),
            "pattern_matches": "exact" if visible else "fallback",
            "source": "exact" if graded else "fallback",
        },
        "preconditions": labels,
        "metrics": {
            "M3.1": m31,
            "M3.5": scores(null_score if commit_ok else None, run_score),
        },
        "warnings": warnings,
    }
    return match.round_floats(
        {"oracle_inputs": rows, "oracle_inputs_run": block}
    )
