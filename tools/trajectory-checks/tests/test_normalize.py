"""Kind 1: table-driven tests of the pure functions in ``parse``.

``normalize`` and ``same_tokens`` follow spec §6.4, ``parse_time`` §2.1,
``unwrap`` §6.2 with decisions R1 and R2, ``noise_reason`` §6.3. The argv
values are copied from real trace records (shortened where marked).
"""

import json

import conftest
import pytest

from trajectory_checks import parse

# (command, expected command_norm)
NORMALIZE_CASES = (
    ("ls -la", "ls -la"),
    ("ls  -la", "ls -la"),
    ("ls\t-la", "ls -la"),
    ("ls \t  -la", "ls -la"),
    ("ls -la\n", "ls -la"),
    ("ls -la   ", "ls -la"),
    ("ls -la \n\n", "ls -la"),
    ("make a\r\nmake b", "make a\nmake b"),
    ("make a\rmake b", "make a\nmake b"),
    ("make a   \nmake b", "make a\nmake b"),
    ("echo 'a  b'", "echo 'a  b'"),
    ('echo "a  b"', 'echo "a  b"'),
    ('echo "a  b"  c', 'echo "a  b" c'),
    ("grep -n 'x\ty' f", "grep -n 'x\ty' f"),
    (
        "cat > run.py <<'EOF'\nif x:\n    y  =  1\nEOF",
        "cat > run.py <<'EOF'\nif x:\n    y  =  1\nEOF",
    ),
    (
        "python3 - <<PY\nprint( 'a' ,  1)\nPY\necho  done",
        "python3 - <<PY\nprint( 'a' ,  1)\nPY\necho done",
    ),
    ("", ""),
)

# (command, split_heredocs pieces); decision D35: a word-initial `#` outside
# quotes is a comment to the end of the line, and its quotes open nothing.
SPLIT_HEREDOCS_CASES = (
    (
        "# it's done\ncat > f <<'EOF'\nrm -rf x\nEOF",
        [
            ("shell", "# it's done"),
            ("shell", "cat > f <<'EOF'"),
            ("body", "rm -rf x"),
            ("body", "EOF"),
        ],
    ),
    (
        '# say "hi\ncat <<EOF\nrm -rf x\nEOF',
        [
            ("shell", '# say "hi'),
            ("shell", "cat <<EOF"),
            ("body", "rm -rf x"),
            ("body", "EOF"),
        ],
    ),
    (
        "make;#  it's\nls  -la",
        [("shell", "make;# it's"), ("shell", "ls -la")],
    ),
    # `<<` inside a comment opens no heredoc.
    (
        "ls  # see <<EOF\necho  x",
        [("shell", "ls # see <<EOF"), ("shell", "echo x")],
    ),
    # `#` inside a word is not a comment: the quote opened after it holds.
    (
        "echo a#b 'c\nd' <<EOF\nx\nEOF",
        [
            ("shell", "echo a#b 'c"),
            ("shell", "d' <<EOF"),
            ("body", "x"),
            ("body", "EOF"),
        ],
    ),
    (
        "echo 'a # b'  \"c # d\" <<EOF\nx\nEOF",
        [
            ("shell", "echo 'a # b' \"c # d\" <<EOF"),
            ("body", "x"),
            ("body", "EOF"),
        ],
    ),
    (
        "echo $#  ${#x}  ${x#y} <<EOF\nx\nEOF",
        [
            ("shell", "echo $# ${#x} ${x#y} <<EOF"),
            ("body", "x"),
            ("body", "EOF"),
        ],
    ),
    (
        "cat <<-'EOF'\n\tit's\n\tEOF\necho  x",
        [
            ("shell", "cat <<-'EOF'"),
            ("body", "\tit's"),
            ("body", "\tEOF"),
            ("shell", "echo x"),
        ],
    ),
)

# (a, b, same tokens?)
SAME_TOKENS_CASES = (
    ("echo 'a b'", 'echo "a b"', True),
    ('echo "a b"', "echo a\\ b", True),
    ("echo 'a b'", "echo a\\ b", True),
    ("echo a b", "echo 'a b'", False),
    ("ls  -la", "ls -la", True),
    ("make&&make test", "make && make test", True),
    ("a|b", "a | b", True),
    ("echo x>out", "echo x > out", True),
    ("ls -la", "ls -l", False),
    (
        "python3 - <<'PY'\nprint('x')\nPY",
        "python3 - <<\"PY\"\nprint('x')\nPY",
        True,
    ),
    (
        "cat <<'EOF'\nx y\nEOF",
        "cat <<'EOF'\nx  y\nEOF",
        False,
    ),
    ("echo 'unterminated", "echo unterminated", False),
)

