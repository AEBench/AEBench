# The recorder runs next to the agent's container, never inside it:
#   docker build -t aebench-ebpf-recorder tools/ebpf-tracing
#   docker run --rm --privileged --pid=host aebench-ebpf-recorder <cgroup id>
FROM ubuntu:26.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends bpftrace \
 && rm -rf /var/lib/apt/lists/*

COPY file_events.bt /opt/aebench/file_events.bt

ENTRYPOINT ["bpftrace", "/opt/aebench/file_events.bt"]
