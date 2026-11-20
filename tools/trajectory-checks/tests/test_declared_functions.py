"""Layer 6: unit tables of declared's pure functions (spec §2.2.2).

One row per case with its hand-written expected value. ``declared`` is
imported inside each test (D46), so each fails alone until it exists.
"""

import pathlib
from typing import Any

import chain_helpers as helpers
import pytest

ROOT = "/repo"
# (case, command, cwd, expected keys, one per simple command)
KEY_CASES = (
    (
        "interpreter flag",
        "python3 -u tools/run.py --iters 3",
        None,
        ["tools/run.py"],
    ),
    (
        "cd then script",
        "cd tools && python run.py",
        None,
        [None, "tools/run.py"],
    ),
    ("module", "python -m pkg.mod -k 2", None, ["module:pkg.mod"]),
    ("inline code", "python3 -c 'print(1)'", None, ["python3"]),
    ("node script", "node agg.js", None, ["agg.js"]),
    ("path program", "./bin/tool -j8", None, ["bin/tool"]),
    ("make -C", "make -C sub target", None, ["make:sub/target"]),
    ("make -C, default target", "make -C sub", None, ["make:sub/"]),
    ("make default", "make", None, ["make:"]),
    ("make value options", "make -j 4 -f other.mk all", None, ["make:all"]),
    ("make variable", "make N=3 bench", None, ["make:bench"]),
    ("program and operand", "cargo bench --bench x", None, ["cargo bench"]),
    ("sed is never its file", "sed -n 1,80p run.py", None, ["sed 1,80p"]),
    (
        "numeric operand skipped",
        "sleep 5 && ./run.sh",
        None,
        ["sleep", "run.sh"],
    ),
    ("prefix words", "sudo -E nohup time python3 x.py", None, ["x.py"]),
    ("timeout", "timeout 600 ./run.sh 5", None, ["run.sh"]),
    ("assignment", "N=3 ./run.sh", None, ["run.sh"]),
    ("env", "env A=1 ./run.sh", None, ["run.sh"]),
    ("substitution", "echo $(./gen.sh)", None, ["echo", "gen.sh"]),
    ("outside the root", "/opt/x/run.sh", None, ["/opt/x/run.sh"]),
    ("absolute inside", "/repo/tools/run.sh", None, ["tools/run.sh"]),
    ("cwd given", "./run.sh", "/repo/tools", ["tools/run.sh"]),
    ("cwd relative path", "python3 ../run.py", "/repo/tools", ["run.py"]),
    ("heredoc body", "cat <<EOF > o.txt\n./run.sh\nEOF", None, ["cat"]),
    ("only an assignment", "N=3", None, [None]),
    (
        "pushd and popd",
        "pushd sub; ./a.sh; popd; ./b.sh",
        None,
        [None, "sub/a.sh", None, "b.sh"],
    ),
    (
        "subshell cd",
        "( cd sub; ./a.sh ); ./b.sh",
        None,
        [None, "sub/a.sh", "b.sh"],
    ),
)


@pytest.mark.parametrize(
    ("command", "cwd", "keys"),
    [row[1:] for row in KEY_CASES],
    ids=[row[0] for row in KEY_CASES],
)
def test_invocation_keys(command: str, cwd: str | None, keys: list) -> None:
    """KEY1 to KEY5, DEC3: one invocation per simple command, cd included.

    Rules: KEY1, KEY2, KEY3, KEY4, KEY5, DEC3.
    """
    from trajectory_checks import declared

    found = declared.invocations(command, cwd=cwd, root=ROOT)
    assert [i["invocation_key"] for i in found] == keys
    for item in found:
        assert set(item) == helpers.INVOCATION_FIELDS


def test_invocation_fields() -> None:
    """program_path, inline and text of the invocation records.

    Rules: KEY1, KEY2.
    """
    from trajectory_checks import declared

    (one,) = declared.invocations(
        "python3 -u tools/run.py --k 3", cwd=None, root=ROOT
    )
    assert one["program_path"] == "tools/run.py"
    assert one["inline"] is False
    assert one["text"] == "python3 -u tools/run.py --k 3"
    (two,) = declared.invocations("python3 -c 'print(1)'", cwd=None, root=ROOT)
    assert (two["invocation_key"], two["inline"]) == ("python3", True)
    assert two["program_path"] is None
    (three,) = declared.invocations("./bin/tool -j8", cwd=None, root=ROOT)
    assert three["program_path"] == "bin/tool"
    (four,) = declared.invocations("cargo bench", cwd=None, root=ROOT)
    assert four["program_path"] is None
    (five,) = declared.invocations("make -C sub t", cwd=None, root=ROOT)
    assert five["program_path"] is None


