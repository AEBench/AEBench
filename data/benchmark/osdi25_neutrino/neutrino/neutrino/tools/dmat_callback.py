"""Analyze the DMAT Output"""
import os
import sys
import subprocess
import struct
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

try:
    import numpy as np
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, hex2color
    import matplotlib.patches as mpatches
except:
    import pip
    pip.main(["install", "numpy"])
    import numpy as np
    pip.main(["install", "matplotlib"])
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, hex2color
    import matplotlib.patches as mpatches

# configure the x:y ratio and their scaling
Y = 10
X = 16
GRIDX, GRIDY = X * 10, Y * 10
DPI = 200

def sparsify(path: str) -> str:
    """Sparsify the raw trace to page reference map format
    NOTE This involves calling external C++ program named `dmat.cc`"""
    # First try to locate the .cc, it shall be in the same folder as this script
    out_path = path[:path.index(".bin")] + ".dmat"
    if os.path.exists(out_path):
        print(f"[note] {out_path} exists")
        return out_path
    CURDIR = os.path.dirname(os.path.realpath(__file__))
    contents = os.listdir(CURDIR)
    # Built it with g++ if it's not built
    cmd = ["g++", os.path.join(CURDIR, "dmat.cc"), "-o", os.path.join(CURDIR, "dmat"),
           "-O3", "-std=c++17"]
    # print(" ".join(cmd))
    if not "dmat" in contents:
        subprocess.check_call(cmd)
    # Now call it to apply the C++ program
    subprocess.check_call([os.path.join(CURDIR, "dmat"), path, out_path])
    return out_path

@dataclass
class Param:
    """We use shape as str to facilitate hash"""
    ptr: int
    size:  Optional[int] = 0
    shape: Optional[str] = ""
    name:  Optional[str] = ""

def read(path: str) -> List[Param]:
    """Read the metadata """
    with open(os.path.join(os.path.dirname(os.path.dirname(path)), "event.log"), "r", encoding='utf-8', errors='ignore') as f:
        event_logs = f.read().split("\n")
    # try to find the last 
    raw_params = set()
    for line in event_logs[::-1]:
        # NOTE remove jit.py because Triton will implicitly call many funcs
        if "[exec]" in line and "param" in line:
            tmp = line.split(" ")[:-1]
            for param in tmp[3:]:
                raw_params.add(int(param, base=16))
            break # JUST THE LAST RECORD
    with open(os.path.join(os.path.dirname(os.path.dirname(path)), "tensor.trace"), "r") as f:
        tensor_traces = f.read().split("\n")
    found = set()
    params: List[Param] = []
    for line in tensor_traces[::-1]:
        if line.startswith("[call]") or line.startswith("[ret]"):
            record = line.split("  ") # split by 2 spaces
            ptr=int(record[4])
            if ptr not in found:
                params.append(Param(
                    ptr = int(record[4]),
                    size = int(record[3]),
                    shape = record[2],
                    name = record[5]
                ))
                found.add(ptr)
    return params

# use our own colormap -> support up to 6 level and 6 colors
colors = [
    ListedColormap([(0, 0, 0, 0), hex2color("#ccddf7"), hex2color("#99baef"),  hex2color("#6698e6"), hex2color("#3375de"), hex2color("#0053d6")]), # blue
    ListedColormap([(0, 0, 0, 0), hex2color("#e6d5f9"), hex2color("#cdaaf3"),  hex2color("#b380ed"), hex2color("#9a55e7"), hex2color("#812be1")]), # purple
    ListedColormap([(0, 0, 0, 0), hex2color("#ccf1cc"), hex2color("#99e499"),  hex2color("#66d666"), hex2color("#33c933"), hex2color("#00bb00")]), # green
    ListedColormap([(0, 0, 0, 0), hex2color("#f0d1cd"), hex2color("#e1a39b"),  hex2color("#d17469"), hex2color("#c24637"), hex2color("#b31805")]), # red
    ListedColormap([(0, 0, 0, 0), hex2color("#f0e0d6"), hex2color("#e0c1ad"),  hex2color("#d1a383"), hex2color("#c1845a"), hex2color("#b26531")]), # yellow
    # ListedColormap(), # 
]

