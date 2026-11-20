# Edits to the parser excerpts

Every `<agent>/<name>.log` is a run of consecutive or selected lines copied
from a real `run/runner_output.log` of the local corpus. Event structure,
key order, ids and timestamps are unchanged. Only the values listed here were
edited, to keep the fixtures free of artifact names, host paths and bulky
output. No line contained a secret (checked for API keys and tokens).

Applied to every file:

- Host workspace paths of the form
  `/tmp/aebench-workspaces/ae_workspace_<case>_<rand>/workspace/<case>` (and
  the `/home/.../workspaces/...` variant) were replaced by `/tmp/ws/project`.

Per file:

| File | Edit |
|---|---|
| `claude/shell_error.log` | Log file name in the command: an artifact-named `/tmp/<name>_build.log` became `/tmp/build.log` |
| `claude/edit_ok.log` | `tool_use_result.originalFile` (a 60-line source file naming the artifact) replaced by a 4-line neutral file |
| `claude/write_ok.log` | Markdown link text and file name naming the artifact became `[Project setup](project-setup.md)` |
| `claude/subagent.log` | **Synthetic edit.** The corpus has no Claude sub-agent tool call. Line 1 is a real `tool_progress` heartbeat (which carries `parent_tool_use_id` and must not become a call). Lines 2-3 are the `shell_ok.log` pair with `"parent_tool_use_id":null` replaced by a tool-use id, the shape Claude Code uses for sub-agent messages |
| `codex/shell_ok.log` | `aggregated_output` of the completed item cut to `/repo\nREADME.md\n` |
| `codex/started_only.log` | Artifact-named log file in the command became `pass-install.log` |
| `codex/web_search.log` | The URL (an artifact repository) became `https://example.org/project/README.md`; the `results` array was cut to one result with neutral values. The item keeps its real duplicated `"id"` key (`item_9`, then `exec-...`); a JSON parser keeps the last one |
| `codex/no_commands.log` | The agent message text naming the artifact was shortened to a neutral sentence |
| `deepseek/parallel_reads.log` | The two read results (whole source files) were cut to one line each |
| `deepseek/write_ok.log` | `input.content` (a README naming the artifact) replaced by a 3-line text |
| `deepseek/web_error.log` | URL became `https://example.org/poster.pdf` |
| `zcode/shell_two_calls.log` | The two result `content` strings were cut to two lines each |
| `zcode/background.log` | Artifact-named script directory became `/tmp/run/` |
| `zcode/write_ok.log` | `input.content` of the scheduled event replaced by a 2-line script |
| `zcode/web_error.log` | URL became `https://example.org/paper.pdf`; `prompt` cut to `Find Table 3.` |

## Metadata (`<name>.meta.json`)

`agent_version` from the excerpt's init event when present, else null. No
excerpt contains an init event that carries a version, so all are null.

## Expected records (`<name>.calls.json`)

Written by hand from the raw lines, field by field per spec §5.1, not by
running any parser. Conventions the tests rely on:

- Only the keys present in an expected record are compared. `timeout_s` is
  never listed (deferred in this layer). `log_ok` is not listed for the Codex
  `web_search` item (it has neither `exit_code` nor `status`).
- Times are compared with a tolerance strictly below the record's
  `t_precision_s`, so a Claude `t_end` taken from the line prefix or from the
  millisecond `user.timestamp` of the result both pass.
- `background` is `null` for non-shell calls and for every Codex call; `false`
  for a shell call of an agent with the flag when the call does not set it
  (spec §1a.5, §1a.6 point 17).
- `seq` counts tool calls from 0 in log order (first event of each call).
- ZCode `t_start` is the `started` event's `startedAt`; `t_end` is the
  envelope `timestamp` of the `result` (or `error`) event; only
  `tool.updated` events count (`model.streaming` repeats the call and must
  not create a second one).
