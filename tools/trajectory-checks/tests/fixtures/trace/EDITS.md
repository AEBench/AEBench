# Edits to the trace excerpts

Every `<name>.jsonl` holds real records copied from a corpus
`monitor/commands.jsonl` (including `env_keys`, which lists variable names
only). Only these values were edited:

- Host workspace paths in `cwd` became `/tmp/ws/project` (all files).
- `claude_eval_login_requote.jsonl`, `claude_eval_incomplete.jsonl`: an
  artifact-named `/tmp/<name>_build.log` became `/tmp/build.log`.
- `claude_eval_no_devnull.jsonl`: an artifact-named process name became
  `app-tester`.
- `claude_nested_plain.jsonl`: an artifact-named script became
  `gen_wrapper.sh`.
- `nested_script_incomplete.jsonl`: `cwd` `/repo/<artifact>` became
  `/repo/project`.
- `codex_truncated.jsonl`: an artifact-named log file became
  `/tmp/apt-install.log`.
- `snapshot_absent.jsonl`: **synthetic edit** of the second record of
  `deepseek_plain.jsonl`: `monitors.file_snapshot` was removed, to give
  `snapshot.status = absent` (no such record in the corpus), and the
  `stderr` block was removed, to give `stderr_bytes = None`.

What each file covers:

| File | Covers |
|---|---|
| `claude_eval_skipped` | `claude_eval`, skipped snapshot |
| `claude_eval_login_requote` | `claude_eval` with `-c -l`, and the `'"'"'` re-quoting |
| `claude_eval_no_devnull` | `claude_eval` without `< /dev/null` (as for heredocs) |
| `claude_eval_incomplete` | `complete = false`, exit code null |
| `claude_noise` | shim probe, Claude `env` probe, Claude snapshot creation |
| `claude_nested_plain` | `bash -c` with no wrapper (nested) |
| `nested_script_incomplete` | `bash script.sh` (no `-c`, decision R1), incomplete |
| `codex_run` | shim probe, Codex snapshot builder and sourcing, `codex_snapshot_exec` with its inner `codex_direct` record (file order differs from start order) |
| `codex_truncated` | truncated snapshot, older record without `scope` |
| `zcode_fg`, `zcode_bg` | `zcode_snapshot`, `zcode_snapshot_bg` |
| `zcode_snapshot_creation` | ZCode snapshot creation noise |
| `deepseek_plain` | DeepSeek probe and plain `bash -c` (wrapper `None`, decision R2) |
| `snapshot_absent` | `snapshot.status = absent` |
| `workspace_scope_probe` | older record without `scope` → `workspace` |

## Expected records (`<name>.execs.json`)

Written by hand per spec §5.2 (the one long path list in
`codex_truncated.execs.json` was copied verbatim from the input). Only keys
present are compared; nested `snapshot` dicts are compared key by key too.
`"<any non-null>"` means the value must be a non-null string (noise reason
names other than `shim_probe` and `claude_env_probe` are not fixed by the
spec). `t_end` is not pinned for incomplete records (the parser has no run
end; `link` extends them). Wrappers of noise records are not pinned.

## Synthesized new-format fixture (decision D26)

`claude_new_format.jsonl` and `file_events/claude_new_format.jsonl` are
**synthesized**, not copied: no corpus trace has the new fields yet. Every
field follows the data formats of `to-change.md`: MC-01 (`parent_command_id`,
`root_command_id`, `depth`, `parent_link`, `ppid`, `pgid`, `sid`,
`start_ticks`, `ancestry`, `ancestors`, `child_pid`, `orphans`, `host_pid`),
MC-04 (`event` = `start` | `end`, `end_reason`), MC-05
(`created_untracked`, `modified_untracked`, `deleted_untracked`, scope
`tracked+untracked`), MC-07 (`hashes`), MC-10 (`ended_at`,
`snapshot_before_ms`, `snapshot_after_ms`; `released_at` on one record only),
`stdio` (the targets of fds 0, 1 and 2; the nested `cc` inherits its
parent's `pipe:[N]` ends), MC-14 (`file_events.jsonl`, with layer 6 §4.3's
`event_type` and one event using MC-14's `kind`) and MC-21
(`schema_version` 2). The record shape otherwise copies
`claude_eval_skipped.jsonl` (the `claude_eval` wrapper, the `stdout` and
`stderr` blocks), with neutral commands and paths.

| Record | Covers |
|---|---|
| `aaaa…1` `make test` | A root with a start event before its end record; explicit `released_at`; hashes and untracked lists; three read events, one duplicated, one `moved_to` (a write, not a read) |
| `bbbb…2` `cc -c src/a.c` | A nested record whose end line comes before its start line in the file; `parent_link = live`; `released_at` from `t_start`; a `kind` read event and a `close_write` (not a read) |
| `cccc…3` `python3 train.py` | `end_reason = broker_shutdown` (`flushed_at_stop`), `complete = false`, no `ended_at`; `parent_link = adopted` |
| `dddd…4` `sleep 600` | A start event with no end record (`start_only`) |
