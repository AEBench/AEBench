# `osdi25_paralegal`

This is a bounded first AEBench slice for the Paralegal OSDI 2025 artifact. It
checks the pinned wrapper checkout, its recursive submodules, the CodeQL 2.19.3
comparison, and one `atomic-data` CI-style benchmark. It does not claim to
reproduce the full benchmark corpus, plots, or the paper's roughly 90-minute
performance run.

## Scope and resources

- Wrapper commit: `d26799fb0f4b0d2bc2cf7b6ad0e1b6afc732b9b4`
- CodeQL bundle: `2.19.3`
- Rust: stable `1.75` and `nightly-2023-08-25`
- Python: Python 3 with `matplotlib==3.*`, `pandas==2.*`, and `six`
- Platform: Linux x86_64 for the published Docker image
- Recommended source-build host: 8 or more CPUs, 64 GB RAM, and 100 GB free disk
- Expected runtime: roughly 10 minutes for CodeQL plus the bounded smoke run,
  after a cold setup/build that may take 30-90 minutes

The analyzer and benchmarker can be built from source without CloudLab-specific
services. CloudLab is recommended because the cold Rust, CodeQL, and case-study
builds need substantial disk and memory. The published Docker image is about
11.5 GB after loading and is an alternative execution path, not a requirement
for the source path.

## Licensing note

The wrapper repository and its Zenodo record do not currently declare a root
artifact license. Individual subprojects have their own licenses. This case
therefore contains only newly authored configuration, parsers, hashes, counts,
and commit metadata. It does not vendor upstream source, expected-output
contents, plots, or generated results. Confirm redistribution terms with the
artifact authors before expanding the bundled reference material.

## CloudLab reproduction

Start an Ubuntu 22.04 x86_64 node with at least 100 GB of writable disk and run
the following in `tmux`. Adjust only the two root paths if your checkouts use
different locations.

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential bzip2 clang cmake curl git libclang-dev liblz4-dev \
  libssl-dev pkg-config python3 python3-pip python3-venv tmux unzip wget

export AEBENCH_ROOT="$HOME/AEBench"
export PARALEGAL_ROOT="$HOME/paralegal-osdi-2025-artifact"

git clone https://github.com/AEBench/AEBench.git "$AEBENCH_ROOT"
git -C "$AEBENCH_ROOT" checkout muneeb/issue-13-osdi25-paralegal
git clone --no-recurse-submodules \
  https://github.com/brownsys/paralegal-osdi-2025-artifact.git \
  "$PARALEGAL_ROOT"

cd "$PARALEGAL_ROOT"
git checkout d26799fb0f4b0d2bc2cf7b6ad0e1b6afc732b9b4
git config --local url."https://github.com/".insteadOf git@github.com:
git submodule sync --recursive
git submodule update --init --recursive
git submodule status --recursive

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs |
  sh -s -- -y --default-toolchain 1.75
source "$HOME/.cargo/env"
rustup toolchain install nightly-2023-08-25 \
  --component rustc-dev --component rust-src \
  --component rustfmt --component clippy

curl -L \
  https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.19.3/codeql-bundle-linux64.tar.gz \
  -o "$PARALEGAL_ROOT/codeql-bundle-linux64.tar.gz"
tar -xzf "$PARALEGAL_ROOT/codeql-bundle-linux64.tar.gz" -C "$PARALEGAL_ROOT"
rm "$PARALEGAL_ROOT/codeql-bundle-linux64.tar.gz"
export PATH="$PARALEGAL_ROOT/codeql:$HOME/.cargo/bin:$PATH"

python3 -m venv "$PARALEGAL_ROOT/.venv"
source "$PARALEGAL_ROOT/.venv/bin/activate"
python3 -m pip install -r "$PARALEGAL_ROOT/plotting/requirements.txt"

(cd "$PARALEGAL_ROOT/paralegal" &&
  cargo install --locked --path crates/paralegal-flow)
(cd "$PARALEGAL_ROOT/paralegal-bench" &&
  cargo build --bin griswold --locked --release)
(cd "$PARALEGAL_ROOT/codeql-experimentation/runner" &&
  cargo build --release --locked)

install -m 0644 \
  "$AEBENCH_ROOT/cases/osdi25_paralegal/refs/smoke_bench_config.toml" \
  "$PARALEGAL_ROOT/paralegal-bench/bconf/aebench-smoke-config.toml"
mkdir -p \
  "$PARALEGAL_ROOT/codeql-experimentation/results" \
  "$PARALEGAL_ROOT/paralegal-bench/results"
```

Run the two experiments in a persistent session:

```bash
tmux new -s paralegal

cd "$PARALEGAL_ROOT/codeql-experimentation"
runner/target/release/runner \
  --keep-intermediates \
  --results-dir results \
  eval-config.toml \
  --codeql-command "$PARALEGAL_ROOT/codeql/codeql" \
  2>&1 | tee "$PARALEGAL_ROOT/codeql-aebench.log"

cd "$PARALEGAL_ROOT/paralegal-bench"
target/release/griswold \
  bconf/aebench-smoke-config.toml \
  --no-install-flow-analyzer \
  2>&1 | tee "$PARALEGAL_ROOT/paralegal-aebench-smoke.log"
```

Detach with `Ctrl-b`, then `d`. Reattach with:

```bash
tmux attach -t paralegal
```

After both commands complete, run the AEBench oracle twice:

```bash
cd "$AEBENCH_ROOT"
uv sync --dev

PYTHONPATH=src uv run aebench case oracle osdi25_paralegal \
  --workspace-dir "$PARALEGAL_ROOT" \
  --output-dir /tmp/aebench-paralegal-oracle-1

PYTHONPATH=src uv run aebench case oracle osdi25_paralegal \
  --workspace-dir "$PARALEGAL_ROOT" \
  --output-dir /tmp/aebench-paralegal-oracle-2
```

Both completed runs must score `4/4` before this case is described as reproduced.
