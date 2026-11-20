"""Declared invocations and option defaults of an artifact (layer 6, T6.1).

Reads one artifact's baseline checkout and its parsed case.toml: the
fenced shell lines of its instructions and their linked Markdown files,
the recipes of the make targets those lines name, the argparse defaults
of its Python files and the case's [declared] table (spec
trajectory-chain-spec.md §6.1, §6.4). `invocations` is the one shell
parser of both sides: declared lines here, executed commands in chain.
"""

import ast
import collections
import pathlib
import posixpath
import re
import types
import typing
import warnings

from trajectory_checks import facts, match, structure, writer


class Value(typing.TypedDict):
    """One option value (KEY7)."""

    value: str
    value_type: str  # int, float, enum, bool, symbolic


class Invocation(typing.TypedDict):
    """One simple command, normalized (KEY1 to KEY8)."""

    invocation_key: str | None  # None: no program (an assignment, cd)
    options: dict[str, Value]  # canonical option -> value
    program_path: str | None  # the script or program path (KEY2), or None
    informational: bool  # KEY6
    inline: bool  # code in argv (INLINE_CODE)
    literal_write: str | None  # FA6: the redirect target, resolved
    text: str  # the words joined by one space


class DeclaredInvocation(typing.TypedDict):
    """One row of declared_invocations.jsonl (spec §5.1)."""

    decl_id: str
    invocation_key: str
    options: dict[str, Value]
    source_type: str  # readme, make_recipe, case_block
    source_path: str | None  # None for case_block
    line: int | None
    block_id: str
    position: int
    cwd: str
    text: str


class DeclaredOption(typing.TypedDict):
    """One row of declared_options.jsonl (spec §5.2)."""

    invocation_key: str
    option: str
    aliases: list[str]
    default: Value | None
    choices: list[str] | None
    takes_value: bool
    source_type: str  # argparse, make_variable
    source_path: str
    line: int


class CaseSpec(typing.TypedDict):
    """The [declared] table of case.toml (spec §4.7, CT1, CT4)."""

    lines: list[str]
    markers: dict[str, dict[str, str]]  # invocation_key -> option -> marker
    experiment_keys: list[str] | None
    allowed_diff_classes: list[str]
    warnings: list[str]


class DeclaredMeta(typing.TypedDict):
    """declared.json (spec §5.3)."""

    schema_version: str
    artifact: str
    commit: str | None
    commit_source: str | None  # checkout, case_ref
    instructions: str | None
    source_url: str | None
    files_read: list[str]
    spec: CaseSpec
    warnings: list[str]


class DeclaredResult(typing.TypedDict):
    """Everything T6.1 produces for one artifact."""

    declared_invocations: list[DeclaredInvocation]
    declared_options: list[DeclaredOption]
    declared: DeclaredMeta


