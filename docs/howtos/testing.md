# Running the Tests

## 1. Prerequisites

The test suite uses [pytest](https://docs.pytest.org/) and the project's dev dependencies. Install everything with [uv](https://docs.astral.sh/uv/):

```bash
uv sync --dev
```

All commands below assume you are running from the repo root (the directory with `pyproject.toml`).

## 2. Running the stable suite

```bash
PYTHONPATH=src uv run python -m pytest tests/unit tests/functional
```

Expected output:
```
61 passed in ~1s
```

To see the full test collection:

```bash
PYTHONPATH=src uv run python -m pytest --collect-only -q
```

This currently reports 71 tests on `main`. The integration tier is still useful for framework development, but in this checkout it may expose the known `unsupported oracle runtime mode: None` issue in older fixtures.

Some useful flags:
```bash
PYTHONPATH=src uv run python -m pytest -v             # show each test as it runs
PYTHONPATH=src uv run python -m pytest -x --tb=short  # stop on first failure, compact traceback
```

## 3. Test layout

```plaintext
tests/
├── conftest.py               # adds src/ to sys.path
├── unit/                     # isolated logic, no I/O
│   ├── test_discovery.py     # oracle phase discovery
│   ├── test_requirements.py  # CheckResult, PathCheck, VersionCheck
│   ├── test_scoring.py       # run_phases scoring and failure modes
│   └── test_loader.py        # load_case_spec bundle validation
├── functional/               # component-level, real filesystem, no agents or Docker
│   ├── test_oracle_execution.py  # run_phases result structure and callbacks
│   ├── test_prompting.py         # Jinja2 prompt rendering
│   └── test_aggregation.py       # benchmark result aggregation and markdown
└── integration/              # multiple components together
    ├── conftest.py           # sets PYTHONPATH for subprocess oracle runner
    ├── test_oracle_executor.py # run_oracle execution behavior
    ├── test_oracle_runner.py # DirectOracleRunner and SubprocessOracleRunner
    └── mock-case/            # full end-to-end with mock agent
        ├── fixture/          # self-contained workspace (aebench.toml + case bundle)
        └── test_mock_case_e2e.py  # CaseRunner and BenchmarkRunner e2e
```

## 4. Running individual tiers

### Unit tests

Isolated logic, no filesystem or subprocess calls. Run in milliseconds.

```bash
PYTHONPATH=src uv run python -m pytest tests/unit/
```

| File | What it covers |
|---|---|
| `test_discovery.py` | finding oracle classes by inheritance, priority ordering, error cases |
| `test_requirements.py` | `CheckResult` factory methods; `PathCheck` and `VersionCheck` pass/fail |
| `test_scoring.py` | `run_phases` score accumulation, FAIL_FAST vs CONTINUE, exception capture |
| `test_loader.py` | `load_case_spec`: valid bundle loading, field parsing, missing-file errors |

### Functional tests

Exercise a full component end-to-end with the real filesystem. Use stub oracle classes and pre-built model objects: no agents or Docker needed.

```bash
PYTHONPATH=src uv run python -m pytest tests/functional/
```

| File | What it covers |
|---|---|
| `test_oracle_execution.py` | `run_phases` result structure, phase status, callbacks |
| `test_prompting.py` | `build_prompt_bundle`: profile resolution, path injection, timeout, prompt_append |
| `test_aggregation.py` | benchmark summarization: pass ratios, phase scores, JSONL output, markdown |

### Integration tests

Multiple real components exercised together. Uses a self-contained fixture case written to a temp directory. Still no live agents or Docker.

```bash
PYTHONPATH=src uv run python -m pytest tests/integration/
```

If these fail with `unsupported oracle runtime mode: None`, the failure is coming from current fixture/runtime configuration drift rather than from missing external services.

| File | What it covers |
|---|---|
| `test_oracle_executor.py` | `run_oracle`: phase execution, failure reporting, result output |
| `test_oracle_runner.py` | `DirectOracleRunner` and `SubprocessOracleRunner`: pass/fail on valid/empty workspace, result written to disk |
| `test_mock_case_e2e.py` | full pipeline with mock agent: `CaseRunner` single-case, `BenchmarkRunner` multi-case, output files, oracle scoring |

Integration tests are tagged `@pytest.mark.sanity`. To run only sanity tests across all tiers:
```bash
PYTHONPATH=src uv run python -m pytest -m sanity
```

## 5. Running a single file or test

```bash
PYTHONPATH=src uv run python -m pytest tests/unit/test_discovery.py
PYTHONPATH=src uv run python -m pytest tests/unit/test_discovery.py::test_discover_all_four_phases
PYTHONPATH=src uv run python -m pytest -k "fail_fast"
```

## 6. Markers

| Marker | Meaning |
|---|---|
| `sanity` | fast, no external services. All integration tests carry this |
| `docker_sanity` | requires Docker + API key + agent image. Not yet implemented |
| `bundle_ci` | official bundle validation. Not yet implemented |

## 7. How source code is found

`pyproject.toml` puts `src/` on `sys.path`:
```toml
[tool.pytest.ini_options]
pythonpath = [".", "src"]
testpaths = ["tests"]
```

The integration conftest additionally injects `src/` into `PYTHONPATH` so child processes spawned by `SubprocessOracleRunner` can import evaluator modules.

No `pip install -e .` is required to run tests. Use `uv sync --dev` to install the project runtime and test dependencies into the venv.

## 8. Adding new tests

- **Unit**: add under `tests/unit/`. Use `tmp_path` for temp filesystem needs. No subprocesses
- **Functional**: add under `tests/functional/`. Use real model objects and stub oracles. No network or Docker
- **Integration**: add under `tests/integration/`. Tag with `@pytest.mark.sanity`. Use `tmp_path` for fixture cases. Pass a `CaseConfig` directly to oracle runners when possible to avoid depending on `load_case_spec`
