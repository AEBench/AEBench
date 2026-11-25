"""Layer 3: rerun structure and file classes of one run (structure spec).

Reads the records of layers 1 and 2 and derives, per run, the
observe/act partition and the three key classes of the agent's shell
calls (T14), the rerun edges between consecutive act calls of a class
(T15), the file class and binary content of every path (T16) and the run
counts M2.1 to M2.3 and M4.2 (T19). Facts only: nothing here judges a run.
"""

import bisect
import collections
import fnmatch
import posixpath
import re
import types
import typing

from trajectory_checks import facts, match, parse


class CallStructure(typing.TypedDict):
    """One agent shell call's classes (spec §5.1)."""

    call_id: str
    seq: int
    call_class: str  # act, observe
    class_reason: str  # observe, program, redirect, background, no_command
    exact_class: str
    shape_class: str
    program_class: str
    program_key: str
    shape_index: int | None
    program_index: int | None
    prev_shape_call: str | None
    prev_program_call: str | None


class Rerun(typing.TypedDict):
    """One rerun edge (spec §5.2)."""

    rerun_id: str
    key_type: str  # shape, program
    class_id: str
    prev_call_id: str
    call_id: str
    prev_seq: int
    seq: int
    prev_outcome: str  # ok, failed, killed, unknown
    prev_match_under: bool
    identical: bool
    arg_change_type: str  # none, numeric, count, words
    arg_count_delta: int
    numeric_changes: list[dict[str, typing.Any]]
    numeric_direction: str | None  # decreased, increased, mixed
    words_removed: list[str]
    words_added: list[str]
    narrowed: bool
    calls_between: int
    act_calls_between: int
    edit_calls_between: int
    writes_between_under: int
    writes_between_over: int
    untracked_writes_between: int | None  # capability untracked_visible
    gap_observed: bool
    unchanged_under: bool
    unchanged_over: bool


class FileClass(typing.TypedDict):
    """One path's file class and binary content (spec §5.3)."""

    path: str
    in_workspace: bool
    extension: str
    file_class: str
    class_rule: str
    mode_source: str | None  # baseline
    binary_content: bool | None  # capability final_content


class RunStructure(typing.TypedDict):
    """structure.json (spec §5.4)."""

    run_key: str | None
    family: str | None
    preconditions: dict[str, str]  # label -> holds, vacuous, fails
    capabilities: dict[str, bool]  # copied from RunInfo
    bases: dict[str, str]  # basis key -> exact, fallback
    metrics: dict[str, dict[str, typing.Any]]  # "M2.1" -> fields
    warnings: list[str]


class StructureResult(typing.TypedDict):
    """Everything T13 to T19 produce for one run."""

    call_structure: list[CallStructure]
    reruns: list[Rerun]
    file_classes: list[FileClass]
    structure: RunStructure


def _words_of(text: str) -> frozenset[str]:
    return frozenset(text.split())


# §6.12. snapshot_slop_s is layer 1's (match.DEFAULTS), not this layer's.
DEFAULTS = types.MappingProxyType(
    {"recursion_depth": 3}  # not measured: no corpus command nests past 2
)
# §3.3, OBS2: general-purpose programs that only read.
OBSERVE_PROGRAMS = _words_of(
    "cat head tail less more ls tree stat file wc du df free uptime nproc "
    "lscpu uname hostname id whoami pwd date which type realpath readlink "
    "basename dirname grep egrep fgrep rg ag find sort uniq cut tr awk sed "
    "jq column nl diff cmp comm md5sum sha1sum sha256sum echo printf true "
    "false test [ [[ sleep wait watch ps pgrep top jobs nvidia-smi seq cd "
    "pushd popd export set read"
)
_CONTAINER_READS = _words_of("ps logs images inspect top stats")
OBSERVE_SUBCOMMANDS = types.MappingProxyType(
    {
        "git": _words_of(
            "status diff log show branch rev-parse ls-files remote"
        ),
        "docker": _CONTAINER_READS,
        "podman": _CONTAINER_READS,
        "kill": frozenset({"-0"}),  # an option that must be present
        "tmux": _words_of("ls list-sessions capture-pane"),
    }
)
# Options that make an observation program write; sed's are read by
# _observes (S2).
ACT_OPTIONS = types.MappingProxyType(
    {
        "find": _words_of(
            "-delete -exec -execdir -ok -okdir -fprint -fprint0 -fprintf -fls"
        ),
        "awk": frozenset({"-i"}),
    }
)
SHELLS = _words_of("bash sh dash zsh ksh")
_SHELL_VALUES = _words_of("--rcfile --init-file")  # and o, O in a cluster
# OBS3: container exec front ends -> their options that take a value (S1).
CONTAINER_EXEC = types.MappingProxyType(
    dict.fromkeys(
        ("docker", "podman"),
        _words_of("-w --workdir -e --env -u --user --env-file"),
    )
    | {"kubectl": _words_of("-n --namespace -c --container")}
)
GLUE_PROGRAMS = _words_of(
    "cd pushd popd export set unset source . ulimit umask echo printf true "
    "false : sleep wait mkdir trap shopt"
)
_OPERATOR = frozenset("();<>|&")
_TERMINALS = _words_of("/dev/null /dev/stdout /dev/stderr /dev/tty")  # S7
_CLASS_FIELDS = ("call_class", "class_reason")

