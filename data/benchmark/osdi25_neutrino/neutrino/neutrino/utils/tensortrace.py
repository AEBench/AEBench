"""Obtain High-Level Tensor Information from PyTorch or other framework

USAGE `TensorTrace` for with-statement and `tensortrace` for function wrapper
TODO Support JAX via arr.__cuda_array_interface__['data'] for .data_ptr()
INTERNAL Use Python's built-in sys.settrace to track call frames"""

import sys
import os
import time
from typing import Callable, TextIO
from functools import wraps
import torch
from neutrino.utils import get_tracedir

__all__ = ["TensorTrace", "tensortrace"]

def get_time() -> int:
    """Python Equivalent of C Style Get Time:
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    long long time = ts.tv_nsec + ts.tv_sec * 1e9;
    """
    ts = time.clock_gettime(time.CLOCK_REALTIME)
    seconds = int(ts)
    nanoseconds = int((ts - seconds) * 1e9)
    return nanoseconds + seconds * int(1e9)

# We use a closure to specify the holder
def callframe_tracer_wrapper(f: TextIO) -> Callable:

    # a trace function conforms sys.settrace() interface
    def trace_calls(frame, event, arg):
        if event == 'call' and "jit" not in frame.f_code.co_filename:
            code = frame.f_code
            func_name = code.co_name
            func_filename = code.co_filename
            func_line_no = frame.f_lineno
            for i, varname in enumerate(code.co_varnames):
                if i < frame.f_code.co_argcount:
                    if isinstance(frame.f_locals[varname], torch.Tensor):
                        print(f"[call]  {get_time()}  {frame.f_locals[varname].shape}  {frame.f_locals[varname].untyped_storage().nbytes()}  {frame.f_locals[varname].data_ptr()}  {varname}  {func_name}  {func_filename}:{func_line_no}", flush=True, file=f)
        # NOTE might not need return because we pause the exec so it shall stall in call
        # elif event == 'return' and "jit" not in code.co_filename:
        #     code = frame.f_code
        #     func_name = code.co_name
        #     func_filename = code.co_filename
        #     func_line_no = frame.f_lineno
        #     if isinstance(arg, torch.Tensor):
        #         print(f"[ret]  {get_time()}  {arg.shape}  {arg.untyped_storage().nbytes()}  {arg.data_ptr()}  {func_name}  {func_filename}:{func_line_no}", flush=True, file=f)
        #     elif isinstance(arg, tuple):
        #         for arg_i in arg:
        #             if isinstance(arg_i, torch.Tensor):
        #                 print(f"[ret]  {get_time()}  {arg_i.shape}  {arg_i.untyped_storage().nbytes()}  {arg_i.data_ptr()}  {func_name}  {func_filename}:{func_line_no}", flush=True, file=f)

        return trace_calls
    
    return trace_calls

class TensorTrace:
    """A context manager to trace call call stacks"""
    def __enter__(self): 
        trace_file: TextIO
        if os.getenv("NEUTRINO_TRACEDIR") is not None:
            trace_dir = get_tracedir()
            # print(f"[info] tensor trace in {os.path.join(trace_dir, 'tensor.trace')}", file=sys.stderr)
            trace_file = open(os.path.join(trace_dir, "tensor.trace"), "w+")
        else: 
            trace_file = sys.stderr
        sys.settrace(callframe_tracer_wrapper(f=trace_file))

    def __exit__(self, exc_type, exc_value, traceback):
        sys.settrace(None) # clear the trace function 

def tensortrace(func: Callable) -> Callable:
    """A decorator to apply TensorTrace to a function."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        with TensorTrace():
            return func(*args, **kwargs)
    return wrapper