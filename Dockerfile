FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG COMPOSE_VERSION=v2.35.1

USER root
WORKDIR /opt/artevalbench

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    docker.io \
    git \
    jq \
    pipx \
    wget \
 && rm -rf /var/lib/apt/lists/*

RUN arch="$(dpkg --print-architecture)" \
 && case "$arch" in \
      amd64) compose_arch="x86_64" ;; \
      arm64) compose_arch="aarch64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac \
 && mkdir -p /usr/local/lib/docker/cli-plugins \
 && curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${compose_arch}" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose \
 && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --frozen --no-dev --no-install-project
RUN uv tool install swe-rex

ENV PATH="/root/.local/bin:${PATH}"

CMD ["bash"]
