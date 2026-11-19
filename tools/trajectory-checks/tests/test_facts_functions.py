"""Layer 2: table-driven tests of the pure functions (facts spec §2.2.1).

Every expected value is written by hand from the rules of spec §6.
The rules these tables exercise are listed in
``facts_helpers.UNIT_TEST_RULES``.
"""

import pathlib
import time
import typing

import conftest
import facts_helpers
import pytest

from trajectory_checks import cli, facts, match

# (command, shape, numeric_args, arg_count): SHP1 to SHP7.
SHAPES = (
    ("ls -la", "ls -la", [], 2),
    ("sleep 30", "sleep N", ["30"], 2),
    ("head -20 log.txt", "head N log.txt", ["-20"], 3),
    ("run 1.5 2e-3 +4 0x1F", "run N N N N", ["1.5", "2e-3", "+4", "0x1F"], 5),
    ("make -j8 v2", "make -j8 v2", [], 3),
    ("python3 a.py", "python3 a.py", [], 2),
    ("./run --iters=5 --name=x", "run --iters=N --name=x", ["5"], 3),
    ("A=3 B=x ./go", "A=N B=x go", ["3"], 3),
    ("cat /repo/out/res.csv", "cat res.csv", [], 2),
    ("ls /repo/out/", "ls out", [], 2),
    ("ls /", "ls /", [], 2),
    ("cd ../x && ls", "cd x && ls", [], 3),
    ("curl https://h.org/a/b.tar.gz", "curl b.tar.gz", [], 2),
    ("curl https://h.org/v/1.2", "curl N", ["1.2"], 2),
    ("cmd 2>&1", "cmd 2 >& 1", [], 3),
    ("cmd 2>/dev/null", "cmd 2 > null", [], 3),
    ("echo x >&2", "echo x >& 2", [], 3),
    ("echo 50 > f", "echo N > f", ["50"], 3),
    ("cmd --n 50 > out.log 2>&1", "cmd --n N > out.log 2 >& 1", ["50"], 6),
    ("grep 'a b' f", "grep a b f", [], 3),
    ("x | y; z && w", "x | y ; z && w", [], 4),
    # The delimiter is a word; the body (with its closing line) follows.
    # SHP4: a quoted word with whitespace is not a path; it stays verbatim.
    ("bash -c 'cd /a && make'", "bash -c cd /a && make", [], 3),
    ('python3 -c "print(1)"', "python3 -c print(1)", [], 3),
    # SHP1: an unquoted newline is an operator token.
    ("cd /a\nmake 2", "cd a \n make N", ["2"], 4),
    (
        "python - <<'EOF'\nprint(42, x2)\nEOF",
        "python - << EOF\nprint(N, x2)\nEOF",
        [],
        3,
    ),
    # SHP1: a backslash-newline joins lines; it is no newline operator.
    pytest.param(
        "python run.py \\\n  --iters 5 \\\n  --out x",
        "python run.py --iters N --out x",
        ["5"],
        6,
    ),
    # SHP1 (S1): quoted or escaped punctuation and {} are words.
    pytest.param(
        "find . -name '*.o' -exec rm {} +",
        "find . -name *.o -exec rm {} +",
        [],
        8,
    ),
    pytest.param(
        "cut -d ';' -f1 file",
        "cut -d ; -f1 file",
        [],
        5,
    ),
    # SHP1: `#` starts a comment only at the start of a word.
    pytest.param(
        "curl http://x/a#frag -o y && ls",
        "curl a#frag -o y && ls",
        [],
        5,
    ),
    pytest.param(
        "echo ${#arr[@]} && ls",
        "echo ${#arr[@]} && ls",
        [],
        3,
    ),
    pytest.param("echo x#y", "echo x#y", [], 2),
    # A whole-line comment goes with its newline (D45 (7)).
    pytest.param("ls\n# c\nls", "ls \n ls", [], 2),
    # D55: a case inside $(…) lexes, so the shape is not the fallback's.
    pytest.param(
        "x=$( case a in (b) p1;; esac ); p2",
        "x=$( case a in (b) p1;; esac ) ; p2",
        [],
        2,
    ),
)


@pytest.mark.parametrize(("command", "shape", "numbers", "count"), SHAPES)
def test_command_shape(
    command: str, shape: str, numbers: list[str], count: int
) -> None:
    """SHP1-SHP7: shape, numeric arguments and word count."""
    assert facts.command_shape(command) == (shape, numbers, count)


def test_command_shape_unclosed_quote() -> None:
    """SHP1: an unclosed quote falls back to whitespace words."""
    shape, numbers, count = facts.command_shape("echo 'a 5")
    assert numbers == ["5"]
    assert count == 3
    assert shape == "echo 'a N"


def test_unclosed_quote_keeps_newlines() -> None:
    """SHP1: the whitespace fallback still separates lines (SHP8)."""
    assert facts.programs("echo 'a\nls -la") == ["echo", "ls"]


