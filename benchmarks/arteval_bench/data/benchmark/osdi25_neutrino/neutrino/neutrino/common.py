"""Neutrino Internal APIs, not for user import"""

from typing import Optional, Literal, Union
from dataclasses import dataclass

@dataclass
class Register:
    name: str
    dtype: Literal['u32', 'u64']
    init: Optional[int] = None


@dataclass
class Probe:
    name:   str                       # name is the key in TOML
    level:  Literal["thread", "warp"] # level of the probe
    pos:    list[str]                 # := tracepoint in the paper
    before: Union[list, str] = None   # snippet inserted before, one of before and after shall be given
    after:  Union[list, str] = None   # snippet inserted after,  one of before and after shall be given


@dataclass
class Map:
    name:   str 
    level:  Literal["thread", "warp"]
    type:   Literal["array"]
    size:   int
    cap:    Union[int, Literal["dynamic"]]
    regs:   list[Register]


def load(raw: dict) -> tuple[list[Probe], list[Map], int]:
    """Unserialize Neutrino probes in Python dict to probes, maps, regs"""
    assert "probe" in raw.keys() and "map" in raw.keys(), "At least a probe and a map"
    probes: list[Probe] = []
    maps: list[Map] = []
    for name, probe in raw["probe"].items():
        # first validate the 
        keys = probe.keys()
        assert "position" in keys or "pos" in keys, f"[error] {name} has no position (required)"
        # assert "datamodel" in keys, f"[error] "
        assert "before" in keys or "after" in keys, f"[error] {name} is empty, one of before or after shall be given"
        assert "level" in keys and probe["level"] in ("warp", "thread"), f"[error] level must be given and one of 'warp', 'thread'"
        probes.append(Probe(name=name,
                            level=probe["level"], 
                            pos=probe["pos"].split(":"), 
                            before=probe["before"] if "before" in keys else None,
                            after=probe["after"] if "after" in keys else None))
    for name, map_ in raw["map"].items():
        maps.append(Map(name=name, 
                        level=map_["level"], 
                        type=map_["type"], 
                        size=map_["size"], 
                        cap=map_["cap"], 
                        regs=[Register(name, val[0], init=val[1]) for name, val in map_["regs"].items()]))
    return probes, maps, raw["regs"]


def dump(probes, maps, regs, callback = "") -> dict:
    """Serialize Neutrino probes to Python dict"""
    dict_probe = { 
        "regs": regs, 
        "probe" : {p.name: {"level": p.level, "pos": p.pos, "before": p.before, "after": p.after} for p in probes},
        "map": {m.name: {"level": m.level, "type": m.type, "size": m.size, "cap": m.cap, "regs": {r.name: [r.dtype, r.init] for r in m.regs}} for m in maps}
    }
    if len(callback) > 0:
        dict_probe["CALLBACK"] = callback
    return dict_probe