SHELL_FENCES = frozenset(
    ("", "sh", "bash", "shell", "console", "zsh", "shell-session")
)
# (basename pattern, options taking a value, the module option or None).
INTERPRETERS = tuple(
    (re.compile(pattern), frozenset(values.split()), module)
    for pattern, values, module in (
        (r"python(\d+(\.\d+)?)?|pypy3?", "-m -W -X", "-m"),
        (r"node(js)?", "-r --require --import", None),
        (r"bash|sh|dash|zsh", "-o -O --rcfile", None),
        (r"perl", "-I -M", None),
        (r"ruby", "-I -r", None),
        (r"Rscript", "", None),
        (r"julia", "-p -t --project", None),
        (r"lua", "-l", None),
    )
)
INFO_OPTIONS = frozenset(("--help", "-h", "--version", "-V"))
MAKE_PROGRAMS = frozenset(("make", "gmake"))
# Option -> what its value is: dir, file, number (only a numeric next
# word is its value) or word (GNU make manual, "Summary of Options").
MAKE_VALUE_OPTIONS = types.MappingProxyType(
    {
        **dict.fromkeys(("-C", "--directory"), "dir"),
        **dict.fromkeys(("-f", "--file", "--makefile"), "file"),
        **dict.fromkeys(("-j", "--jobs", "-l", "--load-average"), "number"),
        **dict.fromkeys(("-I", "--include-dir", "-o", "--old-file"), "word"),
        **dict.fromkeys(("-W", "--what-if"), "word"),
    }
)
MAKE_PATH_OPTIONS = frozenset(
    k for k, v in MAKE_VALUE_OPTIONS.items() if v in ("dir", "file")
)
MAKE_DRY_OPTIONS = frozenset(
    "-n --dry-run --just-print --recon -q --question -p --print-data-base "
    "--version".split()
)
# (program pattern, options carrying code, first operand carries code).
INLINE_CODE = (
    (re.compile(r"python(\d+(\.\d+)?)?|pypy3?"), frozenset(("-c",)), False),
    (re.compile(r"node(js)?"), frozenset(("-e", "-p")), False),
    (re.compile(r"perl|ruby"), frozenset(("-e",)), False),
    (re.compile(r"bash|sh|zsh"), frozenset(("-c",)), False),
    (re.compile(r"[gm]?awk|jq"), frozenset(), True),
    (re.compile(r"sed"), frozenset(("-e",)), True),
)
LITERAL_WRITERS = frozenset(("echo", "printf", "cat", "tee"))  # D60
PLACEHOLDER = re.compile(r"<[^>]*>|\$|\{[^}]*\}|`|\*|\?")  # DEC4
MARKERS = ("exact", "min", "max", "free")
SPEC_KEYS = ("lines", "options", "experiment_keys", "allowed_diff_classes")
DEFAULTS = types.MappingProxyType(
    {"link_follow_depth": 1, "make_follow_depth": 1}  # DEC1, DEC5
)


_INT = re.compile(r"[-+]?\d+")
_NUM = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?")
_DASH_DIGITS = re.compile(r"-\d+(\.\d+)?")
_ATTACHED = re.compile(r"(-[A-Za-z])(\d+(\.\d+)?)")
_ASSIGN = re.compile(r"([A-Za-z_]\w*)=(.*)", re.S)
_MAKE_ASSIGN = re.compile(r"\s*([A-Za-z_][\w.]*)\s*(\?=|::=|:=|=)\s*(.*?)\s*$")
_MAKE_VAR = re.compile(r"\$\(([A-Za-z_][\w.]*)\)|\$\{([A-Za-z_][\w.]*)\}")
_RULE = re.compile(r"([^:#=\t][^:#=]*):(?!=)")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})\s*(.*)$")
_SHA = re.compile(r"[0-9a-fA-F]{40}")
_DIR_TOOLS = frozenset(("cd", "pushd", "popd"))
_SOURCE_ORDER = {"readme": 0, "make_recipe": 1, "case_block": 2}
_NO_VALUE = frozenset(("store_true", "store_false", "count", "store_const"))
_MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
_Tables = typing.Mapping[str, typing.Mapping[str, typing.Any]]  # by key
_Row = tuple[dict, facts.SimpleCommand]  # §5.1 fields so far, the command
_Line = tuple[str, int, int, str, str]  # block_id, line, position, cwd, text


def _typed(value: str) -> Value:
    """KEY7, DEC4: a value with its type."""
    if PLACEHOLDER.search(value):
        return Value(value=value, value_type="symbolic")
    if _INT.fullmatch(value) or _NUM.fullmatch(value):
        kind = "int" if _INT.fullmatch(value) else "float"
        return Value(value=value, value_type=kind)
    return Value(value=value, value_type="enum")


def _resolve(path: str, cwd: str, root: str) -> str:
    """KEY2: a path against cwd, relative to root (absolute outside)."""
    return match.relative_path(posixpath.join(cwd, path), root)[0]


def _first(table: tuple, base: str) -> tuple | None:
    """The row of INTERPRETERS or INLINE_CODE whose pattern is base."""
    return next((row for row in table if row[0].fullmatch(base)), None)


def _segments(text: str) -> list[tuple[str, int]]:
    """Shell text cut at subshell parentheses: (piece, depth) (DEC3)."""
    out, buf, depth, quote, i, subs = [], [], 0, "", 0, 0
    while i < len(text):
        char, cut = text[i], None
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "\\":
            buf.append(text[i : i + 2])
            i += 2
            continue
        elif char == "(" and i and text[i - 1] in "$<>=(":
            subs += 1
        elif char == "(" and text[i + 1 : i + 2] != "(":
            cut = depth + 1
        elif char == ")" and subs:
            subs -= 1
        elif char == ")" and depth:
            cut = depth - 1
        if cut is None:
            buf.append(char)
        else:
            out.append(("".join(buf), depth))
            buf, depth = [], cut
        i += 1
    return [*out, ("".join(buf), depth)]


