"""The per-command facts layer (layer 2).

`derive` is the main entry point. It reads layer 1's records of one run
(parse.py, match.py) and returns per-call, per-exec and per-path facts and
the run counts of facts.json (trajectory-per-command-spec.md §5).
`peer_ratios` is the cross-run step. The shell-text functions
`simple_commands` (returning `SimpleCommand` records, D43), `normalize_word`
and the table `PREFIX_WORDS` are public for layers 3 and 6 (D29). Shell text
is tokenized here, not with shlex: substitutions stay inside their words
and quoted punctuation is never an operator (SHP1, D33).
"""

import collections
import fnmatch
import itertools
import math
import operator
import re
import statistics
import types
import typing

from trajectory_checks import match, parse


class CallFacts(typing.TypedDict):
    """One agent shell call and what its shells did (spec §5.1)."""

    call_id: str
    seq: int
    command_id: str | None
    match_rule: str | None
    match_under: bool
    t_start: float | None
    t_end: float | None
    duration_s: float | None
    complete: bool | None
    span_under_s: float | None
    span_over_s: float | None
    exit_code: int | None
    signal: int | None
    outcome: str  # ok, failed, killed, unknown
    exit_signal: int | None
    command_shape: str
    numeric_args: list[str]
    arg_count: int
    programs: list[str]
    descendants_under: int
    descendants_over: int
    depth_max_over: int | None
    depth_max_under: int | None
    writes_under: dict[str, int]
    writes_over: dict[str, int]
    network_types_under: list[str]
    network_types_over: list[str]
    env_assigned: list[str]
    env_confirmed: list[str] | None


class ExecFacts(typing.TypedDict):
    """One non-noise exec (spec §5.2)."""

    command_id: str
    seq: int
    disposition: str
    root_call_id: str | None
    root_under: bool
    depth: int | None
    outcome: str
    exit_signal: int | None
    network_types: list[str]
    env_added: list[str] | None


class PathFacts(typing.TypedDict):
    """One path of paths.jsonl, with its writers' roles (spec §5.3)."""

    path: str
    in_workspace: bool
    tracked: bool | None
    final_state: str | None
    final_writer: str | None
    final_writer_role: str | None  # edit_call, root_exec, nested_exec
    final_writer_root: str | None
    final_writer_under: bool
    writer_roles: list[str]
    created: bool
    network_created_under: bool
    network_created_over: bool


class RunFacts(typing.TypedDict):
    """facts.json (spec §5.4)."""

    schema_version: str
    run_key: str | None
    family: str | None
    capabilities: dict[str, bool]  # copied from layer 1's RunInfo
    bases: dict[str, str]  # timing, depth, running_at_stop -> exact, fallback
    preconditions: dict[str, str]  # label -> holds, vacuous, fails
    metrics: dict[str, dict[str, typing.Any]]  # "M1.1" -> fields
    warnings: list[str]


class FactsResult(typing.TypedDict):
    """Everything `derive` produces for one run."""

    call_facts: list[CallFacts]
    exec_facts: list[ExecFacts]
    path_facts: list[PathFacts]
    facts: RunFacts


class SystemDiff(typing.TypedDict):
    """monitor/system_files_diff.json: `<A|C|D> <path>` entries (spec §4)."""

    changed: int
    executables: list[str]
    python_startup: list[str]
    etc: list[str]


class SimpleCommand(typing.TypedDict):
    """One simple command of shell text (SHP8; layer 3's OBS1, D43)."""

    words: list[str]  # quotes removed; leading reserved words skipped
    redirects: list[tuple[str, str]]  # (operator, target): ("2>", "x")
    after_pipe: bool  # the separator before it is `|` or `|&`
    in_substitution: bool  # inside $(…), `…`, <(…) or >(…)


class _Token(typing.NamedTuple):
    text: str  # a word with its quotes removed, or an operator
    token_type: str  # word, fd (SHP5) or operator
    inner: typing.Sequence[list["_Token"]] = ()  # each substitution's tokens
    skipped: bool = False  # a case pattern or a function's name (SHP8)


