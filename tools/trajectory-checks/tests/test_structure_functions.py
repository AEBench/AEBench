"""Layer 3: table-driven tests of the pure functions (structure spec §2.2.1).

Every expected value is written by hand from the rules of spec §6. The
rules these tables exercise are listed in
``structure_helpers.UNIT_TEST_RULES``.
"""

import copy
import inspect
import time
from typing import Any

import conftest
import pytest
import structure_helpers

from trajectory_checks import facts

OBSERVE = ("observe", "observe")
ACT = ("act", "program")


def _layer3() -> Any:
    """Imports the layer-3 module inside each test.

    The layer-3 module, imported per test: until structure.py exists
    each test fails on its own and the other suites still run.
    """
    from trajectory_checks import structure

    return structure


# (command, (call_class, class_reason)): OBS1 to OBS6.
CALL_CLASSES = (
    # Each observation program alone (OBS2).
    ("tail -5 log", OBSERVE),
    ("ps aux | grep x", OBSERVE),
    ("ls", OBSERVE),
    ("git status", OBSERVE),
    ("git diff --stat", OBSERVE),
    ("git log -n 5 | head -3", OBSERVE),
    ("test -f x", OBSERVE),
    ("kill -0 12", OBSERVE),
    ("docker ps", OBSERVE),
    ("nvidia-smi", OBSERVE),
    ("jq . f.json", OBSERVE),
    ("sed -n 1,5p f", OBSERVE),
    ("awk '{print $1}' f", OBSERVE),
    ("find . -name '*.py'", OBSERVE),
    ("cd d && ls", OBSERVE),
    ("sleep 60; tail log", OBSERVE),
    # NAME=value words and PREFIX_WORDS are skipped; assignments alone
    # are neutral (OBS2).
    ("A=1 ls", OBSERVE),
    ("timeout 5 tail -f log", OBSERVE),
    ("A=1", OBSERVE),
    # Loops: reserved words and a for header are neutral (OBS2).
    ("while ! grep -q DONE log; do sleep 30; done", OBSERVE),
    ("for i in $(seq 1 9); do [ -f x ] && break; sleep 5; done", OBSERVE),
    ("if [ -f x ]; then cat x; else echo none; fi", OBSERVE),
    # A substitution is part of its word; its commands are classified;
    # arithmetic is a word (OBS1).
    ("while [ $(date +%s) -lt $end ]; do sleep 30; done", OBSERVE),
    ("end=$(( $(date +%s) + 60 ))", OBSERVE),
    ("echo $(date)", OBSERVE),
    ("echo $(./run.sh)", ("act", "program")),
    ("x=`./run.sh`", ("act", "program")),
    # Heredoc bodies are removed before the split (OBS1).
    ("cat <<EOF\nhello > there\nEOF", OBSERVE),
    ("cat <<EOF > f.txt\nx\nEOF", ("act", "redirect")),
    # Redirects to /dev/null or to a descriptor do not write (OBS4).
    ("ls 2>/dev/null", OBSERVE),
    ("ls >/dev/null 2>&1", OBSERVE),
    ("ls 2>&1", OBSERVE),
    ("grep x f >&2", OBSERVE),
    # Writes and background (OBS4).
    ("cat a > b", ("act", "redirect")),
    ("echo x >> f", ("act", "redirect")),
    ("ls &> out.txt", ("act", "redirect")),
    ("ls 2> err.txt", ("act", "redirect")),
    ("./run.sh > out.log", ("act", "redirect")),
    ("cmd &", ("act", "background")),
    ("nohup cmd", ("act", "background")),
    ("nohup ./x.sh > out.log &", ("act", "background")),
    ("tail -f log &", ("act", "background")),
    # Observation programs made to write, and programs that write (OBS2).
    ("sed -i s/a/b/ f", ("act", "program")),
    ("sed -i.bak s/a/b/ f", ("act", "program")),
    ("awk -i inplace '{print}' f", ("act", "program")),
    ("find . -delete", ("act", "program")),
    ("find . -exec rm {} \\;", ("act", "program")),
    ("tee f", ("act", "program")),
    ("cat a | tee b", ("act", "program")),
    ("kill 12", ("act", "program")),
    ("git commit -m x", ("act", "program")),
    ("docker run img", ("act", "program")),
    ("mkdir -p out", ("act", "program")),
    ("rm -rf out", ("act", "program")),
    # Unknown programs are act (OBS5).
    ("./unknown", ("act", "program")),
    ("make", ("act", "program")),
    ("python3 - <<EOF\nprint(1)\nEOF", ("act", "program")),
    # Recursion into shells and container front ends (OBS3).
    ("bash -lc 'tail -5 log'", OBSERVE),
    ('sh -c "ps aux"', OBSERVE),
    ("bash -ic 'ls'", OBSERVE),
    ("docker exec c bash -lc 'grep -l x *.log'", OBSERVE),
    ("docker exec -it c cat /tmp/x", OBSERVE),
    ("podman exec c ls", OBSERVE),
    ("kubectl exec p -- cat f", OBSERVE),
    ("bash -c './run.sh'", ("act", "program")),
    ("docker exec c ./run.sh", ("act", "program")),
    # Depth 3 observe, depth 4 act (DEFAULTS recursion_depth).
    ("bash -c \"bash -c 'bash -c ls'\"", OBSERVE),
    ('bash -c "bash -c \'bash -c \\"bash -c ls\\"\'"', ("act", "program")),
    ("docker exec a docker exec b docker exec c ls", OBSERVE),
    (
        "docker exec a docker exec b docker exec c docker exec d ls",
        ("act", "program"),
    ),
    # No simple command (OBS6).
    ("", ("act", "no_command")),
    ("   ", ("act", "no_command")),
)