# (value, epoch seconds or None)
PARSE_TIME_CASES = (
    ("2026-10-12T16:43:26Z", 1791823406.0),
    ("2026-10-12T16:43:26", 1791823406.0),
    ("2026-10-12T16:43:26.178064+00:00", 1791823406.178064),
    ("2026-10-12T16:43:27.464Z", 1791823407.464),
    ("2026-10-12T18:43:26+02:00", 1791823406.0),
    ("2026-11-03T08:15:30Z", 1793693730.0),
    ("", None),
    ("not a time", None),
    ("2026-13-45T99:00:00Z", None),
    (None, None),
)

_CLAUDE_PREFIX = (
    "source /run/aebench-agent/.claude/shell-snapshots/"
    "snapshot-bash-1791823058123-7r8tgt.sh 2>/dev/null || true && "
    "shopt -u extglob 2>/dev/null || true && "
)
_ZCODE_PROLOGUE = (
    ". '/run/aebench-agent/state/storage/cli/exec/shell-snapshots/"
    "snapshot-bash-1793975918063-es6noz.sh' 2>/dev/null || true\n"
    ". '/run/aebench-agent/state/storage/cli/exec/bash-startup/"
    "sess_0cad0c33-b149-49d2-af75-3ebb52e0c1aa/"
    "embedded-search-startup-d5b33321d1d4a07f.sh'\n"
)
_ZCODE_EPILOGUE = (
    "\n__zcode_status=$?\n"
    'if [ "$__zcode_status" -eq 0 ]; then pwd -P > '
    "'/tmp/zcode-b3f9e4c5-816d-4937-b5b7-7928e3cdb9da-cwd'; fi\n"
    'exit "$__zcode_status"'
)
# Shortened: the real preamble saves and restores about 60 variables.
_CODEX_PREAMBLE = (
    '__CODEX_SNAPSHOT_OVERRIDE_SET_0="${CODEX_THREAD_ID+x}"\n'
    '__CODEX_SNAPSHOT_OVERRIDE_0="${CODEX_THREAD_ID-}"\n'
    "builtin unset __CODEX_SNAPSHOT_OVERRIDE_SET_0 "
    "__CODEX_SNAPSHOT_OVERRIDE_0 2>/dev/null\n\n"
)

