from __future__ import annotations

FAMILIES = {
	"qaoa_regular3": ((30, 40, 50, 60, 80, 100), "qaoa/regular/q{n}_regular3/i0.txt"),
	"qaoa_regular4": ((30, 40, 50, 60, 80), "qaoa/regular/q{n}_regular4/i0.txt"),
	"qaoa_random": ((20, 30), "qaoa/rand/q{n}_p0.5/i0.txt"),
	"qsim_rand_0.3": ((10, 20, 40), "qsim/rand/q{n}_10_p0.3/i0.txt"),
	"qft": ((18, 29), "qft/qft_n{n}.qasm"),
	"vqe": ((30, 50), "vqe/vqe_n{n}.qasm"),
	"bv": ((14, 50, 70), "bv/bv_n{n}.qasm"),
}

WORKLOADS = {
	f"{family}_{n}": (n, path.format(n=n))
	for family, (sizes, path) in FAMILIES.items()
	for n in sizes
}

SCRIPT_WORKLOADS = {
	f"{family}_{comparison}_compare": tuple(f"{family}_{n}" for n in sizes)
	for family, (sizes, _) in FAMILIES.items()
	for comparison in ("storage", "no_storage")
}

PYTHON_COMMAND = (
	"sh",
	"-c",
	'if [ -x .venv/bin/python ]; then exec .venv/bin/python "$@"; else exec python3 "$@"; fi',
	"python-check",
)
