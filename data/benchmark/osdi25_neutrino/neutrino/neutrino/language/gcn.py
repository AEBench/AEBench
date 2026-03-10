"""Generate the AMD GCN Assembly, a x86-like asm

NOTE Currently only targets CDNA branch of GCN, covering MI100/200/300/325
This is because AMD's Assembly diverge into CDNA/RDNA in 2020, before that
there's only one architecture named GCN (so as the name of GCNAsm).

CDNA and RDNA shares the same syntax inherited from GCNAsm, but has slight
difference in instruction set, for example, CDNA use `S_MEMTIME S[0:1]` to
read the clock in 64bit but RDNA use `S_GETREG S0, SHADER_CYCLES` in 32bit

We plan to support CDNA arch first and then port to RDNA arch later.
"""