def _v(value: str, value_type: str) -> dict:
    return {"value": value, "value_type": value_type}


TRUE = _v("true", "bool")
# (case, command, takes_value, aliases, expected options)
OPTION_CASES = (
    ("equals", "./t.sh --k=v", None, None, {"--k": _v("v", "enum")}),
    ("single dash equals", "./t.sh -x=4", None, None, {"-x": _v("4", "int")}),
    ("numeric value", "./t.sh --k 5", None, None, {"--k": _v("5", "int")}),
    ("word value, no takes_value", "./t.sh --k v", None, None, {"--k": TRUE}),
    (
        "word value with takes_value",
        "./t.sh --k v",
        {"t.sh": {"--k": True}},
        None,
        {"--k": _v("v", "enum")},
    ),
    ("short option", "./t.sh -k 5", None, None, {"-k": _v("5", "int")}),
    ("attached digits", "./t.sh -k8", None, None, {"-k": _v("8", "int")}),
    (
        "flag cluster",
        "./t.sh -abc",
        None,
        None,
        {"-a": TRUE, "-b": TRUE, "-c": TRUE},
    ),
    (
        "single-dash long name",
        "./t.sh -repeats 5",
        None,
        None,
        {"-repeats": _v("5", "int")},
    ),
    ("dash and digits", "./t.sh -30", None, None, {"pos:1": _v("-30", "int")}),
    ("leading assignment", "N=3 ./t.sh", None, None, {"env:N": _v("3", "int")}),
    ("make variable", "make N=3 t", None, None, {"var:N": _v("3", "int")}),
    (
        "numeric operands",
        "./run.sh 5 out 7",
        None,
        None,
        {"pos:1": _v("5", "int"), "pos:3": _v("7", "int")},
    ),
    ("negative value", "./t.sh --k -1", None, None, {"--k": _v("-1", "int")}),
    ("float value", "./t.sh --k 1e3", None, None, {"--k": _v("1e3", "float")}),
    (
        "decimal value",
        "./t.sh --k 0.5",
        None,
        None,
        {"--k": _v("0.5", "float")},
    ),
    ("flag alone", "./t.sh --flag", None, None, {"--flag": TRUE}),
    (
        "placeholder",
        "./t.sh --k='<n>'",
        None,
        None,
        {"--k": _v("<n>", "symbolic")},
    ),
    ("braces", "./t.sh --k={n}", None, None, {"--k": _v("{n}", "symbolic")}),
    (
        "glob",
        "./t.sh --k='*.csv'",
        None,
        None,
        {"--k": _v("*.csv", "symbolic")},
    ),
    (
        "variable value",
        "./t.sh --k=$N",
        None,
        None,
        {"--k": _v("$N", "symbolic")},
    ),
    (
        "alias",
        "./t.sh -n 3",
        None,
        {"t.sh": {"-n": "--iters", "--iters": "--iters"}},
        {"--iters": _v("3", "int")},
    ),
    ("no options", "./t.sh in.txt", None, None, {}),
    ("make jobs attached", "make -j4 all", None, None, {"-j": _v("4", "int")}),
    (
        "interpreter script options",
        "python3 x.py --n 2",
        None,
        None,
        {"--n": _v("2", "int")},
    ),
    (
        "module options",
        "python -m pkg.mod -k 2",
        None,
        None,
        {"-k": _v("2", "int")},
    ),
)


@pytest.mark.parametrize(
    ("command", "takes", "aliases", "options"),
    [row[1:] for row in OPTION_CASES],
    ids=[row[0] for row in OPTION_CASES],
)
def test_invocation_options(
    command: str, takes: Any, aliases: Any, options: dict
) -> None:
    """KEY7 option forms and value types; KEY8 canonical names.

    Rules: KEY7, KEY8, DEC4.
    """
    from trajectory_checks import declared

    found = declared.invocations(
        command, cwd=None, root=ROOT, takes_value=takes, aliases=aliases
    )
    assert found[-1]["options"] == options


INFO_CASES = (
    *((f"./t.sh {o}", True) for o in ("--help", "-h", "--version", "-V")),
    *(
        (f"make {o}", True)
        for o in ("-n", "--dry-run", "-q", "-p", "--version")
    ),
    ("./t.sh --helper", False),
    ("./t.sh -hv", False),
    ("make -j4", False),
    ("make all", False),
    ("python3 x.py --help", True),
)


