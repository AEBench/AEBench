################# shim build stage #################
ARG RUST_VERSION=1.83
FROM rust:${RUST_VERSION}-slim-bookworm AS shim-builder

WORKDIR /build/shim
COPY src/runtime/shim ./
RUN cargo build --release --locked


################# agent installers #################
# Keeps only each agent's install.sh, so editing an agent.sh or agent.toml does
# not invalidate the install layer in the final image (those files are copied
# into every run, not baked into the image).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS agent-installers

COPY agents /agents
RUN find /agents -type f ! -name install.sh -delete \
 && find /agents -mindepth 1 -type d -empty -delete


################# everything else! #################
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

USER root
WORKDIR /opt/artevalbench

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git \
    jq \
    pipx \
    sudo \
    wget \
 && rm -rf /var/lib/apt/lists/*

ARG COMPOSE_VERSION=v2.35.1
ARG DOCKER_CLI_VERSION=29.1.3

RUN arch="$(dpkg --print-architecture)" \
 && case "$arch" in \
      amd64) docker_arch="x86_64" ;; \
      arm64) docker_arch="aarch64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac \
 && curl -fsSL "https://download.docker.com/linux/static/stable/${docker_arch}/docker-${DOCKER_CLI_VERSION}.tgz" \
    -o /tmp/docker.tgz \
 && tar -xzf /tmp/docker.tgz --strip-components=1 -C /usr/local/bin docker/docker \
 && rm /tmp/docker.tgz \
 && mkdir -p /usr/local/lib/docker/cli-plugins \
 && curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${docker_arch}" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose \
 && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Node.js 22 is part of the base environment that agents and artifacts see.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# Agent harnesses: runs agents/<name>/install.sh for every agent that has one.
# Each pins its own version; see docs/howtos/add_agent.md.
COPY --from=agent-installers /agents /opt/aebench/agent-installers
RUN set -e; \
    for script in /opt/aebench/agent-installers/*/install.sh; do \
      [ -e "$script" ] || continue; \
      name="$(basename "$(dirname "$script")")"; \
      echo "Installing agent harness: $name"; \
      mkdir -p "/opt/aebench/agents/$name"; \
      AEBENCH_AGENT_NAME="$name" AEBENCH_AGENT_PREFIX="/opt/aebench/agents/$name" \
        bash -euo pipefail "$script"; \
    done; \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --frozen --no-dev --no-install-project

RUN groupadd --gid 1000 agent \
 && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash agent \
 && printf 'agent ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/aebench-agent \
 && chmod 0440 /etc/sudoers.d/aebench-agent

################# shim copy #################
COPY --from=shim-builder /build/shim/target/release/aeshell /usr/lib/aebench/aeshell
RUN chmod 0755 /usr/lib/aebench/aeshell

CMD ["bash"]
