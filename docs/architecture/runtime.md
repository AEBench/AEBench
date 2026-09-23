# Runtime

`aebench case run` performs these steps:

```text
clone or copy source
  -> build prompt
  -> create a temporary support directory and copy the agent credential
  -> start local/Docker runtime
  -> run CLI harness under GNU timeout
  -> stop and commit the agent container
  -> run the four-phase oracle
  -> clean runtime credentials and temporary resources
```

## Agent runtime

`LocalRuntime` executes commands directly in the prepared workspace. The agent
CLIs disable interactive permission checks, so local mode requires
`--allow-unsafe-local`. Run it only on a disposable Chameleon instance. Local
mode provides no process isolation and uses the current user's home directory
and the CLI versions installed on the instance. Docker mode uses the versions
pinned in the AEBench image.

`DockerRuntime` mounts the artifact workspace and a temporary credential directory
in `aebench-agent:latest`. The agent uses `/home/agent` as its home directory so
dependencies installed during the run remain available to the task oracle. If the artifact uses Docker, AEBench
also mounts the host Docker socket. It uses the absolute host workspace path so
Docker can resolve bind mounts created by the artifact. This mode requires
`--allow-host-docker` and a disposable Chameleon instance.

`run.runtime.timeout_ms` sets the agent time limit. GNU `timeout` sends `TERM`
when the limit expires and `KILL` 30 seconds later. AEBench combines stdout and
stderr, adds a UTC timestamp to each line, and writes the result to
`runner_output.log`.

## Command monitoring

`aebench case monitor` runs the same pipeline and additionally records every
shell the agent starts. It adds these steps:

```text
start the broker on a per-run unix socket   (before the container exists)
  -> mount the socket directory read-only at /run/aebench
  -> swap aeshell over /bin/bash, real bash kept at /usr/lib/aebench/bash.real
  -> probe the swapped shell as the agent user
  -> ... agent runs ...
  -> stop the broker   (before the container stops)
```

The shim ships in the image but dormant, so `aebench case run` is unaffected
and both commands use the same image. The broker runs on the host: only the
socket directory is exposed to the container, and the trace is never mounted.

Ordering is deliberate. The broker binds before the container starts, so no
shell can reach a socket that is not yet listening. The probe runs as the
unprivileged `agent` user before the agent does, because the shim fails open by
design: without it, a wrong socket mode or a missing mount produces a run that
looks completely normal with an empty trace. The broker is stopped before the
container is, so no command is left in flight against a socket that is going
away.

The shim is not removed before the snapshot is committed. It does not need to
be: with no socket mounted -- which is the situation in the container the
oracle starts from that snapshot -- the shim execs the preserved real shell
and passes output and exit status through untouched.

Monitoring requires `runtime.mode = "docker"`. If the shim cannot be installed
or the probe fails, the run errors out rather than continuing unmonitored.

### What the trace contains

`<case-runs-dir>/<case>/monitor/<run-token>/`:

- `commands.jsonl`: one record per shell invocation, with argv, cwd, exit
  status, duration, and the names (never the values) of environment variables
- `stream/<command-id>/{stdout,stderr}.log`: what each command wrote
- `broker.log`: the broker's own output

The trace sits beside the run output directories rather than inside one, so
`RunResult.command_monitor` carries the token that links a run to its trace.

### Coverage limits

Read the trace as a record of shell invocations, not of every command:

- **One record per shell, not per command.** `bash -c 'a & b'` is parsed by
  bash itself and appears as a single record. An agent CLI that reuses one
  long-lived shell for a whole session produces correspondingly few records.
- **`/bin/sh` is dash and is not shimmed**, so `sh -c` and most `make` recipe
  lines are invisible.
- **Nested containers are invisible.** A case with
  `artifact_requirements.docker = true` can start its own containers, which
  have neither the shim nor the socket.
- **The agent can defeat it.** It runs with passwordless sudo and could put
  the original bash back itself, or delete the preserved copy -- which would
  leave the snapshot without a working shell. Nothing currently checks for
  this.
- **The file-effects monitor walks the workspace** before and after every
  command. On a large artifact tree it reports `skipped` after spending its
  budget, so treat its absence as normal.

## Oracle scoring

For Docker runs, AEBench stops the agent container and commits its filesystem changes when `commit_before_oracle = true`. It then passes the committed image and host workspace to the oracle runtime registry. The `task` oracle target starts from the committed image. It does not share the agent process tree. Local targets and targets defined in `case.toml` use the evaluator target registry.

The oracle still runs after the agent exits with a nonzero status or reaches its time limit. The agent result records that status. The oracle scores the evidence in the final workspace.

## Credentials

AEBench creates a temporary support directory for each run outside the artifact
workspace. It copies the selected subscription credential into this directory
with file mode `0600`. Docker runs mount the directory into the agent container.
AEBench deletes the directory after the agent exits and before it saves the
container image. The original credential remains unchanged. The agent can read
the copied credential and can run commands without approval. Use a credential
that you can revoke and a disposable Chameleon instance.

## Outputs

The case output directory includes:

- `runner_output.log`: timestamped harness stdout and stderr
- `aebench_prompt_<case>.md`: exact prompt
- `result.jsonl`: runtime result
- `oracle_result.json`: four-phase score
- `case_result.json`: combined case result
- `<case>_report.md`: run report

For `aebench case monitor`, the command trace is written to
`<case-runs-dir>/<case>/monitor/<run-token>/` instead, outside the run output
directory. `RunResult.command_monitor` records the token and the resolved
path.
