# AEBench

AEBench is a benchmark that measures how well AI coding agents reproduce the
results of published systems research papers. Conferences run an Artifact
Evaluation (AE) process, in which reviewers download a paper's code (its
*artifact*), follow its README, and check that the paper's results come out.
AEBench gives the same task to an agent. For why the benchmark exists, see
[WHY.md](WHY.md).

Each benchmark task is a **case**: one paper's artifact pinned to an upstream
commit, plus an **oracle** that checks the agent's work. A run goes like this:

1. AEBench clones the artifact and starts a Docker container with the agent CLI
   (Claude Code, Codex, and others) inside.
2. The agent reads the artifact's instructions and tries to set up, build, and
   run its experiments, with no human help.
3. The oracle scores the workspace the agent left behind, one point per phase:

| Phase | Passes when |
| --- | --- |
| `env_setup` | the artifact's dependencies are installed |
| `artifact_build` | the artifact builds |
| `benchmark_prep` | the inputs and benchmarks the experiments need are ready |
| `experiment_runs` | the experiments ran and their results match the paper's within tolerance |

A score of 4/4 is a full reproduction, and 0/4 means no phase passed.

## Requirements

- Linux on x86_64 or arm64.
- Docker, with your user allowed to run `docker` without `sudo`.
- `git` and [uv](https://docs.astral.sh/uv/). uv installs Python 3.10 or newer
  if you do not have it.
- Credentials for at least one agent (see [Agents](#agents)).
- Disk space. The agent image is several GB, and each run keeps its workspace,
  its outputs, and a Docker image of the container after the agent finishes
  (see [Disk usage](#disk-usage)).

Many artifacts need hours of CPU time and plenty of memory. A disposable cloud
machine works well. The team uses
[Chameleon Cloud](https://www.chameleoncloud.org/) instances, so some guides
show `cc@<floating-ip>` addresses.

## Install

```bash
git clone https://github.com/AEBench/AEBench.git
cd AEBench

uv sync
docker build -t aebench-agent:latest .
uv run aebench --help
```

The image holds the agent CLIs and AEBench's in-container tooling. **Rebuild it
after every `git pull`**: a run uses the image as it was last built, not the
code in your checkout.

Run every command from the repository root. AEBench finds its case catalog by
walking up from the current directory to `aebench.toml` or `cases.json`.

## Run your first case

This example uses Claude Code with an Anthropic API key. `asplos24_gaia` makes
a good first case: its time limit is one hour and it needs nothing beyond the
container.

```bash
export ANTHROPIC_API_KEY=...

uv run aebench case run asplos24_gaia \
  --agent claude \
  --model claude-opus-5-5
```

Before the agent starts, AEBench prints the run's output directory:

```text
Output: /home/<you>/.cache/aebench/case-runs/asplos24_gaia/<timestamp>
```

When the run ends, it prints the case status and the oracle score. The output
directory holds the agent's log (`runner_output.log`), the prompt it was
given, and the result (`case_result.json`).

`--agent` and `--model` are required on every run. AEBench passes `--model` to
the agent CLI unchanged, so use a model your account can access.

## Choosing a case

`cases.json` lists the official cases, and each one lives in
`cases/<case_id>/`. Its `case.toml` describes the paper, the claim to
reproduce, the time limit (`[run.runtime] timeout_ms`), and what the case
needs from the host. Before you run a case, check `case.toml` for these
settings:

| Setting in `case.toml` | Add to the command | Effect |
| --- | --- | --- |
| `[run.artifact_requirements] docker = true` | `--allow-host-docker` | Gives the agent control of the host's Docker daemon |
| `[run.runtime] mode = "local"` | `--allow-unsafe-local` | Runs the agent directly on the host, without a container |

Both options give the agent control of the machine, so use them only on a
disposable one. Time limits range from 1 hour to 96 hours.

To check that a case loads:

```bash
uv run aebench case validate asplos24_gaia
```

## Agents

Each agent is a directory under [`agents/`](agents/). `uv run aebench agent
list` shows the agents it finds.

| `--agent` | Agent | Credential |
| --- | --- | --- |
| `claude` | Claude Code | `ANTHROPIC_API_KEY` |
| `claude_non_api` | Claude Code with a Claude subscription | `CLAUDE_CODE_OAUTH_TOKEN`, or a file named by `AEBENCH_CLAUDE_OAUTH_TOKEN_FILE` |
| `codex` | Codex CLI | `OPENAI_API_KEY` or `CODEX_API_KEY` |
| `codex_non_api` | Codex CLI with a ChatGPT subscription | `~/.codex/auth.json`, or a file named by `AEBENCH_CODEX_AUTH_FILE` |
| `deepseek` | DeepSeek Flash through the DSH CLI | `DEEPSEEK_API_KEY`, or a file named by `AEBENCH_DEEPSEEK_API_KEY_FILE` |
| `zcode` | GLM-5.3-Flash through ZCode | `ZAI_CODING_PLAN_KEY`, or a file named by `AEBENCH_ZAI_CODING_PLAN_KEY_FILE` |

[docs/howtos/add_agent.md](docs/howtos/add_agent.md) covers how to set up
subscription logins and copy them to a remote machine. It also explains how to
add an agent, which needs no change to AEBench's code.

## Looking at a run

The trace viewer turns a run's output directory into a website. The site shows
the prompt, every tool call and its output, and the oracle's verdict:

```bash
uv run aebench trace export ~/.cache/aebench/case-runs/asplos24_gaia/<timestamp> \
  --output-dir /tmp/aebench-traces
uv run aebench trace serve /tmp/aebench-traces --port 8766
```

Then open `http://127.0.0.1:8766`. For a remote machine, use an SSH tunnel, as
[docs/howtos/view_agent_trace.md](docs/howtos/view_agent_trace.md) describes.

To score a workspace again, or to score one you set up by hand, run the oracle
alone:

```bash
uv run aebench case oracle asplos24_gaia \
  --workspace-dir /path/to/artifact/workspace \
  --output-dir /tmp/aebench-gaia-oracle
```

### Disk usage

A run leaves three things behind by default:

- its output directory, under `~/.cache/aebench/case-runs/`;
- the artifact workspace the agent worked in, under `/tmp/aebench-workspaces/`;
- a Docker image of the container after the agent finished.

Pass `--cleanup-workspace` to remove the workspace and `--delete-image` to
remove the image. Otherwise, check `docker images` and the workspace
directory between runs.

## More run options

`aebench case run --help` lists every option.
[docs/howtos/run_benchmark.md](docs/howtos/run_benchmark.md) covers them in
detail. It explains:

- `aebench case monitor`, which also records every shell command the agent
  runs;
- the environment variables that move workspaces, outputs, and sockets;
- the user config file.

Some options need host setup beyond Docker, and that guide also lists those
steps.

One case runs per command. `aebench run`, `aebench case export`,
`aebench case summarize`, and `aebench runtime run` appear in `--help`, but
they exit with "unavailable in this checkout".

## Adding a case

```bash
uv run aebench case init --blank --id my-case --target-dir cases/my-case
uv run aebench case template cases/my-case
uv run aebench case validate cases/my-case
```

[docs/howtos/add_case.md](docs/howtos/add_case.md) walks through `case.toml`,
registering the case in `cases.json`, preparing reference data, and writing the
oracle. Use an existing case such as `cases/osdi24_kondo/` as a model.

## Development

```bash
uv sync --dev
uv run python -m pytest tests/unit tests/functional
uv run ruff check src tests
uv run ruff format --check src tests
```

[docs/howtos/testing.md](docs/howtos/testing.md) describes the test tiers. Some
need Docker or a Rust toolchain. [docs/README.md](docs/README.md) indexes the
architecture notes on the runtime, the oracles, and the glossary.
