"""Neutrino 3rd Party Utilities

Please place each util as a module under neutrino.utils
Following are some common utilities"""

def get_tracedir() -> str:
    """Get or Create (if not yet here) the tracedir
    
    NOTE Impl of this shall match src/common.h !"""
    import os
    import time
    from datetime import datetime

    neutrino_dir = os.getenv("NEUTRINO_TRACEDIR")
    assert neutrino_dir is not None, "NEUTRINO_TRACEDIR must be set"
    if not os.path.isdir(neutrino_dir):
        os.mkdir(neutrino_dir)

    # 1. read the 22nd value of /proc/[pid]/stat (jiffies of proc start time)
    with open("/proc/self/stat", "r") as f:
        jiffies = int(f.read().split()[21])
    
    # 2. get system clock frequency (Hz, usually 100MHz)
    clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    
    # 3. read the systme boot time (second, since 1970)
    with open("/proc/uptime", "r") as f:
        uptime_seconds = int(float(f.read().split()[0]))
    
    # 4. compute absolute timestamp of proc boot time and format
    # NOTE we convert time() and uptime to int to match C algorithm, 
    #      or it's likely to have two folder with 1 second difference
    procstart = int(time.time()) - uptime_seconds + (jiffies / clk_tck)
    procstart = datetime.fromtimestamp(procstart)
    formatted = procstart.strftime("%b%d_%H%M%S") + "_" + str(os.getpid())
    trace_dir = os.path.join(neutrino_dir, formatted)

    if not os.path.isdir(trace_dir):
        os.mkdir(trace_dir)

    return trace_dir