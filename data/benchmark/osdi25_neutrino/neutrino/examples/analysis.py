# Neutrino Generated Code for Reading Trace
import struct
from typing import NamedTuple, List, Tuple
from neutrino import TraceHeader, TraceSection
class block_sched(NamedTuple):
  start: int
  elapsed: int
  cuid: int
def parse(path: str):
  with open(path, "rb") as f:
    header: TraceHeader = TraceHeader(
      struct.unpack("iiiiiiii", f.read(32)))
    sections: List[TraceSection] = []
    for _ in range(header.numProbes):
      size, offset = struct.unpack("QQ", f.read(16))
      sections.append(TraceSection(size, offset))
    gridSize = header.gridDimX * header.gridDimY 
                               * header.gridDimZ
    blockSize = header.blockDimX * header.blockDimY
                                 * header.blockDimZ
    records: List[List[block_sched]] = []
    for i in range(gridSize):
      records.append([])
      for j in range(blockSize):
        start, elapsed, cuid = struct.unpack(
                                 "QII", f.read(16))
        records[i].append(
                 block_sched(start, elapsed, cuid))
  return header, sections, records
# END OF GENERATED CODE
import numpy as np
header, sections, records = parse(sys.argv[1])
unique_cus = set()    
for block in records:
  unique_cus.add(block[0].cuid)
cu_timelines = [[]] * len(unique_cus)
sched_times = [0.0] * len(unique_cus)
work_times = [0.0] * len(unique_cus)
for cur in records:
  sched_out = False
  for block in cu_timelines[cur[0].cuid]:
    if block.start+block.elapsed<=cur[0].start:
      sched_times[cur[0].cuid]+=cur[0].start 
                 - (block.start + block.elapsed)
      cu_timelines[cur[0].cuid].remove(block)
      cu_timelines[cur[0].cuid].append(cur[0])
      work_times[cur[0].cuid] += cur[0].elapsed
      sched_out = True
      break
    if not sched_out:
      cu_timelines[cur[0].cuid].append(cur[0]) 
      work_times[cur[0].cuid] += cur[0].elapsed
print(np.array(sched_times).mean(), 
      np.array(work_times).mean())
