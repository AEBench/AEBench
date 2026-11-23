# AEBench

<p align="center">
<a href="#1-overview">Overview</a> | <a href="#2-requirements">Requirements</a> | <a href="#3-getting-started">Getting started</a> | <a href="#4-running-example-reproducing-gaia">Running example</a> | <a href="#5-choosing-and-running-other-cases">Other cases</a> | <a href="#6-adding-a-new-case">Adding a case</a> | <a href="#7-development">Development</a> | <a href="#8-known-issues">Known issues</a>
</p>

## 1. Overview

AEBench is a framework that evaluates how well AI coding agents reproduce the results of published systems research papers. Many Computer Science conferences run an Artifact Evaluation (AE) process, where reviewers download a paper's code and data (the *artifact*), follow its instructions, and check whether the paper's results hold. AEBench gives the same task to an agent and scores how far the agent gets. For more context on why this benchmark exists, see [WHY.md](WHY.md).

Each benchmark task is a *case*. A case represents a paper's artifact at a particular upstream commit and includes an *oracle* that checks the agent's work. When users run a case, AEBench:

1. clones the artifact and starts a Docker container with the agent's command-line tool (e.g., Claude Code or Codex) installed;
2. prompts the agent to set up, build, and run the artifact's experiments by following the artifact's instructions, without human help;
3. runs the oracle on the workspace the agent leaves behind and awards one point for each phase that passes:

| Phase | Passes when |
| --- | --- |
| `env_setup` | the artifact's dependencies are installed |
| `artifact_build` | the artifact builds |
| `benchmark_prep` | the inputs and benchmarks the experiments need are ready |
| `experiment_runs` | the experiments ran and their results match the paper's within the case's tolerance |

A score of 4/4 indicates all derived checks passed successfully and results are likely reproduced, while 0/4 means that no phase passed.

Users who want to run a case should start with "[Requirements](README.md#2-requirements)" and "[Getting Started](README.md#3-getting-started)", then follow the "[Running example](README.md#4-running-example-reproducing-gaia)". Users who want to add a case or an agent should see "[Adding a new case](README.md#6-adding-a-new-case)" and "[Agents](README.md#24-agents)". Users who want to change AEBench itself should see "[Development](README.md#7-development)".

## 2. Requirements

AEBench was developed and tested with the requirements below. Older versions and smaller machines might work as well, but we have not tested them.

### 2.1 Platform

* x86_64 bare-metal machine running Ubuntu 24.04.4 LTS (Linux 6.8)

The Docker image also builds for arm64, but we have not tested AEBench on arm64 machines, nor on macOS or Windows.

### 2.2 Software