# Set by bash itself on every start or `cd` (bash manual, Shell Variables).
SHELL_MANAGED_ENV = frozenset({"_", "SHLVL", "PWD", "OLDPWD"})
# Commands that run another command (trajectory_stats._PREFIXES).
PREFIX_WORDS = frozenset(
    {"sudo", "env", "nohup", "time", "nice", "exec", "command", "timeout"}
    | {"xargs", "setsid", "stdbuf"}
)
# Reserved words skipped at the start of a simple command (bash manual,
# Reserved Words; layer 3's SYNTAX_WORDS, D41). `[[` is kept as a program.
_RESERVED = frozenset(
    {"!", "if", "then", "else", "elif", "fi", "while", "until", "do"}
    | {"done", "esac", "in"}
)
# A command led by these is a loop or case header or a jump: no program.
_NEUTRAL = frozenset({"for", "select", "case", "break", "continue"})
# Program -> (subcommands or None for any, network type) of spec §6.9.
NETWORK_PROGRAMS = types.MappingProxyType(
    {
        program: (subcommands and frozenset(subcommands.split()), network)
        for names, subcommands, network in (
            ("curl wget aria2c scp rsync gsutil", None, "download"),
            ("git", "clone fetch pull submodule", "download"),
            ("docker", "pull", "download"),
            ("apt", "install update upgrade download", "install"),
            ("apt-get", "install update upgrade download source", "install"),
            ("pip pip3", "install download", "install"),
            ("uv", "pip add sync tool", "install"),
            ("npm pnpm yarn", "install i ci add", "install"),
            ("conda mamba", "install create update", "install"),
            ("gem", "install", "install"),
            ("cargo", "install fetch", "install"),
            ("go", "install get mod", "install"),
            ("rustup", "install toolchain update target component", "install"),
        )
        for program in names.split()
    }
)
NETWORK_TYPES = ("download", "install")
# Text without one of these words has no network type (a fast path).
_NETWORK_WORD = re.compile(
    r"\b(" + "|".join(map(re.escape, NETWORK_PROGRAMS)) + r")\b"
)
# AEBench's own system-file changes (trajectory_stats._SYSTEM_DIFF_BASELINE).
SYSTEM_DIFF_BASELINE = frozenset(
    f"C /etc/{n}{b}" for n in ("group", "gshadow", "passwd") for b in ("", "-")
) | {"A /usr/sbin/docker-init"}
WRITE_OPS = ("create", "delete", "mode", "modify")
ROLES = ("edit_call", "nested_exec", "root_exec")
CHANGED_STATES = ("changed", "deleted", "mode_only")
SYSTEM_LISTS = ("executables", "python_startup", "etc")
# The sub-keys of the dict-valued metric fields, also when PRE4 voids them
# (facts.csv, §5.5).
DICT_FIELDS = types.MappingProxyType(
    {
        "changed_by_final_writer_under": ROLES,
        "changed_by_final_writer_over": ROLES,
        "created_by_final_writer_under": ROLES,
        "created_by_final_writer_over": ROLES,
        "network_execs_by_type_over": NETWORK_TYPES,
    }
)
# (bases key, capability) of spec §6.14.
BASES = (
    ("timing", "exact_timing"),
    ("depth", "parent_links"),
    ("running_at_stop", "start_events"),
)
# MC-04 records of a command still running when the run stopped.
STOP_RECORDS = ("start_only", "flushed_at_stop")
# (label, what its failure voids) of spec §5.4 and §7: metric ids, each
# optionally followed by field patterns; a bare id voids the whole block.
PRECONDITIONS = (
    ("C1_under", "M1.1, M1.3, M1.4, M5.2, M6.1"),
    ("C2_under", "M1.4"),
    ("C6", "M4.1"),
    ("C7_under", "M4.1"),
    (
        "untracked_visible",
        "M4.1 created_by_final_writer_*, M6.1 network_files_created_*",
    ),
    ("env_keys", "M5.2"),
    ("system_diff", "M4.4 executables, python_startup, etc, changed_total"),
    ("log", "M1.2, M4.4 outside_workspace_writes"),
)
# The input file each input label of §5.4 reports as missing.
_INPUT_FILES = types.MappingProxyType(
    {"env_keys": "env_keys", "system_diff": "system_files_diff.json"}
    | {"log": "log"}
)
# Each default with its basis (spec §6.12).
DEFAULTS = types.MappingProxyType(
    {
        # Parent design §15.2 M1.1.
        "percentiles": (0.5, 0.9),
        # 128 + signal number, signals 1-64.
        "exit_signal_range": (129, 192),
        # Not measured; the median of one peer is that peer.
        "min_peers": 2,
    }
)
# Layer 1's (rate key, comparison) per coverage label.
_RATES = types.MappingProxyType(
    {label: (key, op) for label, key, op, _ in match.PRECONDITIONS}
)
# Control and redirection operators, longest first (bash manual,
# Definitions); `{` and `}` are operators only as unquoted words in command
# position (D33, D53).
_OPERATOR = re.compile(
    r"&>>|<<<|<<-|;;&|&&|\|\||;;|;&|\|&|&>|>>|>&|>\||<<|<&|<>|[;&|()<>\n]"
)
_NEWLINE = _Token("\n", "operator")
_WORD_END = frozenset(" \t\r\n;&|()<>")
# Expansions kept in their word; `<(`, `>(` and `((` only at a word's start.
_OPEN = re.compile(r"\$\(\(|\$\(|\$\{|\$'|`|[<>(]\(")
# The rest of a backquoted or ANSI-C quoted text, through its closing quote.
_UNTIL = {q: re.compile(rf"(?:[^{q}\\]|\\.)*{q}", re.S) for q in "`'"}
# After these reserved words a command starts (bash manual, Reserved Words).
_STARTERS = frozenset("! if then else elif while until do time".split())
_FUNCTION = re.compile(r"[ \t]*\([ \t]*\)")  # NAME () of a function definition
_DIGITS = re.compile(r"[0-9]+")
_NUMBER = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?|0[xX][0-9a-fA-F]+")
_BODY_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(\.\d+)?(?![\w.])")
_KEY_VALUE = re.compile(r"(-{0,2}[A-Za-z_][\w.-]*)=(.*)", re.S)
_ASSIGNMENT = re.compile(r"([A-Za-z_]\w*)\+?=")
_NAME = re.compile(r"[A-Za-z_]\w*(=.*)?", re.S)