def _walk(
    text: str, frame: list[str], root: str, takes: _Tables, names: _Tables
) -> typing.Iterator[tuple[facts.SimpleCommand, Invocation, str]]:
    """(simple command, invocation, its cwd) per simple command (DEC3).

    frame is [cwd, *pushd stack] of the top level, changed in place by
    cd, pushd and popd; a subshell's frame does not leak.
    """
    stack = [frame]
    for piece, depth in _segments(text):
        del stack[depth + 1 :]
        while len(stack) <= depth:
            stack.append([stack[-1][0]])
        piece = piece.strip().lstrip(";&|").strip()
        for simple in facts.simple_commands(piece) if piece else ():
            cwd = stack[depth][0]
            yield simple, _one(simple, stack[depth], root, takes, names), cwd


def invocations(
    command: str,
    *,
    cwd: str | None,
    root: str,
    takes_value: typing.Mapping[str, typing.Mapping[str, bool]] | None = None,
    aliases: typing.Mapping[str, typing.Mapping[str, str]] | None = None,
) -> list[Invocation]:
    """KEY1 to KEY8: one Invocation per simple command of shell text.

    cwd is tracked through cd, pushd and popd (DEC3); takes_value and
    aliases are by invocation key, from declared_options (KEY7, KEY8).
    Used on both sides: declared lines and executed commands.
    """
    frame = [posixpath.normpath(posixpath.join(root, cwd or "."))]
    found = _walk(command, frame, root, takes_value or {}, aliases or {})
    return [inv for _, inv, _ in found]


def _one(
    simple: facts.SimpleCommand,
    frame: list[str],
    root: str,
    takes: _Tables,
    names: _Tables,
) -> Invocation:
    """KEY1 to KEY8 for one simple command; cd, pushd, popd move frame."""
    words, cwd = simple["words"], frame[0]
    rest = facts.command_words(words)[1]
    inv = Invocation(
        invocation_key=None,
        options={},
        program_path=None,
        informational=False,
        inline=False,
        literal_write=None,
        text=" ".join(words),
    )
    if rest[:1] == ["popd"]:
        frame[0] = frame.pop(1) if len(frame) > 1 else cwd
    elif rest[:1] == ["pushd"]:
        frame.insert(1, cwd)
    if rest[:1] in (["cd"], ["pushd"]):
        frame[0] = posixpath.normpath(posixpath.join(cwd, (rest + [root])[1]))
    if not rest or rest[0] in _DIR_TOOLS:
        return inv
    base = rest[0].rsplit("/", 1)[-1]
    env = {
        f"env:{m[1]}": _typed(m[2])
        for w in words[: len(words) - len(rest)]
        if (m := _ASSIGN.fullmatch(w))
    }
    interpreter, inline = _first(INTERPRETERS, base), _first(INLINE_CODE, base)
    make = base in MAKE_PROGRAMS
    if interpreter is not None:
        key, after = _script(rest, interpreter, inline, inv, cwd, root)
    elif make:
        key, after = _make(rest, cwd, root)[:2]
    elif "/" in rest[0]:
        key = inv["program_path"] = _resolve(rest[0], cwd, root)
        after = rest[1:]
    else:
        operands = [
            i
            for i, w in enumerate(rest[1:], 1)
            if w[:1] != "-"
            and not _NUM.fullmatch(w)
            and not PLACEHOLDER.search(w)
        ]
        key = f"{base} {rest[operands[0]]}" if operands else base
        after = [w for i, w in enumerate(rest[1:], 1) if operands[:1] != [i]]
        codes = inline is not None and bool(inline[1] & set(rest))
        inv["inline"] = codes or bool(inline and inline[2] and operands)
    inv["invocation_key"] = key
    found = _options(after, takes.get(key, {}), names.get(key, {}), make)
    inv["options"] = env | found
    info = INFO_OPTIONS | (MAKE_DRY_OPTIONS if make else frozenset())
    inv["informational"] = any(w.split("=", 1)[0] in info for w in rest[1:])
    heredoc = any(op.startswith("<<") for op, _ in simple["redirects"])
    if base in LITERAL_WRITERS and (heredoc or base in ("echo", "printf")):
        for op, target in simple["redirects"]:
            if op in (">", ">>", ">|"):
                inv["literal_write"] = _resolve(target, cwd, root)
    return inv