# Guard rows: neighbours of the cycle-2 fixes that must not change.
CALL_CLASS_GUARDS = (
    ("sed -E s/a/b/ f", OBSERVE),  # S2: a cluster without i
    ("sed --posix s/a/b/ f", OBSERVE),  # S2: a long option is no cluster
    ("sed -e s/a/b/ -i f", ACT),
    ("sed --in-place s/a/b/ f", ACT),
    ("docker exec --workdir=/x c ls", OBSERVE),  # S1: value in the word
    ("docker exec -it c ls", OBSERVE),
    ("bash -c cat", OBSERVE),
    ("cat f > /dev/stdout.txt", ("act", "redirect")),  # S7: a real file
)
# Cycle-1 critic findings A1 to A6 and S7, fixed in cycle 2.
CALL_CLASS_FIXES = (
    # A1, S1: the values of container exec options are not the container.
    ("docker exec -w /x c tail log", OBSERVE),
    ("docker exec --workdir /x c tail log", OBSERVE),
    ("docker exec -e A=1 c tail log", OBSERVE),
    ("docker exec --env A=1 c tail log", OBSERVE),
    ("docker exec -u root c ls", OBSERVE),
    ("docker exec --user root c ls", OBSERVE),
    ("docker exec --env-file vars c ls", OBSERVE),
    ("docker exec -it -w /w c cat f", OBSERVE),
    ("podman exec -w /x c ls", OBSERVE),
    ("kubectl exec -n ns pod -- cat f", OBSERVE),
    ("kubectl exec --namespace ns pod -- cat f", OBSERVE),
    ("kubectl exec -c box pod -- cat f", OBSERVE),
    ("kubectl exec --container box pod -- cat f", OBSERVE),
    # A3, S3: an & in a heredoc body is text (OBS1 before OBS4).
    ("cat <<EOF\nfoo &\nEOF", OBSERVE),
    ("cat <<EOF > out\nfoo &\nEOF", ("act", "redirect")),
    # A4, S2: any short-option cluster with i, or --in-place=…, is act.
    ("sed -Ei 's/a/b/' f", ACT),
    ("sed -ni 's/a/b/p' f", ACT),
    ("sed --in-place=.bak s/a/b/ f", ACT),
    # A5: -c is an option only before the first non-option word.
    ("bash run.sh -c cat", ACT),
    ("bash -x run.sh -c cat", ACT),
    # A6: find's -ok, -okdir and -fprint0 act.
    ("find . -ok rm {} \\;", ACT),
    ("find . -okdir rm {} \\;", ACT),
    ("find . -fprint0 out", ACT),
    # S7: /dev/stdout, /dev/stderr and /dev/tty write no file.
    ("cat f > /dev/stdout", OBSERVE),
    ("cat f > /dev/stderr", OBSERVE),
    ("echo x 2>/dev/tty", OBSERVE),
    ("ls >/dev/tty 2>/dev/stderr", OBSERVE),
)
# Cycle-3 critic findings: B1 and the rows its mutants survived.
CALL_CLASS_CYCLE3 = (
    # B1: a shell option's value is not the script operand (OBS3).
    ("bash -o pipefail -c 'cat f'", OBSERVE),
    ("bash -O extglob -c 'cat f'", OBSERVE),
    ("bash +O extglob -c 'cat f'", OBSERVE),
    ("bash -euo pipefail -c 'cat f'", OBSERVE),
    ("bash --rcfile r -c 'cat f'", OBSERVE),
    ("bash --init-file r -c 'cat f'", OBSERVE),
    ("bash +x -c 'cat f'", OBSERVE),
    ("bash +c cat", ACT),  # only a - cluster gives -c
    # A long option is no cluster, even with a c in it (test gap 2).
    ("bash --norc -c 'cat f'", OBSERVE),
    ("bash --norc run.sh", ACT),
    # The script is the first operand after the options, and `--` or `-`
    # ends them.
    ("bash -c -- 'cat f'", OBSERVE),
    ("bash -c -x 'cat f'", OBSERVE),
    ("bash - -c cat", ACT),
    ("bash -o pipefail run.sh", ACT),
    # S2: -e's script is not a cluster, even with an i in it (test gap 1).
    ("sed -es/x/i/ f", OBSERVE),
    ("sed -eis/x/y/ f", OBSERVE),
)


@pytest.mark.parametrize(
    ("command", "expected"),
    (*CALL_CLASSES, *CALL_CLASS_GUARDS, *CALL_CLASS_FIXES, *CALL_CLASS_CYCLE3),
)
def test_call_class(command: str, expected: tuple[str, str]) -> None:
    """OBS1 to OBS6: the observe/act partition and the reason."""
    assert _layer3().call_class(command) == expected


# (command, program key): KEY3.
PROGRAM_KEYS = (
    ("python3 run.py --n 5", "python3 run.py"),
    ("cd d && python3 run.py | tail -5", "python3 run.py"),
    ("python3 run.py | tail -5", "python3 run.py"),
    ("sleep 30; make all", "make all"),
    # An option's value is not told from an operand (KEY3, accepted).
    ("echo start; ./bench.sh --x 1", "bench.sh N"),
    ("timeout 60 ./bench.sh", "bench.sh"),
    ("nohup ./train.sh > log &", "train.sh"),
    ("A=1 ./x.sh", "x.sh"),
    ("T=$(nproc) make all", "make all"),
    ("python3 -m pkg.mod", "python3 pkg.mod"),
    ("make -j8 all", "make all"),
    ("make -C build install", "make build"),
    ("sudo apt-get install -y x", "apt-get install"),
    ("bash -c './run.sh 3'", "run.sh N"),
    ("docker exec c ./run.sh", "run.sh"),
    ("docker exec -it box bash -lc 'python3 run.py'", "python3 run.py"),
    ("kubectl exec pod -- ./bench.sh", "bench.sh"),
    ("for i in 1 2; do ./run.sh; done", "run.sh"),
    ("cd d", "cd d"),
    ("cd d && mkdir out", "cd d"),  # only glue: the first of them
    ("./run 5", "run N"),
    ("/usr/bin/python3 /repo/a/run.py", "python3 run.py"),
    ("cat a > b", "cat a"),
    # No simple command: the key is the shape (KEY3).
    ("", ""),
    ("A=1", "A=N"),
    ("for i in 1 2; do break; done", "for i in N N ; do break ; done"),
)