* Docker Engine 29.8.2, with the current user in the `docker` group (`sudo usermod -aG docker "$USER"`, then log out and back in), since AEBench calls `docker` without `sudo`
* `git` 2.43.0
* [`uv`](https://docs.astral.sh/uv/) 0.12.23, which also installs Python if needed
* Python 3.12.3
* Rust 1.83 (`cargo`), only for running the shell-wrapper tests (see "[Tests](README.md#74-tests)")

### 2.3 Hardware

* 64 CPU cores, 187 GB of RAM, and 210 GB of disk space ([Chameleon Cloud](https://www.chameleoncloud.org/) bare-metal instances, which is why some guides use `cc@<floating-ip>` addresses)

AEBench itself is lightweight, but many artifacts need hours of CPU time, a lot of memory, and tens of GB of disk space. The Docker image that agents run in is about 9 GB on its own, and every run leaves files behind (see "[Disk usage](README.md#82-disk-usage)"). Individual cases may need more; users should check the artifact's own instructions before running a case.

> [!NOTE]
> Agents run with broad permissions inside their container, and some cases give them control of the host (see "[Choosing and running other cases](README.md#5-choosing-and-running-other-cases)"). We recommend running AEBench on a disposable machine, such as a cloud instance, rather than on a personal workstation.

### 2.4 Agents

Users need credentials for at least one supported agent. Each agent harness is a directory under [`agents/`](agents/). AEBench currently includes the following harnesses:

| `--agent` | Agent | Credential |
| --- | --- | --- |
| `claude` | Claude Code | `ANTHROPIC_API_KEY` |
| `claude_non_api` | Claude Code with a Claude subscription | `CLAUDE_CODE_OAUTH_TOKEN`, or a file named by `AEBENCH_CLAUDE_OAUTH_TOKEN_FILE` |
| `codex` | Codex CLI | `OPENAI_API_KEY` or `CODEX_API_KEY` |
| `codex_non_api` | Codex CLI with a ChatGPT subscription | `~/.codex/auth.json`, or a file named by `AEBENCH_CODEX_AUTH_FILE` |
| `deepseek` | DeepSeek Flash through the DSH CLI | `DEEPSEEK_API_KEY`, or a file named by `AEBENCH_DEEPSEEK_API_KEY_FILE` |
| `zcode` | GLM-5.3-Flash through ZCode | `ZAI_CODING_PLAN_KEY`, or a file named by `AEBENCH_ZAI_CODING_PLAN_KEY_FILE` |

Users can list the harnesses AEBench finds, and check their configuration, by running
```bash
uv run aebench agent list
```

To set up subscription logins and copy them to a remote machine, or to add a new harness (which requires no change to AEBench's code), see [docs/howtos/add_agent.md](docs/howtos/add_agent.md).

## 3. Getting Started

Before starting, users should make sure that the dependencies listed in "[Requirements](README.md#2-requirements)" are installed. Then, users should clone this repository, install its dependencies, and build the Docker image that agents run in, by following these steps:

1. Clone the AEBench repository:
```bash
git clone https://github.com/AEBench/AEBench.git
cd AEBench
```

The repository structure should look similar to the one below:
```plaintext
AEBench/
  ├── README.md
  ├── Dockerfile        # the image agents run in
  ├── aebench.toml      # marks the repository root as an AEBench workspace
  ├── cases.json        # the catalog of official cases
  ├── agents/           # one directory per agent harness
  ├── cases/            # one directory per case
  ├── docs/
  ├── src/              # the AEBench command-line tool and runtime
  └── tests/
```
The `cases` directory contains the benchmark itself, while the `agents` directory holds the scripts that start each supported agent.

2. Install the Python dependencies with [`uv`](https://docs.astral.sh/uv/):
```bash
uv sync
```

3. Build the Docker image that agents run in:
```bash
docker build -t aebench-agent:latest .
```

4. Check that the `aebench` command-line tool works:
```bash
uv run aebench --help
```

If successful, users should see a message similar to
```text
usage: aebench [-h] {init,run,case,runtime,agent,trace,audit-trace} ...

AEBench benchmark, case, and runtime CLI.
```

> [!NOTE]
> Users should run every `aebench` command from the repository root, since AEBench finds its case catalog by walking up from the current directory to `aebench.toml` or `cases.json`. Also, users should rebuild the Docker image (step 3) after every `git pull`, since a run uses the image as it was last built, not the code in the checkout.

## 4. Running example: reproducing GAIA

To illustrate how AEBench works, we walk users through running a single case, `asplos24_gaia`. GAIA is a carbon-aware job scheduler from ASPLOS 2024. Its artifact is pure Python and its experiments are a deterministic simulation, so the case has the shortest time limit in the benchmark (1 hour) and needs nothing beyond the container. We use Claude Code with an Anthropic API key.

1. Ensure that the dependencies are installed and the Docker image is built (see "[Requirements](README.md#2-requirements)" and "[Getting Started](README.md#3-getting-started)" above).

2. Check that the case loads correctly:
```bash
uv run aebench case validate asplos24_gaia
```
which should yield
```text
Case bundle is valid: /path/to/AEBench/cases/asplos24_gaia
```

3. Provide the agent's credentials:
```bash
export ANTHROPIC_API_KEY=...
```

4. Run the case:
```bash
uv run aebench case run asplos24_gaia \
  --agent claude \
  --model claude-opus-5-5
```
Note that `--agent` and `--model` are required for every run. AEBench passes `--model` to the agent unchanged, so users should choose a model that their account can access.

Before the agent starts, AEBench prints the directory where it saves the run's data:
```text
Output: /home/<user>/.cache/aebench/case-runs/asplos24_gaia/<timestamp>
```

5. Wait for the run to finish. The agent installs GAIA's dependencies, runs its four figure scripts, and leaves the resulting CSV files under `results/simulation/pai_1k/`. The oracle then compares these files against the paper's reference values. At the end, AEBench prints the case status and the oracle score, similar to
```text
Case status: success
Oracle score: 4
```

6. Inspect what the agent did. The output directory contains the agent's log (`runner_output.log`), the prompt the agent received, and the result (`case_result.json`). For a more readable view, users can export the run as a website and open it in a browser:
```bash
uv run aebench trace export ~/.cache/aebench/case-runs/asplos24_gaia/<timestamp> \
  --output-dir /tmp/aebench-traces
uv run aebench trace serve /tmp/aebench-traces --port 8766
```
and open `http://127.0.0.1:8766`. The website shows the prompt, every tool call the agent made along with its output, and the oracle's verdict for each phase. To view a run from a remote machine, see [docs/howtos/view_agent_trace.md](docs/howtos/view_agent_trace.md).

7. Optionally, run the oracle again on the agent's workspace, or on a workspace users prepared by hand:
```bash
uv run aebench case oracle asplos24_gaia \
  --workspace-dir /path/to/artifact/workspace \
  --output-dir /tmp/aebench-gaia-oracle
```

## 5. Choosing and running other cases

`cases.json` lists the official cases, and each case lives in `cases/<case_id>/`. A case's `case.toml` describes the paper, the claim to reproduce, the time limit (`[run.runtime] timeout_ms`), and what the case needs from the host. Time limits range from 1 hour to 96 hours.

Before running a case, users should check its `case.toml` for the following settings, which require an extra option:

| Setting in `case.toml` | Option to add | Effect |
| --- | --- | --- |
| `[run.artifact_requirements] docker = true` | `--allow-host-docker` | Gives the agent control of the host's Docker daemon |
| `[run.runtime] mode = "local"` | `--allow-unsafe-local` | Runs the agent directly on the host, without a container |

> [!NOTE]
> Both options give the agent control of the host machine. We recommend using them only on a disposable machine.

`uv run aebench case run --help` lists every option. [docs/howtos/run_benchmark.md](docs/howtos/run_benchmark.md) describes them in detail, including:
* `aebench case monitor`, which additionally records every shell command the agent runs;
* the environment variables that change where workspaces, outputs, and sockets live;
* the user configuration file;
* options that need host setup beyond Docker.

## 6. Adding a new case

To add a new case, users should create a case directory, generate starter oracle files, and validate the result:
```bash
uv run aebench case init --blank --id my-case --target-dir cases/my-case
uv run aebench case template cases/my-case
uv run aebench case validate cases/my-case
```

The generated files are templates. Users then need to fill in the upstream metadata in `case.toml`, register the case in `cases.json`, prepare the oracle's reference data, and implement one oracle per phase. [docs/howtos/add_case.md](docs/howtos/add_case.md) walks through each of these steps. We recommend using an existing case, such as `cases/osdi24_kondo/`, as a model.

## 7. Development

This section is for users who want to change AEBench itself, for example to fix a bug in the runtime, support a new kind of oracle check, or change how runs are recorded. Adding a case or an agent does not require changing AEBench's code (see "[Adding a new case](README.md#6-adding-a-new-case)" and "[Agents](README.md#24-agents)").

### 7.1 Code layout

The command-line tool and the runtime live under `src/`:
```plaintext
src/
  ├── cli.py              # the `aebench` command and its subcommands
  ├── models.py           # case.toml schema, run options, and result types
  ├── config.py           # settings and AEBENCH_* environment variables
  ├── prompting.py        # renders the prompt the agent receives
  ├── evaluator/          # loading and validating cases, the oracle framework
  │   ├── authoring/      # `aebench case init` and `case template`
  │   └── oracles/        # base classes and checks that case oracles build on
  ├── runtime/            # running a case end to end
  │   ├── case_runner.py  # one run: workspace, agent, oracle, results
  │   ├── backend.py      # the Docker and local backends that execute commands
  │   ├── agent_runner.py # starts an agent harness inside the backend
  │   ├── agent_support/  # scripts copied into the container for every run
  │   └── shim/           # the shell wrapper that records the agent's commands
  ├── trace_viewer/       # `aebench trace export` and `trace serve`
  └── trajectory_audit/   # `aebench audit-trace`
```
The architecture notes in [docs/architecture/](docs/architecture/) describe how these pieces fit together. In particular, [runtime.md](docs/architecture/runtime.md) covers the run lifecycle and [oracle.md](docs/architecture/oracle.md) the oracle framework.

### 7.2 Setting up a development environment

Install the development dependencies (linters, type checkers, and `pytest`):
```bash
uv sync --dev
```

> [!NOTE]
> Changes under `src/runtime/` that affect what runs inside the container (e.g., `agent_support/` or `shim/`) only take effect after users rebuild the Docker image:
> ```bash
> docker build -t aebench-agent:latest .
> ```

### 7.3 Checking a change

Every pull request runs the following checks, and users should run them locally before opening one:
```bash
uv run ruff check src tests
uv run ruff format --check src tests
MYPYPATH=src uv run mypy --explicit-package-bases src
uv run python -m pytest tests/unit tests/functional
```

The code uses tabs for indentation and a line length of 100, which `uv run ruff format src tests` applies automatically. `mypy` runs in strict mode.

### 7.4 Tests

The test suite is split into tiers:
* `tests/unit/` tests isolated logic without filesystem or subprocess calls;
* `tests/functional/` tests a whole component against the real filesystem, without agents or Docker;
* `tests/integration/` tests several components together, including the shell wrapper, which requires a Rust toolchain (`cargo`) and is skipped without one.

For example, to run the integration tests, or a single test:
```bash
uv run python -m pytest tests/integration/
uv run python -m pytest tests/unit/test_loader.py -k missing
```

See [docs/howtos/testing.md](docs/howtos/testing.md) for more details on each tier and the available `pytest` markers.

## 8. Known issues

### 8.1 Commands that appear in `--help` but are unavailable

AEBench currently runs one case per command. The commands `aebench run`, `aebench case export`, `aebench case summarize`, and `aebench runtime run` appear in `--help`, but exit with a message such as "benchmark runner is unavailable in this checkout".

### 8.2 Disk usage

By default, every run leaves behind its output directory (under `~/.cache/aebench/case-runs/`), the artifact workspace the agent worked in (under `/tmp/aebench-workspaces/`), and a Docker image of the agent's container after the agent finished. Over several runs, these can fill the disk. To remove the workspace and the image after a run, users can pass `--cleanup-workspace` and `--delete-image`, respectively. Otherwise, we recommend checking `docker images` and the workspace directory between runs.

### 8.3 Unregistered cases

A few directories under `cases/` (`atc25_sieve`, `eurosys25_visualinux`, and `osdi25_verified_storage`) are not listed in `cases.json` yet. Users can still validate or run them by path, e.g., `uv run aebench case validate cases/atc25_sieve`.
