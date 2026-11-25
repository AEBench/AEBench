"""Layer 6: declared.declare on the fixture repositories (spec §2.2.1).

Each fixture under ``tests/fixtures/declared/<name>/`` is a tiny,
hand-written repository with a ``case.toml`` and the hand-written
``expected_invocations.json`` and ``expected_options.json``; how each was
made and what it checks is in ``fixtures/declared/EDITS.md``. No fixture
holds a ``.git`` folder, so every commit comes from the case's ref.
``declared`` is imported inside each test (D46).
"""

import json
import pathlib

import chain_helpers as helpers
import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

FIXTURES = helpers.DECLARED_FIXTURES_DIR
REF = "declared commit from case.toml ref"
# fixture -> (files_read, the warnings besides the commit one)
META = {
    "fences": (["README.md"], []),
    "cwd": (["README.md"], []),
    "links": (["README.md", "docs/run.md", "sub/README.md"], []),
    "placeholders": (["README.md"], []),
    "argparse": (
        ["README.md", "tools/broken.py", "tools/lib.py", "tools/run.py"],
        ["tools/broken.py: syntax error, no defaults"],
    ),
    "make": (
        ["Makefile", "README.md", "sub/Makefile"],
        ["make target missing not found in Makefile"],
    ),
    "ci_excluded": (["README.md"], []),
    "case_lines": (["README.md"], []),
    "none": ([], ["instructions README.md missing"]),
}


def _load(name: str) -> tuple[pathlib.Path, dict]:
    folder = FIXTURES / name
    with (folder / "case.toml").open("rb") as handle:
        return folder, tomllib.load(handle)


def _declare(name: str) -> dict:
    from trajectory_checks import declared

    folder, case = _load(name)
    return declared.declare(folder, case, artifact="fixture")


@pytest.mark.parametrize("name", sorted(META))
def test_fixture(name: str) -> None:
    """Declare gives exactly the fixture's hand-written records.

    Both lists are compared whole and in order (decl_id numbering, §5.1;
    options by key and option, §5.2); declared.json's files_read in full
    and its warnings as a set (D59).
    Rules: DEC1, DEC2, DEC3, DEC4, DEC5, DEC6, DEC7, KEY2, CT4, PRE1.
    """
    result = _declare(name)
    folder = FIXTURES / name
    want = json.loads((folder / "expected_invocations.json").read_text())
    options = json.loads((folder / "expected_options.json").read_text())
    assert result["declared_invocations"] == want
    assert result["declared_options"] == options
    meta = result["declared"]
    files_read, warnings = META[name]
    assert meta["files_read"] == files_read
    assert sorted(meta["warnings"]) == sorted([*warnings, REF])
    assert set(meta) == helpers.DECLARED_META_FIELDS
    assert meta["artifact"] == "fixture"
    assert (meta["commit"], meta["commit_source"]) == ("c0ffee", "case_ref")
    assert meta["instructions"] == "README.md"
    assert meta["source_url"] == "https://example.org/owner/repo.git"
    for row in result["declared_invocations"]:
        assert set(row) == helpers.DECLARED_INVOCATION_FIELDS
    for row in result["declared_options"]:
        assert set(row) == helpers.DECLARED_OPTION_FIELDS


def test_case_lines_spec_is_kept() -> None:
    """declared.json holds the parsed [declared] table (§5.3).

    Rules: CT4, DEC1.
    """
    meta = _declare("case_lines")["declared"]
    assert meta["spec"]["lines"] == ["./tool.sh --k 3"]
    assert meta["spec"]["markers"] == {}


def test_no_checkout_reads_only_case_lines() -> None:
    """With repo None only the case's lines are read (DEC1).

    Rules: DEC1, CT4.
    """
    from trajectory_checks import declared

    _, case = _load("case_lines")
    result = declared.declare(None, case, artifact="fixture")
    (row,) = result["declared_invocations"]
    assert (row["invocation_key"], row["source_type"]) == (
        "tool.sh",
        "case_block",
    )
    assert result["declared_options"] == []
    assert result["declared"]["files_read"] == []


def test_link_depth_zero_reads_only_the_instructions() -> None:
    """link_follow_depth 0 follows no link (DEC1, §6.12).

    Rules: DEC1.
    """
    from trajectory_checks import declared

    folder, case = _load("links")
    result = declared.declare(
        folder, case, artifact="fixture", config={"link_follow_depth": 0}
    )
    assert [r["invocation_key"] for r in result["declared_invocations"]] == [
        "top.sh"
    ]
    assert result["declared"]["files_read"] == ["README.md"]


def test_link_depth_two_reads_one_level_more() -> None:
    """link_follow_depth 2 reaches the run guide's own link.

    Rules: DEC1.
    """
    from trajectory_checks import declared

    folder, case = _load("links")
    result = declared.declare(
        folder, case, artifact="fixture", config={"link_follow_depth": 2}
    )
    keys = [r["invocation_key"] for r in result["declared_invocations"]]
    assert "docs/more.sh" in keys
    assert "ci.sh" not in " ".join(keys)


def test_checkout_commit_is_used(tmp_path: pathlib.Path) -> None:
    """A .git HEAD gives the commit, with no case_ref warning (DEC7).

    Rules: DEC7.
    """
    from trajectory_checks import declared

    folder, case = _load("fences")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text((folder / "README.md").read_text())
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("A" * 40 + "\n")
    meta = declared.declare(repo, case, artifact="fixture")["declared"]
    assert (meta["commit"], meta["commit_source"]) == ("a" * 40, "checkout")
    assert REF not in meta["warnings"]
