"""Layer 6's option-literal guard (spec §2.2.8).

No string literal in ``declared.py`` or ``chain.py`` starts with a dash
followed by a letter, or with two dashes, except inside the tool-knowledge
tables of §3.3; SCALE_OPTIONS holds lower-case words only. So no option
name of an artifact can enter the code. The sources are read as text.
"""

import ast
import importlib.util
import pathlib
import re

import pytest

TABLES = frozenset(
    {
        *("INTERPRETERS", "INFO_OPTIONS", "MAKE_PATH_OPTIONS"),
        *("MAKE_VALUE_OPTIONS", "MAKE_DRY_OPTIONS", "INLINE_CODE"),
    }
)
OPTION = re.compile(r"-[A-Za-z]|--")


def _tree(name: str) -> ast.Module:
    """The module's source, found without importing it."""
    spec = importlib.util.find_spec(f"trajectory_checks.{name}")
    assert spec is not None and spec.origin, f"trajectory_checks.{name} missing"
    return ast.parse(pathlib.Path(spec.origin).read_text(encoding="utf-8"))


def _exempt(tree: ast.Module) -> set[int]:
    """The ids of the constants inside the allowed tables' assignments."""
    found = set()
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if names & TABLES:
            found |= {id(n) for n in ast.walk(node)}
    return found


@pytest.mark.parametrize("name", ["declared", "chain"])
def test_no_option_literals(name: str) -> None:
    """Option-like literals occur only in §3.3's tool-knowledge tables.

    Rules: KEY6, KEY7, SC1.
    """
    tree = _tree(name)
    exempt = _exempt(tree)
    hits = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and OPTION.match(node.value)
        and id(node) not in exempt
    ]
    assert hits == [], hits


def test_scale_options_are_words() -> None:
    """SCALE_OPTIONS holds lower-case words without leading dashes.

    Rules: SC1.
    """
    tree = _tree("chain")
    found = None
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if any(getattr(t, "id", None) == "SCALE_OPTIONS" for t in targets):
            found = [  # a literal, or a string of words split
                word
                for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                for word in n.value.split()
            ]
    assert found, "SCALE_OPTIONS not found"
    for word in found:
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", word), word