def plot(path: str, params: List[Param]):
    """Draw the DMAT Plot"""
    unique_pages: List[int] = []
    page_reference_map: Dict[int, Dict[int, int]] = dict()
    max_clock: int = 0
    with open(path, "rb") as f:
        num_pages, num_clocks = struct.unpack("QQ", f.read(16))
        # print(num_pages, num_clocks)
        for _ in range(num_pages):
            unique_pages.append(struct.unpack("Q", f.read(8))[0])
        for _ in range(num_clocks):
            clock = struct.unpack("Q", f.read(8))[0]
            max_clock = max(clock, max_clock)
            size  = struct.unpack("Q", f.read(8))[0]
            page_reference_map[clock] = dict()
            for _ in range(size):
                data = f.read(12)
                if len(data) == 12:
                    page, count = struct.unpack("QI", data)
                    page_reference_map[clock][page] = count
    
    unique_pages = sorted(unique_pages)
    # print(unique_pages)

    # print(unique_pages)
    # now pages are sorted ascendingly -> distinguish into groups
    page_group_start = [unique_pages[0]]
    page_group_sizes = []
    current_size = 1
    for i in range(1, len(unique_pages)):
        if unique_pages[i] - unique_pages[i - 1] > 2 ** 16: # new group
            page_group_sizes.append(current_size)
            page_group_start.append(unique_pages[i])
            current_size = 1
        else: # prev group
            current_size += 1
    page_group_sizes.append(current_size)
    # print(page_group_start)
    # print(page_group_sizes)
    # group name is the starting address and 
    page_to_id = {page: i for i, page in enumerate(unique_pages)}

    # need to have a grid
    page_to_gridy  = len(unique_pages) // (GRIDY - 1)
    clock_to_gridx = max_clock // (GRIDX - 1)

    # Flatten the record
    #clocks: List[int] = []
    #page_ids: List[int] = []
    counts: List[int] = []
    param_matches: Dict[int, Tuple[List[int], List[int], List[int], str, str]] = {i: ([], [], [], p.shape, p.name) for i, p in enumerate(params)}  # page_id: [(clock, param_index, shape)]
    
    # NOTE Fix: Add a unmatched group
    param_matches[len(param_matches)] = ([], [], [], "Unknown", "Unknown")

    # group_matches: Dict[int, Tuple[List[int], List[int], List[int]]] = {i: ([], [], []) for i in range(len(page_group_start))} # page_id -> group_start, group_size
    for clock, items in page_reference_map.items():
        if clock < 5000000: # a useless filter for safety
            for page, count in items.items():
                page_id = page_to_id[page]
                #clocks.append(clock)
                #page_ids.append(page_id)
                #counts.append(count)
                matched = False
                for i, param in enumerate(params):
                    if param.size > 0 and param.ptr <= page <= param.ptr + param.size: # size is raw bytes
                        param_matches[i][0].append(clock)
                        param_matches[i][1].append(page_id)
                        param_matches[i][2].append(count)
                        matched = True
                if not matched:
                    param_matches[len(param_matches) - 1][0].append(clock)
                    param_matches[len(param_matches) - 1][1].append(page_id)
                    param_matches[len(param_matches) - 1][2].append(count)
        else:
            print(f"Find Weird Data {clock}", file=sys.stderr) # might be bugs

    # print(param_matches)

    # filter out unused group
    plotted_matches: List[Tuple[List[int], List[int], List[int], str, str]] = []
    for match in param_matches.values():
        if len(match[0]) > 0:
            plotted_matches.append(match)

    # print(len(plotted_matches))
    dist: List[np.ndarray] = []

    # Create the figure and axis
    fig, ax = plt.subplots(figsize=(X, Y), dpi=DPI)

    n = min(len(plotted_matches), 5)

    sys.stdout.write('\r')
    sys.stdout.write(f"Ploting Tensors: [{' '*5*n}] 0/{n}")
    sys.stdout.flush()

    for i in range(n): # at most 5 now
        tmp = np.zeros((Y * 10 + 1, X * 10 + 1), dtype=np.int32)
        for clock, page, count in zip(plotted_matches[i][0], plotted_matches[i][1], plotted_matches[i][2]):
            tmp[Y * 10 - page // page_to_gridy, clock // clock_to_gridx] += count
        # max_ = tmp.max()
        dist.append(tmp.flatten())
        boundaries = np.percentile(tmp[tmp != 0], [20, 40, 60, 80])
        boundaries = np.concatenate(([0], boundaries))
        # cut into five region based on percentile
        temp = np.zeros_like(tmp)
        for j in range(len(boundaries)):
            temp[boundaries[j] < tmp] = j + 1
        ax.imshow(temp, cmap=colors[i])

        sys.stdout.write('\r')
        sys.stdout.write(f"Ploting Tensors: [{'='*5*(i+1)}{' '*5*(n-(i+1))}] {i+1}/{n}")
        sys.stdout.flush()

    # Set the ticks and labels
    ax.set_xticks(np.arange(0, X * 10, 10))
    ax.set_xticklabels([f'{int(max_clock / X * i)}' for i in range(X)], rotation=45)
    ax.set_yticks(np.arange(0, Y * 10, 10))
    ax.set_yticklabels([f'{int(len(unique_pages) / Y * i)}' for i in range(Y, 0, -1)])
    
    # Manually draw grid lines
    for x in range(X * 10 + 1):  # Vertical lines
        ax.axvline(x - 0.5, color='lightgrey', linewidth=0.4)

    for y in range(Y * 10 + 1):  # Horizontal lines
        ax.axhline(y - 0.5, color='lightgrey', linewidth=0.4)

    # Create handles for the legend
    handles = [mpatches.Patch(color=colors[i].colors[-1], label=f'Ptr {i}: {plotted_matches[i][3]}, {plotted_matches[i][4]}') for i in range(min(len(plotted_matches), 5))]
    plt.legend(handles=handles, title=f"Tensor", loc='lower left')

    plt.title('Page Reference Map')
    plt.xlabel('Clock')
    plt.ylabel('Pages')

    # Save the figure
    plt.tight_layout()
    plt.savefig(path[:path.index(".dmat")] + ".png")
    plt.close(fig)
    print(f'\n[info] save to {path[:path.index(".dmat")] + ".png"}')

if __name__ == "__main__":
    path = sys.argv[1]
    sparsified = sparsify(path)
    params = read(path)
    plot(sparsified, params)