FILE_CLASSES = (
    *("ci", "build", "notebook", "test", "code", "docs", "data", "config"),
    "other",
)
_SHELL_ROLES = frozenset({"root_exec", "nested_exec"})
_MEDIA = _words_of(
    ".pdf .png .jpg .jpeg .gif .svg .eps .ps .bmp .tif .tiff .webp .ico "
    ".mp4 .mp3 .wav"
)
_ARCHIVES = _words_of(".zip .tar .tgz .gz .bz2 .xz .zst .7z")
# FC5's source, script and proof extensions (§6.6).
_SOURCES = _words_of(
    ".c .cc .cpp .cxx .c++ .h .hh .hpp .hxx .h++ .tcc .inl .ipp .rs .go "
    ".java .kt .kts .scala .cs .fs .ml .mli .hs .py .pyx .pxd .js .mjs .cjs "
    ".ts .tsx .jsx .jl .r .rb .pl .pm .php .lua .swift .m .mm .sv .svh .v "
    ".vh .vhd .vhdl .td .cu .cuh .cl .s .asm .sh .bash .zsh .ksh .fish .ps1 "
    ".bat .cmd .awk .dfy .lean .thy .fst .fsti .tla .smt2 .agda .idr .sql "
    ".zig .nim .d .f .f90 .f95 .for .ll .wat .sol .el .clj .ex .exs .erl "
    ".dart .groovy .tcl .y .yy .l .lex .proto .thrift"
)
_CI_DIRS = _words_of(".github .gitlab .circleci .buildkite")
_CI_NAMES = _words_of(
    ".gitlab-ci.yml .travis.yml Jenkinsfile azure-pipelines.yml appveyor.yml "
    ".cirrus.yml"
)
_BUILD_NAMES = _words_of(
    "Makefile GNUmakefile CMakeLists.txt SConstruct SConscript meson.build "
    "BUILD BUILD.bazel WORKSPACE build.gradle build.gradle.kts "
    "settings.gradle gradlew gradlew.bat pom.xml Gemfile.lock "
    "build.xml build.sbt Cargo.toml Cargo.lock package.json package-lock.json "
    "yarn.lock pyproject.toml setup.py setup.cfg Pipfile environment.yml "
    "go.mod go.sum configure configure.ac Gemfile Kconfig dune .gitmodules "
    ".gitignore .dockerignore"
)
_BUILD_PATTERNS = _words_of(
    "Makefile.* requirements*.txt Dockerfile* docker-compose*.yml"
)
_BUILD_EXTS = _words_of(  # manifests, then build products (D31)
    ".cmake .mk .mak .gradle .bzl .gemspec .cabal .opam .dockerfile .o .obj "
    ".a .lib .so .dylib .dll .exe .class .pyc .jar .war .whl .egg"
)
_TEST_NAME = re.compile(
    r"^test_|^test\.|_test\.\w+$|\.test\.\w+$|_spec\.\w+$|^conftest\.py$"
)
_TEST_DIRS = _words_of(
    "test tests testing unittest unittests unit-test unit_tests __tests__ spec"
)
# FC6: a whole name in any case, or with a capital first letter then
# [._-]… (D52, D60), unless its extension is FC9's or FC10's other than
# .txt (S9, D53); and man pages `name.[1-9]`, the name not ending in a
# digit, outside FC8's folders (D54).
_DOC_WORDS = (
    "README|LICENSE|LICENCE|COPYING|CHANGELOG|CHANGES|HISTORY|CONTRIBUTING"
    "|CITATION|AUTHORS|NOTICE|NOTICES|INSTALL"
)
_DOC_NAMES = re.compile(rf"(?i:{_DOC_WORDS})|(?=[A-Z])(?i:{_DOC_WORDS})[._-].*")
_MAN_PAGE = re.compile(r"[^.]*[^.0-9]\.[1-9]")  # not python3.8
_DOC_EXTS = _words_of(".md .markdown .rst .adoc .tex .bib .org .html .htm")
_DOC_DIRS = _words_of("doc docs documentation")
_DOC_DIR_EXTS = _MEDIA | {"", ".txt"}
_DATA_DIRS = _words_of(
    "data datasets dataset results result output outputs out logs log "
    "figures figs plots tables traces inputs samples fixtures"
)
_CONFIG_EXTS = _words_of(
    ".yaml .yml .toml .ini .cfg .conf .properties .json .json5 .xml .env"
)
_DATA_EXTS = _words_of(
    ".csv .tsv .txt .jsonl .ndjson .parquet .npy .npz .h5 .hdf5 .pkl .pickle "
    ".dat .data .bin .arrow .feather .db .sqlite .log .out"
) | (_MEDIA | _ARCHIVES)
_NOT_DOC_EXTS = (_CONFIG_EXTS | _DATA_EXTS) - {".txt"}