PROGRAM_KEY_FIXES = (
    # A1: the container and option values never enter the key (KEY3).
    ("docker exec -w /work box-a ./main.sh", "main.sh"),
    ("docker exec -e IN=inputs box-a ./main.sh", "main.sh"),
    ("docker exec -it -w /w box-a bash run.sh", "bash run.sh"),
    ("docker exec -u root box-a python3 x.py", "python3 x.py"),
    ("podman exec -w /x box-a ./main.sh", "main.sh"),
    ("kubectl exec -n ns pod-a -- ./bench.sh", "bench.sh"),
    ("kubectl exec -c box pod-a -- ./bench.sh", "bench.sh"),
    # A5: bash's script operand is the key's operand, -c after it is not.
    ("bash run.sh -c cat", "bash run.sh"),
    # B1: the script after valued options is the key's source.
    ("bash -o pipefail -c './run.sh 3'", "run.sh N"),
    ("bash --rcfile r -c 'python3 x.py'", "python3 x.py"),
)


@pytest.mark.parametrize(
    ("command", "expected"), (*PROGRAM_KEYS, *PROGRAM_KEY_FIXES)
)
def test_program_key(command: str, expected: str) -> None:
    """KEY3: program plus first operand, glue and filters skipped."""
    assert _layer3().program_key(command) == expected


def test_program_key_falls_back_to_the_shape() -> None:
    """KEY3: with no simple command the key is layer 2's shape."""
    for command in ("", "A=1", "for i in 1 2; do break; done"):
        assert _layer3().program_key(command) == facts.command_shape(command)[0]


def _facts_row(command: str) -> dict[str, Any]:
    """A call_facts row of the command, with its command_norm added."""
    shape, numbers, count = facts.command_shape(command)
    return {
        "command_norm": command,
        "command_shape": shape,
        "numeric_args": numbers,
        "arg_count": count,
        "programs": facts.programs(command),
        "outcome": "ok",
        "match_under": True,
    }


def _delta(
    identical: bool,
    change: str,
    count: int,
    numeric: list[tuple[int, str, str]],
    direction: str | None,
    removed: list[str],
    added: list[str],
    narrowed: bool,
) -> dict[str, Any]:
    return {
        "identical": identical,
        "arg_change_type": change,
        "arg_count_delta": count,
        "numeric_changes": [
            {"index": i, "from": a, "to": b} for i, a, b in numeric
        ],
        "numeric_direction": direction,
        "words_removed": removed,
        "words_added": added,
        "narrowed": narrowed,
    }


# (previous command, next command, expected delta): RR3, RR4.
DELTAS = (
    (
        "./run.sh --n 5",
        "./run.sh --n 5",
        _delta(True, "none", 0, [], None, [], [], False),
    ),
    (
        "./run.sh --n 10",
        "./run.sh --n 3",
        _delta(
            False, "numeric", 0, [(0, "10", "3")], "decreased", [], [], False
        ),
    ),
    (
        "./run.sh 1 2",
        "./run.sh 4 8",
        _delta(
            False,
            "numeric",
            0,
            [(0, "1", "4"), (1, "2", "8")],
            "increased",
            [],
            [],
            False,
        ),
    ),
    (
        "./run.sh 1 2",
        "./run.sh 1 3",
        _delta(
            False, "numeric", 0, [(1, "2", "3")], "increased", [], [], False
        ),
    ),
    (
        "./run.sh 0x1F 5",
        "./run.sh 0x0A 9",
        _delta(
            False,
            "numeric",
            0,
            [(0, "0x1F", "0x0A"), (1, "5", "9")],
            "mixed",
            [],
            [],
            False,
        ),
    ),
    (
        "./x 0x0A",
        "./x 0x1F",
        _delta(
            False,
            "numeric",
            0,
            [(0, "0x0A", "0x1F")],
            "increased",
            [],
            [],
            False,
        ),
    ),
    (
        "./run.sh --lr 0.5",
        "./run.sh --lr 0.25",
        _delta(
            False,
            "numeric",
            0,
            [(0, "0.5", "0.25")],
            "decreased",
            [],
            [],
            False,
        ),
    ),
    (
        "./run.py a",
        "./run.py a b",
        _delta(False, "count", 1, [], None, [], ["b"], False),
    ),
    (
        "./run.py a b c",
        "./run.py a",
        _delta(False, "count", -2, [], None, ["b", "c"], [], True),
    ),
    (
        "./run.py a b",
        "./run.py c",
        _delta(False, "count", -1, [], None, ["a", "b"], ["c"], False),
    ),
    (
        "./run.py go x",
        "./run.py go y",
        _delta(False, "words", 0, [], None, ["x"], ["y"], False),
    ),
    # Shapes differ and the count is equal: words, not numeric (RR3).
    (
        "./a.sh x 5",
        "./a.sh y 7",
        _delta(False, "words", 0, [], None, ["x"], ["y"], False),
    ),
    # A count change with equal numerals lists no numeric change (RR4).
    (
        "./run.sh --n 5",
        "./run.sh --n 5 --fast",
        _delta(False, "count", 1, [], None, [], ["--fast"], False),
    ),
    # Directory changes are invisible: shapes keep basenames (RR3).
    (
        "./run.py /a/x.csv",
        "./run.py /b/x.csv",
        _delta(False, "none", 0, [], None, [], [], False),
    ),
    # Heredoc numerals are not arguments (RR4, layer 2 SHP6).
    (
        "python3 g.py <<EOF\nn = 1\nEOF",
        "python3 g.py <<EOF\nn = 2\nEOF",
        _delta(False, "none", 0, [], None, [], [], False),
    ),
)


@pytest.mark.parametrize(("prev", "cur", "expected"), DELTAS)
def test_rerun_delta(prev: str, cur: str, expected: dict[str, Any]) -> None:
    """RR3, RR4: the argument-delta fields of a rerun edge.

    The rows are call_facts rows with the call's command_norm added (the
    exact key and the words are read from the call's text).
    """
    got = _layer3().rerun_delta(_facts_row(prev), _facts_row(cur))
    assert conftest.partial_mismatches(got, expected) == []


