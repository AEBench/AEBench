# Declared-extraction fixtures (layer 6, spec §2.2.1)

Each folder is a tiny synthetic repository, written by hand with neutral
names; no file comes from an artifact. `expected_invocations.json` and
`expected_options.json` were written by hand from the rules DEC1 to DEC7
and KEY1 to KEY8, never by running the code. Every `case.toml` has
`[run.instructions] path = "README.md"`, `[upstream] url =
"https://example.org/owner/repo.git"` and `ref = "c0ffee"`; no fixture has
a `.git` folder, so each commit is the ref with the warning `declared
commit from case.toml ref` (DEC7, D50).

| Fixture | How it was made | What it checks |
|---|---|---|
| `fences` | README with a `bash` block of `$ ` prompts and `&&`, a `python` block, an untagged block with a pipe, a `text` block, a `console` block with a `\` continuation and a trailing comment | DEC2: which fences are read, prompt stripping, splitting, line and position, `text` |
| `cwd` | One `sh` block with `cd`, `cd .. &&`, a subshell `( cd sub; … )`, `pushd`/`popd`, `cd tools && ./g.sh` then `./h.sh` | DEC3, KEY2: `cwd` per command; `cd` holds a position but gives no record (D76); `cd … &&` moves the rest of the block (D57); the subshell's `cd` does not leak |
| `links` | README linking `docs/run.md` (relative), the case's own repository's `sub/README.md` (absolute `/blob/<ref>/` URL with an anchor), another repository and a CI file; `docs/run.md` links `more.md` | DEC1: one level of links; own-repository absolute links; other repositories and CI never read; `files_read`; a linked file's block starts in its folder |
| `placeholders` | One line with `<file>`, `$N`, `${N}`, `{n}`, `*`, `?` and a backquote in `--opt=value` form | DEC4: `symbolic` values; the backquoted command is an invocation of its own (D54) |
| `argparse` | `tools/run.py` with `add_argument` calls (aliases, `type=int`, `choices`, `store_true`, a negative default, `nargs`, a positional), `tools/lib.py` without any, `tools/broken.py` with a syntax error, scripts under `.github/` and `.hidden/` | DEC6, D43: option rows; positional not recorded; the broken file warns; hidden and CI folders not read; every Python file opened is in `files_read` (D74) |
| `make` | README `make RUNS=3 bench` and `make missing`; `Makefile` with `?=`, `:=`, prefixes `@`, `-`, `+`, `$(MAKE) -C sub t`, an unknown variable; `sub/Makefile` with a further `$(MAKE) -C deep x`; `sub/deep/Makefile` | DEC5: recipe records, the line's variable wins, `$(MAKE) -C` followed one level only, `var:RUNS` row for the declared key (D58), the missing target's warning |
| `ci_excluded` | README runs `./run.sh --n 10` and links a workflow that runs it with `--n 1` | DEC1: no record from CI |
| `case_lines` | `case.toml` with `[declared] lines = ["./tool.sh --k 3"]`; README names only `./other.sh` | DEC1 (4), CT4: one `case_block` record after the README's |
| `none` | `case.toml` only, no README | DEC1, PRE1: both lists empty, the warning `instructions README.md missing` |