def _word(
    shell: str, i: int, pair: str = "", depth: int = 1
) -> tuple[_Token, bool, int]:
    """Reads the word at i: (token, whether any of it is quoted, end).

    Quotes and escapes are removed, a backslash-newline joins lines and
    expansions are kept as written; `$'…'` is quoted text, its escapes as
    written. The tokens of each `$(…)`, `` `…` ``, `<(…)` and `>(…)` go to
    `inner`, also inside `$((…))`, `((…))`, `${…}` and an array `NAME=(…)`
    (D54, D56). With `pair`, reads such a body up to the bracket that closes
    `depth` open ones instead. Raises ValueError when a quote or an
    expansion is unclosed.
    """
    out, inner, quoted, start, double = [], [], False, i, False
    while i < len(shell):
        char = shell[i]
        opened = char in "$`<>(" and _OPEN.match(shell, i)
        array = (
            not pair
            and char == "("
            and not quoted
            and _ASSIGNMENT.fullmatch("".join(out))
        )
        kind = "(" if array else opened and opened[0]  # D56: NAME=(…)
        dollar = char in "$`" and not (double and kind == "$'")
        if kind and (dollar or array or i == start and not pair):
            body = i + len(kind)
            if kind in ("`", "$'"):
                close = _UNTIL[kind[-1]].match(shell, body)
                if close is None:
                    raise ValueError("unclosed quote")
                end = close.end()
                if kind == "`":  # bash unescapes \$, \` and \\ first
                    text = re.sub(r"\\([$`\\])", r"\1", shell[body : end - 1])
                    inner.append(_lex(text)[0])
            elif kind in ("$(", "<(", ">("):
                tokens, end = _lex(shell, body, nested=True)
                inner.append(tokens)
            elif array:  # its comments as in bash; only substitutions run
                tokens, end = _lex(shell, body, nested=True, array=True)
                inner += [found for token in tokens for found in token.inner]
            else:
                pairs = "{}" if kind == "${" else "()"
                token, _, end = _word(shell, body, pairs, kind.count("(") or 1)
                inner += token.inner
            quoted |= kind == "$'"
            out.append(shell[body : end - 1] if kind == "$'" else shell[i:end])
            i = end
        elif char == '"':
            double, quoted, i = not double, True, i + 1
        elif char == "\\" and (not double or shell[i + 1 : i + 2] in '$`"\\\n'):
            if shell[i + 1 : i + 2] != "\n":  # a backslash-newline joins lines
                out.append(shell[i + 1 : i + 2] or char)
                quoted = True
            i += 2
        elif pair and not double and char in pair:
            depth += 1 if char == pair[0] else -1
            if depth == 0:
                return _Token("".join(out), "word", inner), quoted, i + 1
            out.append(char)
            i += 1
        elif double or char != "'" and (pair or char not in _WORD_END):
            out.append(char)
            i += 1
        elif char == "'":
            end = shell.index("'", i + 1)
            out.append(shell[i + 1 : end])
            quoted, i = True, end + 1
        else:
            break
    if double or pair:
        raise ValueError("unclosed quote or expansion")
    return _Token("".join(out), "word", inner), quoted, i


def _lex(
    shell: str, i: int = 0, nested: bool = False, array: bool = False
) -> tuple[list[_Token], int]:
    """Tokens of shell text from i without heredoc bodies, and their end.

    SHP1, SHP5, D33: only unquoted punctuation and, in command position, an
    unquoted `{` or `}` word are operators; `#` at a word's start comments
    to the end of the line. Case patterns and function names are marked
    `skipped` (SHP8, D52, D55). `nested` reads the body of `$(…)`, `<(…)`,
    `>(…)` or, with `array`, an array value, where `case` is a word (D56):
    it ends after the `)` that closes it. Raises ValueError on an unclosed
    quote or substitution.
    """
    found: list[_Token] = []
    depth, start, naming, cases, end = 0, True, False, [], i
    while i < len(shell):
        if shell[i] in " \t\r" or shell.startswith("\\\n", i):
            i += 2 if shell[i] == "\\" else 1
            continue
        if shell[i] == "#":  # a whole-line comment goes with its newline
            stop = shell.find("\n", i)
            whole = not found or found[-1] == _NEWLINE
            i = len(shell) if stop < 0 else stop + whole
            continue
        mode = cases[-1] if cases else ""
        operator = _OPERATOR.match(shell, i)
        if operator and not shell.startswith(("((", "<(", ">("), i):
            text = operator[0]
            pattern = mode == "pattern" and text in ("(", "|", ")")
            if pattern and text == ")":
                cases[-1] = "body"
            elif text in ("(", ")") and not pattern:
                depth += 1 if text == "(" else -1
            elif text in (";;", ";&", ";;&") and cases:
                cases[-1] = "pattern"
            if depth < 0 and nested:
                return found, operator.end()
            found.append(_Token(text, "operator", skipped=pattern))
            start = not set(text) & set("<>")
            i = end = operator.end()
            continue
        adjacent = i == end
        token, quoted, i = _word(shell, i)
        word = None if quoted else token.text
        named, naming = naming, start and word == "function"
        if mode == "pattern" and word != "esac" or named or naming:
            token = token._replace(skipped=True)
        elif start and word in ("{", "}"):
            token = token._replace(token_type="operator")
        elif word and _DIGITS.fullmatch(word):
            after = adjacent and found and found[-1].text in (">&", "<&")
            if after or shell[i : i + 1] in ("<", ">"):
                token = token._replace(token_type="fd")
        elif start and word and _FUNCTION.match(shell, i):
            token = token._replace(skipped=True)
        if word == "esac" and (mode == "pattern" or start and mode == "body"):
            cases.pop()
        elif start and word == "case" and not array:
            cases.append("header")
        elif mode == "header" and word == "in":
            cases[-1] = "pattern"
        found.append(token)
        start = naming or named or start and word in _STARTERS
        start |= token.token_type == "operator"
        end = i
    if nested:
        raise ValueError("unclosed substitution")
    return found, i


def _tokens(command: str) -> tuple[list[_Token], list[str]]:
    """The tokens of the shell part and the heredoc body lines (SHP1).

    When the shell part does not lex (an unclosed quote, or nesting too
    deep for the recursion limit), it falls back to whitespace words with
    newline operators, as parse.tokens does (D59).
    """
    pieces = list(parse.split_heredocs(command))
    shell = "\n".join(text for part, text in pieces if part == "shell")
    bodies = [text for part, text in pieces if part == "body"]
    try:
        return _lex(shell)[0], bodies
    except (ValueError, RecursionError):
        words = re.findall(r"\S+|\n", shell.strip())
        found = [_NEWLINE if w == "\n" else _Token(w, "word") for w in words]
        return found, bodies


def normalize_word(word: str) -> tuple[str, list[str]]:
    """Normalizes one word token by SHP2 to SHP4.

    Public for layers 3 and 6 (D29).

    Args:
        word: One word token, quotes removed.

    Returns:
        The normalized word and the numeric literals it replaced, as
        written. A word containing whitespace is returned unchanged (SHP4).
    """
    if any(char.isspace() for char in word):
        return word, []
    key, value = "", word
    key_value = _KEY_VALUE.fullmatch(word)
    if key_value:  # SHP3: the value by SHP2 and SHP4 only
        key, value = key_value[1] + "=", key_value[2]
    if "/" in value and not value.startswith("-") and value != "/":
        value = value.rstrip("/").rsplit("/", 1)[-1] or "/"
    if _NUMBER.fullmatch(value):
        return key + "N", [value]
    return key + value, []