# (word, normalized, numbers): SHP2 to SHP4 on one word.
WORDS = (
    ("50", "N", ["50"]),
    ("-3", "N", ["-3"]),
    ("-j8", "-j8", []),
    ("/repo/out/", "out", []),
    ("/", "/", []),
    ("./v/1.5", "N", ["1.5"]),
    ("--out=/tmp/a.csv", "--out=a.csv", []),
    ("RUNS=3", "RUNS=N", ["3"]),
    ("-I/usr/include", "-I/usr/include", []),
    ("cd /a && make", "cd /a && make", []),
    # SHP3: the value is normalized by SHP2 and SHP4 only, not by SHP3.
    pytest.param("a=b=3", "a=b=3", []),
    pytest.param("--x=k=5", "--x=k=5", []),
)


@pytest.mark.parametrize(("word", "normalized", "numbers"), WORDS)
def test_normalize_word(word: str, normalized: str, numbers: list[str]) -> None:
    """SHP2-SHP4 on one word; a word with whitespace is unchanged."""
    assert facts.normalize_word(word) == (normalized, numbers)


# (command, simple commands): SHP8's split.
SIMPLE = (
    ("ls -la", [["ls", "-la"]]),
    ("a | b && c; d & e", [["a"], ["b"], ["c"], ["d"], ["e"]]),
    ("cmd > out.log 2>&1 x", [["cmd", "x"]]),
    ("cat < in >> out", [["cat"]]),
    ("echo x >&2", [["echo", "x"]]),
    ("(cd a && ls)", [["cd", "a"], ["ls"]]),
    ("a\nb", [["a"], ["b"]]),
    ("bash -c 'cd /a && make'", [["bash", "-c", "cd /a && make"]]),
    ("cat <<'EOF'\nwget x\nEOF", [["cat"]]),
    ('python3 -c "a\nb"', [["python3", "-c", "a\nb"]]),
    pytest.param("echo a\\\nb", [["echo", "ab"]]),
    # S1: an escaped `;`, `(` or `)` and a bare {} are words.
    pytest.param(
        "find . -exec chmod +x {} \\; && make",
        [["find", ".", "-exec", "chmod", "+x", "{}", ";"], ["make"]],
    ),
    pytest.param(
        "find . \\( -name a -o -name b \\) -print",
        [["find", ".", "(", "-name", "a", "-o", "-name", "b", ")", "-print"]],
    ),
    pytest.param(
        "echo a # note\nls -la",
        [["echo", "a"], ["ls", "-la"]],
    ),
    ("{ echo a; echo b; } > out", [["echo", "a"], ["echo", "b"]]),
    # T4: `{` and `}` are operators only in command position.
    ("a && { b; }", [["a"], ["b"]]),
    ("a | { b; }", [["a"], ["b"]]),
    pytest.param("echo }", [["echo", "}"]]),
    pytest.param("echo {", [["echo", "{"]]),
    pytest.param("echo a }", [["echo", "a", "}"]]),
    pytest.param("{ echo }; }", [["echo", "}"]]),
)


@pytest.mark.parametrize(("command", "expected"), SIMPLE)
def test_simple_commands(command: str, expected: list[list[str]]) -> None:
    """SHP8: separators split, redirections and their targets removed.

    Q14 (a): each simple command is a SimpleCommand; these rows hold no
    substitution, so the word lists are the whole split.
    """
    found = facts.simple_commands(command)
    assert [c["words"] for c in found] == expected
    assert not any(c["in_substitution"] for c in found)


def _cmd(
    words: list[str],
    redirects: tuple[tuple[str, str], ...] = (),
    pipe: bool = False,
    nested: bool = False,
) -> dict:
    return {
        "words": words,
        "redirects": list(redirects),
        "after_pipe": pipe,
        "in_substitution": nested,
    }


