"""T1: agent logs and the monitor trace to normalized Call and Exec records.

Each agent family has one parsing function in CALL_PARSERS; harness wrappers,
harness noise and tool names are data tables (WRAPPERS, NOISE, TOOL_TYPES).
Nothing here links a call to an exec: that is match.py's job.
"""

import datetime
import json
import logging
import pathlib
import re
import shlex
import types
import typing
from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)


class UnsupportedFormatError(ValueError):
    """The agent log or the trace is in a format no parser knows."""


class Snapshot(typing.TypedDict):
    """The per-command file snapshot of one trace record.

    The last four fields are None when the monitor did not record them
    (decision D26).
    """

    status: str  # ok, skipped, truncated, absent
    scope: str | None  # tracked, workspace, tracked+untracked
    created: list[str]
    modified: list[str]
    deleted: list[str]
    totals: dict[str, int] | None
    skip_reason: str | None
    hashes: dict[str, str] | None  # MC-07
    created_untracked: list[str] | None  # MC-05
    modified_untracked: list[str] | None
    deleted_untracked: list[str] | None


class Call(typing.TypedDict):
    """One agent tool call (spec §5.1)."""

    call_id: str
    seq: int
    family: str
    raw_name: str
    tool_type: str
    actor: str
    t_start: float | None
    t_end: float | None
    t_precision_s: float
    command: str | None
    command_norm: str | None
    background: bool | None
    timeout_s: float | None
    paths: list[str]
    edit_content: bool
    log_ok: bool | None
    log_exit_code: int | None


class Exec(typing.TypedDict):
    """One monitor trace record (spec §5.2, decisions F4, D26).

    Every field from env_keys on is None when the monitor did not record it.
    """

    command_id: str
    seq: int
    t_start: float
    t_end: float
    duration_s: float
    exit_code: int | None
    signal: int | None
    complete: bool
    cwd: str | None
    pid: int | None
    argv0: str | None
    command: str
    command_norm: str
    wrapper: str | None
    noise: str | None
    snapshot: Snapshot
    stdout_bytes: int | None
    stderr_bytes: int | None
    env_keys: list[str] | None
    schema_version: int | None  # MC-21
    parent_command_id: str | None  # MC-01
    root_command_id: str | None
    depth: int | None
    ppid: int | None
    pgid: int | None
    sid: int | None
    parent_link: str | None  # live, closed, adopted, none
    record_type: str | None  # MC-04: closed, flushed_at_stop, start_only
    released_at: float | None  # MC-10
    ended_at: float | None
    snapshot_before_s: float | None
    snapshot_after_s: float | None
    reads: list[str] | None  # MC-14
    orphans: list[dict] | None  # MC-01: {pid, comm} left running
    stdio: list[str | None] | None  # targets of fds 0, 1, 2


def _by_name(table: dict[str, str]) -> typing.Mapping[str, str]:
    """Inverts {value: "name name ..."} to an immutable {name: value}."""
    return types.MappingProxyType(
        {
            name: value
            for value, names in table.items()
            for name in names.split()
        }
    )


# Raw tool name -> tool_type (design §3.2); unknown names become "other".
TOOL_TYPES = _by_name(
    {
        "shell": "Bash bash command_execution",
        "read": "Read read read_image grep Grep Glob",
        "edit": "Edit MultiEdit NotebookEdit edit",
        "write": "Write write",
        "web": "web_search WebSearch WebFetch web_fetch webReader",
        "wait": "Monitor ScheduleWakeup TaskOutput BashOutput job_output "
        "job_list",
        "control": "TaskStop KillShell job_kill",
        "meta": "TodoWrite todo_write TaskCreate TaskUpdate ToolSearch "
        "AskUserQuestion",
    }
)

