# Neutrino Auto-Generated Code for Trace Reading
import struct
from typing import NamedTuple, List, Tuple, Dict
from neutrino import TraceHeader, TraceSection


class block_sched(NamedTuple):
    start: int
    elapsed: int
    cuid: int


def parse(path: str) -> Tuple[TraceHeader, List[TraceSection], Dict[str, List[List[NamedTuple]]]]:
    with open(path, "rb") as f:
        header: TraceHeader = TraceHeader(*struct.unpack("iiiiiiii", f.read(32)))
        sections: List[TraceSection] = []
        for _ in range(header.numProbes):
            sections.append(TraceSection(*struct.unpack("IIQ", f.read(16))))
        gridSize = header.gridDimX * header.gridDimY * header.gridDimZ
        blockSize = header.blockDimX * header.blockDimY * header.blockDimZ
        records: Dict[str, List[List[NamedTuple]]] = dict()

        # Read block_sched
        records["block_sched"] = []
        f.seek(sections[0].offset)
        for i in range(gridSize):
            records["block_sched"].append([])
            for j in range(blockSize // sections[0].warpDiv):
                records["block_sched"][-1].append([])
                for k in range(sections[0].size // 16):
                    records["block_sched"][i][j].append(block_sched(*struct.unpack("qII", f.read(16))))

    return header, sections, records
# END of Neutrino Auto-Generated Code for Trace Reading
import sys
import numpy as np
header, sections, records_map = parse(sys.argv[1]) # filled by path to trace

records = records_map["block_sched"]

unique_sms = set()    
for block in records:
    unique_sms.add(block[0][0].cuid)

sm_timelines = []
for _ in range(len(unique_sms)):
    sm_timelines.append([])
sched_times = [0.0] * len(unique_sms)
work_times = [0.0] * len(unique_sms)

for cur in records:
    # print(sm_timelines[cur[0].cuid])
    sched_out = False
    cuid = cur[0][0].cuid
    if len(sm_timelines[cuid]) > 0:
        for block in sm_timelines[cuid]:
            if block.start + block.elapsed <= cur[0][0].start:
                # if cur[0].lstart - (block.lstart + block.elapse) < 100000:
                #     print(cur[0], block)
                sched_times[cuid] += cur[0][0].start - (block.start + block.elapsed)
                sm_timelines[cuid].remove(block)
                sm_timelines[cuid].append(cur[0][0])
                work_times[cuid] += cur[0][0].elapsed
                sched_out = True
                break
            if not sched_out:
                sm_timelines[cuid].append(cur[0][0]) 
                work_times[cuid] += cur[0][0].elapsed
                break
    else:
        sm_timelines[cuid].append(cur[0][0]) 
        work_times[cuid] += cur[0][0].elapsed

print(f"No.block:{header.gridDimX * header.gridDimY  * header.gridDimZ} Running:{int(np.array(work_times).mean())} Scheduling:{int(np.array(sched_times).mean())}(cycle)")
