# AEBench Documentation

## 1. Architecture

Background on how things are put together. Good starting point before making changes to the codebase.

- [**Overview**](architecture/overview.md) — what AEBench is, the four phases, how a case run works end-to-end, and the source layout
- [**Runtime**](architecture/runtime.md) — workspaces, agent execution, Docker and local backends, output files
- [**Oracle**](architecture/oracle.md) — how oracle classes get discovered, how checks work, scoring
- [**Glossary**](architecture/glossary.md) — public classes, models, enums, and functions grouped by module

## 2. How-to guides

Step-by-step instructions for common tasks.

- [**Run the benchmark**](howtos/run_benchmark.md) — environment setup, running individual cases or the full benchmark, understanding scores and output files
- [**Add a case**](howtos/add_case.md) — scaffold a case bundle, write `case.toml`, implement oracle phases, test
- [**Add an agent**](howtos/add_agent.md) — configure CLI, Python, remote, or MCP agents; write a custom agent class; testing with the mock agent
- [**Run the tests**](howtos/testing.md) — unit, functional, and integration tests with pytest