# (command, SimpleCommands): Q14 (a), layer 3's OBS1, OBS2, OBS4, KEY3.
PARTS = (
    (
        "cmd > out.log 2>&1 x",
        [_cmd(["cmd", "x"], ((">", "out.log"), ("2>&", "1")))],
    ),
    ("cat < in >> out", [_cmd(["cat"], (("<", "in"), (">>", "out")))]),
    ("a 2>/dev/null &> f", [_cmd(["a"], (("2>", "/dev/null"), ("&>", "f")))]),
    (
        "a | b |& c",
        [_cmd(["a"]), _cmd(["b"], pipe=True), _cmd(["c"], pipe=True)],
    ),
    ("a | (b; c)", [_cmd(["a"]), _cmd(["b"], pipe=True), _cmd(["c"])]),
    # A substitution stays in its word; its commands follow, nested.
    (
        "echo $(date +%s) `id -u`",
        [
            _cmd(["echo", "$(date +%s)", "`id -u`"]),
            _cmd(["date", "+%s"], nested=True),
            _cmd(["id", "-u"], nested=True),
        ],
    ),
    (
        'x="$(cat f | wc -l)"',
        [
            _cmd(["x=$(cat f | wc -l)"]),
            _cmd(["cat", "f"], nested=True),
            _cmd(["wc", "-l"], pipe=True, nested=True),
        ],
    ),
    ("echo '$(date)'", [_cmd(["echo", "$(date)"])]),
    # Arithmetic holds no command; a parameter expansion is one word.
    ("end=$(( 3 + 60 ))", [_cmd(["end=$(( 3 + 60 ))"])]),
    ("echo ${a:-x y}", [_cmd(["echo", "${a:-x y}"])]),
    (
        "while [ $(date +%s) -lt $end ]; do sleep 30; done",
        [
            _cmd(["[", "$(date +%s)", "-lt", "$end", "]"]),
            _cmd(["date", "+%s"], nested=True),
            _cmd(["sleep", "30"]),
        ],
    ),
    # A for header is dropped; its substitution's command is kept.
    (
        "for i in $(seq 1 9); do ls; done",
        [_cmd(["seq", "1", "9"], nested=True), _cmd(["ls"])],
    ),
    ("for ((i=0; i<3; i++)); do ls; done", [_cmd(["ls"])]),
    ("case $x in a) ls;; esac", [_cmd(["ls"])]),
    # A group's redirection goes to its last command.
    ("for f in a; do ls; done > out", [_cmd(["ls"], ((">", "out"),))]),
    ("> empty", [_cmd([], ((">", "empty"),))]),
    # A quoted brace or digit is a word, not an operator or a descriptor.
    ("echo '{' '2'>x", [_cmd(["echo", "{", "2"], ((">", "x"),))]),
    # B1: every case pattern is dropped, not only the first.
    pytest.param(
        "case $x in\n a|b) curl u ;;\n *) ls ;;\nesac",
        [_cmd(["curl", "u"]), _cmd(["ls"])],
    ),
    pytest.param(
        "case $x in (a) ls;; (b|c) pwd;; esac", [_cmd(["ls"]), _cmd(["pwd"])]
    ),
    # B2: NAME=( … ) is one assignment word (SHP1).
    pytest.param("arr=(a b c); ls", [_cmd(["arr=(a b c)"]), _cmd(["ls"])]),
    pytest.param("declare -a x=(1 2)", [_cmd(["declare", "-a", "x=(1 2)"])]),
    pytest.param(
        "arr=($(ls))", [_cmd(["arr=($(ls))"]), _cmd(["ls"], nested=True)]
    ),
    pytest.param("arr+=(d)", [_cmd(["arr+=(d)"])]),
    # B3, D45(5): a newline or a comment after `|` continues the pipeline.
    pytest.param(
        "a |\n  grep x", [_cmd(["a"]), _cmd(["grep", "x"], pipe=True)]
    ),
    pytest.param(
        "a |  # c\n grep x", [_cmd(["a"]), _cmd(["grep", "x"], pipe=True)]
    ),
    # B4, T5: a substitution inside $((…)), ((…)) or ${…} is returned.
    pytest.param(
        "echo $((1+$(date +%s)))",
        [
            _cmd(["echo", "$((1+$(date +%s)))"]),
            _cmd(["date", "+%s"], nested=True),
        ],
    ),
    pytest.param(
        "echo ${x:-$(date)}",
        [_cmd(["echo", "${x:-$(date)}"]), _cmd(["date"], nested=True)],
    ),
    pytest.param("(( x = $(./run.sh) ))", [_cmd(["./run.sh"], nested=True)]),
    pytest.param(
        "end=$(( $(date +%s) + 60 ))",
        [
            _cmd(["end=$(( $(date +%s) + 60 ))"]),
            _cmd(["date", "+%s"], nested=True),
        ],
    ),
    pytest.param(
        "echo $((1 + `id -u`))",
        [_cmd(["echo", "$((1 + `id -u`))"]), _cmd(["id", "-u"], nested=True)],
    ),
    # D45(2), narrowed: only a group close takes a following redirection.
    ("(a) > f", [_cmd(["a"], ((">", "f"),))]),
    ("if a; then b; fi > f", [_cmd(["a"]), _cmd(["b"], ((">", "f"),))]),
    ("case $x in a) ls;; esac > f", [_cmd(["ls"], ((">", "f"),))]),
    pytest.param("echo a; > f", [_cmd(["echo", "a"]), _cmd([], ((">", "f"),))]),
    pytest.param("echo a\n> f", [_cmd(["echo", "a"]), _cmd([], ((">", "f"),))]),
    pytest.param(
        "echo a && > f", [_cmd(["echo", "a"]), _cmd([], ((">", "f"),))]
    ),
    # The reserved word `time` before a group: the group reads the pipe.
    pytest.param("p1 | time { p2; }", [_cmd(["p1"]), _cmd(["p2"], pipe=True)]),
)


@pytest.mark.parametrize(("command", "expected"), PARTS)
def test_simple_command_parts(command: str, expected: list[dict]) -> None:
    """Q14 (a): redirections, pipe position and substitutions."""
    assert facts.simple_commands(command) == expected