# (family, wrapper id, pattern on the -c script, group is shell-quoted).
# Tried in order; the first match wins (design §3.3, decision R2).
WRAPPERS = tuple(
    (family, wrapper, re.compile(pattern, re.S), quoted)
    for family, wrapper, pattern, quoted in (
        (
            "claude",
            "claude_eval",
            r"&& eval (?P<cmd>.*?)(?: < /dev/null)?"
            r" && pwd -P >\| /tmp/claude-[0-9a-f]+-cwd\Z",
            True,
        ),
        (
            "codex",
            "codex_snapshot_exec",
            r"\A__CODEX_SNAPSHOT_OVERRIDE_.*?"
            r"\nexec '[^']*bash' -c (?P<cmd>.*)\Z",
            True,
        ),
        ("codex", "codex_direct", r"\A(?P<cmd>.*)\Z", False),
        (
            "zcode",
            "zcode_snapshot",
            r"\A(?:\. '/run/aebench-agent/[^\n]*\n)+(?P<cmd>.*?)"
            r"\n__zcode_status=\$\?\n",
            False,
        ),
        (
            "zcode",
            "zcode_snapshot_bg",
            r"\A(?:\. '/run/aebench-agent/[^\n]*\n)+(?P<cmd>.*)\Z",
            False,
        ),
    )
)

# (family or None for any, reason, pattern on the unwrapped command, only
# when the record has no wrapper). Decided on text, never on timing (§6.3).
NOISE = tuple(
    (family, reason, re.compile(pattern, re.S), unwrapped_only)
    for family, reason, pattern, unwrapped_only in (
        (None, "shim_probe", r"\Aecho AEBENCH_SHIM_PROBE_OK\Z", False),
        (
            None,
            "snapshot_creation",
            r"\ASNAPSHOT_FILE=.*# Snapshot file",
            False,
        ),
        (
            "codex",
            "codex_snapshot_source",
            r"\Aset -e; \. \"/run/aebench-agent/\.codex/"
            r"shell_snapshots/[^\"]*\"\Z",
            False,
        ),
        (
            "codex",
            "codex_snapshot_builder",
            r"\Aif \[ -z \"\$\{BASH_ENV-\}\" \].*echo '# Snapshot file'",
            False,
        ),
        ("claude", "claude_env_probe", r"\Aenv\Z", True),
    )
)