@pytest.mark.parametrize(("command", "want"), INFO_CASES)
def test_informational(command: str, want: bool) -> None:
    """KEY6: help, version and make's dry runs; near misses are not (D56).

    Rules: KEY6.
    """
    from trajectory_checks import declared

    (found,) = declared.invocations(command, cwd=None, root=ROOT)
    assert found["informational"] is want


LITERAL_CASES = (
    ("echo 1 > out/r.json", "out/r.json"),
    ("printf '%s' x >> out/r.json", "out/r.json"),
    ("cat > out/r.json <<EOF\n{}\nEOF", "out/r.json"),
    ("cat a.json > out/r.json", None),
    ("./gen.sh > out/r.json", None),
    ("echo 1", None),
)


@pytest.mark.parametrize(("command", "want"), LITERAL_CASES)
def test_literal_write(command: str, want: str | None) -> None:
    """FA6: text written from argv or a heredoc by a literal writer.

    Rules: FA6, KEY5.
    """
    from trajectory_checks import declared

    found = declared.invocations(command, cwd="/repo", root=ROOT)
    assert found[0]["literal_write"] == want


README = """\
# Title

```bash
$ ./a.sh --k 1
$ cd sub
$ ./b.sh && ./c.sh
# ./root.sh
```

```python
print(1)
```

~~~
./d.sh \\
  --long 2
# a comment
./e.sh # trailing
( cd x; ./f.sh ); ./g.sh
cd y && ./h.sh
./i.sh
~~~

```console
$ make all
```
"""


def test_fenced_lines() -> None:
    """DEC2, DEC3: prompts, comments, continuations, cwd, positions.

    A python fence is not read; a `# ` line is a prompt in a block of `$`
    prompts and a comment elsewhere; `cd` holds a position and moves the
    rest of the block (D57); a subshell's cd does not leak.
    Rules: DEC2, DEC3.
    """
    from trajectory_checks import declared

    got = declared.fenced_lines(README, source_path="README.md")
    assert got == [
        ("README.md:3", 4, 0, ".", "./a.sh --k 1"),
        ("README.md:3", 6, 2, "sub", "./b.sh"),
        ("README.md:3", 6, 3, "sub", "./c.sh"),
        ("README.md:3", 7, 4, "sub", "./root.sh"),
        ("README.md:14", 15, 0, ".", "./d.sh --long 2"),
        ("README.md:14", 18, 1, ".", "./e.sh"),
        ("README.md:14", 19, 3, "x", "./f.sh"),
        ("README.md:14", 19, 4, ".", "./g.sh"),
        ("README.md:14", 20, 6, "y", "./h.sh"),
        ("README.md:14", 21, 7, "y", "./i.sh"),
        ("README.md:24", 25, 0, ".", "make all"),
    ]


def test_fenced_lines_of_a_linked_file() -> None:
    """A block starts in the folder of its Markdown file (DEC3).

    Rules: DEC3.
    """
    from trajectory_checks import declared

    text = "Run:\n\n```sh\n./run.sh 2\n```\n"
    got = declared.fenced_lines(text, source_path="docs/run.md")
    assert got == [("docs/run.md:3", 4, 0, "docs", "./run.sh 2")]


MAKEFILE = """\
RUNS ?= 10
OUT = out
CC := gcc

all: bench

bench: prep
\t@./bench.sh --runs $(RUNS) --out ${OUT}
\t-$(MAKE) -C sub t
\t+echo $(UNKNOWN) x

prep:
\t./prep.sh

%.o: %.c
\t$(CC) -c $<
"""


def test_make_recipe() -> None:
    """DEC5: recipe lines, prefixes, variables, ?= defaults.

    Rules: DEC5.
    """
    from trajectory_checks import declared

    lines, defaults, warnings = declared.make_recipe(
        MAKEFILE, "bench", variables={}
    )
    assert lines == [
        (8, "./bench.sh --runs 10 --out out"),
        (9, "make -C sub t"),
        (10, "echo $(UNKNOWN) x"),
    ]
    assert defaults == {"RUNS": ("10", 1)}
    assert warnings == []