def _script(
    rest: list[str],
    interpreter: tuple,
    inline: tuple | None,
    inv: Invocation,
    cwd: str,
    root: str,
) -> tuple[str, list[str]]:
    """KEY1 for an interpreter: (key, the words after the script)."""
    _, values, module = interpreter
    codes = inline[1] if inline else frozenset()
    i = 1
    while i < len(rest) and rest[i][:1] == "-" and rest[i] != "-":
        if rest[i] in codes:
            inv["inline"] = True
            return rest[0].rsplit("/", 1)[-1], rest[i + 2 :]
        if rest[i] == module and i + 1 < len(rest):
            return f"module:{rest[i + 1]}", rest[i + 2 :]
        i += 2 if rest[i] in values else 1
    if i >= len(rest):
        return rest[0].rsplit("/", 1)[-1], []
    inv["program_path"] = _resolve(rest[i], cwd, root)
    return inv["program_path"], rest[i + 1 :]


def _make(
    rest: list[str], cwd: str, root: str
) -> tuple[str, list[str], str | None, str, str | None]:
    """KEY1, DEC5 for make.

    Returns (make:<-C folder/>target, every other word, the target, the
    folder make runs in relative to root, the -f file or None).
    """
    target = folder = file = None
    words, i = [], 1
    while i < len(rest):
        word, used = rest[i], 1
        name, equals, value = word.partition("=")
        option = MAKE_VALUE_OPTIONS.get(name) if word[:1] == "-" else None
        if option and not equals:
            nxt = rest[i + 1] if i + 1 < len(rest) else None
            ok = nxt is not None and (option != "number" or _NUM.fullmatch(nxt))
            value, used = (nxt, 2) if ok else (None, 1)
        if option:
            words += rest[i : i + used]
            if option == "dir" and value is not None:
                folder = posixpath.normpath(
                    posixpath.join(folder or cwd, value)
                )
            file = value if option == "file" and value is not None else file
        elif target is None and word[:1] != "-" and not _ASSIGN.fullmatch(word):
            target = word
        else:
            words.append(word)
        i += used
    here = match.relative_path(folder or cwd, root)[0]
    prefix = "" if folder is None or here == "." else here + "/"
    return f"make:{prefix}{target or ''}", words, target, here, file


def _options(
    words: list[str],
    takes: typing.Mapping[str, bool],
    names: typing.Mapping[str, str],
    make: bool,
) -> dict[str, Value]:
    """KEY7, KEY8: the options of the words after the key's words."""
    out: dict[str, Value] = {}
    operand, i, ended = 0, 0, False
    true = Value(value="true", value_type="bool")
    while i < len(words):
        word, i = words[i], i + 1
        nxt = words[i] if i < len(words) else None
        name = names.get(word, word)
        if not ended and len(word) > 1 and not word.strip("-"):
            ended = True  # the end of options
        elif (
            ended
            or word[:1] != "-"
            or len(word) < 2
            or _DASH_DIGITS.fullmatch(word)
        ):
            if make and not ended and (found := _ASSIGN.fullmatch(word)):
                out[f"var:{found[1]}"] = _typed(found[2])
                continue
            operand += 1
            if _NUM.fullmatch(word):
                out[f"pos:{operand}"] = _typed(word)
        elif "=" in word:
            option, value = word.split("=", 1)
            out[names.get(option, option)] = _typed(value)
        elif word[1] != "-" and (found := _ATTACHED.fullmatch(word)):
            out[names.get(found[1], found[1])] = _typed(found[2])
        elif nxt is not None and (
            _NUM.fullmatch(nxt)
            or takes.get(word)
            or takes.get(name)
            or (make and MAKE_VALUE_OPTIONS.get(word, "number") != "number")
        ):
            out[name] = _typed(nxt)
            i += 1
        elif word[1] != "-" and len(word) > 2:
            out.update({names.get(f"-{c}", f"-{c}"): true for c in word[1:]})
        else:
            out[name] = true
    return out