def test_rerun_delta_precedence() -> None:
    """RR3's partition is exclusive: one arg_change_type per pair."""
    for prev, cur, expected in DELTAS:
        got = _layer3().rerun_delta(_facts_row(prev), _facts_row(cur))
        assert got["arg_change_type"] == expected["arg_change_type"]
        if got["identical"]:
            assert got["arg_change_type"] == "none"
        assert (got["arg_change_type"] == "numeric") == bool(
            got["numeric_changes"]
        )


# (path, baseline mode, (file class, rule)): FC0 to FC11, §6.6.
FILE_CLASSES = (
    (".github/workflows/x.yml", None, ("ci", "FC1")),
    (".circleci/config.yml", None, ("ci", "FC1")),
    (".gitlab-ci.yml", None, ("ci", "FC1")),
    ("Jenkinsfile", None, ("ci", "FC1")),
    ("CMakeLists.txt", None, ("build", "FC2")),
    ("requirements-dev.txt", None, ("build", "FC2")),
    ("requirements.txt", None, ("build", "FC2")),
    ("Makefile", 0o755, ("build", "FC2")),
    ("Makefile.am", None, ("build", "FC2")),
    ("Dockerfile.gpu", None, ("build", "FC2")),
    ("docker-compose.dev.yml", None, ("build", "FC2")),
    ("pyproject.toml", None, ("build", "FC2")),
    ("package.json", None, ("build", "FC2")),
    (".gitignore", None, ("build", "FC2")),
    ("SConstruct", 0o600, ("build", "FC2")),
    ("cmake/x.cmake", None, ("build", "FC2")),
    ("gradle/wrapper/x.jar", None, ("build", "FC2")),
    ("lib/a.so", None, ("build", "FC2")),
    ("obj/a.o", None, ("build", "FC2")),
    ("src/__pycache__/a.cpython-311.pyc", None, ("build", "FC2")),
    ("x.ipynb", None, ("notebook", "FC3")),
    ("results/x.ipynb", None, ("notebook", "FC3")),
    ("tests/test_a.py", None, ("test", "FC4")),
    ("src/a_test.go", None, ("test", "FC4")),
    ("test_a.py", None, ("test", "FC4")),
    ("conftest.py", None, ("test", "FC4")),
    ("web/a.test.js", None, ("test", "FC4")),
    ("lib/a_spec.rb", None, ("test", "FC4")),
    ("src/__tests__/a.ts", None, ("test", "FC4")),
    ("Testing/a.c", None, ("test", "FC4")),
    ("tests/run", 0o755, ("test", "FC4")),
    ("tests/x.csv", None, ("data", "FC10")),
    ("tests/README.md", None, ("docs", "FC6")),
    ("src/a.py", None, ("code", "FC5")),
    ("src/A.PY", None, ("code", "FC5")),
    ("src/lib.rs", None, ("code", "FC5")),
    ("src/Main.java", None, ("code", "FC5")),
    ("include/a.h", None, ("code", "FC5")),
    ("proofs/x.lean", None, ("code", "FC5")),
    ("specs/x.dfy", None, ("code", "FC5")),
    ("x.sh", None, ("code", "FC5")),
    ("results/run.py", None, ("code", "FC5")),
    ("run", 0o755, ("code", "FC5")),
    ("bin/tool", 0o700, ("code", "FC5")),
    ("run", 0o644, ("other", "FC11")),
    ("run", None, ("other", "FC11")),
    ("README", None, ("docs", "FC6")),
    ("readme.txt", None, ("docs", "FC6")),
    ("LICENSE", None, ("docs", "FC6")),
    ("CHANGELOG.md", None, ("docs", "FC6")),
    ("docs/notes.txt", None, ("docs", "FC6")),
    ("docs/notes", None, ("docs", "FC6")),
    ("doc/fig.png", None, ("docs", "FC6")),
    ("docs/fig.png", None, ("docs", "FC6")),
    ("docs/FIG.PNG", None, ("docs", "FC6")),
    ("NOTES.txt", None, ("docs", "FC6")),
    ("paper/main.tex", None, ("docs", "FC6")),
    ("results/index.html", None, ("docs", "FC6")),
    ("outputs/report.md", None, ("docs", "FC6")),
    ("results/plot.png", None, ("data", "FC8")),
    ("data/x.dat", None, ("data", "FC8")),
    ("results/summary.json", None, ("data", "FC8")),
    ("Outputs/run.log", None, ("data", "FC8")),
    ("logs/train", None, ("data", "FC8")),
    ("icons/flag.png", None, ("data", "FC10")),
    ("assets/x.zip", None, ("data", "FC10")),
    ("notes/a.txt", None, ("data", "FC10")),
    ("x.csv", None, ("data", "FC10")),
    ("paper/fig.pdf", None, ("data", "FC10")),
    ("conf/app.json", None, ("config", "FC9")),
    ("settings.yaml", None, ("config", "FC9")),
    ("src/model.xyz", None, ("other", "FC11")),
    ("src/x.mir", None, ("other", "FC11")),
    # FC0: outside the workspace, the same rules on basename and folders.
    ("/tmp/x.py", None, ("code", "FC5")),
    ("/tmp/notes.txt", None, ("data", "FC10")),
)


