# Paralegal bounded artifact task

Work only at wrapper commit
`d26799fb0f4b0d2bc2cf7b6ad0e1b6afc732b9b4`. Apply the SSH-to-HTTPS rewrite
to this repository's local Git configuration, initialize all recursive
submodules, and preserve their pinned revisions.

Build either from source or use the published
`paralegal:osdi25-artifact` image. For the source path, install stable Rust
`1.75`, `nightly-2023-08-25`, CodeQL `2.19.3`, and the Python plotting
requirements. Build/install `cargo-paralegal-flow`, the release `griswold`
binary, and the release CodeQL runner.

Copy the overlaid `aebench-smoke-config.toml` to
`paralegal-bench/bconf/aebench-smoke-config.toml`. Run:

1. The complete CodeQL comparison from `codeql-experimentation` with
   `--keep-intermediates`, `--results-dir results`, and
   `--codeql-command` pointing to CodeQL `2.19.3`.
2. The release `griswold` binary against
   `bconf/aebench-smoke-config.toml`.

Keep the generated CodeQL `results/<timestamp>/tmp/` tables and the Paralegal
`results/<timestamp>-run`, `-logs`, and `-pp` directories in the artifact
checkout. Redirect the two top-level commands to
`codeql-aebench.log` and `paralegal-aebench-smoke.log`.

This task intentionally covers only the CodeQL comparison and the
`atomic-data` smoke benchmark. Do not claim full-paper reproduction or run the
full approximately 90-minute benchmark corpus for this slice.
