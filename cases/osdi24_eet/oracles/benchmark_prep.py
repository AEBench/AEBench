from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

# Passes when the source in the current directory is at version $1: a MySQL
# release (from MYSQL_VERSION, as in the release tarball) or a git commit.
_SOURCE_VERSION_SCRIPT = """\
if [ -f MYSQL_VERSION ]; then
	. ./MYSQL_VERSION
	found="$MYSQL_VERSION_MAJOR.$MYSQL_VERSION_MINOR.$MYSQL_VERSION_PATCH"
	expected="$1"
else
	found=$(git -c safe.directory='*' rev-parse HEAD) || exit 1
	expected=$(git -c safe.directory='*' rev-parse --verify "$1^{commit}") || exit 1
fi
[ "$found" = "$expected" ] || { echo "source is at $found, expected $1" >&2; exit 1; }
"""

# Passes when an executable named $1 was built somewhere under the current directory.
_BUILT_BINARY_SCRIPT = """\
found=$(find . -type f -name "$1" -perm -u+x -print -quit)
[ -n "$found" ] || { echo "no executable named $1" >&2; exit 1; }
echo "$found"
"""


def _load_benchmark_metadata(
	path: Path,
) -> tuple[list[str], dict[str, str], dict[str, str], dict[str, str]]:
	try:
		bench_metadata = json.loads(path.read_text(encoding="utf-8"))
	except OSError as exc:
		raise ValueError(f"failed to read benchmark metadata: {exc}") from exc
	except json.JSONDecodeError as exc:
		raise ValueError(f"invalid benchmark metadata: {exc}") from exc

	benchmarks = bench_metadata.get("benchmarks")
	if not isinstance(benchmarks, list) or not all(
		isinstance(value, str) and value.strip() for value in benchmarks
	):
		raise ValueError("benchmark metadata has invalid benchmarks")

	fields: list[dict[str, str]] = []
	for field in ("sources", "binaries", "versions"):
		values = bench_metadata.get(field)
		if not isinstance(values, dict) or not all(
			isinstance(values.get(benchmark), str) and values[benchmark].strip()
			for benchmark in benchmarks
		):
			raise ValueError(f"benchmark metadata has invalid {field}")
		fields.append(values)

	sources, binaries, versions = fields
	return benchmarks, sources, binaries, versions


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.workspace_path()
		metadata_path = self.ref_path("benchmark_bench_metadata.json")

		benchmarks, sources, binaries, versions = _load_benchmark_metadata(metadata_path)

		reqs: list[BaseCheck] = [
			self.path_check(
				name="repo_root_exists",
				path=repo_root,
				kind=PathKind.DIRECTORY,
			),
		]

		# Each database is built natively from source in <repo>/<db>_test/<source>,
		# the directory scripts/<db>/run_test.sh downloads it to.
		for benchmark in benchmarks:
			source_dir = repo_root / f"{benchmark}_test" / sources[benchmark]

			reqs.append(
				self.path_check(
					name=f"source_dir_{benchmark}",
					path=source_dir,
					kind=PathKind.DIRECTORY,
				)
			)
			reqs.append(
				self.command_check(
					name=f"source_version_{benchmark}",
					cwd=source_dir,
					cmd=("sh", "-c", _SOURCE_VERSION_SCRIPT, "sh", versions[benchmark]),
					timeout_seconds=30.0,
				)
			)
			reqs.append(
				self.command_check(
					name=f"built_binary_{benchmark}",
					cwd=source_dir,
					cmd=("sh", "-c", _BUILT_BINARY_SCRIPT, "sh", binaries[benchmark]),
					timeout_seconds=300.0,
				)
			)

		return tuple(reqs)