# Guard rows: neighbours of the cycle-2 fixes that must not change.
FILE_CLASS_GUARDS = (
    ("pkg/README-dev.txt", None, ("docs", "FC6")),
    ("pkg/README_zh.md", None, ("docs", "FC6")),
    ("pkg/LICENSE-MIT", None, ("docs", "FC6")),
    ("pkg/Changes", None, ("docs", "FC6")),
    ("pkg/CONTRIBUTING.rst", None, ("docs", "FC6")),
    ("logs/train.log.1", None, ("data", "FC8")),  # a rotated log
)
# FC2 and FC6 additions and reading S9, adopted in cycle 2.
FILE_CLASS_FIXES = (
    # FC6 is a whole-name rule ^(README|…|INSTALL)([._-].*)?$ …
    ("pkg.dist-info/INSTALLER", None, ("other", "FC11")),
    ("vendor/NOTICES.txt", None, ("docs", "FC6")),  # D60 reverses D52
    ("pkg/READMEFIRST", None, ("other", "FC11")),
    # … that applies only with no extension, .txt or a docs extension (S9).
    ("HISTORY.json", None, ("config", "FC9")),
    ("INSTALL.cfg", None, ("config", "FC9")),
    ("results/README.csv", None, ("data", "FC8")),
    ("pkg/LICENSE.xml", None, ("config", "FC9")),
    # FC2 adds build.gradle.kts, gradlew.bat and Gemfile.lock.
    ("build.gradle.kts", None, ("build", "FC2")),
    ("app/build.gradle.kts", None, ("build", "FC2")),
    ("gradlew.bat", None, ("build", "FC2")),
    ("Gemfile.lock", None, ("build", "FC2")),
    # FC6 adds .1-style man pages.
    ("man/tool.1", None, ("docs", "FC6")),
    ("tool.8", None, ("docs", "FC6")),
    # Corpus cases of the cycle-2 writer's readings (D53, D54): a README
    # with an extension no other class claims stays docs; a versioned
    # program and a test input under a data folder are no man pages.
    ("pkg/README.dns", None, ("docs", "FC6")),
    ("pkg/README.platform", None, ("docs", "FC6")),
    (".venv/bin/f2py3.8", None, ("other", "FC11")),
    (".venv/bin/python3.8", 0o755, ("other", "FC11")),
    ("test/Object/Inputs/macho-load-command.1", None, ("data", "FC8")),
)
# Cycle-3 FC6 readings (D60) and the rows the mutants survived.
FILE_CLASS_CYCLE3 = (
    # The [._-]… suffix form needs a capital first letter; the bare word
    # is any case.
    ("test/MachO/install-name-tool-add-rpath.test", None, ("other", "FC11")),
    ("pkg/readme.dns", None, ("other", "FC11")),
    ("logs/history.txt", None, ("data", "FC8")),
    ("OpenBLAS/Changelog.txt", None, ("docs", "FC6")),
    ("pkg/License-MIT", None, ("docs", "FC6")),
    ("pkg/license", None, ("docs", "FC6")),
    ("pkg/Readme", None, ("docs", "FC6")),
    ("pkg/LICENSE.dns", None, ("docs", "FC6")),
    # NOTICES joins the words.
    ("NOTICES", None, ("docs", "FC6")),
    ("pkg/NOTICES-third-party", None, ("docs", "FC6")),
    ("pkg/NOTICESX", None, ("other", "FC11")),
    # Man pages: section 1 to 9 only, one dot (test gap 9).
    ("pkg/foo.0", None, ("other", "FC11")),
    ("lib/libz.so.1", None, ("other", "FC11")),
)


@pytest.mark.parametrize(
    ("path", "mode", "expected"),
    (*FILE_CLASSES, *FILE_CLASS_GUARDS, *FILE_CLASS_FIXES, *FILE_CLASS_CYCLE3),
)
def test_file_class(
    path: str, mode: int | None, expected: tuple[str, str]
) -> None:
    """FC0 to FC11 in order, first match wins (FC7 retired)."""
    assert _layer3().file_class(path, mode) == expected


def test_file_class_reads_no_content() -> None:
    """D31: the class is a function of the path and the mode only."""
    params = list(inspect.signature(_layer3().file_class).parameters)
    assert params == ["path", "mode"]
    classes = {cls for _, _, (cls, _) in FILE_CLASSES}
    assert "binary" not in classes
    assert classes <= set(structure_helpers.FILE_CLASSES)


def _scenario(raw: dict[str, Any]) -> dict[str, Any]:
    return structure_helpers.build_scenario(
        {"name": "unit", "description": "unit", "rules": ["CLS1"], **raw}
    )


def test_extension_field() -> None:
    """§5.3: the extension field of file_classes rows.

    §5.3: lower-case suffix of the basename; "" when none, also for a
    leading-dot name.
    """
    scenario = _scenario(
        {
            "calls": [{"call_id": "c1", "command": "./x.sh", "t_start": 10}],
            "execs": [
                {"command_id": "e1", "call": "c1", "t_start": 10.1},
            ],
            "links": [
                {"link_type": "write_event", "path": p, "writer_id": "e1"}
                for p in ("src/A.PY", ".gitignore", "data/a.tar.gz", "bin/run")
            ],
        }
    )
    rows = {
        r["path"]: r["extension"]
        for r in structure_helpers.run_derive(scenario)["file_classes"]
    }
    assert rows == {
        "src/A.PY": ".py",
        ".gitignore": "",
        "data/a.tar.gz": ".gz",
        "bin/run": "",
    }


TEXT_PATCH = (
    "diff --git a/src/a.py b/src/a.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -1 +1 @@\n"
    "-x = 1\n"
    "+x = 2\n"
)
BINARY_PATCH = (
    "diff --git a/data/x.bin b/data/x.bin\n"
    "index 1111111..2222222 100644\n"
    "GIT binary patch\n"
    "literal 4\n"
    "LcmZ?wWME|g00961\n"
    "\n"
    "literal 0\n"
    "HcmV?d00001\n"
    "\n"
)
BINARY_LINE = (
    "diff --git a/img/a.png b/img/a.png\n"
    "index 3333333..4444444 100644\n"
    "Binary files a/img/a.png and b/img/a.png differ\n"
)
CREATED = (
    "diff --git a/new.txt b/new.txt\n"
    "new file mode 100644\n"
    "index 0000000..1111111\n"
    "--- /dev/null\n"
    "+++ b/new.txt\n"
    "@@ -0,0 +1 @@\n"
    "+x\n"
)
CREATED_BINARY = (
    "diff --git a/new.bin b/new.bin\n"
    "new file mode 100644\n"
    "index 0000000..1111111\n"
    "Binary files /dev/null and b/new.bin differ\n"
)
DELETED = (
    "diff --git a/old.txt b/old.txt\n"
    "deleted file mode 100644\n"
    "index 1111111..0000000\n"
    "--- a/old.txt\n"
    "+++ /dev/null\n"
    "@@ -1 +0,0 @@\n"
    "-x\n"
)
RENAMED = (
    "diff --git a/old.py b/new.py\n"
    "similarity index 90%\n"
    "rename from old.py\n"
    "rename to new.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/old.py\n"
    "+++ b/new.py\n"
    "@@ -1 +1 @@\n"
    "-a\n"
    "+b\n"
)
QUOTED = (
    'diff --git "a/my file.txt" "b/my file.txt"\n'
    "index 1111111..2222222 100644\n"
    '--- "a/my file.txt"\n'
    '+++ "b/my file.txt"\n'
    "@@ -1 +1 @@\n"
    "-a\n"
    "+b\n"
)
MODE_ONLY = "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"

