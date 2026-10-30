from __future__ import annotations

from pathlib import Path

from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor

# Files the README indicates running CINNAMON will produce.
EXPECTED_OUTPUTS = (
    "keyswitch_comparison.pdf",
    "bootstrap_comparison.pdf",
    "performance.pdf",
    "performance_per_dollar.pdf",
    "performance_table.txt",
)


def find_outputs_dir(
    workspace: Path, *, executor: RuntimeCheckExecutor
) -> Path | None:
    """Locates the outputs directory by its contents rather than its name.

    NOTE: The README's `mkdir -p asplos25_cinnamon_artifact/outputs` could 
    end up at a different location depending on where the agent runs it, 
    so search the workspace for the expected result files instead. As a
    simple heuristic, we select the directory with most files.
    """
    try:
        root = executor.resolve_path(workspace)
    except (OSError, ValueError):
        return None

    found: dict[Path, set[str]] = {}
    for name in EXPECTED_OUTPUTS:
        try:
            matches = executor.glob(workspace, f"**/{name}")
        except (OSError, NotImplementedError):
            continue
        for match in matches:
            # Glob results are executor-native; map them back under `workspace`.
            try:
                rel = Path(match).relative_to(root)
            except ValueError:
                continue
            if ".git" in rel.parts:
                continue
            found.setdefault(rel.parent, set()).add(name)

    if not found:
        return None

    best = min(
        found,
        key=lambda d: (-len(found[d]), d.name != "outputs", len(d.parts), str(d)),
    )
    return workspace / best