# The keys of each block, and the patterns of §7, PRE4, PRE5 (D37) over
# them: (label, block and keys voided), in warning order.
_M42 = (
    "changed_by_class mode_only_by_class reverted_by_class "
    "changed_by_class_and_role_under changed_by_class_and_role_over "
    "shell_written_by_class_under shell_written_by_class_over "
    "created_by_class_under created_by_class_over baseline_by_class "
    "binary_changed_by_class"
).split()
_SHELL_KEYS = (
    "changed_by_class_and_role_under, changed_by_class_and_role_over, "
    "shell_written_by_class_under, shell_written_by_class_over"
)
PRECONDITIONS = (
    ("log", "M2.1, M2.3"),
    ("C1_under", "M2.2"),
    (
        "final_diff",
        f"M4.2 changed_by_class, mode_only_by_class, reverted_by_class, "
        f"{_SHELL_KEYS}, binary_changed_by_class",
    ),
    ("C6", f"M4.2 reverted_by_class, {_SHELL_KEYS}"),
    ("C7_under", f"M4.2 {_SHELL_KEYS}"),
    ("untracked_visible", "M4.2 created_by_class_under, created_by_class_over"),
    ("tracked_files", "M4.2 baseline_by_class"),
    ("final_content", "M4.2 binary_changed_by_class"),
)
_LAYER2_LABELS = ("log", "C1_under", "C6", "C7_under", "untracked_visible")
_INPUT_LABELS = ("log", "final_diff", "tracked_files")
_RATED = {row[0]: row[1:3] for row in match.PRECONDITIONS}
# D30: basis key -> the capability its exact form needs.
BASES = (
    ("writes_between", "parent_links"),  # RR5 roots
    ("gap", "exact_timing"),  # RR5, RR6 times
    ("unchanged_under", "untracked_visible"),  # RR6; or workspace scope
)
_UNTRACKED = ("created_untracked", "modified_untracked", "deleted_untracked")
# PRE8: capability -> (fields, which non-noise execs must carry them).
IMPLIED_FIELDS = types.MappingProxyType(
    {
        "parent_links": (("root_command_id", "depth"), lambda e: True),
        "exact_timing": (
            ("ended_at", "released_at"),
            lambda e: e["complete"],
        ),
        "untracked_visible": (  # a skipped untracked walk is known-partial
            _UNTRACKED,
            lambda e: (
                e["snapshot"]["status"] in ("ok", "truncated")
                and e["snapshot"].get("untracked_skipped") is None
            ),
        ),
    }
)


def _untracked_skipped(record: parse.Exec) -> bool:
    """The exec's untracked walk was skipped: its untracked writes unknown."""
    return record["snapshot"].get("untracked_skipped") is not None


def _complete(record: parse.Exec | None) -> bool:
    """RR6 (D32): a snapshot with status ok and its untracked part known."""
    return bool(
        record
        and record["snapshot"]["status"] == "ok"
        and not _untracked_skipped(record)
    )


def _field(record: parse.Exec, name: str) -> typing.Any:
    """An exec field, also from its snapshot (the *_untracked lists)."""
    return record[name] if name in record else record["snapshot"].get(name)