CLAUDE_DEFAULT_TIMEOUT_S = 3600.0  # BASH_DEFAULT_TIMEOUT_MS of the harness
_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T[\d:.]+Z?)\]\s+(\{.*)$")
_TIME = re.compile(
    r"\A(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(\.\d+)?"
    r"(Z|[+-]\d\d:?\d\d)?\Z"
)
_HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\2")
# Characters after which `#` starts a word, so a comment (bash metacharacters).
_WORD_BREAK = frozenset(" \t|&;()<>")
_BLANKS = re.compile(r"[ \t]+")
_PATH_KEYS = ("file_path", "path", "notebook_path")
# MC-01 fields copied from a trace record as they are.
_LINK_KEYS = (
    *("parent_command_id", "root_command_id", "depth", "ppid", "pgid"),
    *("sid", "parent_link"),
)
# MC-14 event types that write; every other event type reads (spec §5.6).
WRITE_EVENT_TYPES = frozenset({"close_write", "moved_from", "moved_to"})
_EDIT_KEYS = ("old_string", "new_string", "content", "edits")


def parse_time(value: typing.Any) -> float | None:
    """Converts an ISO 8601 timestamp to epoch seconds.

    Args:
        value: Text such as `2026-10-12T16:43:26Z` or with an offset; a
            timestamp without zone is taken as UTC.

    Returns:
        Epoch seconds, or None when the value is not a valid timestamp.
    """
    match = _TIME.match(value.strip()) if isinstance(value, str) else None
    if not match:
        return None
    fraction, zone = match.group(7, 8)
    sign = -1 if zone and zone[0] == "-" else 1
    digits = (zone or "Z")[1:].replace(":", "")
    offset = datetime.timedelta(
        hours=int(digits[:2] or 0), minutes=int(digits[2:] or 0)
    )
    try:
        moment = datetime.datetime(
            *map(int, match.groups()[:6]),
            tzinfo=datetime.timezone(sign * offset),
        )
    except ValueError:
        return None
    return moment.timestamp() + float(fraction or 0.0)


def log_events(
    log_path: pathlib.Path,
) -> Iterator[tuple[float | None, dict]]:
    """Yields the JSON event lines of a runner log.

    Args:
        log_path: The run's runner_output.log.

    Yields:
        (line time, event) for every `[time] {json object}` line.
    """
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            match = _LINE.match(raw.strip())
            if not match:
                continue
            try:
                event = json.loads(match.group(2))
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield parse_time(match.group(1)), event


def _obj(value: typing.Any) -> dict:
    return value if isinstance(value, dict) else {}


def _new_call(
    call_id: str,
    family: str,
    raw_name: typing.Any,
    tool_input: typing.Any,
    t_start: float | None,
    precision: float = 1.0,
    actor: str = "main",
) -> Call:
    """Builds a Call from its tool input; result fields are filled later."""
    tool_input, raw_name = _obj(tool_input), str(raw_name)
    tool_type = TOOL_TYPES.get(raw_name, "other")
    shell = tool_type == "shell"
    command = tool_input.get("command") if shell else None
    command = command if isinstance(command, str) else None
    timeout = tool_input.get("timeout", tool_input.get("timeoutMs"))
    timeout_s = timeout / 1000 if isinstance(timeout, (int, float)) else None
    if family == "claude" and timeout_s is None:
        timeout_s = CLAUDE_DEFAULT_TIMEOUT_S
    paths = [
        tool_input[k] for k in _PATH_KEYS if isinstance(tool_input.get(k), str)
    ]
    return Call(
        call_id=call_id,
        seq=0,
        family=family,
        raw_name=raw_name,
        tool_type=tool_type,
        actor=actor,
        t_start=t_start,
        t_end=None,
        t_precision_s=precision,
        command=command,
        command_norm=normalize(command) if command is not None else None,
        background=(
            bool(tool_input.get("run_in_background"))
            if shell and family != "codex"
            else None
        ),
        timeout_s=timeout_s if shell else None,
        paths=paths[:1],
        edit_content=tool_type in ("edit", "write")
        and any(k in tool_input for k in _EDIT_KEYS),
        log_ok=None,
        log_exit_code=None,
    )


def _finish(
    call: Call, t_end: float | None, ok: bool, code: typing.Any
) -> None:
    """Records a call's result: end time, success and integer exit code."""
    call["t_end"], call["log_ok"] = t_end, ok
    call["log_exit_code"] = code if isinstance(code, int) else None


def _claude_calls(log_path: pathlib.Path) -> list[Call]:
    """Claude Code stream-json: tool_use blocks and their tool_result."""
    calls: dict[str, Call] = {}
    for when, event in log_events(log_path):
        event_type = event.get("type")
        actor = "subagent" if event.get("parent_tool_use_id") else "main"
        content = _obj(event.get("message")).get("content")
        for block in content if isinstance(content, list) else []:
            block = _obj(block)
            call_id = str(block.get("id"))
            if event_type == "assistant" and block.get("type") == "tool_use":
                if call_id not in calls:
                    calls[call_id] = _new_call(
                        call_id,
                        "claude",
                        block.get("name"),
                        block.get("input"),
                        when,
                        actor=actor,
                    )
            elif event_type == "user" and block.get("type") == "tool_result":
                call = calls.get(str(block.get("tool_use_id")))
                if call is not None and call["log_ok"] is None:
                    _finish(call, when, not block.get("is_error"), None)
    return list(calls.values())


def _codex_calls(log_path: pathlib.Path) -> list[Call]:
    """Codex exec --json: item.started and item.completed of tool items."""
    calls: dict[str, Call] = {}
    for when, event in log_events(log_path):
        item = _obj(event.get("item"))
        name = str(item.get("type"))
        if event.get("type") not in ("item.started", "item.completed") or (
            name not in TOOL_TYPES
            and name not in ("file_change", "mcp_tool_call")
        ):
            continue
        call_id = str(item.get("id"))
        call = calls.get(call_id)
        if call is None:
            changes = [_obj(c) for c in item.get("changes") or []]
            command = item.get("command")
            words = shlex.split(command) if isinstance(command, str) else []
            inner = {"command": words[-1] if len(words) > 2 else command}
            call = calls[call_id] = _new_call(
                call_id, "codex", name, inner, when
            )
            if name == "file_change":
                change_types = {c.get("kind") for c in changes}
                call["tool_type"] = (
                    "write" if change_types == {"add"} else "edit"
                )
                call["paths"] = [str(c.get("path")) for c in changes]
        if event["type"] == "item.completed":
            code = item.get("exit_code")
            ok = item.get("status") != "failed" and code in (0, None)
            _finish(call, when, ok, code)
    return list(calls.values())


def _deepseek_calls(log_path: pathlib.Path) -> list[Call]:
    """DeepSeek dsh --json: tool_call and tool_result events by callId."""
    calls: dict[str, Call] = {}
    for when, event in log_events(log_path):
        call_id = str(event.get("callId"))
        call = calls.get(call_id)
        if event.get("type") == "tool_call" and call is None:
            calls[call_id] = _new_call(
                call_id, "deepseek", event.get("tool"), event.get("input"), when
            )
        elif event.get("type") == "tool_result" and call is not None:
            if call["log_ok"] is None:
                _finish(call, when, event.get("status") == "completed", None)
    return list(calls.values())


def _zcode_calls(log_path: pathlib.Path) -> list[Call]:
    """ZCode stream-json: tool.updated events, deduplicated by toolCallId."""
    calls: dict[str, Call] = {}
    sessions: list[str] = []
    for _, event in log_events(log_path):
        payload = _obj(event.get("payload"))
        call_id = payload.get("toolCallId")
        stamp = event.get("timestamp")
        stamp = stamp / 1000 if isinstance(stamp, (int, float)) else None
        if event.get("type") != "tool.updated" or not isinstance(call_id, str):
            continue
        call = calls.get(call_id)
        if call is None:
            if not payload.get("toolName"):
                continue
            sessions = sessions or [event.get("sessionId")]
            actor = "main" if event.get("sessionId") in sessions else "side"
            call = calls[call_id] = _new_call(
                call_id,
                "zcode",
                payload["toolName"],
                payload.get("input"),
                stamp,
                0.001,
                actor,
            )
        update_type = payload.get("kind")
        started = payload.get("startedAt")
        if update_type == "started" and isinstance(started, (int, float)):
            call["t_start"] = started / 1000
        elif update_type in ("result", "error") and call["log_ok"] is None:
            result = _obj(payload.get("result"))
            detail = _obj(_obj(result.get("perf")).get("detail"))
            ok = (
                update_type == "result"
                and not payload.get("error")
                and result.get("success") is not False
            )
            _finish(
                call, stamp, ok, _obj(detail.get("command")).get("exitCode")
            )
    return list(calls.values())


CALL_PARSERS: typing.Mapping[str, Callable[[pathlib.Path], list[Call]]] = (
    types.MappingProxyType(
        {
            "claude": _claude_calls,
            "codex": _codex_calls,
            "deepseek": _deepseek_calls,
            "zcode": _zcode_calls,
        }
    )
)
# Event types that only one family's log contains (trajectory_stats).
_FIRST_EVENTS = _by_name(
    {
        "claude": "system assistant rate_limit_event",
        "codex": "thread.started turn.started item.started item.completed",
        "deepseek": "session status tool_call",
    }
)


def detect_family(run_dir: pathlib.Path) -> str:
    """Names the agent family of a run.

    Args:
        run_dir: The run's `run/` directory (or the folder holding it).

    Returns:
        One of the keys of CALL_PARSERS.

    Raises:
        UnsupportedFormatError: Neither the agent kind in result.jsonl nor
            the first log events name a known family.
    """
    run_dir = pathlib.Path(run_dir)
    if (run_dir / "run").is_dir():
        run_dir = run_dir / "run"
    result = last_json_line(run_dir / "result.jsonl")
    agent_type = str(result.get("agent_kind") or "")
    for family in CALL_PARSERS:
        if agent_type.startswith(family):
            return family
    log_path = run_dir / "runner_output.log"
    if log_path.is_file():
        for count, (_, event) in enumerate(log_events(log_path)):
            if "eventId" in event and isinstance(event.get("payload"), dict):
                return "zcode"
            if event.get("type") in _FIRST_EVENTS:
                return _FIRST_EVENTS[event["type"]]
            if count >= 20:
                break
    raise UnsupportedFormatError(f"unknown agent format in {run_dir}")


def last_json_line(path: pathlib.Path) -> dict:
    """Reads the last JSON line of a file such as result.jsonl.

    Args:
        path: The file.

    Returns:
        The last non-blank line's JSON object, or {} when unreadable.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
        return _obj(json.loads(lines[-1])) if lines else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_calls(log_path: pathlib.Path, family: str) -> list[Call]:
    """Reads an agent log into Call records, numbered by log order.

    Args:
        log_path: The run's runner_output.log.
        family: The agent family (detect_family).

    Returns:
        One Call per tool use, in order of first appearance.

    Raises:
        UnsupportedFormatError: The family has no parser.
    """
    if family not in CALL_PARSERS:
        raise UnsupportedFormatError(f"no parser for family {family!r}")
    calls = CALL_PARSERS[family](pathlib.Path(log_path))
    for seq, call in enumerate(calls):
        call["seq"] = seq
    return calls


def unwrap(argv: typing.Any, family: str) -> tuple[str, str | None]:
    """Removes the harness wrapper from a trace record's argv.

    Args:
        argv: The record's argv list.
        family: The agent family; only its wrappers are tried.

    Returns:
        (command, wrapper id or None). A shell with `-c` gives its script;
        one without gives the rest of argv, shell-joined (decision R1).
    """
    words = [str(word) for word in argv] if isinstance(argv, list) else []
    has_c, rest = False, words[1:]
    while rest and rest[0].startswith("-") and rest[0] not in ("-", "--"):
        has_c = has_c or (not rest[0].startswith("--") and "c" in rest[0])
        rest = rest[1:]
    if not has_c or not rest:
        return shlex.join(words[1:]), None
    script = rest[0]
    for wrapper_family, wrapper, pattern, quoted in WRAPPERS:
        match = pattern.search(script) if wrapper_family == family else None
        if not match:
            continue
        command = match.group("cmd")
        if quoted:
            try:
                command = " ".join(shlex.split(command))
            except ValueError:
                continue
        return command, wrapper
    return script, None


def noise_reason(command: str, wrapper: str | None, family: str) -> str | None:
    """Says why a trace record is harness noise.

    Args:
        command: The unwrapped command.
        wrapper: The wrapper id unwrap found, or None.
        family: The agent family.

    Returns:
        The NOISE reason, or None for an ordinary record.
    """
    for noise_family, reason, pattern, unwrapped_only in NOISE:
        if noise_family not in (None, family):
            continue
        if unwrapped_only and wrapper is not None:
            continue
        if pattern.search(command):
            return reason
    return None


def split_heredocs(command: str) -> Iterator[tuple[str, str]]:
    """Splits a command into ("shell", line) and ("body", line) pieces.

    Heredoc bodies (and their terminator lines) are "body"; quote state is
    carried across shell lines so a quoted newline stays inside its quote.
    A `#` that starts a word outside quotes begins a comment to the end of
    the line; quotes and `<<` inside it are ignored (decision D35).
    Public (decision D31): layer 2 must split heredocs exactly as J1 does.

    Args:
        command: Shell text.

    Yields:
        (piece type, line); shell lines after outside-quote whitespace
        collapsing, body lines verbatim.
    """
    pending: list[tuple[bool, str]] = []
    quote = ""
    for line in command.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if pending and not quote:
            strip, delimiter = pending[0]
            if (line.lstrip("\t") if strip else line) == delimiter:
                pending.pop(0)
            yield "body", line
            continue
        out, i = [], 0
        while i < len(line):
            char = line[i]
            if quote == "'":
                quote = "" if char == "'" else quote
            elif char == "\\" and i + 1 < len(line):
                out.append(line[i : i + 2])
                i += 2
                continue
            elif quote == '"':
                quote = "" if char == '"' else quote
            elif char == "#" and (not out or out[-1] in _WORD_BREAK):
                # A comment runs to the end of the line: its quote
                # characters and `<<` open nothing.
                out.append(_BLANKS.sub(" ", line[i:]))
                break
            elif char in "'\"":
                quote = char
            elif char in " \t":
                if out and out[-1] == " ":
                    i += 1
                    continue
                char = " "
            elif char == "<":
                match = _HEREDOC.match(line, i)
                if match and not line.startswith("<<<", i):
                    pending.append((match.group(1) == "-", match.group(3)))
            out.append(char)
            i += 1
        yield "shell", "".join(out).rstrip()


def normalize(command: str) -> str:
    """Normalizes a command for the J1 key (spec §6.4).

    Unifies line endings, strips trailing whitespace per line and at the end,
    and collapses runs of spaces and tabs outside quotes. Heredoc bodies are
    kept verbatim.

    Args:
        command: Shell text.

    Returns:
        The normalized text.
    """
    return "\n".join(line for _, line in split_heredocs(command)).rstrip()


def tokens(command: str) -> list[str]:
    """Splits shell text into POSIX tokens (shlex, punctuation_chars).

    Args:
        command: Shell text.

    Returns:
        The tokens; on a lexing error (an unclosed quote) the
        whitespace-separated words.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return command.split()


def same_tokens(a: str, b: str) -> bool:
    """Rule `quoting_equivalent`: equal tokens, heredoc bodies verbatim.

    Args:
        a: One command.
        b: The other command.

    Returns:
        Whether the two are the same shell input up to quoting.
    """

    def key(command: str) -> tuple[list[str], list[str]]:
        pieces = list(split_heredocs(command))
        shell = "\n".join(text for part, text in pieces if part == "shell")
        return tokens(shell), [t for part, t in pieces if part == "body"]

    return key(a) == key(b)


def _snapshot(monitors: dict) -> Snapshot:
    """Normalizes one record's file_snapshot monitor."""
    raw = monitors.get("file_snapshot")
    status = "absent"
    if isinstance(raw, dict):
        status = "skipped" if raw.get("skipped") else "ok"
        status = (
            "truncated" if status == "ok" and raw.get("truncated") else status
        )
    listed = status in ("ok", "truncated")
    lists = {
        op: [str(p) for p in raw.get(op) or []] if listed else []
        for op in ("created", "modified", "deleted")
    }
    untracked = {
        f"{op}_untracked": _strings(raw.get(f"{op}_untracked"))
        if listed
        else None
        for op in ("created", "modified", "deleted")
    }
    hashes = raw.get("hashes") if listed else None
    return Snapshot(
        status=status,
        scope=str(raw.get("scope") or "workspace") if listed else None,
        **lists,
        totals={
            op: int(raw.get(f"total_{op}") or len(lists[op]))
            for op in ("created", "deleted", "modified")
        }
        if listed
        else None,
        skip_reason=str(raw["skipped"]) if status == "skipped" else None,
        hashes=dict(hashes) if isinstance(hashes, dict) else None,
        **untracked,
    )


def _strings(value: typing.Any) -> list[str] | None:
    """A list of strings as recorded, or None when not a list."""
    return [str(v) for v in value] if isinstance(value, list) else None


def _seconds(value: typing.Any) -> float | None:
    """Milliseconds to seconds, or None when not a number."""
    number = isinstance(value, (int, float)) and not isinstance(value, bool)
    return value / 1000 if number else None


def _record_type(record: dict) -> str | None:
    """MC-04's record type; None on traces without it (decision D27)."""
    if record.get("event") == "start":
        return "start_only"
    if record.get("end_reason") == "broker_shutdown":
        return "flushed_at_stop"
    return "closed" if "event" in record or "end_reason" in record else None


def read_events(
    path: pathlib.Path,
) -> tuple[dict[str, list[str]], list[dict]]:
    """Reads MC-14's file_events.jsonl (spec §5.6).

    Args:
        path: The file.

    Returns:
        (sorted unique paths read per command_id, the read events with no
        command_id, as recorded and in file order).
    """
    found: dict[str, set[str]] = {}
    unattributed: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = _obj(json.loads(line))
            except json.JSONDecodeError:
                continue
            event_type = event.get("event_type", event.get("kind"))
            cid, name = event.get("command_id"), event.get("path")
            if name is None or event_type in WRITE_EVENT_TYPES | {None}:
                continue
            if cid is None:
                unattributed.append(event)
            else:
                found.setdefault(str(cid), set()).add(str(name))
    reads = {cid: sorted(names) for cid, names in found.items()}
    return reads, unattributed


def _exec(record: dict, family: str) -> Exec | None:
    """One trace line to an Exec, or None without a start time."""
    monitors = _obj(record.get("monitors"))
    timing = _obj(monitors.get("timing"))
    t_start = parse_time(timing.get("started_at"))
    if t_start is None or "command_id" not in record:
        return None
    duration = (timing.get("duration_ms") or 0) / 1000
    argv = record.get("argv")
    command, wrapper = unwrap(argv, family)
    code = record.get("return_code", record.get("exit_code"))
    ended_at = parse_time(timing.get("ended_at"))
    released_at = parse_time(timing.get("released_at"))
    stdio = record.get("stdio")
    if released_at is None and ended_at is not None:
        released_at = t_start  # MC-10 moves started_at to the release
    return Exec(
        command_id=str(record["command_id"]),
        seq=0,
        t_start=t_start,
        t_end=t_start + duration,
        duration_s=duration,
        exit_code=code,
        signal=record.get("signal"),
        complete=bool(record.get("complete")),
        cwd=record.get("cwd"),
        pid=record.get("pid"),
        argv0=str(argv[0]) if isinstance(argv, list) and argv else None,
        command=command,
        command_norm=normalize(command),
        wrapper=wrapper,
        noise=noise_reason(command, wrapper, family),
        snapshot=_snapshot(monitors),
        stdout_bytes=_obj(record.get("stdout")).get("bytes_received"),
        stderr_bytes=_obj(record.get("stderr")).get("bytes_received"),
        env_keys=_strings(record.get("env_keys")),
        schema_version=record.get("schema_version"),
        **{key: record.get(key) for key in _LINK_KEYS},
        record_type=_record_type(record),
        released_at=released_at,
        ended_at=ended_at,
        snapshot_before_s=_seconds(timing.get("snapshot_before_ms")),
        snapshot_after_s=_seconds(timing.get("snapshot_after_ms")),
        reads=None,
        orphans=record["orphans"]
        if isinstance(record.get("orphans"), list)
        else None,
        stdio=stdio if isinstance(stdio, list) and len(stdio) == 3 else None,
    )


def load_execs(
    trace_path: pathlib.Path,
    family: str,
    file_events: pathlib.Path | None = None,
) -> list[Exec]:
    """Reads commands.jsonl into Exec records sorted by (t_start, id).

    A start event (MC-04) is dropped when the trace also has a line for its
    command that is not a start event, in either file order (spec §5.6).

    Args:
        trace_path: The monitor trace.
        family: The agent family, which selects the wrappers and noise.
        file_events: MC-14's file_events.jsonl, or None; when given, every
            exec's reads is a list.

    Returns:
        One Exec per record with a start time, numbered by sorted position.
    """
    execs: list[Exec] = []
    starts: dict[str, Exec] = {}
    with pathlib.Path(trace_path).open(
        encoding="utf-8", errors="replace"
    ) as handle:
        for line in handle:
            try:
                record = _obj(json.loads(line))
            except json.JSONDecodeError:
                continue
            found = _exec(record, family)
            if found is None:
                logger.warning("%s: record without start skipped", trace_path)
            elif found["record_type"] == "start_only":
                starts.setdefault(found["command_id"], found)
            else:
                execs.append(found)
    ended = {e["command_id"] for e in execs}
    execs += [e for cid, e in starts.items() if cid not in ended]
    reads = read_events(pathlib.Path(file_events))[0] if file_events else None
    execs.sort(key=lambda e: (e["t_start"], e["command_id"]))
    for seq, record in enumerate(execs):
        record["seq"] = seq
        if reads is not None:
            record["reads"] = reads.get(record["command_id"], [])
    return execs
