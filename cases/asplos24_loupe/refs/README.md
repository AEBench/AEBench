# Loupe smoke reference

`smoke_expectations.ref.json` describes the pinned Nginx/wrk example, not
results from Loupe's paper. This case does not score Table 1 or Figures 2-5.

- `loupe_commit` and `loupedb_commit` pin the two upstream checkouts used by
  the example. `input_sha256` pins the supplied Dockerfile and test script,
  plus the compatibility Dockerfile and Loupe base image recipe.
- `dockerfile_md5` identifies the generated database directory for the pinned
  Nginx Dockerfile. It is an input identity, not an expected experiment score.
- `syscall_min` and `syscall_max` require the complete Linux x86_64 syscall
  table produced by this pinned Loupe version, IDs 0 through 334.
- `min_used = 10` is a deliberately loose smoke floor, below the 50 observed
  in the archived interactive reproduction. `min_replaceable = 1` requires
  at least one nontrivial stubbing or faking result. Neither number is a
  paper measurement.
- `essential_used` lists basic process syscalls observed in the archived
  Nginx/wrk reproduction. These IDs must appear as used, but their exact
  stub/fake classifications are not fixed across kernels and runtimes.

The oracle checks the values in the fresh query output against the dynamic
CSV: its required IDs must equal the used but nonreplaceable IDs, and the
union of its other three lists must equal the replaceable IDs. Loupe's query
swaps the labels of the two single-mode categories relative to `dyn.csv`, so
the oracle deliberately compares their union instead of each label.
