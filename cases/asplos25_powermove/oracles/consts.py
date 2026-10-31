from __future__ import annotations

# workload -> (test_scripts family, qubits, released input)
WORKLOADS = {
	"qaoa_regular3_30": ("qaoa_regular3", 30, "qaoa/regular/q30_regular3/i0.txt"),
	"qsim_rand_0.3_10": ("qsim_rand_0.3", 10, "qsim/rand/q10_10_p0.3/i0.txt"),
	"qft_18": ("qft", 18, "qft/qft_n18.qasm"),
	"vqe_30": ("vqe", 30, "vqe/vqe_n30.qasm"),
	"bv_14": ("bv", 14, "bv/bv_n14.qasm"),
}