# (command, programs): SHP8.
PROGRAMS = (
    ("ls", ["ls"]),
    ("cd /repo && make; ./run.sh | tee log", ["cd", "make", "run.sh", "tee"]),
    ("sudo -E apt-get install y", ["apt-get"]),
    ("env A=1 B=2 python3 t.py", ["python3"]),
    ("A=1 ./go", ["go"]),
    ("nohup ./long.sh > out 2>&1 &", ["long.sh"]),
    ("timeout 10 make test", ["make"]),
    ("time nice -n5 /usr/bin/x", ["x"]),
    ("(cd a && ls) || echo no", ["cd", "ls", "echo"]),
    ("cat <<'EOF'\nwget x\nEOF", ["cat"]),
    ("cd /repo\nmake", ["cd", "make"]),
    pytest.param(
        "python run.py \\\n  --iters 5 \\\n  --out x",
        ["python"],
    ),
    pytest.param(
        "find . -name '*.o' -exec rm {} +",
        ["find"],
    ),
    pytest.param("cut -d ';' -f1 file", ["cut"]),
    pytest.param("tr '|' ' ' < a", ["tr"]),
    pytest.param(
        "find . \\( -name a -o -name b \\) -print",
        ["find"],
    ),
    ("find . -exec chmod +x {} \\; && make", ["find", "make"]),
    pytest.param(
        "echo a # note\nls -la",
        ["echo", "ls"],
    ),
    pytest.param("echo $#\nls", ["echo", "ls"]),
    pytest.param(
        "echo ${#arr[@]} && ls",
        ["echo", "ls"],
    ),
    ("# setup\npip install x", ["pip"]),
    # SHP8: each command of a process substitution is a simple command.
    pytest.param(
        "diff <(sort a) <(sort b)",
        ["diff", "sort", "sort"],
    ),
    # S9: shell keywords are skipped before the program word.
    pytest.param(
        "if true; then pip install y; fi",
        ["true", "pip"],
    ),
    pytest.param(
        "while true; do make; done",
        ["true", "make"],
    ),
    pytest.param("until x; do y; done", ["x", "y"]),
    pytest.param(
        "if a; then b; elif c; then d; else e; fi",
        ["a", "b", "c", "d", "e"],
    ),
    pytest.param("! grep x f", ["grep"]),
    # SHP8: only timeout's first operand is its duration.
    pytest.param("timeout 5 10", ["10"]),
    # B1: case patterns are no programs.
    pytest.param(
        "case $x in\n a|b) curl u ;;\n *) ls ;;\nesac", ["curl", "ls"]
    ),
    pytest.param(
        'ls /proc/*/comm | while read f; do c=$(cat "$f"); '
        'case "$c" in *dotnet*|*Dafny*|z3*) echo busy;; *) ;; esac; done',
        ["ls", "read", "cat", "echo"],
    ),
    # B2: array elements are no programs.
    pytest.param("arr=(a b c); ls", ["ls"]),
    pytest.param("declare -a x=(1 2)", ["declare"]),
    pytest.param("arr=($(ls))", ["ls"]),
    pytest.param("tools=(curl wget)", []),
    pytest.param(
        "counts=(4 11 2 7 3)\nbenches=(analytics ml web)\n"
        'for i in "${!benches[@]}"; do '
        './run.sh "${benches[$i]}" "${counts[$i]}"; done',
        ["run.sh"],
    ),
    # B4, T5: commands nested in arithmetic or ${…} are programs.
    pytest.param("echo $((1+$(date +%s)))", ["echo", "date"]),
    pytest.param("echo ${x:-$(date)}", ["echo", "date"]),
    pytest.param("(( x = $(./run.sh) ))", ["run.sh"]),
    pytest.param("x=$(( $(./run.sh) + 1 ))", ["run.sh"]),
    pytest.param(
        "end=$(( $(date +%s) + 60 ))\n"
        "while [ $(date +%s) -lt $end ]; do sleep 5; done",
        ["date", "[", "date", "sleep"],
    ),
    # B5, T1: `command -v`/`-V` is a lookup, not a run of its operand.
    pytest.param("command -v curl", []),
    pytest.param("command -V curl", []),
    pytest.param("command -v curl; command -v wget", []),
    ("command ls", ["ls"]),
    # D55: `;;&` and `;&` return to the patterns; `esac` as an argument.
    pytest.param(
        "case x in a) p1;;& b) p2;& c) p3;; esac; p4", ["p1", "p2", "p3", "p4"]
    ),
    pytest.param("case x in a) echo esac;; esac; p1", ["echo", "p1"]),
    pytest.param(
        "case x in a) echo esac;; b) p2;; esac; p1", ["echo", "p2", "p1"]
    ),
    # D56: a comment inside a multi-line array value, also with `)` or `(`.
    pytest.param("arr=(\n a # c )\n b\n); p1", ["p1"]),
    pytest.param("arr=(\n a # c ( x\n b\n); p1", ["p1"]),
    pytest.param("arr=(case x in y); p1", ["p1"]),
    pytest.param("arr=( <(p0) ); p1", ["p0", "p1"]),
    pytest.param("arr=(\n a # don't\n b\n); curl -O u; ls", ["curl", "ls"]),
    # B10, T3: `function` and a function definition's name are skipped.
    pytest.param("function f { ls; }", ["ls"]),
    pytest.param("f() { ls; }", ["ls"]),
    pytest.param("function f() { ls; }", ["ls"]),
    pytest.param('run() { ./bench.sh "$1"; }; run a', ["bench.sh", "run"]),
    # B11: ANSI-C quoting $'…' with an escaped quote splits as bash does.
    pytest.param("echo $'a\\'b' && ls", ["echo", "ls"]),
)


@pytest.mark.parametrize(("command", "expected"), PROGRAMS)
def test_programs(command: str, expected: list[str]) -> None:
    """SHP8: the program of each simple command."""
    assert facts.programs(command) == expected


