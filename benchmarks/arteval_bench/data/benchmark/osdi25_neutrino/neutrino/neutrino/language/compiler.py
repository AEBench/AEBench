"""CLI Entry of Language Submodule"""
from neutrino.common import Probe, dump
from neutrino.language.frontend import parse

def compile(mode: str, source: str) -> tuple[str, str]:
    """Compile the Tracing DSL into Assembly Probes"""
    regs, probes, maps, callback = parse(source)
    if mode == "CUDA":
        from neutrino.language.ptx import gencode
        probes = gencode(probes)
    elif mode == "HIP":
        from neutrino.language.gcn import gencode
        probes = gencode(probes)

    # NOTE Merge probes of the same level and pos
    merged_probes: dict[tuple[str, str], Probe] = {}
    for probe in probes:
        key = (probe.level, probe.pos)
        if key not in merged_probes:
            merged_probes[key] = probe
        else: # merge
            merged_probes[key].name += "_" + probe.name
            merged_probes[key].before = (
                (merged_probes[key].before or "") + (probe.before or "")
                if merged_probes[key].before or probe.before
                else None
            )
            merged_probes[key].after = (
                (merged_probes[key].after or "") + (probe.after or "")
                if merged_probes[key].after or probe.after
                else None
            )
    probes = list(merged_probes.values())

    dumped = dump(probes, maps, regs, callback)

    for map_ in maps:
        if map_.cap == "dynamic":
            dumped["dynamic"] = True

    return dumped

if __name__ == "__main__": # A small test case
    import sys
    import toml
    mode, source = sys.argv[1], sys.argv[2]
    source = open(source, "r").read()
    asm_probes = compile(mode, source)
    print(toml.dumps(asm_probes))