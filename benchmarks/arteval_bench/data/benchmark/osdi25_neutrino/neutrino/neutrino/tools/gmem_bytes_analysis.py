# Neutrino Auto-Generated Code for Trace Reading
import struct
from typing import NamedTuple, List, Tuple
from neutrino import TraceHeader, TraceSection

class saving(NamedTuple):
	sync_bytes: int
	async_bytes: int


def parse(path: str) -> Tuple[TraceHeader, List[TraceSection], List[List[saving]]]:
    with open(path, "rb") as f:
        gridDimX, gridDimY, gridDimZ, blockDimX, blockDimY, blockDimZ, sharedMemBytes, numProbes = struct.unpack("iiiiiiii", f.read(32))
        header: TraceHeader = TraceHeader(gridDimX, gridDimY, gridDimZ, blockDimX, blockDimY, blockDimZ, sharedMemBytes, numProbes)
        assert header.numProbes == 1 # currently only one saving probe is supported
        sections: List[TraceSection] = []
        for _ in range(header.numProbes):
            size, offset = struct.unpack("QQ", f.read(16))
            sections.append(TraceSection(size, offset))
        gridSize = header.gridDimX * header.gridDimY * header.gridDimZ
        blockSize = header.blockDimX * header.blockDimY * header.blockDimZ
        records: List[List[saving]] = []
        for i in range(gridSize):
            records.append([])
            for j in range(blockSize):
                sync_bytes, async_bytes = struct.unpack("II", f.read(8))
                records[i].append(saving(sync_bytes, async_bytes))
        return header, sections, records
# END OF GENERATED CODE
import sys
header, sections, records = parse(sys.argv[1]) # filled by path to trace

gridSize = header.gridDimX * header.gridDimY * header.gridDimZ
blockSize = header.blockDimX * header.blockDimY * header.blockDimZ
gmem_bytes = 0
for i in range(gridSize):
    for j in range(blockSize):
         gmem_bytes += records[i][j].sync_bytes + records[i][j].async_bytes

print(f"gmem_bytes:{gmem_bytes}")