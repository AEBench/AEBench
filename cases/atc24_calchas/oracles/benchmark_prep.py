from __future__ import annotations

import json
from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase
from evaluator.oracles.reporting import BaseCheck

from .parsing import DatasetManifestCheck


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	"""Validate the four released prediction datasets."""

	def requirements(self) -> Sequence[BaseCheck]:
		manifest = json.loads(self.ref_path("datasets.ref.json").read_text(encoding="utf-8"))
		return (
			DatasetManifestCheck(
				name="released_dataset_manifest",
				root=self.runtime_path(),
				files=manifest["files"],
			),
		)
