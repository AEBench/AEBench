FROM ghcr.io/vmware-research/verifiable-controllers/verus:1a9e3c577847b4a230ff7fe15b5b080a71cdf3c8 as builder

WORKDIR /anvil

SHELL ["/bin/bash", "-c"]

COPY . .

RUN apt-get update && \
    apt-get install -y pkg-config libssl-dev python3 python3-pip
RUN pip3 install tabulate