def test_programs_skip_loop_keywords() -> None:
    """S9: no shell keyword is a program; the loop body's command is."""
    keywords = {"for", "in", "do", "done", "while", "until", "if", "then"}
    found = facts.programs("for i in 1 2; do curl x; done")
    assert "curl" in found
    assert not keywords & set(found)


# (command, network types): NET1.
NETWORK = (
    ("curl -sO https://h.org/f", ["download"]),
    ("wget -q u -O f", ["download"]),
    ("git clone https://h.org/r.git", ["download"]),
    ("git status", []),
    ("git pull --rebase", ["download"]),
    ("docker pull img", ["download"]),
    ("pip install -r req.txt", ["install"]),
    ("pip list", []),
    ("python3 -m pip install x", ["install"]),
    ("python3 -m pytest", []),
    ("uv run script.py", []),
    ("uv pip install x", ["install"]),
    ("sudo apt-get install -y x", ["install"]),
    ("apt-get update && curl -L u | tar x", ["download", "install"]),
    ("echo curl", []),
    ("grep wget log", []),
    ("cat <<'EOF'\ncurl u\nEOF", []),
    ("/usr/bin/curl u", ["download"]),
    ("cargo build", []),
    ("cargo install tool", ["install"]),
    pytest.param("pip \\\n install x", ["install"]),
    pytest.param(
        "for i in 1 2; do curl x; done",
        ["download"],
    ),
    pytest.param(
        "if true; then pip install y; fi",
        ["install"],
    ),
    # B1: a case arm's command keeps its type.
    ("case $x in\n a|b) curl u ;;\n *) ls ;;\nesac", ["download"]),
    # B2: an array element is not a program.
    pytest.param("tools=(curl wget)", []),
    pytest.param("tools=(curl wget); for t in $tools; do echo $t; done", []),
    # B5, T1: `command -v`/`-V` has no network type; `type` is a lookup.
    pytest.param("command -v curl", []),
    pytest.param("command -V wget", []),
    pytest.param("command -v curl; command -v wget", []),
    pytest.param(
        "command -v curl >/dev/null || apt-get install -y curl", ["install"]
    ),
    ("type curl", []),
    ("command curl -sO u", ["download"]),
    # B4: a download inside arithmetic or ${…} is classified.
    pytest.param("n=$(( $(curl -s u | wc -l) + 1 ))", ["download"]),
    # D56: a quote inside an array's comment does not open.
    pytest.param("arr=(\n a # don't\n b\n); curl -O u; ls", ["download"]),
)


@pytest.mark.parametrize(("command", "expected"), NETWORK)
def test_network_types(command: str, expected: list[str]) -> None:
    """NET1: network types from the program table, with subcommands."""
    assert facts.network_types(command) == expected


# (command, names): ENV1.
ENV = (
    ("A=1 cmd", ["A"]),
    ("A=1 B=2 cmd", ["A", "B"]),
    ("A=1", []),
    ("A=1; cmd", []),
    ("export A=1", ["A"]),
    ("export A", ["A"]),
    ("export -p", []),
    ("declare -x B=2", ["B"]),
    ("typeset C=3", ["C"]),
    ("env D=4 cmd", ["D"]),
    ("env -i E=5 cmd", ["E"]),
    ("echo 'A=1 cmd'", []),
    ("cat <<'EOF'\nexport Z=1\nEOF", []),
    ("cd x && F=6 ./run.sh && export G=7", ["F", "G"]),
    # S10: PREFIX_WORDS are stripped before env and export.
    pytest.param("sudo env X=1 cmd", ["X"]),
    pytest.param("sudo -E env Y=2 cmd", ["Y"]),
)


@pytest.mark.parametrize(("command", "expected"), ENV)
def test_env_assigned(command: str, expected: list[str]) -> None:
    """ENV1: environment names assigned by shell syntax only."""
    assert facts.env_assigned(command) == expected


# (exit_code, signal, outcome, exit_signal): OUT1, OUT2.
OUTCOMES = (
    (0, None, "ok", None),
    (1, None, "failed", None),
    (127, None, "failed", None),
    (128, None, "failed", None),
    (129, None, "failed", 1),
    (137, None, "failed", 9),
    (192, None, "failed", 64),
    (193, None, "failed", None),
    (255, None, "failed", None),
    (None, 15, "killed", None),
    (143, 15, "killed", None),
    (None, None, "unknown", None),
)


@pytest.mark.parametrize(("code", "signal", "outcome", "sig"), OUTCOMES)
def test_outcome_and_exit_signal(
    code: int | None, signal: int | None, outcome: str, sig: int | None
) -> None:
    """OUT1, OUT2: outcome and the signal a 128+n exit reports."""
    record = {"exit_code": code, "signal": signal, "complete": code is not None}
    assert facts.outcome(record) == outcome
    assert facts.exit_signal(record) == sig


def test_outcome_without_exec() -> None:
    """OUT3: no exec means an unknown outcome."""
    assert facts.outcome(None) == "unknown"
    assert facts.exit_signal(None) is None


PERCENTILES = (
    ([5.0], 0.5, 5.0),
    ([5.0], 0.9, 5.0),
    ([1.0, 2.0], 0.5, 1.0),
    ([3.0, 1.0, 2.0], 0.5, 2.0),
    ([0.027, 0.027, 0.088, 0.38], 0.5, 0.027),
    ([0.027, 0.027, 0.088, 0.38], 0.9, 0.38),
    ([float(n) for n in range(1, 11)], 0.9, 9.0),
    ([], 0.5, None),
)


