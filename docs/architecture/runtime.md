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

`run.runtime.timeout_ms` sets the agent time limit. GNU `timeout` sends `TERM` when the limit expires and `KILL` 30 seconds later. AEBench writes raw stdout and stderr to `runner_output.log`.

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

- `runner_output.log`: raw harness output
- `aebench_prompt_<case>.md`: exact prompt
- `result.jsonl`: runtime result
- `oracle_result.json`: four-phase score
- `case_result.json`: combined case result
- `<case>_report.md`: run report
