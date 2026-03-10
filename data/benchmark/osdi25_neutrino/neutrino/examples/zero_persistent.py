import torch
import triton
import triton.language as tl
@triton.jit
def zero_persistent_kernel(output_ptr, numel,
  BLOCK_SIZE: tl.constexpr, NUM_SMS: tl.constexpr):
  start_pid = tl.program_id(axis=0)
  num_blocks = tl.cdiv(numel, BLOCK_SIZE)
  blocks_per_sm = num_blocks // NUM_SMS
  if start_pid < num_blocks % NUM_SMS:
    blocks_per_sm += 1
  block_id = start_pid - NUM_SMS
  for _ in range(blocks_per_sm):
    block_id += NUM_SMS
    offsets=block_id*BLOCK_SIZE+tl.arange(0,BLOCK_SIZE)
    mask = offsets < numel
    tl.store(output_ptr + offsets, 
      tl.zeros([BLOCK_SIZE], dtype=tl.float16), mask)
def zero_persistent(x: torch.Tensor):
  numel = x.numel()
  NUM_SMS = torch.cuda.get_device_properties("cuda")\
                      .multi_processor_count
  BLOCK_SIZE = 128
  grid = lambda META: (min(NUM_SMS, 
     triton.cdiv(numel, META['BLOCK_SIZE'])),)
  zero_persistent_kernel[grid](
    x, numel, BLOCK_SIZE, NUM_SMS)
t=torch.empty((4096,4096),torch.float16,device="cuda")
zero_persistent(t)
