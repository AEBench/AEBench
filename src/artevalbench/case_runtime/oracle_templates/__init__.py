from __future__ import annotations

from importlib import resources
from typing import Mapping

_TEMPLATE_PACKAGE = "artevalbench.case_runtime.oracle_templates"
_ORACLE_TEMPLATE_FILENAMES = (
    "custom.py",
    "env_setup.py",
    "artifact_build.py",
    "benchmark_prep.py",
    "experiment_runs.py",
)


def render_oracle_template_set(
    template_set: str,
    *,
    replacements: Mapping[str, str] | None = None,
) -> dict[str, str]:
    template_root = resources.files(_TEMPLATE_PACKAGE).joinpath(template_set)
    rendered: dict[str, str] = {}
    template_replacements = replacements or {}
    for filename in _ORACLE_TEMPLATE_FILENAMES:
        template_path = template_root.joinpath(f"{filename}.tmpl")
        rendered[filename] = _render_template_text(
            template_path.read_text(encoding="utf-8"),
            template_replacements,
        )
    return rendered


def _render_template_text(template_text: str, replacements: Mapping[str, str]) -> str:
    rendered = template_text
    for needle, value in replacements.items():
        rendered = rendered.replace(needle, value)
    return rendered


__all__ = ["render_oracle_template_set"]