def test_make_recipe_line_variables_win() -> None:
    """A VAR=value argument of the make line wins over the Makefile's.

    Rules: DEC5.
    """
    from trajectory_checks import declared

    lines, _, _ = declared.make_recipe(
        MAKEFILE, "bench", variables={"RUNS": "3"}
    )
    assert lines[0] == (8, "./bench.sh --runs 3 --out out")


@pytest.mark.parametrize(
    ("target", "lines", "warned"),
    [
        ("all", [], False),
        ("prep", [(13, "./prep.sh")], False),
        ("missing", [], True),
        ("x.o", [], True),
    ],
)
def test_make_recipe_targets(target: str, lines: list, warned: bool) -> None:
    """Prerequisites only give no line; a missing or pattern target warns.

    Rules: DEC5.
    """
    from trajectory_checks import declared

    got, _, warnings = declared.make_recipe(MAKEFILE, target, variables={})
    assert got == lines
    if warned:
        (warning,) = warnings
        assert warning.startswith(f"make target {target} not found in ")
    else:
        assert warnings == []


SCRIPT = """\
import argparse, os
p = argparse.ArgumentParser()
p.add_argument("-n", "--iters", type=int, default=10)
p.add_argument("--rate", default=0.5)
p.add_argument("--k", type=int, default="5")
p.add_argument("--mode", choices=["fast", "full"], default="full")
p.add_argument("--fast", action="store_true")
p.add_argument("--slow", action="store_false")
p.add_argument("--jobs", default=os.cpu_count())
p.add_argument("--offset", default=-1)
p.add_argument("--files", nargs="+")
p.add_argument("input")
p.add_argument("--out", dest="target", default="out")
p.add_argument("-v", "--verbose", action="count", default=0)
"""


def _opt(option: str, line: int, default: Any, **extra: Any) -> dict:
    row = {
        "invocation_key": "tools/run.py",
        "option": option,
        "aliases": [option],
        "default": default,
        "choices": None,
        "takes_value": True,
        "source_type": "argparse",
        "source_path": "tools/run.py",
        "line": line,
    }
    row.update(extra)
    return row


def test_argparse_defaults() -> None:
    """DEC6: literal defaults typed, flags, choices, computed defaults.

    Rules: DEC6.
    """
    from trajectory_checks import declared

    rows, warnings = declared.argparse_defaults(
        SCRIPT, source_path="tools/run.py"
    )
    assert warnings == []
    got = {r["option"]: r for r in rows}
    want = {
        "--iters": _opt(
            "--iters", 3, _v("10", "int"), aliases=["--iters", "-n"]
        ),
        "--rate": _opt("--rate", 4, _v("0.5", "float")),
        "--k": _opt("--k", 5, _v("5", "int")),
        "--mode": _opt(
            "--mode", 6, _v("full", "enum"), choices=["fast", "full"]
        ),
        "--fast": _opt("--fast", 7, _v("false", "bool"), takes_value=False),
        "--slow": _opt("--slow", 8, _v("true", "bool"), takes_value=False),
        "--offset": _opt("--offset", 10, _v("-1", "int")),
        "--files": _opt("--files", 11, None),
        "--out": _opt("--out", 13, _v("out", "enum")),
        "--verbose": _opt(
            "--verbose",
            14,
            _v("0", "int"),
            aliases=["--verbose", "-v"],
            takes_value=False,
        ),
    }
    jobs = got.pop("--jobs")
    assert jobs["default"]["value_type"] == "symbolic"
    assert jobs["line"] == 9
    assert got == want
    for row in rows:
        assert set(row) == helpers.DECLARED_OPTION_FIELDS


def test_argparse_syntax_error() -> None:
    """A file that does not parse gives no rows and one warning.

    Rules: DEC6.
    """
    from trajectory_checks import declared

    rows, warnings = declared.argparse_defaults(
        "def broken(:\n", source_path="tools/bad.py"
    )
    assert rows == []
    assert warnings == ["tools/bad.py: syntax error, no defaults"]


def _empty_spec() -> dict:
    return {
        "lines": [],
        "markers": {},
        "experiment_keys": None,
        "allowed_diff_classes": [],
        "warnings": [],
    }