@pytest.mark.parametrize(("values", "fraction", "expected"), PERCENTILES)
def test_percentile(
    values: list[float], fraction: float, expected: float | None
) -> None:
    """Nearest rank: the value at rank ceil(p*n)."""
    assert facts.percentile(values, fraction) == expected


def _coverage(**changes: object) -> dict:
    coverage = {
        "inputs": {
            "log": True,
            "trace": True,
            "tracked_files": True,
            "final_diff": True,
        },
        "integrity": {"C0.1": True, "C0.2": 1.0, "C0.5": True},
        "counts": {
            "shell_calls": 3,
            "calls_running_at_stop": 0,
            "execs_total": 4,
            "execs_by_disposition": {"noise": 1},
            "final_diff_files": 2,
            "snapshot_status_counts": {
                "ok": 3,
                "truncated": 0,
                "skipped": 1,
                "absent": 0,
            },
        },
        "rates": {
            "call_match_rate_under": 1.0,
            "exec_explained_rate_under": 1.0,
            "listed_path_rate": 1.0,
            "final_diff_attributed_rate_under": 1.0,
            "untracked_visible": False,
        },
    }
    for key, value in changes.items():
        block, name = key.split("__")
        coverage[block][name] = value
    return coverage


CONFIG = {"join_rate_warn": 0.9}
# (label, coverage changes, expected): PRE1, PRE2, PRE3, PRE6.
PRECONDITIONS = (
    ("C1_under", {}, "holds"),
    ("C1_under", {"rates__call_match_rate_under": 0.9}, "holds"),
    ("C1_under", {"rates__call_match_rate_under": 0.89}, "fails"),
    (
        "C1_under",
        {"rates__call_match_rate_under": None, "counts__shell_calls": 0},
        "vacuous",
    ),
    (
        "C1_under",
        {
            "rates__call_match_rate_under": None,
            "counts__calls_running_at_stop": 3,
        },
        "vacuous",
    ),
    ("C1_under", {"rates__call_match_rate_under": None}, "fails"),
    ("C1_under", {"integrity__C0.1": False}, "fails"),
    ("C1_under", {"inputs__trace": False}, "fails"),
    ("C2_under", {"rates__exec_explained_rate_under": 0.5}, "fails"),
    (
        "C2_under",
        {"rates__exec_explained_rate_under": None, "counts__execs_total": 1},
        "vacuous",
    ),
    ("C6", {"rates__listed_path_rate": 0.99}, "fails"),
    ("C6", {"rates__listed_path_rate": None}, "vacuous"),
    (
        "C6",
        {
            "rates__listed_path_rate": None,
            "counts__snapshot_status_counts": {
                "ok": 0,
                "truncated": 0,
                "skipped": 4,
                "absent": 0,
            },
        },
        "fails",
    ),
    ("C7_under", {"rates__final_diff_attributed_rate_under": 0.5}, "fails"),
    (
        "C7_under",
        {
            "rates__final_diff_attributed_rate_under": None,
            "counts__final_diff_files": 0,
        },
        "vacuous",
    ),
    (
        "C7_under",
        {
            "rates__final_diff_attributed_rate_under": None,
            "inputs__final_diff": False,
        },
        "fails",
    ),
    ("untracked_visible", {}, "fails"),
    ("untracked_visible", {"rates__untracked_visible": True}, "holds"),
    ("log", {}, "holds"),
    ("log", {"inputs__log": False}, "fails"),
)


@pytest.mark.parametrize(("label", "changes", "expected"), PRECONDITIONS)
def test_precondition(label: str, changes: dict, expected: str) -> None:
    """PRE1-PRE3, PRE6: three values, read from coverage values only."""
    coverage = _coverage(**changes)
    coverage["warnings"] = ["precondition C1_under >= 0.9 failed (0.10)"]
    assert facts.precondition(coverage, label, CONFIG) == expected


COPIED = ("C1_under", "C2_under", "C6", "C7_under")


@pytest.mark.parametrize(("label", "changes", "expected"), PRECONDITIONS)
def test_precondition_copied(label: str, changes: dict, expected: str) -> None:
    """PRE6: a preconditions block of coverage.json is copied as it is."""
    coverage = _coverage(**changes)
    states = {name: "holds" for name in COPIED}
    coverage["preconditions"] = {**states, label: expected}
    assert facts.precondition(coverage, label, CONFIG) == expected
    if label in COPIED:
        flipped = "holds" if expected != "holds" else "vacuous"
        coverage["preconditions"][label] = flipped
        assert facts.precondition(coverage, label, CONFIG) == flipped


def test_untracked_visible_from_capability() -> None:
    """PRE5: the capability makes created files visible in tracked scope."""
    coverage = _coverage()
    coverage["capabilities"] = {"untracked_visible": True}
    assert facts.precondition(coverage, "untracked_visible", CONFIG) == "holds"


