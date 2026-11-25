# Diff fixtures (layer 5, M4.3)

Each `<name>.diff` is the input of `writer.parse_diff`; `<name>.hunks.json`
holds the hunks expected from it, written by hand from rules HK1 to HK8 of
`trajectory-writer-spec.md` (never produced by the code under test). Files
of one name with a `.u<n>` infix share one expectation.

- `context.u0.diff`, `context.u3.diff`, `context.u10.diff`: one edit of a
  20-line Python file (`cfg.py`: `<letter> = <n>` per line; line 2's
  number changed, a line inserted after line 4, line 16 deleted), diffed
  with Python's `difflib.unified_diff` at 0, 3 and 10 lines of context
  (git's range conventions, so `-4,0` starts before the insertion), with
  a `diff --git` and an `index` line prepended by hand. U0 gives three
  `@@` sections, U3 two, U10 one; the three change blocks are the same.
- `example_b.diff`: the final diff of the spec's Example B (§1a.5),
  copied verbatim (the removed line of `src/util.c` is `{` and three
  spaces). Its expected hunks are the spec's `hunks.jsonl` without the
  file class and filter fields, which depend on the run.