def _preconditions(
    linked: match.LinkResult, labels: dict, caps: dict, live: list, text: str
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """T13: the labels of §7, the bases and PRE8's warnings."""
    held = {
        **linked["coverage"]["inputs"],
        "final_content": caps["final_content"] and text is not None,
    }
    labels = {
        label: labels[label]
        if label in _LAYER2_LABELS
        else ("holds" if held[label] else "fails")
        for label, _ in PRECONDITIONS
    }
    warnings, exact = [], {}
    for name, (fields, should) in IMPLIED_FIELDS.items():
        missing = [
            next(f for f in fields if _field(e, f) is None)
            for e in live
            if should(e) and any(_field(e, f) is None for f in fields)
        ]
        exact[name] = caps[name] and not missing
        if name == "untracked_visible":  # D47: or visible by scope
            exact[name] |= bool(linked["coverage"]["rates"].get(name))
        if caps[name] and missing:
            warnings.append(
                f"capability {name} true but {missing[0]} missing on "
                f"{len(missing)} records"
                + ("" if exact[name] else ": fallback used")
            )
    bases = {k: ("fallback", "exact")[exact[c]] for k, c in sorted(BASES)}
    return labels, bases, warnings


def _void(metrics: dict, labels: dict, coverage: dict, slack: float) -> list:
    """PRE4: voids the keys of failed labels, one warning per label."""
    warnings = []
    for label, voids in PRECONDITIONS:
        if labels[label] != "fails":
            continue
        fresh = False
        for item in voids.split(", "):
            if item.startswith("M"):
                block, _, item = item.partition(" ")
            for key in fnmatch.filter(metrics[block], item or "*"):
                fresh |= metrics[block][key] is not None
                metrics[block][key] = None
        if not fresh:
            continue  # PRE4: everything it guards is void already
        if label in _INPUT_LABELS:
            head = f"input {label} missing"
        elif label in _RATED:
            key, op = _RATED[label]
            value = coverage["rates"].get(key)
            shown = "null" if value is None else f"{value:.2f}"
            bound = f">= {slack}" if op == ">=" else "= 1"
            head = f"precondition {label} {bound} failed ({shown})"
        else:
            head = f"precondition {label} failed"
        warnings.append(f"{head}: {voids} null")
    return warnings


def _program(words: list[str]) -> list[str]:
    """The words from the program on (OBS2, KEY3; layer 2's SHP8 skip)."""
    return facts.command_words(words)[1]


def _shell_part(command: str) -> str:
    """OBS1: the text without its heredoc bodies."""
    pieces = parse.split_heredocs(command)
    return "\n".join(text for part, text in pieces if part == "shell")


def _inner(rest: list[str]) -> tuple[str | None, list[str] | None]:
    """OBS3: the script of `SHELL -c`, or the words of a container exec."""
    front = posixpath.basename(rest[0])
    if front in SHELLS:
        i, script = 1, False
        while i < len(rest) and rest[i][:1] in ("-", "+"):
            word = rest[i]  # A5, B1: options end at the first operand
            if word in ("-", "--"):
                i += 1
                break
            if word[:2] == "--":
                i += 1 + (word in _SHELL_VALUES)
                continue
            script |= word[0] == "-" and "c" in word
            i += 1 + word.count("o") + word.count("O")  # -o NAME, -O NAME
        if script and i < len(rest):
            return rest[i], None
    elif rest[1:2] == ["exec"] and front in CONTAINER_EXEC:
        i = 2
        while i < len(rest) and rest[i].startswith("-"):
            i += 1 + (rest[i] in CONTAINER_EXEC[front])
        i += 1 + (rest[i + 1 : i + 2] == ["--"])  # the container, then `--`
        return None, _program(rest[i:])
    return None, None


def _writes(command: facts.SimpleCommand) -> bool:
    """OBS4: an output redirection to a file other than a _TERMINALS one."""
    return any(
        ">" in op
        and target not in _TERMINALS
        and not (op.endswith(">&") and (target.isdigit() or target == "-"))
        for op, target in command["redirects"]
    )


def _observes(rest: list[str], depth: int, limit: int) -> bool:
    """OBS2, OBS3: whether a command's program words only read."""
    script, inner = _inner(rest)
    if script is not None or inner is not None:
        if depth >= limit or inner == []:
            return False
        if script is not None:
            return call_class(script, limit, depth + 1)[0] == "observe"
        return _observes(inner, depth + 1, limit)
    program, args = posixpath.basename(rest[0]), rest[1:]
    if program in OBSERVE_SUBCOMMANDS:
        operand = next((w for w in args if not w.startswith("-")), None)
        return (
            "-0" in args
            if program == "kill"
            else operand in OBSERVE_SUBCOMMANDS[program]
        )
    if program == "sed" and any(
        w.startswith("--in-place") or re.fullmatch(r"-[nrsuzE]*i.*", w)
        for w in args
    ):
        return False
    acting = ACT_OPTIONS.get(program, frozenset())
    return program in OBSERVE_PROGRAMS and not acting.intersection(args)


def call_class(
    command: str, limit: int = DEFAULTS["recursion_depth"], depth: int = 0
) -> tuple[str, str]:
    """Returns (act or observe, class_reason) of shell text (§6.2).

    Args:
        command: Shell text (a call's command_norm).
        limit: The recursion depth of OBS3 (DEFAULTS recursion_depth).
        depth: The current depth (OBS3's recursion).

    Returns:
        ("observe", "observe"), or ("act", the first of no_command,
        background, redirect, program that applies, OBS6).
    """
    commands = facts.simple_commands(command)
    if not commands:
        return "act", "no_command"
    if match.backgrounds(_shell_part(command)):  # S3
        return "act", "background"
    if any(map(_writes, commands)):
        return "act", "redirect"
    for found in commands:
        rest = _program(found["words"])
        if rest and not _observes(rest, depth, limit):
            return "act", "program"
    return "observe", "observe"


def program_key(
    command: str, limit: int = DEFAULTS["recursion_depth"], depth: int = 0
) -> str:
    """Returns the program key of shell text (§6.3, KEY3).

    Args:
        command: Shell text.
        limit: The recursion depth into shells and container front ends.
        depth: The current depth.

    Returns:
        The normalized program and its first operand; layer 2's shape when
        the text has no simple command.
    """
    found = [
        (_program(c["words"]), c["after_pipe"])
        for c in facts.simple_commands(command)
        if not c["in_substitution"]
    ]
    found = [(rest, piped) for rest, piped in found if rest]
    if not found:
        return facts.command_shape(command)[0]
    rest = next(
        (
            w
            for w, piped in found
            if not (piped or posixpath.basename(w[0]) in GLUE_PROGRAMS)
        ),
        found[0][0],
    )
    while depth < limit:
        script, inner = _inner(rest)
        if script is not None:
            return program_key(script, limit, depth + 1)
        if not inner:
            break
        rest, depth = inner, depth + 1
    operand = next((w for w in rest[1:] if not w.startswith("-")), None)
    words = (rest[0], operand) if operand is not None else (rest[0],)
    return " ".join(facts.normalize_word(w)[0] for w in words)


def _words(command: str) -> list[str]:
    """RR3: the normalized word tokens of the shell part, no operators."""
    return [
        facts.normalize_word(token)[0]
        for token in parse.tokens(_shell_part(command))
        if not set(token) <= _OPERATOR
    ]


def _number(text: str) -> float:
    lowered = text.lower()
    return int(lowered, 16) if "0x" in lowered else float(lowered)


def rerun_delta(
    prev: typing.Mapping[str, typing.Any], cur: typing.Mapping[str, typing.Any]
) -> dict[str, typing.Any]:
    """Returns the argument-delta fields of a rerun edge (§6.4, RR3, RR4).

    Args:
        prev: The previous call's call_facts row, with its command_norm
            added (D40).
        cur: The next call's, likewise.

    Returns:
        identical, arg_change_type, arg_count_delta, numeric_changes,
        numeric_direction, words_removed, words_added and narrowed.
    """
    pairs = zip(prev["numeric_args"], cur["numeric_args"], strict=False)
    changes, direction = [], None
    if prev["command_shape"] == cur["command_shape"]:
        changes = [
            {"index": i, "from": a, "to": b}
            for i, (a, b) in enumerate(pairs)
            if a != b
        ]
        change = "numeric" if changes else "none"
    else:
        change = "count" if prev["arg_count"] != cur["arg_count"] else "words"
    try:  # RR4
        signs = {
            (_number(c["to"]) > _number(c["from"]))
            - (_number(c["to"]) < _number(c["from"]))
            for c in changes
        }
        direction = {(-1,): "decreased", (1,): "increased", (): None}.get(
            tuple(signs), "mixed"
        )
    except ValueError:
        pass
    before = collections.Counter(_words(prev["command_norm"] or ""))
    after = collections.Counter(_words(cur["command_norm"] or ""))
    removed = sorted((before - after).elements())
    added = sorted((after - before).elements())
    return {
        "identical": prev["command_norm"] == cur["command_norm"],
        "arg_change_type": change,
        "arg_count_delta": cur["arg_count"] - prev["arg_count"],
        "numeric_changes": changes,
        "numeric_direction": direction,
        "words_removed": removed,
        "words_added": added,
        "narrowed": bool(removed) and not added,
    }


def _call_structure(
    shell: list[parse.Call], call_facts: dict, limit: int
) -> tuple[list[CallStructure], list]:
    """T14 and RR1: classes, indexes, previous members and the edges."""
    ids: dict[str, dict[str, str]] = {"x": {}, "s": {}, "p": {}}
    keys: dict[str, str] = {}  # KEY4: per shape, of its first member
    last: dict[str, CallStructure] = {}
    sizes: collections.Counter[str] = collections.Counter()
    rows, edges = [], []
    for call in shell:
        text = call["command_norm"] or ""
        shape = call_facts[call["call_id"]]["command_shape"]
        if shape not in keys:
            keys[shape] = program_key(text, limit)
        row = CallStructure(
            call_id=call["call_id"],
            seq=call["seq"],
            **dict(zip(_CLASS_FIELDS, call_class(text, limit), strict=True)),
            program_key=keys[shape],
            **dict.fromkeys(("shape_index", "program_index")),
            **dict.fromkeys(("prev_shape_call", "prev_program_call")),
        )
        for prefix, field, value in (
            ("x", "exact", text),
            ("s", "shape", shape),
            ("p", "program", keys[shape]),
        ):  # KEY5
            table = ids[prefix]
            class_id = table.setdefault(value, f"{prefix}{len(table)}")
            row[f"{field}_class"] = class_id
            if prefix == "x" or row["call_class"] != "act":
                continue
            sizes[class_id] += 1
            row[f"{field}_index"] = sizes[class_id]
            if class_id in last:
                row[f"prev_{field}_call"] = last[class_id]["call_id"]
                edges.append((field, last[class_id], row))
            last[class_id] = row
        rows.append(row)
    return rows, sorted(edges, key=lambda e: (e[0] != "shape", e[2]["seq"]))


def _reruns(
    rows: list[CallStructure],
    edges: list,
    calls: list[parse.Call],
    execs: list[parse.Exec],
    linked: match.LinkResult,
    found: facts.FactsResult,
    run: dict[str, typing.Any],
) -> list[Rerun]:
    """T15: the rerun edges with deltas and changes in the gap (§6.4)."""
    caps, slop, exact_under = run["caps"], run["slop"], run["exact_under"]
    links = collections.defaultdict(list)
    for link in linked["links"]:
        links[link["link_type"]].append(link)
    j1 = {k["command_id"]: k["call_id"] for k in links["call_exec"]}
    dup = {k["command_id"]: k["of"] for k in links["wrapper_duplicate"]}
    call_facts = {f["call_id"]: f for f in found["call_facts"]}
    exec_facts = {f["command_id"]: f for f in found["exec_facts"]}
    by_id = {e["command_id"]: e for e in execs}
    by_call = {c["call_id"]: c for c in calls}
    index = {r["call_id"]: i for i, r in enumerate(rows)}

    def sure(call: str | None) -> bool:
        return bool(call and call_facts.get(call, {}).get("match_under"))

    def root(record: parse.Exec) -> tuple[str | None, bool]:
        """(root call, certain) of an exec (RR5 by form, D41)."""
        recorded = record["root_command_id"]
        if caps["parent_links"] and recorded is not None:
            call = j1.get(dup.get(recorded, recorded))
            return call, sure(call)
        own = dup.get(record["command_id"], record["command_id"])
        if own in j1:
            return j1[own], sure(j1[own])
        row = exec_facts.get(own) or {}
        return row.get("root_call_id"), bool(row.get("root_under"))

    def interval(record: parse.Exec) -> tuple[float, float]:
        """TIM5's interval; an incomplete exec ends at the run's end."""
        start, end = record["t_start"], record["t_end"]
        if caps["exact_timing"] and record["ended_at"] is not None:
            start, end = record["released_at"] or start, record["ended_at"]
        if not record["complete"] and run["t_end"] is not None:
            end = run["t_end"]
        return start, start if end is None else end

    def window(keys: list, low: float, high: float) -> slice:
        """The positions of the sorted keys in (low, high]."""
        return slice(
            bisect.bisect_right(keys, low), bisect.bisect_right(keys, high)
        )

    spans = sorted(  # (interval, root, exec) of the non-noise execs
        ((*interval(e), root(e)[0], e) for e in execs if e["noise"] is None),
        key=lambda span: span[:2],
    )
    starts = [span[0] for span in spans]
    longest = max((end - start for start, end, *_ in spans), default=0.0)
    events = []  # (end, root, certain) of each exec write event (RR5)
    for event in links["write_event"]:
        writer = by_id.get(event["writer_id"])
        if event["writer_type"] != "exec":
            continue
        end, traced = event["order_t"], caps["parent_links"]
        if writer and caps["exact_timing"] and writer["ended_at"] is not None:
            end = writer["ended_at"]
        if writer and traced and writer["root_command_id"] is not None:
            events.append((end, *root(writer)))
        else:
            events.append((end, event["root_call_id"], event["writer_under"]))
    events.sort(key=lambda event: event[0])
    event_ends = [event[0] for event in events]
    edit_seqs = sorted(  # integers: (a, b) is (a, b - 1]
        by_call.get(w["writer_id"], {"seq": -1})["seq"]
        for w in links["write_event"]
        if w["writer_type"] == "edit_call"
    )
    out = []
    for number, (key_type, a, b) in enumerate(edges):
        prev, cur = call_facts[a["call_id"]], call_facts[b["call_id"]]
        delta = rerun_delta(
            {**prev, "command_norm": by_call[a["call_id"]]["command_norm"]},
            {**cur, "command_norm": by_call[b["call_id"]]["command_norm"]},
        )
        between = rows[index[a["call_id"]] + 1 : index[b["call_id"]]]
        acts = [r for r in between if r["call_class"] == "act"]
        edits = len(edit_seqs[window(edit_seqs, a["seq"], b["seq"] - 1)])
        before, after = by_call[a["call_id"]], by_call[b["call_id"]]
        low = prev["t_end"] if prev["command_id"] else None
        high = cur["t_start"] if cur["command_id"] else None
        for value in (before["t_end"], before["t_start"]):
            low = value if low is None else low
        high = after["t_start"] if high is None else high
        # S4, S5: no times, no interval or a running root: the gap is unknown
        known = (
            low is not None
            and high is not None
            and low < high
            and by_id.get(prev["command_id"] or "", {}).get("complete", True)
        )
        ends = {a["call_id"], b["call_id"]}
        gap, others = [], []
        if known:
            gap = events[window(event_ends, low, high)]
            others = [  # overlapping the gap; a start <= low - slop - longest
                (start, end, x)  # ends by low - slop
                for start, end, r, x in spans[
                    window(starts, low - slop - longest, high)
                ]
                if r not in ends and end + slop > low
            ]
        over = sum(r not in ends for _, r, _ in gap)
        under = sum(
            bool(s and r is not None and r not in ends) for _, r, s in gap
        )
        untracked = None
        inside = [x for _, end, x in others if low < end <= high]
        if caps["untracked_visible"]:  # a skipped walk lists none
            untracked = sum(
                len(_field(x, f) or ()) for x in inside for f in _UNTRACKED
            ) + sum(  # a collapsed folder's changes are counted, not listed
                c[op]
                for x in inside
                for c in x["snapshot"].get("collapsed_untracked") or ()
                for op in ("created", "modified", "deleted")
            )
        observed = known and all(
            x["snapshot"]["status"] == "ok" and not _untracked_skipped(x)
            for *_, x in others
        )
        if exact_under:  # RR6 (D32)
            calm = untracked in (0, None) and all(
                sure(r["call_id"])
                and _complete(
                    by_id.get(call_facts[r["call_id"]]["command_id"] or "")
                )
                for r in acts
            )
        else:
            calm = not acts
        out.append(
            Rerun(
                rerun_id=f"r{number}",
                key_type=key_type,
                class_id=a[f"{key_type}_class"],
                prev_call_id=a["call_id"],
                call_id=b["call_id"],
                prev_seq=a["seq"],
                seq=b["seq"],
                prev_outcome=prev["outcome"],
                prev_match_under=bool(prev["match_under"]),
                **delta,
                calls_between=len(between),
                act_calls_between=len(acts),
                edit_calls_between=edits,
                writes_between_under=under,
                writes_between_over=over,
                untracked_writes_between=untracked,
                gap_observed=observed,
                unchanged_under=calm and not edits and not over and observed,
                unchanged_over=edits == 0 and under == 0,
            )
        )
    return out


def file_class(path: str, mode: int | None) -> tuple[str, str]:
    """Returns (file class, rule id) of a path (§6.6).

    The class is a function of the path and the baseline mode only, never
    of the content, so it is the same on runs with and without
    final_content (D31).

    Args:
        path: A workspace-relative or absolute path (FC0).
        mode: Its baseline mode bits, or None when unknown.

    Returns:
        One of FILE_CLASSES and the rule FC1 to FC11 that gave it.
    """
    parts = [p for p in path.split("/") if p]
    name = parts[-1] if parts else ""
    lower = {d.lower() for d in parts[:-1]}
    ext = posixpath.splitext(name)[1].lower()
    if set(parts[:-1]) & _CI_DIRS or name in _CI_NAMES:
        return "ci", "FC1"
    if (
        name in _BUILD_NAMES
        or ext in _BUILD_EXTS
        or any(fnmatch.fnmatchcase(name, p) for p in _BUILD_PATTERNS)
    ):
        return "build", "FC2"
    if ext == ".ipynb":
        return "notebook", "FC3"
    if ext in _SOURCES or (ext == "" and mode is not None and mode & 0o111):
        if _TEST_NAME.search(name) or lower & _TEST_DIRS:
            return "test", "FC4"
        return "code", "FC5"
    if (
        (_DOC_NAMES.fullmatch(name) and ext not in _NOT_DOC_EXTS)
        or (_MAN_PAGE.fullmatch(name) and not lower & _DATA_DIRS)
        or ext in _DOC_EXTS
        or (ext in _DOC_DIR_EXTS and lower & _DOC_DIRS)
        or (ext == ".txt" and path == name)
    ):
        return "docs", "FC6"
    if lower & _DATA_DIRS:
        return "data", "FC8"
    if ext in _CONFIG_EXTS:
        return "config", "FC9"
    if ext in _DATA_EXTS:
        return "data", "FC10"
    return "other", "FC11"


_QUOTED = r'"(?:[^"\\]|\\.)*"'
_HEADER = re.compile(rf"({_QUOTED}|a/.*?) ({_QUOTED}|b/.*)")
_BINARY = re.compile(r"^(GIT binary patch|Binary files .* differ)$", re.M)


def binary_sections(diff_text: str) -> dict[str, bool]:
    """Maps each path of a git diff to whether its section is binary (BIN1).

    Args:
        diff_text: The text of a `git diff`.

    Returns:
        The `b/` path of each `diff --git` section -> whether the section
        holds a `GIT binary patch` or a `Binary files … differ` line.
    """
    out = {}
    for section in re.split(r"^diff --git ", diff_text or "", flags=re.M)[1:]:
        header, _, body = section.partition("\n")
        found = header_paths(header)
        if found:
            out[unquote(found[1])[2:]] = bool(_BINARY.search(body))
    return out


def header_paths(header: str) -> tuple[str, str] | None:
    """The two paths of a `diff --git` header, as written (and layer 5).

    git writes `a/P b/P` unless the file was renamed, so an unquoted
    header is split where both sides name one path, which a path holding
    " b/" does not break; otherwise at the first " b/".
    """
    half = (len(header) - 1) // 2
    if header[:2] == "a/" and header[2:half] == header[half + 3 :]:
        if header[half : half + 3] == " b/":
            return header[:half], header[half + 1 :]
    found = _HEADER.fullmatch(header)
    return (found[1], found[2]) if found else None


def unquote(path: str) -> str:
    """A diff header path with git's C-style quotes decoded (and layer 5)."""
    if path.startswith('"'):
        raw = path[1:-1].encode("latin-1", "backslashreplace")
        path = raw.decode("unicode_escape").encode("latin-1").decode("utf-8")
    return path


def _m42(
    path_facts: list[facts.PathFacts],
    classes: list[FileClass],
    modes: dict[str, int | None],
) -> dict[str, typing.Any]:
    """CLS1 to CLS5: the M4.2 class counts, before voiding."""
    counts = collections.defaultdict(collections.Counter)
    for row, found in zip(path_facts, classes, strict=True):
        name, role = found["file_class"], row["final_writer_role"]
        sure, state = bool(row["final_writer_under"]), row["final_state"]
        if not row["in_workspace"]:
            continue
        if state in ("changed", "deleted"):
            counts["changed_by_class"][name] += 1
            if role:
                counts["changed_by_class_and_role_over"][name, role] += 1
                counts["changed_by_class_and_role_under"][name, role] += sure
            shell = _SHELL_ROLES.intersection(row["writer_roles"] or ())
            counts["shell_written_by_class_over"][name] += bool(shell)
            counts["shell_written_by_class_under"][name] += (
                role in _SHELL_ROLES and sure
            )
            counts["binary_changed_by_class"][name] += bool(
                found["binary_content"]
            )
        elif state in ("mode_only", "reverted"):
            counts[f"{state}_by_class"][name] += 1
        if row["tracked"] is not True and row["created"]:
            counts["created_by_class_over"][name] += 1
            counts["created_by_class_under"][name] += sure
    counts["baseline_by_class"].update(
        file_class(path, mode)[0] for path, mode in modes.items()
    )
    out = {}
    for key in _M42:
        nested = collections.defaultdict(dict)
        for cell, count in sorted(counts[key].items()):
            if count and isinstance(cell, tuple):
                nested[cell[0]][cell[1]] = count
            elif count:
                nested[cell] = count
        out[key] = dict(nested)
    return out


def _rerun_counts(
    rows: list[CallStructure], reruns: list[Rerun]
) -> dict[str, dict[str, typing.Any]]:
    """REP1 to REP3: the M2.1, M2.2 and M2.3 blocks."""
    acts = [r for r in rows if r["call_class"] == "act"]
    shapes = collections.Counter(r["shape_class"] for r in rows)
    sizes = {
        field: collections.Counter(r[f"{field}_class"] for r in acts)
        for field in ("shape", "exact", "program")
    }

    def repeated(counter: collections.Counter[str]) -> int:
        return sum(v >= 2 for v in counter.values())

    def largest(counter: collections.Counter[str]) -> int:
        return max(counter.values(), default=0)

    shape = [r for r in reruns if r["key_type"] == "shape"]
    program = [r for r in reruns if r["key_type"] == "program"]
    m22: dict[str, int] = {"shape_reruns": len(shape)}
    for name, outcomes in (
        ("failure", ("failed", "killed")),
        ("success", ("ok",)),
    ):
        over = [r for r in shape if r["prev_outcome"] in outcomes]
        under = [r for r in over if r["prev_match_under"]]
        m22[f"reruns_after_{name}_under"] = len(under)
        m22[f"reruns_after_{name}_over"] = len(over)
        for form, edges in (("under", under), ("over", over)):
            m22[f"reruns_after_{name}_unchanged_{form}"] = sum(
                r[f"unchanged_{form}"] for r in edges
            )
    m22["reruns_after_unknown"] = sum(
        r["prev_outcome"] == "unknown" for r in shape
    )
    change = collections.Counter(r["arg_change_type"] for r in program)
    direction = collections.Counter(r["numeric_direction"] for r in program)
    counted = [
        r["arg_count_delta"] for r in program if r["arg_change_type"] == "count"
    ]
    return {
        "M2.1": {
            "shell_calls": len(rows),
            "act_calls": len(acts),
            "observe_calls": len(rows) - len(acts),
            "shape_classes": len(shapes),
            "repeated_shapes_all": repeated(shapes),
            "repeat_max_all": largest(shapes),
            "repeated_shapes_act": repeated(sizes["shape"]),
            "repeat_max_act": largest(sizes["shape"]),
            "exact_repeat_max_act": largest(sizes["exact"]),
            "program_classes_act": len(sizes["program"]),
            "repeated_programs_act": repeated(sizes["program"]),
            "program_repeat_max_act": largest(sizes["program"]),
        },
        "M2.2": m22,
        "M2.3": {
            "program_reruns": len(program),
            "args_unchanged": change["none"],
            "numeric_changed": change["numeric"],
            "count_changed": change["count"],
            "words_changed": change["words"],
            "numeric_decreased": direction["decreased"],
            "numeric_increased": direction["increased"],
            "count_decreased": sum(d < 0 for d in counted),
            "count_increased": sum(d > 0 for d in counted),
            "narrowed": sum(r["narrowed"] for r in program),
        },
    }


def derive(
    calls: list[parse.Call],
    execs: list[parse.Exec],
    linked: match.LinkResult,
    facts_result: facts.FactsResult,
    *,
    run_info: match.RunInfo,
    baseline: typing.Mapping[str, typing.Any] | None = None,
    final_diff_text: str | None = None,
    config: typing.Mapping[str, typing.Any] | None = None,
) -> StructureResult:
    """Derives the structure records and run counts of one run.

    Args:
        calls: Layer 1's Call records.
        execs: Layer 1's Exec records (universal fields included).
        linked: Layer 1's links, dispositions, paths and coverage.
        facts_result: Layer 2's call, exec and path facts and facts.json.
        run_info: The run's RunInfo; its capabilities select the exact
            or the fallback form of each capability-dependent rule.
        baseline: The content of monitor/tracked_files.json, or None.
        final_diff_text: The text of monitor/final.diff, read by cli.py
            when capabilities["final_content"] is true, else None.
        config: Overrides of DEFAULTS, and layer 1's snapshot_slop_s and
            join_rate_warn.

    Returns:
        The per-item records and structure.json of the run.
    """
    cfg = {**match.DEFAULTS, **DEFAULTS, **(config or {})}
    given = run_info.get("capabilities") or {}
    caps = {name: bool(given.get(name)) for name in sorted(match.CAPABILITIES)}
    live = [e for e in execs if e["noise"] is None]
    labels, bases, warnings = _preconditions(
        linked,
        facts_result["facts"]["preconditions"],
        caps,
        live,
        final_diff_text,
    )
    shell = sorted(
        (c for c in calls if c["tool_type"] == "shell"), key=lambda c: c["seq"]
    )
    call_facts = {f["call_id"]: f for f in facts_result["call_facts"]}
    rows, edges = _call_structure(shell, call_facts, cfg["recursion_depth"])
    run = {
        "caps": caps,
        "slop": cfg["snapshot_slop_s"],
        "t_end": run_info.get("t_end"),
        "exact_under": bases["unchanged_under"] == "exact",
    }
    reruns = _reruns(rows, edges, calls, execs, linked, facts_result, run)
    files = (baseline or {}).get("files") or {}
    modes = {path: (entry or {}).get("mode") for path, entry in files.items()}
    binary = None
    if labels["final_content"] == "holds":
        root = run_info.get("workspace_root") or "/"
        binary = {
            match.relative_path(path, root)[0]: value
            for path, value in binary_sections(final_diff_text).items()
        }
    path_facts = facts_result["path_facts"]
    file_rows = []
    for row in path_facts:
        mode = modes.get(row["path"])
        found, rule = file_class(row["path"], mode)
        file_rows.append(
            FileClass(
                path=row["path"],
                in_workspace=row["in_workspace"],
                extension=posixpath.splitext(row["path"])[1].lower(),
                file_class=found,
                class_rule=rule,
                mode_source=None if mode is None else "baseline",
                binary_content=(binary or {}).get(row["path"]),
            )
        )
    metrics = {
        **_rerun_counts(rows, reruns),
        "M4.2": _m42(path_facts, file_rows, modes),
    }
    warnings += _void(
        metrics, labels, linked["coverage"], cfg["join_rate_warn"]
    )
    structure = RunStructure(
        run_key=run_info.get("run_key"),
        family=run_info.get("family"),
        preconditions=labels,
        capabilities=caps,
        bases=bases,
        metrics=metrics,
        warnings=warnings,
    )
    return match.round_floats(
        StructureResult(
            call_structure=rows,
            reruns=reruns,
            file_classes=file_rows,
            structure=structure,
        )
    )


# §5.5: the structure.csv sub-keys of each class-keyed M4.2 dict, for
# cli.facts_row; a class or role absent from a dict is a 0 cell.
DICT_FIELDS = types.MappingProxyType(
    {
        key: [f"{c}.{r}" for c in FILE_CLASSES for r in facts.ROLES]
        if "_role_" in key
        else FILE_CLASSES
        for key in _M42
    }
)