def test_peer_ratios() -> None:
    """PEER1: ratio to the median of the same artifact's other runs."""
    rows = [
        {"run_key": "a__x__1", "family": "x", "M1.n": 10, "M1.t": None},
        {"run_key": "a__x__2", "family": "x", "M1.n": 20, "M1.t": 4.0},
        {"run_key": "a__y__1", "family": "y", "M1.n": 40, "M1.t": 2.0},
        {"run_key": "b__x__1", "family": "x", "M1.n": 5, "M1.t": 1.0},
        {"run_key": "c__x__1", "family": "x", "M1.n": 0, "M1.t": 1.0},
        {"run_key": "c__x__2", "family": "x", "M1.n": 0, "M1.t": 1.0},
        {"run_key": "c__x__3", "family": "x", "M1.n": 3, "M1.t": 1.0},
    ]
    out = {row["run_key"]: row for row in facts.peer_ratios(rows)}
    assert out["a__x__1"]["M1.n_peer_ratio"] == pytest.approx(10 / 30)
    assert out["a__x__2"]["M1.n_peer_ratio"] == pytest.approx(20 / 25)
    # The run's own value is null: its ratio is null.
    assert out["a__x__1"]["M1.t_peer_ratio"] is None
    # Only one peer has a value for t.
    assert out["a__x__2"]["M1.t_peer_ratio"] is None
    # No peer at all.
    assert out["b__x__1"]["M1.n_peer_ratio"] is None
    # Median of the peers is 0.
    assert out["c__x__3"]["M1.n_peer_ratio"] is None
    assert out["c__x__1"]["M1.n_peer_ratio"] == pytest.approx(0.0)
    assert "run_key_peer_ratio" not in out["a__x__1"]
    assert "family_peer_ratio" not in out["a__x__1"]


def test_peer_ratios_min_peers_override() -> None:
    """PEER1, §6.12: a min_peers override is honoured."""
    rows = [
        {"run_key": "a__x__1", "family": "x", "M1.n": 10},
        {"run_key": "a__x__2", "family": "x", "M1.n": 20},
    ]
    out = facts.peer_ratios(rows, config={"min_peers": 1})
    assert out[0]["M1.n_peer_ratio"] == pytest.approx(0.5)
    assert out[1]["M1.n_peer_ratio"] == pytest.approx(2.0)


def _layer1_coverage(execs: list[dict]) -> dict:
    """Layer 1's coverage of a run with no call and these execs."""
    run = {**dict(conftest.DEFAULT_RUN), "t_start": 1000.0, "t_end": 2000.0}
    run["capabilities"] = dict(run["capabilities"])
    records = [
        conftest.make_exec(e, i, run["workspace_root"])
        for i, e in enumerate(execs)
    ]
    conftest.assign_exec_seq(records)
    return match.link([], records, run=run)["coverage"]


# (label, execs): runs whose layer-1 preconditions block is dropped.
LAYER1_RUNS = (
    ("no_exec", []),
    ("in_window", [{"command_id": "e1", "command": "ls", "t_start": 1500}]),
    pytest.param(
        "out_of_window",
        [{"command_id": "e1", "command": "ls", "t_start": 10}],
    ),
)


@pytest.mark.parametrize(("name", "execs"), LAYER1_RUNS)
def test_precondition_fallback_agrees_with_layer1(
    name: str, execs: list[dict]
) -> None:
    """PRE6: without the block, the states equal layer 1's.

    S2: C2's denominator is the non-noise execs outside out_of_window.
    """
    coverage = _layer1_coverage(execs)
    states = coverage.pop("preconditions")
    for label in COPIED:
        assert facts.precondition(coverage, label, CONFIG) == states[label], (
            name,
            label,
        )


def _facts_rows() -> tuple[dict, dict]:
    """facts.csv rows of two runs: untracked_visible holds, then fails.

    In the second, PRE4 voids the dict-valued created-file metrics.
    """
    rows = []
    for stem in ("F21_network_created", "F22_network_tracked_scope"):
        path = facts_helpers.FACTS_SCENARIOS_DIR / f"{stem}.yaml"
        result = facts_helpers.run_derive(facts_helpers.load_scenario(path))
        rows.append(cli.facts_row(result["facts"]))
    return rows[0], rows[1]


def test_facts_row_columns_fixed() -> None:
    """§5.5: a voided dict metric keeps its flattened sub-key columns."""
    held, voided = _facts_rows()
    assert held["preconditions.untracked_visible"] == "holds"
    assert voided["preconditions.untracked_visible"] == "fails"
    assert list(voided) == list(held)
    for role in ("edit_call", "nested_exec", "root_exec"):
        key = f"M4.1.created_by_final_writer_under.{role}"
        assert voided[key] is None
        assert isinstance(held[key], int)


def test_facts_row_leaves_out_voided_name_lists() -> None:
    """§5.5: name lists are left out, also when PRE4 voids them to null."""
    for row in _facts_rows():
        assert row["preconditions.env_keys"] == "fails"
        listed = [k for k in row if k.startswith("M5.2.names_")]
        assert listed == []


def test_facts_csv_header_independent_of_row_order(
    tmp_path: pathlib.Path,
) -> None:
    """§5.5: facts.csv's column order does not depend on the run order."""
    held, voided = _facts_rows()
    headers = []
    for order in ([held, voided], [voided, held]):
        path = tmp_path / "facts.csv"
        cli._write_csv(facts.peer_ratios(order), path)
        headers.append(path.read_text(encoding="utf-8").splitlines()[0])
    assert headers[0] == headers[1]