def test_case_spec_absent_and_full() -> None:
    """CT1, CT4: no table gives an empty spec; a full table is parsed.

    Rules: CT1, CT4.
    """
    from trajectory_checks import declared

    assert declared.case_spec({"run": {}}) == _empty_spec()
    case = {
        "declared": {
            "lines": ["./bench.sh --runs 10"],
            "experiment_keys": ["bench.sh"],
            "allowed_diff_classes": ["whitespace_only"],
            "options": {
                "bench.sh --runs": "min",
                "tools/run.py --iters": "min",
                "cargo bench --bench": "exact",
                "make:all_datasets -j": "free",
            },
        }
    }
    spec = declared.case_spec(case)
    assert set(spec) == helpers.CASE_SPEC_FIELDS
    assert spec["lines"] == ["./bench.sh --runs 10"]
    assert spec["experiment_keys"] == ["bench.sh"]
    assert spec["allowed_diff_classes"] == ["whitespace_only"]
    assert spec["markers"] == {
        "bench.sh": {"--runs": "min"},
        "tools/run.py": {"--iters": "min"},
        "cargo bench": {"--bench": "exact"},
        "make:all_datasets": {"-j": "free"},
    }
    assert spec["warnings"] == []


MALFORMED = (
    ("unknown marker", {"options": {"t.sh --k": "sometimes"}}),
    ("key without a space", {"options": {"nospace": "min"}}),
    ("non-string marker", {"options": {"t.sh --k": 3}}),
    ("lines not a list", {"lines": "./t.sh"}),
    ("lines not strings", {"lines": [1]}),
    ("experiment_keys not a list", {"experiment_keys": "t.sh"}),
    ("unknown key", {"lineup": ["x"]}),
    ("unknown hunk type", {"allowed_diff_classes": ["prettier"]}),
)


@pytest.mark.parametrize(
    "table", [r[1] for r in MALFORMED], ids=[r[0] for r in MALFORMED]
)
def test_case_spec_malformed(table: dict) -> None:
    """CT5: each malformed entry is skipped with one declared warning.

    Rules: CT5.
    """
    from trajectory_checks import declared

    spec = declared.case_spec({"declared": table})
    (warning,) = spec["warnings"]
    assert warning.startswith("declared")
    assert spec["markers"] == {}
    assert spec["lines"] == []
    assert spec["allowed_diff_classes"] == []


def test_case_spec_not_a_table() -> None:
    """A [declared] that is not a table is one warning.

    Rules: CT5.
    """
    from trajectory_checks import declared

    spec = declared.case_spec({"declared": ["x"]})
    (warning,) = spec["warnings"]
    assert warning.startswith("declared")


SHA = "4D9BEF55E4F2E3B3B8B0EFE8F91CD35D34ED35A8"


def _git(root: pathlib.Path, **files: str) -> pathlib.Path:
    for name, text in files.items():
        path = root / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_checkout_commit(tmp_path: pathlib.Path) -> None:
    """DEC7: the commit from .git files only, lower-case hex.

    Detached HEAD, a branch ref, a packed ref, a .git file pointing to a
    folder; no .git gives None.
    Rules: DEC7.
    """
    from trajectory_checks import declared

    lower = SHA.lower()
    detached = _git(tmp_path / "a", **{".git__HEAD": SHA + "\n"})
    assert declared.checkout_commit(detached) == lower
    branch = _git(
        tmp_path / "b",
        **{
            ".git__HEAD": "ref: refs/heads/x\n",
            ".git__refs__heads__x": lower + "\n",
        },
    )
    assert declared.checkout_commit(branch) == lower
    packed = _git(
        tmp_path / "c",
        **{
            ".git__HEAD": "ref: refs/heads/x\n",
            ".git__packed-refs": f"# pack-refs\n{SHA} refs/heads/x\n",
        },
    )
    assert declared.checkout_commit(packed) == lower
    worktree = _git(
        tmp_path / "d",
        **{".git": "gitdir: ../store\n"},
    )
    _git(tmp_path / "store", HEAD=SHA + "\n")
    assert declared.checkout_commit(worktree) == lower
    plain = tmp_path / "e"
    plain.mkdir()
    assert declared.checkout_commit(plain) is None


def test_declared_tables() -> None:
    """The tables of §3.3 that the tests depend on.

    Rules: DEC2, KEY6, CT1, CT4.
    """
    from trajectory_checks import declared

    assert declared.SHELL_FENCES == {
        *("", "sh", "bash", "shell", "console", "zsh", "shell-session"),
    }
    assert declared.INFO_OPTIONS == {"--help", "-h", "--version", "-V"}
    assert declared.MAKE_PROGRAMS == {"make", "gmake"}
    assert declared.MARKERS == ("exact", "min", "max", "free")
    assert declared.SPEC_KEYS == (
        *("lines", "options", "experiment_keys", "allowed_diff_classes"),
    )
    assert dict(declared.DEFAULTS) == {
        "link_follow_depth": 1,
        "make_follow_depth": 1,
    }
    assert declared.LITERAL_WRITERS == {"echo", "printf", "cat", "tee"}  # D60


