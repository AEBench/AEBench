from neutrino import probe, Map
import neutrino.language as nl

CALLBACK = "gmem_bytes_analysis.py"

@Map(level="thread", type="array", size=8, cap=1)
class GMEMBytes:
    sync_bytes: nl.u32
    async_bytes: nl.u32

sync_bytes:  nl.u64 = 0
async_bytes: nl.u64 = 0

@probe(level="thread", pos="kernel", before=True)
def init():
    sync_bytes = 0
    async_bytes = 0

@probe(level="thread", pos="ld.global:st.global")
def record_sync():
    sync_bytes += nl.bytes

@probe(level="thread", pos="cp.async.ca:cp.async.cg")
def record_async():
    async_bytes += nl.bytes

@probe(level="thread", pos="kernel")
def save():
    GMEMBytes.save(sync_bytes, async_bytes)