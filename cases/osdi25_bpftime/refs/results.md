# bpftime Reference Provenance

This case uses paper-derived reference values only.

## Source

- Paper: see the pinned `[paper]` metadata in `case.toml`
- Reference table: Table 3
- Reference hardware: Intel Xeon Gold 5418Y (24 cores, 2.0GHz, 256GB DDR5)

## Included Reference Metrics

The structured reference values in `speedup_ratios.paper.json` are extracted from Table 3 for these micro-benchmarks:

- `__bench_uprobe`
- `__bench_uretprobe`
- `__bench_read`
- `__bench_write`
- `__bench_hash_map_update`
- `__bench_hash_map_lookup`
- `__bench_hash_map_delete`

`__bench_hash_map_lookup` is intentionally direction-reversed relative to the other checks because the paper reports kernel uprobe outperforming bpftime on that operation.

## Non-Goals

- No local benchmark output is stored in `refs/`
- No machine-specific verification data is tracked in this bundle
- No unpublished thresholds are introduced beyond the paper/artifact source
