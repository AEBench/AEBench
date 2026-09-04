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

# Install pinned CLI harness versions.
ARG CLAUDE_CODE_VERSION=2.1.157
ARG CODEX_VERSION=0.146.0

RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g \
    "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    "@openai/codex@${CODEX_VERSION}" \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --frozen --no-dev --no-install-project

RUN groupadd --gid 1000 agent \
 && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash agent \
 && printf 'agent ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/aebench-agent \
 && chmod 0440 /etc/sudoers.d/aebench-agent

CMD ["bash"]
