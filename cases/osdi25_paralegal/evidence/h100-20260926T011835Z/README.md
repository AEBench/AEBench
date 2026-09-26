# Paralegal H100 reproduction — 2026-09-26

The bounded `osdi25_paralegal` case was reproduced on the Chameleon H100 host
`vlm-demo`. Two independent final oracle invocations passed all four phases.

## Result

- Oracle pass 1: `4/4` (`success`)
- Oracle pass 2: `4/4` (`success`)
- Focused tests: `17 passed`
- Case validation: passed
- CodeQL retry: all 10 intermediate tables semantically matched
- Atomic-data smoke: one expected pass and one expected fail, both reproduced
- Run window: `2026-09-26T01:20:34Z`–`2026-09-26T01:53:14Z`
- CodeQL retry duration: 401 seconds
- Atomic-data smoke duration: 25 seconds

| run | expected/result | analyzer time | policy time | PDG functions/LoC | seen functions/LoC | controller nodes/edges |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 0 | pass/pass | 20,505,722 | 4,070 | 5 / 209 | 1,386 / 20,845 | 1,776 / 4,326 |
| 1 | fail/fail | 1,000,161 | 3,759 | 5 / 209 | 1,386 / 20,845 | 1,776 / 4,322 |

Timing values are the artifact's raw integer measurements; no unit conversion
was applied.

## Provenance

- AEBench base: `aebb4feb38a079f96ae3899bbb57e603d2b97557`
- Paralegal wrapper: `d26799fb0f4b0d2bc2cf7b6ad0e1b6afc732b9b4`
- Paralegal: `5e6e565d566eddccae61c4a81f396f3c8e261b77`
- Paralegal bench: `ff1c2a6ae4e54a78d21a7cd6a6be1f35e119cbe1`
- CodeQL experimentation: `6a0341d3c50cf3caf90c2fc8dde3b364e2422954`
- CodeQL: 2.19.3
- Required Rust: 1.75.0 and nightly-2023-08-25
- Host: Ubuntu 24.04, Intel Xeon Platinum 8468, 48 logical CPUs, 240 GiB RAM
- GPU recorded but unused by this CPU case: NVIDIA H100, 95,830 MiB, driver 560.35.05

The complete 65-file raw archive is stored outside Git at
`../paralegal-h100-artifacts/20260926T011835Z` relative to this AEBench
worktree. `SHA256SUMS` inventories that archive and was verified both before
and after transfer.

## Compatibility notes

- The pinned nested compiler lockfile contains Edition 2024 crates, which
  Cargo 1.75 cannot parse. Only the `griswold` build used Rust 1.98.1, with
  `time` minimally updated from 0.3.34 to 0.3.36. The raw archive contains the
  exact lockfile patch.
- Ubuntu 24 required adding `#include <cstdint>` to the pinned Plume C++
  fixture. The exact one-line patch and the failed 9/10 CodeQL attempt are in
  the raw archive; the clean retry is the scored run.
- The upstream CodeQL runner reported a raw-text Lemmy mismatch, while the
  AEBench semantic comparison normalized paths/ordering and passed all ten
  tables.
- The first oracle exposed two case issues: the flow binary was probed from
  the wrapper root, and legitimate zero auxiliary controller statistics were
  rejected. Commit `416e30e` fixes both and adds regression coverage.
- `paralegal-aebench-smoke.log` is empty because this successful smoke run
  wrote no top-level output. The required non-empty compile and policy logs are
  present in the timestamped result bundle and passed the oracle.

## Commands

The source path built `cargo-paralegal-flow`, release `griswold`, and the
release CodeQL runner. The scored experiments were:

```text
runner --keep-intermediates --results-dir results eval-config.toml --codeql-command <wrapper>/codeql/codeql
griswold bconf/aebench-smoke-config.toml --no-install-flow-analyzer
aebench case oracle osdi25_paralegal --workspace-dir <wrapper> --output-dir <oracle-output>
```

The raw archive includes the complete detached-run scripts, logs, environment
record, compatibility patches, failed attempts, generated result bundles, and
both final oracle outputs.