# (diff text, expected map): BIN1.
BINARY_SECTIONS = (
    (BINARY_PATCH, {"data/x.bin": True}),
    (BINARY_LINE, {"img/a.png": True}),
    (TEXT_PATCH, {"src/a.py": False}),
    (CREATED, {"new.txt": False}),
    (CREATED_BINARY, {"new.bin": True}),
    (DELETED, {"old.txt": False}),
    (RENAMED, {"new.py": False}),
    (QUOTED, {"my file.txt": False}),
    (MODE_ONLY, {"run.sh": False}),
    (
        TEXT_PATCH + BINARY_PATCH + BINARY_LINE,
        {"src/a.py": False, "data/x.bin": True, "img/a.png": True},
    ),
    ("", {}),
    ("not a diff\njust text\n", {}),
)


@pytest.mark.parametrize(("text", "expected"), BINARY_SECTIONS)
def test_binary_sections(text: str, expected: dict[str, bool]) -> None:
    """BIN1: whether each section of the final diff is binary.

    BIN1: each path of the final diff to whether its section is
    binary.
    """
    assert _layer3().binary_sections(text) == expected


def _structure(voided: bool) -> dict[str, Any]:
    """A structure.json value, computed or with M2.2 and dicts voided."""
    classes = {"code": 1, "data": 2}
    metrics = {
        "M2.1": {
            "shell_calls": 5,
            "act_calls": 3,
            "observe_calls": 2,
            "shape_classes": 4,
            "repeated_shapes_all": 1,
            "repeat_max_all": 2,
            "repeated_shapes_act": 1,
            "repeat_max_act": 2,
            "exact_repeat_max_act": 2,
            "program_classes_act": 2,
            "repeated_programs_act": 1,
            "program_repeat_max_act": 2,
        },
        "M2.2": dict.fromkeys(
            (
                *("shape_reruns", "reruns_after_failure_under"),
                *("reruns_after_failure_over", "reruns_after_success_under"),
                *("reruns_after_success_over", "reruns_after_unknown"),
                "reruns_after_failure_unchanged_under",
                "reruns_after_failure_unchanged_over",
                "reruns_after_success_unchanged_under",
                "reruns_after_success_unchanged_over",
            ),
            None if voided else 1,
        ),
        "M2.3": dict.fromkeys(
            (
                *("program_reruns", "args_unchanged", "numeric_changed"),
                *("count_changed", "words_changed", "numeric_decreased"),
                *("numeric_increased", "count_decreased", "count_increased"),
                "narrowed",
            ),
            0,
        ),
        "M4.2": {
            "changed_by_class": classes,
            "mode_only_by_class": {},
            "reverted_by_class": None if voided else {"code": 1},
            "changed_by_class_and_role_under": None
            if voided
            else {"code": {"root_exec": 1}},
            "changed_by_class_and_role_over": None
            if voided
            else {"code": {"root_exec": 1}, "data": {"edit_call": 2}},
            "shell_written_by_class_under": None if voided else {"code": 1},
            "shell_written_by_class_over": None if voided else {"code": 1},
            "created_by_class_under": None,
            "created_by_class_over": None,
            "baseline_by_class": {"code": 10, "docs": 2},
            "binary_changed_by_class": None,
        },
    }
    return {
        "run_key": "art__agent__1",
        "family": "claude",
        "preconditions": {
            "log": "holds",
            "C1_under": "fails" if voided else "holds",
            "final_diff": "holds",
            "C6": "fails" if voided else "holds",
            "C7_under": "holds",
            "untracked_visible": "fails",
            "tracked_files": "holds",
            "final_content": "fails",
        },
        "capabilities": dict.fromkeys(conftest.CAPABILITIES, False)
        | {"parent_links": True},
        "bases": {
            "gap": "fallback",
            "unchanged_under": "fallback",
            "writes_between": "exact",
        },
        "metrics": metrics,
        "warnings": ["first warning", "second warning"],
    }


def _csv_row(structure_json: dict[str, Any]) -> dict[str, Any]:
    """One structure.csv row, as cli.main builds it (R9)."""
    from trajectory_checks import cli

    return cli.facts_row(structure_json, _layer3().DICT_FIELDS)


def test_csv_row_flattens() -> None:
    """§5.5: one row flattens structure.json.

    §5.5: identity, preconditions, capabilities, bases, scalars, class
    dicts one column per class with 0 when absent, warnings joined.
    """
    row = _csv_row(_structure(voided=False))
    assert row["run_key"] == "art__agent__1"
    assert row["family"] == "claude"
    assert row["preconditions.log"] == "holds"
    assert row["preconditions.final_content"] == "fails"
    assert row["capabilities.parent_links"] is True
    assert row["capabilities.egress_log"] is False
    assert row["bases.writes_between"] == "exact"
    assert row["bases.gap"] == "fallback"
    assert row["M2.1.repeat_max_act"] == 2
    assert row["M2.2.shape_reruns"] == 1
    for cls in structure_helpers.FILE_CLASSES:
        want = {"code": 1, "data": 2}.get(cls, 0)
        assert row[f"M4.2.changed_by_class.{cls}"] == want, cls
        assert row[f"M4.2.mode_only_by_class.{cls}"] == 0
        for role in structure_helpers.ROLES:
            key = f"M4.2.changed_by_class_and_role_over.{cls}.{role}"
            want = {("code", "root_exec"): 1, ("data", "edit_call"): 2}
            assert row[key] == want.get((cls, role), 0), key
    assert row["warnings"] == "first warning;second warning"
    assert list(row)[:2] == ["run_key", "family"]
    assert list(row)[-1] == "warnings"


