# The eBPF file recorder; it runs next to the agent's container, never inside it.
# AEBench builds it on first use (runtime/file_events.py), tagged by a hash of
# this file and file_events.bt.
FROM ubuntu:26.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends bpftrace \
 && rm -rf /var/lib/apt/lists/*

COPY file_events.bt /opt/aebench/file_events.bt

ENTRYPOINT ["bpftrace", "/opt/aebench/file_events.bt"]