def fenced_lines(text: str, *, source_path: str) -> list[_Line]:
    """DEC2, DEC3: (block_id, line, position, cwd, text) per command."""
    keys = ("block_id", "line", "position", "cwd", "text")
    return [tuple(r[k] for k in keys) for r, _ in _fenced(text, source_path)]


def _fenced(text: str, source_path: str) -> list[_Row]:
    """The rows (§5.1 fields) and SimpleCommands of every shell fence."""
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        found, start = _FENCE.match(lines[i]), i + 1
        i += 1
        if not found:
            continue
        char, size = found[1][0], len(found[1])
        close = re.compile(f" {{0,3}}{re.escape(char)}{{{size},}}\\s*$")
        while i < len(lines) and not close.match(lines[i]):
            i += 1
        body = list(enumerate(lines[start:i], start + 1))
        i += 1
        if (found[2].split() or [""])[0].lower() in SHELL_FENCES:
            block = f"{source_path}:{start}"
            out += _commands(_logical(body), block, "readme", source_path)
    return out


def _logical(body: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """DEC2: prompts stripped, comments dropped, continuations joined."""
    prompts = any(line.lstrip().startswith("$ ") for _, line in body)
    out: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    for number, line in body:
        line = line.strip()
        if pending is None:
            if line.startswith("$ ") or (prompts and line.startswith("# ")):
                line = line[2:]
            elif not line or line.startswith("#"):
                continue
        start, text = pending or (number, "")
        pending = (start, f"{text} {line.removesuffix(chr(92))}".strip())
        if not line.endswith("\\"):
            out.append(pending)
            pending = None
    return out + ([pending] if pending else [])


def _commands(
    block: list[tuple[int | None, str]],
    block_id: str,
    source_type: str,
    source_path: str | None,
) -> list[_Row]:
    """The rows of one block or recipe; cd holds a position (D76).

    A recipe line runs in a shell of its own, in the Makefile's folder.
    """
    out, position = [], 0
    home = posixpath.normpath(
        posixpath.join("/", posixpath.dirname(source_path or ""))
    )
    frame = [home]
    for number, text in block:
        frame = [home] if source_type == "make_recipe" else frame
        for simple, inv, cwd in _walk(text, frame, "/", {}, {}):
            rest = facts.command_words(simple["words"])[1]
            if rest[:1] != [] and rest[0] in _DIR_TOOLS:
                position += 1
                continue
            row = {"block_id": block_id, "line": number, "position": position}
            row |= {
                "cwd": match.relative_path(cwd, "/")[0],
                "text": inv["text"],
            }
            row |= {"source_type": source_type, "source_path": source_path}
            out.append((row, simple))
            position += 1
    return out


def make_recipe(
    makefile: str,
    target: str,
    *,
    variables: typing.Mapping[str, str],
) -> tuple[list[tuple[int, str]], dict[str, tuple[str, int]], list[str]]:
    """DEC5: (recipe lines with numbers, ?= defaults, warnings)."""
    values, defaults = {}, {}
    lines = makefile.split("\n")
    for number, line in enumerate(lines, 1):
        found = None if line.startswith("\t") else _MAKE_ASSIGN.match(line)
        if found and found[2] == "?=":
            defaults[found[1]] = (found[3], number)
            values.setdefault(found[1], found[3])
        elif found:
            values[found[1]] = found[3]
    values = {**values, "MAKE": "make", **variables}
    rule = _rule_line(lines, target)
    if rule is None:
        return [], defaults, [f"make target {target} not found in Makefile"]
    out = []
    for number, line in enumerate(lines[rule:], rule + 1):
        if line.startswith("\t"):
            body = line[1:].lstrip("@-+ \t")
            if body.strip():
                sub = _MAKE_VAR.sub(
                    lambda m: values.get(m[1] or m[2], m[0]), body
                )
                out.append((number, sub))
        elif line.strip() and not line.lstrip().startswith("#"):
            break
    return out, defaults, []


def _rule_line(lines: list[str], target: str) -> int | None:
    """The 1-based line of the explicit rule `target:`, or None."""
    if "%" in target:
        return None
    for number, line in enumerate(lines, 1):
        found = _RULE.match(line)
        if found and target in found[1].split():
            return number
    return None


def argparse_defaults(
    source: str, *, source_path: str
) -> tuple[list[DeclaredOption], list[str]]:
    """DEC6: the add_argument options of one script, and warnings."""
    try:
        with warnings.catch_warnings():  # the checkout's escapes are its own
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return [], [f"{source_path}: syntax error, no defaults"]
    out = []
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    for node in calls:
        if getattr(node.func, "attr", None) != "add_argument":
            continue
        strings = [
            a.value
            for a in node.args
            if isinstance(a, ast.Constant)
            and isinstance(a.value, str)
            and a.value[:1] == "-"
        ]
        if not strings:
            continue  # a positional argument
        given = {k.arg: k.value for k in node.keywords if k.arg}
        action = getattr(given.get("action"), "value", None)
        default = None
        if "default" in given:
            default = _default(
                given["default"], getattr(given.get("type"), "id", None)
            )
        elif action in ("store_true", "store_false"):
            default = Value(
                value=str(action == "store_false").lower(), value_type="bool"
            )
        seq = given.get("choices")
        items = seq.elts if isinstance(seq, ast.List | ast.Tuple) else [None]
        choices = [str(e.value) for e in items if isinstance(e, ast.Constant)]
        choices = choices if len(choices) == len(items) else None
        out.append(
            DeclaredOption(
                invocation_key=source_path,
                option=max(strings, key=len),
                aliases=sorted(strings),
                default=default,
                choices=choices,
                takes_value=action not in _NO_VALUE,
                source_type="argparse",
                source_path=source_path,
                line=node.lineno,
            )
        )
    return out, []


def _default(node: ast.AST, type_name: str | None) -> Value | None:
    """DEC6: a literal default typed by type= or the literal; else symbolic."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = getattr(node.operand, "value", None)
        value = -operand if type(operand) in (int, float) else node
    else:
        value = node.value if isinstance(node, ast.Constant) else node
    if isinstance(value, ast.AST):
        return Value(value=ast.unparse(node), value_type="symbolic")
    if value is None:
        return None
    if isinstance(value, bool):
        return Value(value=str(value).lower(), value_type="bool")
    if type_name in ("int", "float"):
        return Value(value=str(value), value_type=type_name)
    if isinstance(value, str) and not PLACEHOLDER.search(value):
        return Value(value=value, value_type="enum")
    return _typed(str(value))


def checkout_commit(repo: pathlib.Path) -> str | None:
    """DEC7: the commit HEAD names, from .git files; None if unknown."""
    git = pathlib.Path(repo) / ".git"
    try:
        if git.is_file():
            line = git.read_text(encoding="utf-8").strip()
            if not line.startswith("gitdir:"):
                return None
            git = pathlib.Path(repo) / line.split(":", 1)[1].strip()
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            name = head.split(":", 1)[1].strip()
            if (git / name).is_file():
                head = (git / name).read_text(encoding="utf-8").strip()
            else:
                packed = (git / "packed-refs").read_text(encoding="utf-8")
                rows = (r.split() for r in packed.splitlines())
                head = next((r[0] for r in rows if r[1:] == [name]), "")
    except OSError:
        return None
    return head.lower() if _SHA.fullmatch(head) else None


def case_spec(case: typing.Mapping[str, typing.Any]) -> CaseSpec:
    """CT1, CT4, CT5: the [declared] table; malformed entries warn."""
    spec = CaseSpec(
        lines=[],
        markers={},
        experiment_keys=None,
        allowed_diff_classes=[],
        warnings=[],
    )
    table, warn = case.get("declared"), spec["warnings"].append
    if table is not None and not isinstance(table, typing.Mapping):
        warn("declared: not a table")
    for key, value in (table or {}).items() if not spec["warnings"] else ():
        if key not in SPEC_KEYS:
            warn(f"declared {key}: unknown key")
        elif key == "options" and not isinstance(value, typing.Mapping):
            warn("declared options: not a table")
        elif key == "options":
            for name, marker in value.items():
                if " " not in name.strip():
                    warn(f"declared {name}: no space")
                elif marker not in MARKERS:
                    warn(f"declared {name}: unknown marker {marker}")
                else:
                    head, option = name.strip().rsplit(" ", 1)
                    spec["markers"].setdefault(head, {})[option] = marker
        elif key == "allowed_diff_classes" and not isinstance(value, list):
            warn("declared allowed_diff_classes: not a list")
        elif key == "allowed_diff_classes":
            for item in value:
                if item in writer.HUNK_TYPES:
                    spec["allowed_diff_classes"].append(item)
                else:
                    warn(f"declared allowed_diff_classes: unknown {item}")
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            spec[key] = list(value)
        else:
            warn(f"declared {key}: not a list of strings")
    return spec


def _skipped(rel: str) -> bool:
    """DEC1: .git, hidden folders and CI files are never read."""
    hidden = any(p.startswith(".") for p in rel.split("/")[:-1])
    return hidden or rel == ".git" or structure.file_class(rel, None)[0] == "ci"


def _option_names(options: list[DeclaredOption]) -> tuple[dict, dict]:
    """KEY7, KEY8: takes_value and canonical names, by key and alias."""
    takes, names = collections.defaultdict(dict), collections.defaultdict(dict)
    for row in options:
        for alias in (*row["aliases"], row["option"]):
            takes[row["invocation_key"]][alias] = row["takes_value"]
            names[row["invocation_key"]][alias] = row["option"]
    return takes, names


def _read(repo: pathlib.Path, rel: str, read: set[str]) -> str:
    """The text of one checkout file, recorded in files_read."""
    read.add(rel)
    return (repo / rel).read_text(encoding="utf-8", errors="replace")


def declare(
    repo: pathlib.Path | None,
    case: typing.Mapping[str, typing.Any],
    *,
    artifact: str,
    config: typing.Mapping[str, typing.Any] | None = None,
) -> DeclaredResult:
    """T6.1: declared invocations and option defaults of one artifact.

    case is the parsed case.toml; repo the checkout at the case's
    [upstream] ref, or None (then only [declared] lines are read).

    Raises:
        ValueError: A config key is not in DEFAULTS.
    """
    if unknown := sorted(set(config or {}) - set(DEFAULTS)):
        raise ValueError(f"config keys declared does not know: {unknown}")
    cfg = {**DEFAULTS, **(config or {})}
    spec, upstream = case_spec(case), case.get("upstream") or {}
    run = (case.get("run") or {}).get("instructions") or {}
    instructions = run.get("path", "README.md")
    warnings, read, found, options = [], set(), [], []
    commit = source = None
    if repo is not None:
        repo = pathlib.Path(repo)
        commit = checkout_commit(repo)
        source = "checkout" if commit else None
        if commit is None and upstream.get("ref"):
            commit, source = upstream["ref"], "case_ref"
            warnings.append("declared commit from case.toml ref")
        depth = cfg["link_follow_depth"]
        for path, text in _markdown(repo, instructions, upstream, depth, read):
            found += _fenced(text, path)
        if not read:
            warnings.append(f"instructions {instructions} missing")
        for item in list(found):
            depth = cfg["make_follow_depth"]
            found += _follow(repo, item, depth, warnings, read, options)
        for path in sorted(
            p.relative_to(repo).as_posix() for p in repo.rglob("*.py")
        ):
            if not _skipped(path):
                text = _read(repo, path, read)
                rows, notes = argparse_defaults(text, source_path=path)
                options, warnings = options + rows, warnings + notes
    lines = [(None, text) for text in spec["lines"]]  # CT4
    found += _commands(lines, "case.toml", "case_block", None)
    takes, names = _option_names(options)
    rows = []
    for row, simple in found:
        inv = _one(simple, [posixpath.join("/", row["cwd"])], "/", takes, names)
        if inv["invocation_key"] is not None:
            rows.append(
                row | {k: inv[k] for k in ("invocation_key", "options")}
            )
    rank, order = _SOURCE_ORDER, ("source_path", "line", "position")
    rows.sort(
        key=lambda r: (rank[r["source_type"]], *(r[k] or 0 for k in order))
    )
    keys = {r["invocation_key"] for r in rows}
    for key, table in spec["markers"].items():  # CT5
        if key not in keys:
            spec["warnings"] += [
                f"declared {key} {o}: no declared invocation" for o in table
            ]
    meta = DeclaredMeta(
        schema_version="1",
        artifact=artifact,
        commit=commit,
        commit_source=source,
        instructions=instructions,
        source_url=upstream.get("url"),
        files_read=sorted(read),
        spec=spec,
        warnings=warnings + spec["warnings"],  # D59
    )
    return DeclaredResult(
        declared_invocations=[
            DeclaredInvocation(decl_id=f"d{n}", **r) for n, r in enumerate(rows)
        ],
        declared_options=sorted(
            options, key=lambda o: (o["invocation_key"], o["option"])
        ),
        declared=meta,
    )


def _markdown(
    repo: pathlib.Path,
    instructions: str,
    upstream: typing.Mapping[str, typing.Any],
    depth: int,
    read: set[str],
) -> list[tuple[str, str]]:
    """DEC1: the instructions file and its linked Markdown, depth levels."""
    start = posixpath.normpath(instructions)
    out, seen, level = [], {start}, [start]
    for step in range(depth + 1):
        following = []
        for rel in (r for r in level if (repo / r).is_file()):
            out.append((rel, text := _read(repo, rel, read)))
            for target in (
                _links(text, rel, upstream.get("url")) if step < depth else ()
            ):
                if target not in seen and not _skipped(target):
                    seen.add(target)
                    following.append(target)
        level = following
    return out


def _links(text: str, source: str, url: str | None) -> list[str]:
    """DEC1: in-repository Markdown links, relative or to the own URL."""
    base = re.escape((url or "").removesuffix(".git").rstrip("/"))
    out = []
    for target in re.findall(r"\]\(([^)\s#]+)", text):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):
            own = re.fullmatch(base + r"/(?:blob|tree)/[^/]+/(.+)", target)
            if not (url and own):
                continue
            rel = posixpath.normpath(own[1])
        else:
            rel = posixpath.normpath(
                posixpath.join(posixpath.dirname(source), target)
            )
        if not rel.startswith("..") and rel.lower().endswith(
            (".md", ".markdown")
        ):
            out.append(rel)
    return out


def _follow(
    repo: pathlib.Path,
    item: _Row,
    depth: int,
    warnings: list[str],
    read: set[str],
    options: list[DeclaredOption],
) -> list[_Row]:
    """DEC5: the recipe rows of a make line, followed depth levels."""
    row, simple = item
    frame = posixpath.join("/", row["cwd"])
    inv = _one(simple, [frame], "/", {}, {})
    key = inv["invocation_key"] or ""
    if inv["informational"] or not key.startswith("make:"):
        return []
    _, _, target, folder, file = _make(
        facts.command_words(simple["words"])[1], frame, "/"
    )
    names = [file] if file else _MAKEFILES
    paths = [posixpath.normpath(posixpath.join(folder, n)) for n in names]
    inside = [p for p in paths if not p.startswith(("/", ".."))]  # D81
    makefile = next((p for p in inside if (repo / p).is_file()), None)
    if makefile is None:
        warnings.append(f"make target {target or ''} not found in Makefile")
        return []
    text = _read(repo, makefile, read)
    first = re.search(r"^([^:#=\s.%][^:#=\s%]*)\s*:(?!=)", text, re.M)
    target = target or (first[1] if first else "")
    variables = {
        k[4:]: v["value"] for k, v in inv["options"].items() if k[:4] == "var:"
    }
    recipe, defaults, notes = make_recipe(text, target, variables=variables)
    warnings += [n.replace("Makefile", makefile) for n in notes]
    if notes:
        return []
    for name, (value, number) in defaults.items():
        variable = DeclaredOption(
            invocation_key=key,
            option=f"var:{name}",
            aliases=[f"var:{name}"],
            default=_typed(value),
            choices=None,
            takes_value=True,
            source_type="make_variable",
            source_path=makefile,
            line=number,
        )
        options += [variable] if variable not in options else []  # once
    block = f"{makefile}:{_rule_line(text.split(chr(10)), target)}"
    out = _commands(recipe, block, "make_recipe", makefile)
    for found in list(out) if depth > 0 else ():
        out += _follow(repo, found, depth - 1, warnings, read, options)
    return out
