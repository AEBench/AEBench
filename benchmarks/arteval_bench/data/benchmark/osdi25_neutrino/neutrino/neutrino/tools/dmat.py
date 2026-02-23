from neutrino import probe, Map
import neutrino.language as nl

CALLBACK = "dmat_callback.py"

@Map(level="thread", type="array", size=16, cap="dynamic")
class DMAT:
    clock: nl.u64
    addr:  nl.u64

start: nl.u64 = 0
mem_clock: nl.u64 = 0

# define probes with decorator
@probe(pos="kernel", level="thread", before=True)
def thread_start():
    start = nl.clock()

@probe(pos="ld.global:st.global:cp.async.cg:cp.async.ca", level="thread")
def memory_access():
    mem_clock = nl.clock() - start
    DMAT.save(mem_clock, nl.addr)