def command_shape(command: str) -> tuple[str, list[str], int]:
    """Returns (shape, numeric_args, arg_count) of shell text (§6.3).

    Args:
        command: Shell text.

    Returns:
        The shape (SHP1 to SHP6), the numeric literals it replaced in text
        order (SHP2, SHP3) and the number of word tokens (SHP7).
    """
    found, bodies = _tokens(command)
    out, numbers = [], []
    for token in found:
        word, replaced = (
            normalize_word(token.text)
            if token.token_type == "word"
            else (token.text, [])
        )
        out.append(word)
        numbers += replaced
    shape = " ".join(out) + "".join(
        "\n" + _BODY_NUMBER.sub("N", body) for body in bodies
    )
    return shape, numbers, sum(t.token_type != "operator" for t in found)


def simple_commands(command: str) -> list[SimpleCommand]:
    """Splits shell text into simple commands (§6.3, SHP8, D43).

    Heredoc bodies are split off with parse.split_heredocs. Each simple
    command's redirections, with their targets, are kept apart from its
    words; a redirection after a group close (`}`, `)`, `done`, `fi`,
    `esac`) goes to the group's last command (D45). Leading reserved words
    are skipped, and a `for`, `select` or `case` header, case patterns, a
    function definition's `function` and name, a `break` or `continue` and
    an arithmetic command are dropped (D41, D52, layer 3's OBS2). A
    substitution stays inside its word; the commands inside `$(…)`,
    backquotes, `<(…)` and `>(…)`, also within arithmetic or `${…}`, follow
    the command that holds them, with `in_substitution` true (D54). Public
    for layers 3 and 6 (D29).

    Args:
        command: Shell text.

    Returns:
        The simple commands in text order.
    """
    return _split(_tokens(command)[0], nested=False)


def _split(found: list[_Token], nested: bool) -> list[SimpleCommand]:
    """simple_commands over tokens; `nested` inside a substitution."""
    commands: list[SimpleCommand] = []
    inner: list[list[_Token]] = []
    current = SimpleCommand(
        words=[], redirects=[], after_pipe=False, in_substitution=nested
    )
    last: SimpleCommand | None = None  # the last one at this level
    redirect, fd, closed = None, "", False
    for token in (*found, _NEWLINE):
        inner += token.inner
        if token.skipped and token.text != ")":  # a case pattern's close splits
            continue
        if redirect is not None and token.token_type != "operator":
            current["redirects"].append((redirect, token.text))
            redirect = None
        elif token.token_type == "fd":
            fd = token.text
        elif token.token_type == "word":
            current["words"].append(token.text)
        elif set(token.text) & set("<>"):
            redirect, fd = fd + token.text, ""
        else:
            words = current["words"]
            while words and words[0] in _RESERVED:
                closed |= words.pop(0) in ("done", "fi", "esac")
            if words and (words[0] in _NEUTRAL or words[0][:2] == "(("):
                words.clear()
            elif words == ["time"] and token.text in ("(", "{"):  # time { …; }
                words.clear()
            if words or current["redirects"] and not (last and closed):
                commands.append(current)
                last = current
            elif current["redirects"]:
                last["redirects"] += current["redirects"]
            for tokens in inner:
                commands += _split(tokens, nested=True)
            empty = not (words or current["redirects"])
            pipe = token.text in ("|", "|&") or (
                token.text in ("(", "{", "\n")
                and empty
                and current["after_pipe"]
            )
            closed = token.text in (")", "}") and not token.skipped
            inner, redirect = [], None
            current = SimpleCommand(
                words=[], redirects=[], after_pipe=pipe, in_substitution=nested
            )
    return commands


def _command_words(words: list[str]) -> tuple[list[str], list[str]]:
    """(assigned names, the words from the program on) of one command.

    Leading NAME=value words and PREFIX_WORDS with their options (and the
    duration of `timeout`) are skipped (SHP8, ENV1). `command -v` and
    `command -V` look a name up: no program (D50).
    """
    names, i = [], 0
    while i < len(words):
        head, i = words[i], i + 1
        if assigned := _ASSIGNMENT.match(head):
            names.append(assigned[1])
            continue
        if head not in PREFIX_WORDS:
            return names, words[i - 1 :]
        if head == "command" and words[i : i + 1] in (["-v"], ["-V"]):
            break
        while i < len(words) and words[i].startswith("-"):
            i += 1
        if head == "timeout" and words[i : i + 1] and words[i][:1].isdigit():
            i += 1
    return names, []


def _heads(command: str) -> list[tuple[list[str], list[str]]]:
    """_command_words of each simple command of shell text."""
    return [_command_words(c["words"]) for c in simple_commands(command)]


def programs(command: str) -> list[str]:
    """Returns the program word of each simple command (§6.3, SHP8)."""
    return [rest[0].rsplit("/", 1)[-1] for _, rest in _heads(command) if rest]


def network_types(command: str) -> list[str]:
    """Returns the sorted network types of shell text (§6.9, NET1)."""
    if not _NETWORK_WORD.search(command):
        return []
    found = set()
    for _, (program, *rest) in filter(lambda h: h[1], _heads(command)):
        program = program.rsplit("/", 1)[-1]
        if program in ("python", "python3") and rest[:2] == ["-m", "pip"]:
            program, rest = "pip", rest[2:]
        subcommands, network_type = NETWORK_PROGRAMS.get(program, ((), None))
        operand = next((w for w in rest if not w.startswith("-")), None)
        if network_type and (subcommands is None or operand in subcommands):
            found.add(network_type)
    return sorted(found)


def env_assigned(command: str) -> list[str]:
    """Returns the environment names the text assigns (§6.8, ENV1)."""
    names = set()
    for assigned, rest in _heads(command):
        if rest:
            names.update(assigned)
        if rest[:1] in (["export"], ["declare"], ["typeset"]):
            names.update(
                w.split("=", 1)[0]
                for w in rest[1:]
                if not w.startswith("-") and _NAME.fullmatch(w)
            )
    return sorted(names)