def test_peer_ratio_columns_fixed() -> None:
    """§5.5, PEER1 (B8): one ratio column per metric column of the template.

    The columns do not depend on which metrics have a value: here
    M1.4.depth_max_under and the M4.4 counts are null in every run.
    """
    rows = _facts_rows()
    metrics = {k for k in rows[0] if k.startswith("M")}
    assert rows[0]["M1.4.depth_max_under"] is None
    assert rows[1]["M1.4.depth_max_under"] is None
    for row in facts.peer_ratios(list(rows)):
        ratios = {k for k in row if k.endswith("_peer_ratio")}
        assert ratios == {f"{k}_peer_ratio" for k in metrics}


def test_join_rate_min_dropped() -> None:
    """T2: layer 1's join_rate_warn is the one threshold; no layer-2 copy."""
    assert "join_rate_min" not in facts.DEFAULTS


# (command, programs, word lists of the substitution commands): SHP1 (B6).
QUOTED_SUBSTITUTIONS = (
    ('x=$(echo "a\\"b") && ls', ["echo", "ls"], [["echo", 'a"b']]),
    (
        'echo "$(echo "$(echo ")")")" && ls',
        ["echo", "echo", "echo", "ls"],
        [["echo", '$(echo ")")'], ["echo", ")"]],
    ),
    ("echo $(echo ${x%)}) && ls", ["echo", "echo", "ls"], [["echo", "${x%)}"]]),
    ("x=$(case $a in a) echo 1;; esac)", ["echo"], [["echo", "1"]]),
    ("x=$( case a in (b) p1;; esac ); p2", ["p1", "p2"], [["p1"]]),
    (
        "echo `echo \\`date\\``",
        ["echo", "echo", "date"],
        [["echo", "`date`"], ["date"]],
    ),
)


@pytest.mark.parametrize(
    ("command", "expected", "nested"),
    QUOTED_SUBSTITUTIONS,
)
def test_quoting_inside_substitution(
    command: str, expected: list[str], nested: list[list[str]]
) -> None:
    """SHP1 (B6): quoting inside $(…) and backquotes is read exactly."""
    assert facts.programs(command) == expected
    found = facts.simple_commands(command)
    assert [c["words"] for c in found if c["in_substitution"]] == nested


def _nested(depth: int) -> str:
    return "echo " + "$(echo " * depth + "x" + ")" * depth + " && ls"


def _raises_recursion(function: typing.Callable, *args: object) -> bool:
    """Whether the call raises RecursionError.

    The error is caught and dropped, so a failure reports one line, not a
    traceback of 1,000 frames (slow to render).
    """
    try:
        function(*args)
    except RecursionError:
        return True
    return False


def test_deep_substitution_does_not_raise() -> None:
    """B7: about 1,000 nested $( do not raise; a fallback is allowed.

    The bound is loose (whole seconds), so the test does not flake.
    """
    command = _nested(1000)
    started = time.monotonic()
    functions = (facts.simple_commands, facts.programs, facts.network_types)
    functions += (facts.env_assigned, facts.command_shape)
    raised = [f.__name__ for f in functions if _raises_recursion(f, command)]
    assert raised == []
    assert time.monotonic() - started < 10


def test_deep_substitution_derive() -> None:
    """B7: derive does not fail on a run with about 1,000 nested $(."""
    path = facts_helpers.FACTS_SCENARIOS_DIR / "F20_network.yaml"
    scenario = facts_helpers.load_scenario(path)
    command = _nested(1000)
    scenario["calls"][0]["command"] = command
    scenario["calls"][0]["command_norm"] = command
    scenario["execs"][0]["command"] = command
    results = []
    assert not _raises_recursion(
        lambda: results.append(facts_helpers.run_derive(scenario))
    )
    assert len(results[0]["call_facts"]) == len(scenario["calls"])


def test_ansi_c_quote_shape() -> None:
    r"""B11: $'a\'b' is one word, so && still separates two commands."""
    command = "echo $'a\\'b' && ls"
    assert [c["words"][0] for c in facts.simple_commands(command)] == [
        "echo",
        "ls",
    ]
    assert facts.command_shape(command)[2] == 3


@pytest.mark.parametrize(
    "command",
    ["((" * 50000, "(" * 50000 + ")" * 50000],
    ids=["open", "balanced"],
)
def test_parentheses_linear(command: str) -> None:
    """Many nested parentheses split in linear time (D56).

    The bound is loose (whole seconds), so the test does not flake; the
    quadratic reading took about 27 s for the first command.
    """
    started = time.monotonic()
    facts.simple_commands(command)
    assert time.monotonic() - started < 5


def test_derive_rejects_unknown_config_key() -> None:
    """§6.12, D51: a key in neither DEFAULTS raises ValueError."""
    path = facts_helpers.FACTS_SCENARIOS_DIR / "F20_network.yaml"
    scenario = facts_helpers.load_scenario(path)
    with pytest.raises(ValueError, match="no_such_key"):
        facts_helpers.run_derive(scenario, config={"no_such_key": 1})
