"""Authoring agent backend config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_DOCKER_IMAGE = "bastoica/ae-agent-ubuntu24.04:latest"

_KNOWN_BACKENDS = {
    "benchmate": "src/cli.py",
}


@dataclass(frozen=True, slots=True)
class AuthoringBackend:
    name: str
    repo_path: Path
    docker_image: str
    cli_module: str


def get_backend(name: str) -> AuthoringBackend:
    cli_module = _KNOWN_BACKENDS.get(name)
    if cli_module is None:
        raise ValueError(f"unknown authoring backend: {name!r} (known: {sorted(_KNOWN_BACKENDS)})")

    name_upper = name.upper().replace("-", "_")

    raw_repo = (
        os.environ.get(f"AEBENCH_AUTHORING_{name_upper}_REPO_PATH")
        or os.environ.get("AEBENCH_AUTHORING_REPO_PATH")
    )
    if not raw_repo:
        raise ValueError(f"backend {name}: repo_path not set (export AEBENCH_AUTHORING_REPO_PATH)")
    repo_path = Path(raw_repo).expanduser().resolve()

    raw_image = (
        os.environ.get(f"AEBENCH_AUTHORING_{name_upper}_DOCKER_IMAGE")
        or os.environ.get("AEBENCH_AUTHORING_DOCKER_IMAGE")
    )
    docker_image = raw_image or _DEFAULT_DOCKER_IMAGE

    return AuthoringBackend(
        name=name,
        repo_path=repo_path,
        docker_image=docker_image,
        cli_module=cli_module,
    )
