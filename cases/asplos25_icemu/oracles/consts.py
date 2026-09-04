from __future__ import annotations

BENCHMARKS = (
	"adpcm",
	"aes",
	"coremark",
	"crc",
	"dijkstra",
	"picojpeg",
	"quicksort",
	"sha",
	"towers",
)

BUILD_CONFIGURATIONS = ("uninstrumented", "replay-cache")
OPT_LEVELS = ("Os", "O1", "O2", "O3")

NACHO_CONFIGURATIONS = (
	"nacho_naive",
	"nacho_pw",
	"nacho_pw_st",
	"nacho_pw_stcont",
	"nacho_stcont",
	"nacho_clank",
)
CACHE_SHAPES = ("256-2", "256-4", "512-2", "512-4", "1024-2", "1024-4")
POWER_ON_DURATIONS = (250000, 500000, 2500000, 5000000, 25000000)

README_PATH = "README.md"
ICEMU_SOURCE_PATH = "icemu/icemu/CMakeLists.txt"
LLVM_SOURCE_PATH = "llvm/llvm-16.0.2/llvm/CMakeLists.txt"
RISCV_TOOLCHAIN_PATH = "toolchains/riscv32/toolchain.cmake"

PATCHED_CLANG_PATH = "llvm/llvm-16.0.2/install/bin/clang"
ICEMU_BINARY_PATH = "icemu/icemu/build/ICEmu"
PLUGIN_PATHS = tuple(
	f"icemu/plugins/build/plugins/{name}.so"
	for name in (
		"custom_cache_plugin",
		"memory_stats_plugin",
		"replay_cache_baseline_plugin",
		"replay_cache_plugin",
		"simple_war_detect_plugin",
	)
)

LOGS_DIR = "benchmarks/logs"
RESULTS_REF = "evaluation.ref.json"
NOTEBOOK_OUTPUTS = (
	"plotting/BenchmarkPlots.nbconvert.ipynb",
	"plotting/CacheVariation.nbconvert.ipynb",
	"plotting/PowerFailures.nbconvert.ipynb",
)
PLOT_OUTPUTS = (
	"plotting/plots/benchmark-checkpoints.pdf",
	"plotting/plots/benchmark-execution-time-O3-512.pdf",
	"plotting/plots/benchmark-execution-time.pdf",
	"plotting/plots/benchmark-exploration.pdf",
	"plotting/plots/benchmark-non-volatile-accesses.pdf",
	"plotting/plots/benchmark-power-failure.pdf",
	"plotting/plots/benchmark-volatile-accesses.pdf",
	"plotting/tables/benchmarks-component-overhead.tex",
	"plotting/tables/benchmarks-reexecution-overhead.tex",
)


def expected_elf_paths() -> tuple[str, ...]:
	return tuple(
		f"{benchmark}/build-{build_config}-{opt}/{benchmark}.elf"
		for benchmark in BENCHMARKS
		for build_config in BUILD_CONFIGURATIONS
		for opt in OPT_LEVELS
	)


def _continuous_result_names(benchmark: str) -> list[str]:
	names = [
		f"{benchmark}-uninstrumented-run-{configuration}-{cache}-0-0-Os-final"
		for configuration in NACHO_CONFIGURATIONS
		for cache in CACHE_SHAPES
	]
	names.extend(
		f"{benchmark}-uninstrumented-run-prowl-{cache}-0-0-Os-final"
		for cache in ("256-2", "512-2", "1024-2")
	)
	names.extend(
		(
			f"{benchmark}-uninstrumented-run-clank-0-0-Os-final",
			f"{benchmark}-uninstrumented-run-plain_c-Os-final",
			f"{benchmark}-uninstrumented-run-replay_cache_baseline-8192-2-0-0-Os-final",
		)
	)
	names.extend(
		f"{benchmark}-replay-cache-run-replay_cache-{cache}-0-0-Os-final"
		for cache in ("256-2", "512-2", "1024-2", "8192-2")
	)
	return names


def _optimization_result_names(benchmark: str) -> list[str]:
	names: list[str] = []
	for opt in ("O1", "O2", "O3"):
		names.extend(
			(
				f"{benchmark}-uninstrumented-run-plain_c-{opt}-final",
				f"{benchmark}-uninstrumented-run-clank-0-0-{opt}-final",
				f"{benchmark}-uninstrumented-run-prowl-512-2-0-0-{opt}-final",
				f"{benchmark}-replay-cache-run-replay_cache-512-2-0-0-{opt}-final",
				f"{benchmark}-uninstrumented-run-nacho_pw_stcont-512-2-0-0-{opt}-final",
			)
		)
	return names


def _power_failure_result_names(benchmark: str) -> list[str]:
	names: list[str] = []
	for on_duration in POWER_ON_DURATIONS:
		checkpoint_period = on_duration // 2
		names.extend(
			(
				f"{benchmark}-uninstrumented-run-clank-{checkpoint_period}-{on_duration}-Os-final",
				f"{benchmark}-uninstrumented-run-prowl-512-2-{checkpoint_period}-{on_duration}-Os-final",
				f"{benchmark}-replay-cache-run-replay_cache-512-2-0-{on_duration}-Os-final",
				f"{benchmark}-uninstrumented-run-nacho_pw_stcont-512-2-{checkpoint_period}-{on_duration}-Os-final",
			)
		)
	return names


def expected_result_names() -> tuple[str, ...]:
	return tuple(
		name
		for benchmark in BENCHMARKS
		for name in (
			*_continuous_result_names(benchmark),
			*_optimization_result_names(benchmark),
			*_power_failure_result_names(benchmark),
		)
	)


def reference_result_name(benchmark: str, system: str) -> str:
	configurations = {
		"plain_c": f"{benchmark}-uninstrumented-run-plain_c-Os-final",
		"clank": f"{benchmark}-uninstrumented-run-clank-0-0-Os-final",
		"prowl": f"{benchmark}-uninstrumented-run-prowl-512-2-0-0-Os-final",
		"replay_cache": f"{benchmark}-replay-cache-run-replay_cache-512-2-0-0-Os-final",
		"nacho": f"{benchmark}-uninstrumented-run-nacho_pw_stcont-512-2-0-0-Os-final",
		"nacho_oracle": f"{benchmark}-uninstrumented-run-nacho_clank-512-2-0-0-Os-final",
	}
	return configurations[system]


def nacho_power_result_name(benchmark: str, on_duration: int) -> str:
	checkpoint_period = on_duration // 2
	return (
		f"{benchmark}-uninstrumented-run-nacho_pw_stcont-512-2-"
		f"{checkpoint_period}-{on_duration}-Os-final"
	)