def test_csv_row_void_cells() -> None:
    """§5.5: null blocks and class dicts become empty cells.

    §5.5: a null block or class dict is written as empty cells, with
    the same columns as a computed one.
    """
    held = _csv_row(_structure(voided=False))
    voided = _csv_row(_structure(voided=True))
    assert list(voided) == list(held)
    assert voided["M2.2.shape_reruns"] is None
    for cls in structure_helpers.FILE_CLASSES:
        assert voided[f"M4.2.reverted_by_class.{cls}"] is None
        assert voided[f"M4.2.created_by_class_under.{cls}"] is None
        assert held[f"M4.2.created_by_class_under.{cls}"] is None
        assert voided[f"M4.2.binary_changed_by_class.{cls}"] is None
        for role in structure_helpers.ROLES:
            key = f"M4.2.changed_by_class_and_role_under.{cls}.{role}"
            assert voided[key] is None
    for value in held.values():
        assert not isinstance(value, list | dict)


def test_csv_row_columns() -> None:
    """§5.5: the column set of a structure.csv row.

    §5.5: every precondition, capability and basis, every scalar key,
    nine columns per class dict and nine times three per role split.
    """
    row = _csv_row(_structure(voided=False))
    metric_columns = [k for k in row if k.startswith("M")]
    scalars = 12 + 10 + 10
    class_dicts = 9 * 9  # nine class dicts of M4.2 without the role splits
    role_dicts = 2 * 9 * 3
    assert len(metric_columns) == scalars + class_dicts + role_dicts
    assert len([k for k in row if k.startswith("preconditions.")]) == 8
    assert len([k for k in row if k.startswith("capabilities.")]) == 10
    assert len([k for k in row if k.startswith("bases.")]) == 3


def test_peer_ratios_on_structure_rows() -> None:
    """T20: layer 2's PEER1 on structure.csv rows of one artifact."""
    rows = []
    for run, value in (("1", 2), ("2", 4), ("3", 6)):
        structure_json = _structure(voided=False)
        structure_json["run_key"] = f"art__agent__{run}"
        structure_json["metrics"]["M2.1"]["repeat_max_act"] = value
        rows.append(_csv_row(structure_json))
    voided = _structure(voided=True)
    voided["run_key"] = "other__agent__1"
    rows.append(_csv_row(voided))
    out = facts.peer_ratios(rows)
    assert out[0]["M2.1.repeat_max_act_peer_ratio"] == 0.4  # 2 / median(4, 6)
    assert out[2]["M2.1.repeat_max_act_peer_ratio"] == 2.0  # 6 / median(2, 4)
    assert out[3]["M2.1.repeat_max_act_peer_ratio"] is None  # no peer
    assert out[3]["M2.2.shape_reruns_peer_ratio"] is None
    ratio_keys = {k for k in out[0] if k.endswith("_peer_ratio")}
    assert ratio_keys == {
        f"{k}_peer_ratio" for k in rows[0] if k.startswith("M")
    }


def test_defaults() -> None:
    """§6.12: the defaults of this layer.

    §6.12: the recursion depth; the slop is layer 1's, not this
    layer's.
    """
    assert _layer3().DEFAULTS["recursion_depth"] == 3
    assert "snapshot_slop_s" not in _layer3().DEFAULTS


