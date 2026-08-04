# Runtime

`aebench case run` performs these steps:

```text
clone or copy source
  -> build prompt
  -> prepare per-run auth home
  -> start local/Docker runtime
  -> run CLI harness under GNU timeout
  -> stop and commit the agent container
  -> run the four-phase oracle
  -> clean runtime credentials and temporary resources
```

## Agent runtime

`LocalRuntime` executes commands directly in the prepared workspace. Both harnesses disable interactive permission checks, so local mode requires `--allow-unsafe-local`. Run it only on a disposable host. Local mode does not provide process isolation or pinned CLI versions. Use Docker for comparable benchmark runs.

`DockerRuntime` mounts the artifact workspace and a private agent home in `aebench-agent:latest`. If the artifact requires Docker, AEBench also mounts the host Docker socket. It uses the absolute host workspace path so Docker can resolve bind mounts created by the artifact. This mode requires `--allow-host-docker` and a disposable worker.

`run.runtime.timeout_ms` sets the agent time limit. GNU `timeout` sends `TERM` when the limit expires and `KILL` 30 seconds later. AEBench writes raw stdout and stderr to `runner_output.log`.

## Oracle scoring

For Docker runs, AEBench stops the agent container and commits its filesystem changes when `commit_before_oracle = true`. It then passes the committed image and host workspace to the oracle runtime registry. The `task` oracle target starts from the committed image. It does not share the agent process tree. Local targets and targets defined in `case.toml` use the evaluator target registry.

The oracle still runs after the agent exits with a nonzero status or reaches its time limit. The agent result records that status. The oracle scores the evidence in the final workspace.

## Credentials

AEBench creates a temporary home outside the artifact workspace for each run. It copies subscription credentials there with mode `0600` and mounts the directory into Docker when needed. AEBench clears the directory before it commits the container and removes it after the harness exits. The bind-mounted directory is not part of the committed image. The agent can read these credentials while it runs without command approval. Use dedicated credentials and disposable workers.

## Outputs

The case output directory includes:

- `runner_output.log`: raw harness output
- `aebench_prompt_<case>.md`: exact prompt
- `result.jsonl`: runtime result
- `oracle_result.json`: four-phase score
- `case_result.json`: combined case result
- `<case>_report.md`: human-readable report
