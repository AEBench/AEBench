# Agent Trace Viewer

AEBench can export a completed Codex or Claude Code run as a static website.
The viewer shows the prompt, agent events, tool calls, command output, run
details, and oracle results.

![AEBench agent trace viewer](../images/agent-trace-viewer.png)

## Export a Run

When `aebench case run` starts, it prints the directory where it will save the
run data:

```text
Output: /home/cc/.cache/aebench/case-runs/osdi24_kondo/2026-08-06_04-41-59_094599
```

After the case finishes, pass this directory to `aebench trace export`:

```bash
uv run aebench trace export \
  /home/cc/.cache/aebench/case-runs/osdi24_kondo/2026-08-06_04-41-59_094599 \
  --output-dir /tmp/aebench-traces

uv run aebench trace serve /tmp/aebench-traces --port 8766
```

Open `http://127.0.0.1:8766`. Export more run directories to
`/tmp/aebench-traces` to add them to the same site.

## View Traces From a Chameleon Instance

Run the export command above on the Chameleon instance. Then start the viewer
there:

```bash
uv run aebench trace serve /tmp/aebench-traces --port 8766
```

On your local computer, open an SSH tunnel to the Chameleon instance:

```bash
ssh -N -L 8766:127.0.0.1:8766 cc@<floating-ip>
```

Keep this command running. Open `http://127.0.0.1:8766` on your local computer.

## Input Files

If the run contains oracle re-evaluations, the viewer uses the latest
`oracle-evaluations/*/evaluation.json` result as the current score.

The exporter reads these files from the run output directory:

- `runner_output.log`
- `result.jsonl`
- `case_result.json`
- `aebench_prompt_<case-id>.md`, when present

The runner adds a UTC timestamp to each line in `runner_output.log`. The
exporter uses this timestamp when an agent event has no timestamp.

The exporter removes values from environment variables whose names contain
`KEY`, `TOKEN`, or `SECRET`. Review the exported files before publishing them.
