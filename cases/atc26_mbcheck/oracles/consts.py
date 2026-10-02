from __future__ import annotations

# The README places SVF, the kernel tree and the IR corpus under "~"; in the
# Docker runtime that is the agent user's home.
AGENT_HOME = "/home/agent"
SVF_DIR = f"{AGENT_HOME}/SVF"
KERNEL_DIR = f"{AGENT_HOME}/linux-6.19"
CORPUS_DIR = f"{AGENT_HOME}/ir-v6.19"

MBCHECK_BIN = "build/mbcheck"
SWEEP_JSON = "results/sweep-v619.json"
SWEEP_LITMUS_DIR = "results/litmus-v619"
SUITE_IR_DIR = "results/sync-ir"
TABLE8_JSON = "results/table8.json"
RESULTS_REF = "mbcheck_results.ref.json"

# opam only puts herd7 on PATH through `opam env`; a login shell also picks up
# whatever the agent added to ~/.profile.
HERD7_ENV = 'command -v opam >/dev/null 2>&1 && eval "$(opam env 2>/dev/null)"; '

# Table 10: the 23 correctly synchronised microbenchmark modules.
SYNC_CASES = (
	"01_rcu_grace",
	"02_spinlock",
	"03_init_exit",
	"04_seqlock_ww",
	"05_mutex",
	"06_rwsem_ww",
	"07_rtnl_lock",
	"08_completion",
	"09_seqlock_rw",
	"10_srcu",
	"11_device_probe",
	"12_bh_teardown",
	"13_kref_release",
	"14_irq_sync",
	"15_dma",
	"16_sysfs",
	"17_regmap",
	"18_iomem",
	"19_conservative",
	"20_callgraph",
	"21_pm_lifecycle",
	"22_read_read",
	"23_initstub",
)
