from __future__ import annotations

import argparse
import ast
import contextlib
import json
import math
import random
import time
from pathlib import Path

from qiskit import QuantumCircuit, transpile

from Construct_Circuit import QsimRandBenchmark, get_cz_blocks
from enola import enola
from mvqc import mvqc


WORKLOADS = {
	"qaoa_regular3_30": (30, "qaoa/regular/q30_regular3/i0.txt"),
	"qsim_rand_0.3_10": (10, "qsim"),
	"qft_18": (18, "qft/qft_n18.qasm"),
	"vqe_30": (30, "vqe/vqe_n30.qasm"),
	"bv_14": (14, "bv/bv_n14.qasm"),
}

_METRIC_NAMES = (
	"transfer_duration",
	"move_duration",
	"fidelity",
	"one_qubit_fidelity",
	"two_qubit_fidelity",
	"excitation_fidelity",
	"transfer_fidelity",
	"coherence_fidelity",
	"movement_stages",
)


def _load_blocks(kind: str, qubits: int) -> list[list[tuple[int, int]]]:
	if kind == "qsim":
		return get_cz_blocks(QsimRandBenchmark(qubits, 10, 0.3, 0).circ)
	if kind.endswith(".txt"):
		gates = ast.literal_eval(Path(kind).read_text(encoding="utf-8"))
		return [gates]
	circuit = QuantumCircuit.from_qasm_file(kind)
	transpiled = transpile(
		circuit,
		basis_gates=["u1", "u2", "u3", "cz", "id"],
		optimization_level=2,
	)
	return get_cz_blocks(transpiled)


def _compile(
	blocks: list[list[tuple[int, int]]],
	qubits: int,
	*,
	storage: bool,
) -> dict[str, float | int]:
	started = time.monotonic()
	metrics = dict(
		zip(
			_METRIC_NAMES,
			mvqc(blocks, math.ceil(math.sqrt(qubits)), qubits, storage),
			strict=True,
		)
	)
	metrics["compile_seconds"] = time.monotonic() - started
	return metrics


def _compile_enola(
	blocks: list[list[tuple[int, int]]],
	qubits: int,
) -> dict[str, float | int]:
	started = time.monotonic()
	metrics = dict(
		zip(
			_METRIC_NAMES,
			enola(blocks, math.ceil(math.sqrt(qubits)), qubits),
			strict=True,
		)
	)
	metrics["compile_seconds"] = time.monotonic() - started
	return metrics


def _run_workload(name: str, output_dir: Path) -> None:
	random.seed(0)
	qubits, kind = WORKLOADS[name]
	blocks = _load_blocks(kind, qubits)
	log_path = output_dir / "logs" / f"{name}.log"
	result_path = output_dir / "results" / f"{name}.json"
	log_path.parent.mkdir(parents=True, exist_ok=True)
	result_path.parent.mkdir(parents=True, exist_ok=True)

	with log_path.open("w", encoding="utf-8") as log:
		with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
			print(f"===== BEGIN {name} =====")
			result: dict[str, object] = {
				"qubits": qubits,
				"cz_blocks": len(blocks),
				"non_storage": _compile(blocks, qubits, storage=False),
				"with_storage": _compile(blocks, qubits, storage=True),
			}
			if name == "qaoa_regular3_30":
				result["enola"] = _compile_enola(blocks, qubits)
			print(f"===== END {name} status=0 =====")
	result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _smoke_test() -> None:
	blocks = [[(0, 1), (2, 3)], [(1, 2), (4, 5)]]
	for storage in (False, True):
		metrics = _compile(blocks, 6, storage=storage)
		if not 0 < float(metrics["fidelity"]) <= 1:
			raise RuntimeError("compiler smoke test produced invalid fidelity")
	print("PowerMove compiler smoke test passed")


def main() -> None:
	parser = argparse.ArgumentParser(description="Run the scoped PowerMove evaluation.")
	parser.add_argument("--output-dir", type=Path, default=Path("evaluation"))
	parser.add_argument("--workload", choices=tuple(WORKLOADS))
	parser.add_argument("--smoke", action="store_true")
	args = parser.parse_args()

	if args.smoke:
		_smoke_test()
		return
	selected = (args.workload,) if args.workload else tuple(WORKLOADS)
	for workload in selected:
		_run_workload(workload, args.output_dir)


if __name__ == "__main__":
	main()