def outcome(record: parse.Exec | None) -> str:
    """Returns ok, failed, killed or unknown (§6.2, OUT1, OUT3)."""
    if record is None or (
        record["signal"] is None and record["exit_code"] is None
    ):
        return "unknown"
    if record["signal"] is not None:
        return "killed"
    return "ok" if record["exit_code"] == 0 else "failed"


def exit_signal(
    record: parse.Exec | None,
    bounds: tuple[int, int] = DEFAULTS["exit_signal_range"],
) -> int | None:
    """Returns the signal a 128+n exit code reports, or None (§6.2, OUT2)."""
    if record is None or record["signal"] is not None:
        return None
    code = record["exit_code"]
    low, high = bounds
    return code - 128 if code is not None and low <= code <= high else None


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile, or None for no values (§6.10)."""
    if not values:
        return None
    rank = max(math.ceil(fraction * len(values)), 1)
    return sorted(values)[rank - 1]


def precondition(
    coverage: match.Coverage,
    label: str,
    config: typing.Mapping[str, typing.Any],
) -> str:
    """Evaluates a coverage label of §7 to holds, vacuous or fails.

    The labels read from coverage.json: C1_under, C2_under, C6, C7_under
    (copied from its preconditions block when present, PRE6),
    untracked_visible and log. env_keys depends on the execs and
    system_diff on derive's argument; both are evaluated inside derive.

    Args:
        coverage: Layer 1's coverage report.
        label: The label.
        config: Holds layer 1's `join_rate_warn` (D51).

    Returns:
        holds, vacuous or fails.
    """
    inputs, counts, rates = (coverage[k] for k in ("inputs", "counts", "rates"))
    if label == "log":
        return "holds" if inputs["log"] else "fails"
    if label == "untracked_visible":
        visible = coverage.get("capabilities", {}).get(label)
        return "holds" if visible or rates.get(label) else "fails"
    if "preconditions" in coverage:
        return coverage["preconditions"][label]
    if not (inputs["trace"] and coverage["integrity"]["C0.1"]):
        return "fails"  # PRE3
    key, op = _RATES[label]
    value = rates[key]
    if value is not None:
        held = value == 1 if op == "=" else value >= config["join_rate_warn"]
        return "holds" if held else "fails"
    by_disposition = counts["execs_by_disposition"]
    live = counts["execs_total"] - by_disposition.get("noise", 0)
    timed = live - by_disposition.get("out_of_window", 0)  # D34
    statuses = counts["snapshot_status_counts"]
    empty = {
        "C1_under": counts["shell_calls"] == counts["calls_running_at_stop"],
        "C2_under": timed == 0,
        "C6": live == 0 or statuses["ok"] + statuses["truncated"] > 0,
        "C7_under": inputs["final_diff"] and counts["final_diff_files"] == 0,
    }
    return "vacuous" if empty[label] else "fails"


def _failure(
    label: str,
    coverage: match.Coverage,
    config: typing.Mapping[str, typing.Any],
) -> str:
    """The start of a failed label's warning (spec §5.4)."""
    if label in _INPUT_FILES:
        return f"input {_INPUT_FILES[label]} missing"
    if label not in _RATES:
        return f"precondition {label} failed"
    key, op = _RATES[label]
    value = coverage["rates"].get(key)
    shown = "null" if value is None else f"{value:.2f}"
    bound = f">= {config['join_rate_warn']}" if op == ">=" else "= 1"
    return f"precondition {label} {bound} failed ({shown})"


_seq = operator.itemgetter("seq")  # the sort key of calls and execs


def _max(values: typing.Iterable[float | None]) -> float | None:
    return max((v for v in values if v is not None), default=None)