def _derived(raw: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return structure_helpers.run_derive(_scenario(raw), **overrides)


SHALLOW = {
    "calls": [
        {"call_id": "c1", "command": "bash -c \"bash -c 'ls'\"", "t_start": 10}
    ],
}


def test_recursion_depth_override() -> None:
    """§6.12: config recursion_depth bounds OBS3's recursion."""
    row = _derived(SHALLOW)["call_structure"][0]
    assert row["call_class"] == "observe"
    row = _derived(SHALLOW, config={"recursion_depth": 1})["call_structure"][0]
    assert (row["call_class"], row["class_reason"]) == ("act", "program")


# An exec of another root that ends 1 s before the gap opens: inside the
# 2 s slop, so it overlaps the gap; with a slop of 0 it does not (RR5).
SLOP = {
    "calls": [
        {"call_id": "c1", "command": "./run.sh", "t_start": 10, "t_end": 21},
        {"call_id": "c2", "command": "./prep.sh", "t_start": 12, "t_end": 20},
        {"call_id": "c3", "command": "./run.sh", "t_start": 30, "t_end": 32},
    ],
    "execs": [
        {"command_id": "e1", "call": "c1", "t_start": 10.1, "t_end": 20.0},
        {
            "command_id": "e2",
            "call": "c2",
            "t_start": 12.1,
            "t_end": 19.0,
            "snapshot": {"status": "skipped", "scope": None},
        },
        {"command_id": "e3", "call": "c3", "t_start": 30.1, "t_end": 31.9},
    ],
}


def _shape_edge(result: dict[str, Any]) -> dict[str, Any]:
    return next(r for r in result["reruns"] if r["key_type"] == "shape")


def test_snapshot_slop_widens_overlap() -> None:
    """RR5: overlap tests widen the end by layer 1's snapshot_slop_s."""
    assert _shape_edge(_derived(SLOP))["gap_observed"] is False
    edge = _shape_edge(_derived(SLOP, config={"snapshot_slop_s": 0.0}))
    assert edge["gap_observed"] is True


def test_missing_capability_keys_read_as_false() -> None:
    """Missing capability keys read as false.

    An old RunInfo without some capability keys: every rule takes its
    fallback form and structure.json lists all nine capabilities, false.
    """
    scenario = _scenario(copy.deepcopy(SLOP))
    run_info = copy.deepcopy(scenario["run_info"])
    run_info["capabilities"] = {}
    structure_json = structure_helpers.run_derive(scenario, run_info=run_info)[
        "structure"
    ]
    assert structure_json["capabilities"] == dict.fromkeys(
        conftest.CAPABILITIES, False
    )
    assert set(structure_json["bases"].values()) == {"fallback"}


def test_function_signatures() -> None:
    """§3.4: the public interface the CLI and layers 5 and 6 rely on."""
    derive = inspect.signature(_layer3().derive)
    assert list(derive.parameters)[:4] == [
        "calls",
        "execs",
        "linked",
        "facts_result",
    ]
    for name in ("run_info", "baseline", "final_diff_text", "config"):
        assert derive.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    for name in ("baseline", "final_diff_text", "config"):
        assert derive.parameters[name].default is None
    for name in (
        *("CallStructure", "Rerun", "FileClass", "RunStructure"),
        "StructureResult",
    ):
        assert hasattr(_layer3(), name), name


def _untimed(which: int) -> dict[str, Any]:
    """Two reruns, one call with no time and no root exec (A2, S5).

    ``which`` is the index of the untimed call. A third exec of no call
    makes the overlap test of RR5 run.
    """
    scenario = _scenario(
        {
            "calls": [
                {"call_id": "c1", "command": "./run.sh", "t_start": 10},
                {"call_id": "c2", "command": "./run.sh", "t_start": 30},
            ],
            "execs": [
                {
                    "command_id": f"e{2 - which}",
                    "call": f"c{2 - which}",
                    "t_start": 10.1 + 20 * (1 - which),
                    "t_end": 11.9 + 20 * (1 - which),
                },
                {
                    "command_id": "e9",
                    "command": "touch x",
                    "t_start": 20,
                    "t_end": 21,
                },
            ],
        }
    )
    scenario["calls"][which].update(t_start=None, t_end=None)
    return scenario


@pytest.mark.parametrize(
    "which",
    [
        pytest.param(0, id="previous-untimed"),
        pytest.param(1, id="next-untimed"),
    ],
)
def test_untimed_call_has_an_unknown_gap(which: int) -> None:
    """S5: a call with no time and no root exec has an unknown gap.

    ``parse.Call.t_start`` may be None. derive must not raise; the gap
    is unknown, so gap_observed and unchanged_under are false and
    unchanged_over is read from edit_calls_between alone.
    """
    scenario = _untimed(which)
    assert scenario["facts_result"]["call_facts"][which]["t_start"] is None
    result = structure_helpers.run_derive(scenario)
    assert len(result["reruns"]) == 2
    for edge in result["reruns"]:
        assert edge["gap_observed"] is False, edge
        assert edge["unchanged_under"] is False, edge
        assert edge["edit_calls_between"] == 0
        assert edge["unchanged_over"] is True, edge


# The run is untracked-visible by scope, so unchanged_under stays exact
# (D47) although one ok snapshot lacks the MC-05 lists (A8).
SCOPE_VISIBLE = {
    "run": {"capabilities": {"untracked_visible": True}},
    "coverage": {
        "rates.untracked_visible": True,
        "rates.snapshot_scope": "workspace",
    },
    "calls": [
        {"call_id": "c1", "command": "./run.sh", "t_start": 10},
        {"call_id": "c2", "command": "./run.sh", "t_start": 30},
    ],
    "execs": [
        {
            "command_id": "e1",
            "call": "c1",
            "t_start": 10.1,
            "t_end": 10.9,
            "snapshot": {
                "scope": "workspace",
                "created_untracked": [],
                "modified_untracked": [],
                "deleted_untracked": [],
            },
        },
        {
            "command_id": "e2",
            "call": "c2",
            "t_start": 30.1,
            "t_end": 30.9,
            "snapshot": {"scope": "workspace"},
        },
    ],
}


def test_pre8_warning_does_not_claim_a_fallback_it_did_not_use() -> None:
    """A8: PRE8's text says "fallback used" only when the basis fell back.

    In a run untracked-visible by scope the unchanged_under basis stays
    exact (D47), so the warning still reports the missing field but must
    not say a fallback was used.
    """
    scenario = _scenario(copy.deepcopy(SCOPE_VISIBLE))
    problems = structure_helpers.validate(scenario, "unit")
    assert [p for p in problems if "stem" not in p] == []
    structure_json = structure_helpers.run_derive(scenario)["structure"]
    assert structure_json["bases"]["unchanged_under"] == "exact"
    found = [
        w
        for w in structure_json["warnings"]
        if w.startswith(
            "capability untracked_visible true but created_untracked "
            "missing on 1 records"
        )
    ]
    assert len(found) == 1, structure_json["warnings"]
    assert "fallback used" not in found[0]


def _many_reruns(count: int) -> dict[str, Any]:
    """``count`` identical act calls, each with one other exec's write."""
    calls, execs, links = [], [], []
    for i in range(count):
        start = 10.0 * i
        calls.append(
            {
                "call_id": f"c{i}",
                "command": "./run.sh",
                "t_start": start,
                "t_end": start + 5,
            }
        )
        execs.append(
            {
                "command_id": f"e{i}",
                "call": f"c{i}",
                "t_start": start + 0.1,
                "t_end": start + 4.9,
            }
        )
        execs.append(
            {
                "command_id": f"n{i}",
                "command": "touch x",
                "t_start": start + 1,
                "t_end": start + 2,
            }
        )
        links.append(
            {
                "link_type": "write_event",
                "event_id": f"w{i}",
                "path": f"out/p{i}.dat",
                "writer_type": "exec",
                "writer_id": f"n{i}",
                "t_end": start + 2,
            }
        )
    return _scenario({"calls": calls, "execs": execs, "links": links})


def test_many_reruns_are_fast() -> None:
    """A7: 2,000 act reruns of one class derive in a few seconds.

    The bound is loose: the cycle-1 code takes about 7.5 s here, code that
    computes each program key once per shape and precomputes the write
    events, exec spans and edit seqs once takes about 2 s, and with
    bisection under 1 s.
    """
    scenario = _many_reruns(2000)
    started = time.perf_counter()
    result = structure_helpers.run_derive(scenario)
    elapsed = time.perf_counter() - started
    assert len(result["reruns"]) == 2 * 1999
    assert elapsed < 4.0, f"{elapsed:.1f} s"