def test_declare_rejects_unknown_config(tmp_path: pathlib.Path) -> None:
    """A config key outside declared.DEFAULTS raises ValueError (D45).

    Rules: DEC1.
    """
    from trajectory_checks import declared

    with pytest.raises(ValueError):
        declared.declare(
            tmp_path, {}, artifact="fixture", config={"min_peers": 2}
        )


def test_declare_assignment_line_before_make(tmp_path: pathlib.Path) -> None:
    """A fenced assignment (no invocation key) next to a make line.

    The assignment gives no record and no recipe follow; the make line
    still reaches its recipe (critic cycle 1: T6.1 crashed on it).

    Rules: DEC2, DEC5, KEY1.
    """
    from trajectory_checks import declared

    (tmp_path / "README.md").write_text(
        "```\nJOBS=4\nmake all\n```\n", encoding="utf-8"
    )
    (tmp_path / "Makefile").write_text(
        "all:\n\t./tool.sh --k 3\n", encoding="utf-8"
    )
    got = declared.declare(tmp_path, {}, artifact="fixture")
    keys = [d["invocation_key"] for d in got["declared_invocations"]]
    assert keys == ["make:all", "tool.sh"]


def test_declare_make_target_with_a_slash(tmp_path: pathlib.Path) -> None:
    """A file target holding a slash is looked up whole (DEC5).

    Rules: DEC5, KEY1.
    """
    from trajectory_checks import declared

    (tmp_path / "README.md").write_text(
        "```\nmake out/fig.pdf\nmake -C sub out/t\n```\n", encoding="utf-8"
    )
    (tmp_path / "Makefile").write_text(
        "out/fig.pdf:\n\t./plot.sh --dpi 300\n", encoding="utf-8"
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "Makefile").write_text(
        "out/t:\n\t./gen.sh\n", encoding="utf-8"
    )
    got = declared.declare(tmp_path, {}, artifact="fixture")
    keys = [d["invocation_key"] for d in got["declared_invocations"]]
    assert keys == [
        "make:out/fig.pdf",
        "make:sub/out/t",
        "plot.sh",
        "sub/gen.sh",
    ]
    assert got["declared"]["warnings"] == []


def test_argparse_defaults_quiet_on_invalid_escapes(recwarn: Any) -> None:
    """A checkout's invalid string escape warns nothing (DEC6).

    Rules: DEC6.
    """
    from trajectory_checks import declared

    source = 'p.add_argument("--k", default="a\\d")\n'
    rows, _ = declared.argparse_defaults(source, source_path="x.py")
    assert [r["default"]["value"] for r in rows] == ["a\\d"]
    assert not [w for w in recwarn if w.category is SyntaxWarning]


def test_declare_make_variable_row_once_per_key(tmp_path: pathlib.Path) -> None:
    """Two make lines of one target give one var: row, not two (D58).

    Rules: DEC5.
    """
    from trajectory_checks import declared

    (tmp_path / "README.md").write_text(
        "```\nmake all\nmake all N=3\n```\n", encoding="utf-8"
    )
    (tmp_path / "Makefile").write_text(
        "N ?= 5\nall:\n\t./run.sh -n $(N)\n", encoding="utf-8"
    )
    got = declared.declare(tmp_path, {}, artifact="fixture")
    rows = [(o["invocation_key"], o["option"]) for o in got["declared_options"]]
    assert rows == [("make:all", "var:N")]
    assert len(got["declared_invocations"]) == 4


def test_argparse_choices() -> None:
    """Literal choices are kept as text; any other choices give None.

    Rules: DEC6.
    """
    from trajectory_checks import declared

    source = (
        'p.add_argument("--a", choices=["x", 2])\n'
        'p.add_argument("--b", choices=MODES)\n'
        'p.add_argument("--c", choices=("x", y))\n'
        'p.add_argument("--d", choices=[])\n'
        'p.add_argument("--e")\n'
    )
    rows, _ = declared.argparse_defaults(source, source_path="x.py")
    got = {r["option"]: r["choices"] for r in rows}
    assert got == {
        "--a": ["x", "2"],
        "--b": None,
        "--c": None,
        "--d": [],
        "--e": None,
    }
