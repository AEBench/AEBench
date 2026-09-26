# Loupe H100 reproduction — 2026-09-26

The bounded `asplos24_loupe` case was reproduced on the Chameleon H100 host
`vlm-demo`. The scored run built the pinned artifact and its supplied
Nginx/wrk example, ran two privileged analysis replicas, and produced complete
dynamic and static syscall tables. Two independent final oracle invocations
passed all four phases.

## Result

- Pre-install oracle: `0/4` (`error`), as expected
- Final oracle pass 1: `4/4` (`success`)
- Final oracle pass 2: `4/4` (`success`)
- Focused Loupe oracle tests: `4 passed`
- Local AEBench unit suite: `86 passed`
- Case validation: passed
- Scored Loupe analysis duration: 302 seconds
- Full setup/debug/finalization window: `2026-09-26T02:15:16Z`–`2026-09-26T02:55:11Z`

The semantic result summary was:

| measurement | count |
| --- | ---: |
| Dynamic syscalls observed | 50 |
| Required implementation | 19 |
| Replaceable by at least one mode | 31 |
| `dyn.csv` `works faked` | 13 |
| `dyn.csv` `works stubbed` | 1 |
| `dyn.csv` `works both` | 17 |
| Static-binary syscalls observed | 93 |

Both tables contain exactly one row for every syscall number from 0 through
334. The two dynamic replicas reported zero differences. The upstream
`loupe search` output reported 19 required, 13 stub-capable, one fake-capable,
and 17 capable of both. The upstream query maps the two single-mode columns in
the opposite order from the literal `dyn.csv` header; the oracle therefore
scores the stable required-versus-replaceable distinction rather than relying
on those two labels.

## Provenance and hardware

- AEBench case commit used by the final oracle: `0e8c04106d2843c6be5c00ec0a27d32a17583da8`
- Loupe: `07ca3d25e32ebb2205c38735255084bfb8dab798` (`asplos24-ae-v1`)
- loupedb: `a076cf972d7d7bacc73693455d979a9522ed2c5a` (`asplos24-ae-v1`)
- Final compatibility image: `sha256:032aa29b9dbf1c18f9a53c49026db85753d9f6939418b2634a9830f234a2f0ec`
- Host: Ubuntu 24.04.4, Linux 6.8.0-124, Intel Xeon Platinum 8468, 48 logical CPUs, 240 GiB RAM
- Docker Engine: 29.1.3, `overlayfs`, data root under the run's `/dev/shm` directory
- Python: 3.12.3; GitPython 3.1.40; setuptools 75.8.2
- Container compatibility packages: LIEF 0.13.2; Capstone 4.0.2
- GPU recorded but unused by this CPU case: NVIDIA H100, 95,830 MiB, driver 560.35.05

## Compatibility and audit notes

- The temporary Docker daemon's bridge connected to GitHub but transferred no
  data. Builds were resumed through Docker's host network; pinned source and
  experiment inputs were not changed.
- Python 3.12 removed `distutils`, which the 2023 wrapper imports. Pinned
  setuptools 75.8.2 supplied the compatibility module without an upstream
  source patch.
- The upstream base Dockerfile installs LIEF and Capstone without versions.
  Current releases removed the APIs used by the pinned static analyzer. The
  AEBench compatibility image layer pins the contemporary LIEF 0.13.2 and
  Capstone 4.0.2 releases; it does not modify Loupe source.
- The first successful dynamic run failed during static analysis with current
  LIEF. That attempt and traceback are retained in the raw archive.
- The first live oracle scored `3/4` because it assumed `exit_group(231)` must
  be observed. Loupe externally terminates the server workload, so a correct
  run need not observe that call. The final check keeps seven essential
  process syscalls and all complete-table/category/evidence requirements.
- The first post-run query omitted upstream's required
  `--allow-dirty-db` flag. The corrected read-only query succeeded without
  rerunning the experiment; both attempts are retained.

## Commands

The scored path was:

```text
make src/seccomp-run
make docker
docker build --tag loupe-base:latest -f Dockerfile.aebench-compat .
loupe generate -b -db loupedb -a aebench-nginx -w wrk -d examples/E1/Dockerfile.nginx
loupe --allow-dirty-db search --show-usage -db loupedb -a aebench-nginx -w bench
aebench case oracle asplos24_loupe --workspace-dir <loupe-workspace> --output-dir <oracle-output>
```

The complete 49-file raw archive is stored outside Git at
`../loupe-h100-artifacts/20260926T021411Z` relative to the AEBench worktree.
Its relative-path `SHA256SUMS` manifest was verified after transfer. This
directory commits only the two final oracle results, archive manifest, and
this summary; upstream source, Docker layers, build products, and generated
CSV/log results remain outside Git.