# (argv, family, (command, wrapper))
UNWRAP_CASES = (
    (
        [
            "/bin/bash",
            "-c",
            _CLAUDE_PREFIX + "eval 'sleep 1; echo waiting' < /dev/null && "
            "pwd -P >| /tmp/claude-7f22-cwd",
        ],
        "claude",
        ("sleep 1; echo waiting", "claude_eval"),
    ),
    (
        [
            "/bin/bash",
            "-c",
            "-l",
            "shopt -u extglob 2>/dev/null || true && eval 'ls -la' "
            "< /dev/null && pwd -P >| /tmp/claude-6559-cwd",
        ],
        "claude",
        ("ls -la", "claude_eval"),
    ),
    (
        [
            "/bin/bash",
            "-c",
            "-l",
            "shopt -u extglob 2>/dev/null || true && eval 'sed -n "
            "'\"'\"'1416,1480p'\"'\"' /tmp/build.log | head -70' "
            "< /dev/null && pwd -P >| /tmp/claude-d73b-cwd",
        ],
        "claude",
        ("sed -n '1416,1480p' /tmp/build.log | head -70", "claude_eval"),
    ),
    (
        [
            "/bin/bash",
            "-c",
            _CLAUDE_PREFIX + "eval 'cat > /tmp/v.py <<'\"'\"'EOF'\"'\"'\n"
            "print(1)\nEOF\npython3 /tmp/v.py' && "
            "pwd -P >| /tmp/claude-1810-cwd",
        ],
        "claude",
        (
            "cat > /tmp/v.py <<'EOF'\nprint(1)\nEOF\npython3 /tmp/v.py",
            "claude_eval",
        ),
    ),
    (["/bin/bash", "-c", "env"], "claude", ("env", None)),
    (
        ["bash", "-c", "source gen_wrapper.sh"],
        "claude",
        ("source gen_wrapper.sh", None),
    ),
    (["/bin/bash", "-c", "pytest -q"], "codex", ("pytest -q", "codex_direct")),
    (
        [
            "/bin/bash",
            "-c",
            _CODEX_PREAMBLE + "exec '/bin/bash' -c 'pwd && rg --files -g "
            "'\"'\"'README.md'\"'\"' | sort && ls -la'",
        ],
        "codex",
        (
            "pwd && rg --files -g 'README.md' | sort && ls -la",
            "codex_snapshot_exec",
        ),
    ),
    (
        [
            "/usr/bin/bash",
            "-c",
            _ZCODE_PROLOGUE + "tail -15 /tmp/run.log" + _ZCODE_EPILOGUE,
        ],
        "zcode",
        ("tail -15 /tmp/run.log", "zcode_snapshot"),
    ),
    (
        [
            "/usr/bin/bash",
            "-c",
            _ZCODE_PROLOGUE + "make check\nmake install" + _ZCODE_EPILOGUE,
        ],
        "zcode",
        ("make check\nmake install", "zcode_snapshot"),
    ),
    (
        [
            "/usr/bin/bash",
            "-c",
            _ZCODE_PROLOGUE + "sudo python3 bench.py > /tmp/b.log 2>&1; "
            'echo "EXIT: $?" >> /tmp/b.log',
        ],
        "zcode",
        (
            'sudo python3 bench.py > /tmp/b.log 2>&1; echo "EXIT: $?" >> '
            "/tmp/b.log",
            "zcode_snapshot_bg",
        ),
    ),
    (["bash", "-c", "pwd && ls -la"], "deepseek", ("pwd && ls -la", None)),
    (["bash", "-lc", "make"], "deepseek", ("make", None)),
    # Decision R1: no -c, so the command is shlex.join(argv[1:]).
    (
        ["/bin/bash", "./run-verify-all.sh"],
        "claude",
        ("./run-verify-all.sh", None),
    ),
    (
        ["bash", "script.sh", "arg one"],
        "deepseek",
        ("script.sh 'arg one'", None),
    ),
    (["bash", "-n", "tools/check.sh"], "deepseek", ("-n tools/check.sh", None)),
)


@pytest.mark.parametrize(("command", "expected"), NORMALIZE_CASES)
def test_normalize(command: str, expected: str) -> None:
    """Line endings, trailing whitespace and blank runs (§6.4)."""
    assert parse.normalize(command) == expected


@pytest.mark.parametrize(("command", "_"), NORMALIZE_CASES)
def test_normalize_is_idempotent(command: str, _: str) -> None:
    """Normalising twice changes nothing."""
    once = parse.normalize(command)
    assert parse.normalize(once) == once


@pytest.mark.parametrize(("command", "expected"), SPLIT_HEREDOCS_CASES)
def test_split_heredocs(command: str, expected: list[tuple[str, str]]) -> None:
    """Heredoc bodies cut off; comments open no quote (§6.4, D35)."""
    assert list(parse.split_heredocs(command)) == expected


@pytest.mark.parametrize(("a", "b", "expected"), SAME_TOKENS_CASES)
def test_same_tokens(a: str, b: str, expected: bool) -> None:
    """POSIX tokens joined by one space; heredoc bodies verbatim (§6.4)."""
    assert parse.same_tokens(a, b) is expected
    assert parse.same_tokens(b, a) is expected


@pytest.mark.parametrize("command", [c for c, _ in NORMALIZE_CASES])
def test_same_tokens_reflexive(command: str) -> None:
    """A command has the same tokens as itself."""
    assert parse.same_tokens(command, command) is True


@pytest.mark.parametrize(("value", "expected"), PARSE_TIME_CASES)
def test_parse_time(value: str | None, expected: float | None) -> None:
    """ISO 8601 with or without ``Z``, offsets, fractions; bad input → None."""
    got = parse.parse_time(value)
    if expected is None:
        assert got is None
    else:
        assert conftest.close(got, expected)


