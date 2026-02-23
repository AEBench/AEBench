from neutrino import probe, Map
import neutrino.language as nl

@Map(level="thread", type="array", size=8, cap=1)
class TensorOpCount:
    count: nl.u64

counter: nl.u64 = 0

@probe(level="thread", pos="kernel", before=True)
def init():
    counter = 0

@probe(level="thread", pos="mma.sync.aligned")
def count():
    counter += 1

@probe(level="thread", pos="kernel")
def save():
    TensorOpCount.save(counter)