def derive(
    calls: list[parse.Call],
    execs: list[parse.Exec],
    linked: match.LinkResult,
    *,
    run: match.RunInfo,
    system_diff: SystemDiff | None = None,
    config: typing.Mapping[str, typing.Any] | None = None,
) -> FactsResult:
    """Derives the per-command facts and run counts of one run.

    Environment names come from each Exec.env_keys and the capabilities
    from linked.coverage (layer 1's RunInfo.capabilities).

    Args:
        calls: Call records of the run, any order.
        execs: Exec records of the run, any order.
        linked: The value of match.link on them.
        run: The run's RunInfo.
        system_diff: monitor/system_files_diff.json, or None when missing.
        config: Overrides of DEFAULTS and match.DEFAULTS; of layer 1's,
            only `join_rate_warn` is read, for the warnings (D51).

    Returns:
        call_facts, exec_facts, path_facts and facts (spec §5).

    Raises:
        ValueError: A config key is in neither DEFAULTS.
    """
    unknown = set(config or {}) - set(DEFAULTS) - set(match.DEFAULTS)
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    cfg = {**match.DEFAULTS, **DEFAULTS, **(config or {})}
    coverage = linked["coverage"]
    capabilities = dict(coverage["capabilities"])
    bases = {
        key: "exact" if capabilities[name] else "fallback"
        for key, name in BASES
    }
    run_start, run_end = run["t_start"], run["t_end"]
    by_type = collections.defaultdict(list)
    for k in linked["links"]:
        by_type[k["link_type"]].append(k)
    j1 = {k["call_id"]: k for k in by_type["call_exec"]}
    root_call = {k["command_id"]: k["call_id"] for k in j1.values()}
    j2 = {k["command_id"]: k for k in by_type["exec_parent"]}
    outer = {k["command_id"]: k["of"] for k in by_type["wrapper_duplicate"]}
    events = sorted(by_type["write_event"], key=lambda w: w["event_id"])
    event_of = {w["event_id"]: w for w in events}
    events_at = {
        p["path"]: [event_of[i] for i in p["events"]] for p in linked["paths"]
    }
    rows = {(r["item_type"], r["id"]): r for r in linked["dispositions"]}
    record_of = {e["command_id"]: e for e in execs}
    live = [e for e in sorted(execs, key=_seq) if e["noise"] is None]

    def bounds(record: parse.Exec, over: bool) -> tuple[float, float]:
        """TIM5's measured interval; F9 (over) and EXT6 extend it."""
        start, end = record["t_start"], record["t_end"]
        if bases["timing"] == "exact" and record["ended_at"] is not None:
            start, end = record["released_at"], record["ended_at"]
        stopped = record["record_type"] in STOP_RECORDS
        if run_end is not None and (stopped or over and not record["complete"]):
            end = max(end, run_end)
        return start, end

    def owner(cid: str) -> str | None:
        cid = outer.get(cid, cid)
        return root_call.get(cid) or (
            j2[cid]["root_call_id"] if cid in j2 else None
        )

    chain: dict[str, int | None] = {}

    def exact_depth(cid: str) -> int | None:
        """NST2's exact depth of a rooted exec; None when not given."""
        own = record_of[cid]["depth"]
        root = record_of[j1[owner(cid)]["command_id"]]["depth"]
        known = bases["depth"] == "exact" and None not in (own, root)
        return own - root if known else None

    def depth(cid: str) -> int | None:
        """NST2: exact from Exec.depth, else by the J2 chain.

        A wrapper duplicate has its outer exec's depth in both forms (D39).
        """
        cid = outer.get(cid, cid)
        if owner(cid) is None:
            return None
        if (exact := exact_depth(cid)) is not None:
            return exact
        trail, node = [], cid
        while node not in chain:
            if node in root_call:
                chain[node] = 0
            elif j2[node]["parent"] is None:
                chain[node] = 1  # reparented: its parent is unknown
            else:
                trail.append(node)
                node = outer.get(j2[node]["parent"], j2[node]["parent"])
        for item in reversed(trail):
            parent = j2[item]["parent"]
            chain[item] = chain[outer.get(parent, parent)] + 1
        return chain[cid]

    missing = any(e["env_keys"] is None for e in live)
    env_label = "fails" if missing else "holds" if live else "vacuous"
    env = env_label != "fails"
    first = min((record_of[c] for c in root_call), key=_seq, default=None)
    # ENV2; None without a matched root, so env_added is ⊥ there (D38).
    baseline = set(first["env_keys"] or ()) if env and first else None

    # Exec facts.
    exec_facts = []
    for record in live:
        cid = record["command_id"]
        parent = record_of.get(j2[cid]["parent"]) if cid in j2 else None
        reference = baseline
        if parent is not None and parent["env_keys"] is not None:
            reference = set(parent["env_keys"])
        added = None
        if env and reference is not None:
            added = sorted(
                set(record["env_keys"] or ()) - reference - SHELL_MANAGED_ENV
            )
        exec_facts.append(
            ExecFacts(
                command_id=cid,
                seq=record["seq"],
                disposition=rows["exec", cid]["disposition"],
                root_call_id=owner(cid),
                root_under=bool(rows["exec", cid]["root_under"]),
                depth=depth(cid),
                outcome=outcome(record),
                exit_signal=exit_signal(record, cfg["exit_signal_range"]),
                network_types=network_types(record["command"]),
                env_added=added,
            )
        )
    fact_of = {f["command_id"]: f for f in exec_facts}

    # Call facts.
    descendants = collections.defaultdict(list)
    for k in by_type["exec_parent"]:
        descendants[k["root_call_id"]].append(fact_of[k["command_id"]])
    writes = collections.defaultdict(lambda: dict.fromkeys(WRITE_OPS, 0))
    for w in events:
        if w["writer_type"] == "exec":
            writes[w["root_call_id"], False][w["op"]] += 1
            writes[w["root_call_id"], True][w["op"]] += w["writer_under"]

    def span(group: list[ExecFacts], over: bool) -> float | None:
        """TIM2 over a call's root exec and the given descendants."""
        limits = [bounds(record_of[f["command_id"]], over) for f in group]
        if not limits:
            return None
        return max(b for _, b in limits) - min(a for a, _ in limits)

    call_facts = []
    shell = [c for c in calls if c["tool_type"] == "shell"]
    for call in sorted(shell, key=_seq):
        link = j1.get(call["call_id"])
        root = record_of[link["command_id"]] if link else None
        mine = (
            [fact_of[root["command_id"]], *descendants[call["call_id"]]]
            if root
            else []
        )
        under = mine[:1] + [f for f in mine[1:] if f["root_under"]]
        start, end = bounds(root, False) if root else (None, None)
        shape, numbers, count = command_shape(call["command_norm"] or "")
        assigned = env_assigned(call["command"] or "")
        seen = set().union(
            *(record_of[f["command_id"]]["env_keys"] or () for f in mine)
        )
        depth_max = (
            max((f["depth"] for f in mine[1:]), default=0) if root else None
        )
        exact = root and all(
            exact_depth(f["command_id"]) is not None for f in mine
        )
        call_facts.append(
            CallFacts(
                call_id=call["call_id"],
                seq=call["seq"],
                command_id=link["command_id"] if link else None,
                match_rule=link["rule"] if link else None,
                match_under=bool(rows["call", call["call_id"]]["match_under"]),
                t_start=start,
                t_end=end,
                duration_s=None if root is None else end - start,
                complete=root["complete"] if root else None,
                span_under_s=span(under, False),
                span_over_s=span(mine, True),
                exit_code=root["exit_code"] if root else None,
                signal=root["signal"] if root else None,
                outcome=outcome(root),
                exit_signal=exit_signal(root, cfg["exit_signal_range"]),
                command_shape=shape,
                numeric_args=numbers,
                arg_count=count,
                programs=programs(call["command_norm"] or ""),
                descendants_under=sum(
                    f["root_under"] for f in descendants[call["call_id"]]
                ),
                descendants_over=len(descendants[call["call_id"]]),
                depth_max_over=depth_max,
                depth_max_under=depth_max if exact else None,
                writes_under=dict(writes[call["call_id"], True]),
                writes_over=dict(writes[call["call_id"], False]),
                network_types_under=sorted(
                    {t for f in under for t in f["network_types"]}
                ),
                network_types_over=sorted(
                    {t for f in mine for t in f["network_types"]}
                ),
                env_assigned=assigned,
                env_confirmed=sorted(set(assigned) & (seen - baseline))
                if env and root
                else None,
            )
        )

    # Path facts.
    def role(w: match.WriteEvent) -> str:
        if w["writer_type"] == "edit_call":
            return "edit_call"
        return "root_exec" if w["writer_id"] in root_call else "nested_exec"

    others = [(bounds(e, True), owner(e["command_id"])) for e in live]

    def exclusive(w: match.WriteEvent) -> bool:
        return not any(
            a < w["t_end"] and w["t_start"] < b and call != w["root_call_id"]
            for (a, b), call in others
        )

    path_facts = []
    for p in sorted(
        linked["paths"], key=lambda p: (not p["in_workspace"], p["path"])
    ):
        found = events_at[p["path"]]
        final = event_of.get(p["final_writer"])
        downloads = [
            w
            for w in found
            if w["op"] == "create"
            and w["writer_type"] == "exec"
            and fact_of.get(w["writer_id"], {}).get("network_types")
        ]
        path_facts.append(
            PathFacts(
                path=p["path"],
                in_workspace=p["in_workspace"],
                tracked=p["tracked"],
                final_state=p["final_state"],
                final_writer=p["final_writer"],
                final_writer_role=role(final) if final else None,
                final_writer_root=final["root_call_id"] if final else None,
                final_writer_under=p["final_writer_under"],
                writer_roles=sorted({role(w) for w in found}),
                created=any(w["op"] == "create" for w in found),
                network_created_under=any(
                    w["writer_under"] and exclusive(w) for w in downloads
                ),
                network_created_over=bool(downloads),
            )
        )

    # Preconditions and run facts.
    pre = {
        label: precondition(coverage, label, cfg)
        for label, _ in PRECONDITIONS
        if label not in ("env_keys", "system_diff")  # evaluated here
    }
    pre["env_keys"] = env_label
    pre["system_diff"] = "fails" if system_diff is None else "holds"
    rooted = [r for r in call_facts if r["command_id"]]
    failing = ("failed", "killed")
    timed = [
        e
        for e in live
        if rows["exec", e["command_id"]]["disposition"] != "out_of_window"
    ]

    def window(over: bool) -> float:
        """TIM1: the union of the exec intervals, clipped to the run."""
        low = -math.inf if run_start is None else run_start
        high = math.inf if run_end is None else run_end
        spans = [bounds(record, over) for record in timed]
        clipped = [(max(a, low), min(b, high)) for a, b in spans]
        return match.union_length([(a, b) for a, b in clipped if b > a])

    def lengths(over: bool) -> list[float]:
        found = []
        for r in rooted:
            if over or r["match_under"] and r["complete"]:
                a, b = bounds(record_of[r["command_id"]], over)
                found.append(b - a)
        return found

    def streak(fails: typing.Iterable[bool]) -> int:
        groups = itertools.groupby(fails)
        return max((len([*g]) for fail, g in groups if fail), default=0)

    metrics: dict[str, dict[str, typing.Any]] = {
        "M1.1": {
            "command_time_under_s": window(False),
            "command_time_over_s": window(True),
            "wall_time_s": None
            if None in (run_start, run_end)
            else run_end - run_start,
            "longest_span_under_s": _max(r["span_under_s"] for r in call_facts),
            "longest_span_over_s": _max(r["span_over_s"] for r in call_facts),
        }
    }
    for fraction in cfg["percentiles"]:
        name = f"duration_p{round(100 * fraction)}"
        metrics["M1.1"][f"{name}_under_s"] = percentile(
            lengths(False), fraction
        )
        metrics["M1.1"][f"{name}_over_s"] = percentile(lengths(True), fraction)
    nested = [f for f in exec_facts if f["disposition"] == "nested"]
    stopped = coverage["counts"]["calls_running_at_stop"]
    if bases["running_at_stop"] == "exact":
        stopped = sum(
            record_of[r["command_id"]]["record_type"] in STOP_RECORDS
            for r in rooted
        )
    metrics["M1.2"] = {
        "shell_calls": len(call_facts),
        "root_shapes": len({r["command_shape"] for r in call_facts}),
    }
    metrics["M1.3"] = {
        "nonzero_exits_under": sum(
            r["outcome"] in failing and r["match_under"] for r in rooted
        ),
        "nonzero_exits_over": sum(r["outcome"] in failing for r in rooted),
        "kills_under": sum(r["outcome"] == "killed" for r in rooted),
        "kills_over": sum(
            r["outcome"] == "killed" or r["exit_signal"] is not None
            for r in rooted
        ),
        "exits_unknown": sum(r["outcome"] == "unknown" for r in call_facts),
        "failure_streak_under": streak(
            r["outcome"] in failing and r["match_under"] for r in call_facts
        ),
        "failure_streak_over": streak(r["outcome"] != "ok" for r in call_facts),
        "nested_nonzero_exits_under": sum(
            f["outcome"] in failing and f["root_under"] for f in nested
        ),
        "nested_nonzero_exits_over": sum(
            f["outcome"] in failing for f in nested
        ),
        "running_at_stop": stopped,
    }
    metrics["M1.4"] = {
        "depth_max_over": max(
            (r["depth_max_over"] or 0 for r in call_facts), default=0
        )
    }
    metrics["M1.4"]["depth_max_under"] = (
        metrics["M1.4"]["depth_max_over"]
        if bases["depth"] == "exact"
        and all(r["depth_max_under"] is not None for r in rooted)
        else None
    )
    for version in ("under", "over"):
        counts = [r[f"descendants_{version}"] for r in call_facts]
        metrics["M1.4"][f"roots_with_descendants_{version}"] = sum(
            n > 0 for n in counts
        )
        metrics["M1.4"][f"descendants_total_{version}"] = sum(counts)
        metrics["M1.4"][f"descendants_max_{version}"] = max(counts, default=0)

    def by_role(items: list[PathFacts], under: bool) -> dict[str, int]:
        tally = collections.Counter(
            p["final_writer_role"]
            for p in items
            if p["final_writer_role"] and (p["final_writer_under"] or not under)
        )
        return {name: tally[name] for name in ROLES}

    def shell_written(p: PathFacts, under: bool) -> bool:
        return any(
            w["writer_type"] == "exec" and (w["writer_under"] or not under)
            for w in events_at[p["path"]]
        )

    changed = [p for p in path_facts if p["final_state"] in CHANGED_STATES]
    created = [
        p
        for p in path_facts
        if p["in_workspace"] and p["tracked"] is not True and p["created"]
    ]
    metrics["M4.1"] = {"files_changed": len(changed)}
    for version, under in (("under", True), ("over", False)):
        metrics["M4.1"][f"changed_by_final_writer_{version}"] = by_role(
            changed, under
        )
        metrics["M4.1"][f"changed_any_shell_writer_{version}"] = sum(
            shell_written(p, under) for p in changed
        )
        metrics["M4.1"][f"created_by_final_writer_{version}"] = by_role(
            created, under
        )
    metrics["M4.1"]["changed_unattributed"] = sum(
        p["final_writer"] is None for p in changed
    )
    listed = system_diff or {}
    metrics["M4.4"] = {
        name: sum(x not in SYSTEM_DIFF_BASELINE for x in listed.get(name) or ())
        for name in SYSTEM_LISTS
    }
    metrics["M4.4"]["changed_total"] = listed.get("changed")
    metrics["M4.4"]["outside_workspace_writes"] = linked["coverage"]["counts"][
        "write_events_outside_workspace"
    ]
    names = {
        "names_assigned_over": {
            n for r in call_facts for n in r["env_assigned"]
        },
        "names_assigned_under": {
            n for r in call_facts for n in r["env_confirmed"] or ()
        },
        "names_observed": {n for f in exec_facts for n in f["env_added"] or ()},
    }
    metrics["M5.2"] = {key: sorted(value) for key, value in names.items()}
    for key, value in names.items():
        metrics["M5.2"]["env_" + key] = len(value)
    networked = [  # D35: a wrapper duplicate is not a separate exec
        f
        for f in exec_facts
        if f["network_types"] and f["disposition"] != "wrapper_duplicate"
    ]
    metrics["M6.1"] = {
        "network_execs_under": sum(
            f["disposition"] == "matched_root" or f["root_under"]
            for f in networked
        ),
        "network_execs_over": len(networked),
        "network_calls_under": sum(
            bool(r["network_types_under"]) for r in call_facts
        ),
        "network_calls_over": sum(
            bool(r["network_types_over"]) for r in call_facts
        ),
        "network_execs_by_type_over": {
            t: sum(t in f["network_types"] for f in networked)
            for t in NETWORK_TYPES
        },
        "network_files_created_under": sum(
            p["network_created_under"] for p in path_facts
        ),
        "network_files_created_over": sum(
            p["network_created_over"] for p in path_facts
        ),
        "network_execs_unobserved": sum(
            not rows["exec", f["command_id"]]["snapshot_ok"] for f in networked
        ),
    }
    warnings = []
    for label, voids in PRECONDITIONS:
        if pre[label] != "fails":
            continue
        for item in voids.split(", "):  # PRE4
            if item.startswith("M"):
                block, _, item = item.partition(" ")
            names = [
                k for k in metrics[block] if fnmatch.fnmatchcase(k, item or "*")
            ]
            metrics[block].update(dict.fromkeys(names))
        warnings.append(f"{_failure(label, coverage, cfg)}: {voids} null")
    facts = RunFacts(
        schema_version="1",
        run_key=run["run_key"],
        family=run["family"],
        capabilities=capabilities,
        bases=bases,
        preconditions=dict(sorted(pre.items())),
        metrics=metrics,
        warnings=warnings,
    )
    return match.round_floats(
        FactsResult(
            call_facts=call_facts,
            exec_facts=exec_facts,
            path_facts=path_facts,
            facts=facts,
        )
    )