@pytest.mark.parametrize(
    ("argv", "family", "expected"),
    UNWRAP_CASES,
    ids=[f"{c[1]}-{i}" for i, c in enumerate(UNWRAP_CASES)],
)
def test_unwrap(
    argv: list[str], family: str, expected: tuple[str, str | None]
) -> None:
    """Wrapper table of §6.2, decisions R1 (no ``-c``) and R2 (DeepSeek)."""
    assert parse.unwrap(argv, family) == expected


def _trace_command(name: str, index: int, family: str) -> tuple[str, str]:
    path = conftest.TRACE_FIXTURES_DIR / name
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[index])
    return parse.unwrap(record["argv"], family)


# (fixture file, line index, family, expected reason; ANY_NON_NULL = any)
NOISE_RECORDS = (
    ("claude_noise.jsonl", 0, "claude", "shim_probe"),
    ("claude_noise.jsonl", 1, "claude", "claude_env_probe"),
    ("claude_noise.jsonl", 2, "claude", conftest.ANY_NON_NULL),
    ("codex_run.jsonl", 0, "codex", "shim_probe"),
    ("codex_run.jsonl", 1, "codex", conftest.ANY_NON_NULL),
    ("codex_run.jsonl", 2, "codex", conftest.ANY_NON_NULL),
    ("zcode_snapshot_creation.jsonl", 0, "zcode", conftest.ANY_NON_NULL),
    ("deepseek_plain.jsonl", 0, "deepseek", "shim_probe"),
    ("workspace_scope_probe.jsonl", 0, "claude", "shim_probe"),
)


@pytest.mark.parametrize(
    ("name", "index", "family", "expected"),
    NOISE_RECORDS,
    ids=[f"{r[0]}-{r[1]}" for r in NOISE_RECORDS],
)
def test_noise_reason_on_real_noise(
    name: str, index: int, family: str, expected: str
) -> None:
    """Every harness noise pattern of §6.3 is recognised."""
    command, wrapper = _trace_command(name, index, family)
    reason = parse.noise_reason(command, wrapper, family)
    if expected == conftest.ANY_NON_NULL:
        assert isinstance(reason, str) and reason
    else:
        assert reason == expected


# Real agent commands that must not be noise.
NOT_NOISE_RECORDS = (
    ("codex_run.jsonl", 3, "codex"),
    ("codex_run.jsonl", 4, "codex"),
    ("claude_eval_skipped.jsonl", 0, "claude"),
    ("claude_nested_plain.jsonl", 0, "claude"),
    ("zcode_fg.jsonl", 0, "zcode"),
    ("zcode_bg.jsonl", 0, "zcode"),
    ("deepseek_plain.jsonl", 1, "deepseek"),
)


@pytest.mark.parametrize(
    ("name", "index", "family"),
    NOT_NOISE_RECORDS,
    ids=[f"{r[0]}-{r[1]}" for r in NOT_NOISE_RECORDS],
)
def test_noise_reason_on_real_agent_commands(
    name: str, index: int, family: str
) -> None:
    """Agent commands of the same runs are not noise."""
    command, wrapper = _trace_command(name, index, family)
    assert parse.noise_reason(command, wrapper, family) is None


# (command, wrapper, family): near-misses that must not be noise.
NEAR_MISSES = (
    ("env", "claude_eval", "claude"),
    ("env | sort", None, "claude"),
    ("env", None, "deepseek"),
    ("echo done", None, "claude"),
    ("grep -r AEBENCH_SHIM_PROBE_OK src/", "claude_eval", "claude"),
    (
        "echo AEBENCH_SHIM_PROBE_OK > probe.txt && cat probe.txt",
        None,
        "deepseek",
    ),
    ("echo '# Snapshot file' >> notes.md", "claude_eval", "claude"),
    ("set -e; . ./venv/bin/activate", "codex_direct", "codex"),
    ("SNAPSHOT_FILE=out.txt python3 snap.py", None, "deepseek"),
)


@pytest.mark.parametrize(("command", "wrapper", "family"), NEAR_MISSES)
def test_noise_reason_near_misses(
    command: str, wrapper: str | None, family: str
) -> None:
    """Commands that merely resemble harness noise are not noise."""
    assert parse.noise_reason(command, wrapper, family) is None
