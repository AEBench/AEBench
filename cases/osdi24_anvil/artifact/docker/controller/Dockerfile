FROM ghcr.io/vmware-research/verifiable-controllers/verus:1a9e3c577847b4a230ff7fe15b5b080a71cdf3c8 as builder

ARG APP
WORKDIR /anvil

SHELL ["/bin/bash", "-c"]

COPY . .

RUN apt-get update && \
    apt-get install -y pkg-config libssl-dev

RUN VERUS_DIR=/verus ./build.sh ${APP}_controller.rs --no-verify --time
RUN mv /anvil/src/${APP}_controller /anvil/src/controller

# =============================================================================

FROM debian:bullseye-slim

COPY --from=builder /anvil/src/controller /usr/local/bin/controller

ENTRYPOINT ["/usr/local/bin/controller", "run"]
