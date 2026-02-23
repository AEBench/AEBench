from neutrino import probe, Map
import neutrino.language as nl

CALLBACK = "block_sched_callback.py" # for trace analysis

# declare maps for persistence
@Map(level="warp", type="array", size=16, cap=1)
class block_sched:
    start: nl.u64
    elapsed: nl.u32
    cuid: nl.u32

# declare probe registers shared across probes
start: nl.u64 = 0 # starting clock
elapsed: nl.u64 = 0 # elapsed time, initialized to 0

# define probes with decorator
@probe(pos="kernel", level="warp", before=True)
def thread_start():
    start = nl.clock()

@probe(pos="kernel", level="warp")
def thread_end():
    elapsed = nl.clock() - start
    block_sched.save(start, elapsed, nl.cuid())