def peer_ratios(
    rows: list[dict[str, typing.Any]],
    config: typing.Mapping[str, typing.Any] | None = None,
) -> list[dict[str, typing.Any]]:
    """Adds `<key>_peer_ratio` columns to flattened facts rows (§6.11).

    Args:
        rows: One flattened facts row per run, each with `run_key`.
        config: Overrides of DEFAULTS; `min_peers` is read.

    Returns:
        Copies of the rows with one ratio per metric column of the first
        row (its `M…` keys; every row has facts.csv's fixed columns, §5.5):
        the value over the median of the same artifact's other runs with
        a value; None when the value is None, fewer than `min_peers` peers
        have one, or the median is 0.
    """

    def number(value: typing.Any) -> bool:
        return isinstance(value, int | float) and not isinstance(value, bool)

    least = {**DEFAULTS, **(config or {})}["min_peers"]
    keys = [k for k in (rows[0] if rows else ()) if k.startswith("M")]
    artifacts = [str(row["run_key"]).split("__")[0] for row in rows]
    groups = collections.defaultdict(list)
    for artifact, row in zip(artifacts, rows, strict=True):
        groups[artifact].append(row)
    out = []
    for artifact, row in zip(artifacts, rows, strict=True):
        others = [r for r in groups[artifact] if r is not row]
        ratios = {}
        for key in keys:
            peers = [r[key] for r in others if number(r.get(key))]
            median = statistics.median(peers) if peers else 0
            enough = len(peers) >= least and median
            ratios[f"{key}_peer_ratio"] = (
                round(row[key] / median, 6)
                if enough and number(row.get(key))
                else None
            )
        out.append({**row, **ratios})
    return out
