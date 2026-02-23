from neutrino import probe, Map
import neutrino.language as nl

@Map(level="thread", type="array", size=8, cap=128)
class Sample:
    clock: nl.u64

@probe(level="thread", pos="bra")
def bra_sample():
    Sample.save(nl.clock())