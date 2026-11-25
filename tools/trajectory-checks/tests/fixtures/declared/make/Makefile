RUNS ?= 10
OUT := out

all: bench

bench:
	@./bench.sh --runs $(RUNS) --out $(OUT)
	-$(MAKE) -C sub t
	+echo ${UNKNOWN}

prep: ; @true
