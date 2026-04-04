from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import click
import typer

from ...git import ensure_git_checkout, protected_git_checkout_paths
from ...constants import default_docker_image, default_timeout_ms
from ...log import print_console
from ...models import ArchiveSource, LocalSource, PromptProfile, RuntimeMode
from ...project_config import ArtifactMode, ProjectConfigState
from ...sources import prepare_archive_source, prepare_local_source
from ...utils import safe_name
from ..loader import load_case_spec
from .case_spec import write_case_spec
from .templates import render_placeholder_oracle_files, render_starter_oracle_files
from .registry import register_case_bundle, write_placeholder_oracle_package
from ..constants import ORACLE_DIRNAME, REFS_DIRNAME

_EXPECTED_RESULT_FILENAME = "expected_result.txt"
_DEFAULT_OUTPUT_PATH = "demo-output/result.txt"
_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz")


@dataclass(frozen=True)
class AuthoringAnswers:
    core_claim: str
    acceptable_evidence: str
    allowed_tolerance: str
    runtime_mode: RuntimeMode
    instruction_path: str
    quick_check: bool
    expected_output_path: str | None = None
    expected_output_text: str | None = None
    runtime_image: str | None = None


@dataclass(frozen=True)
class SourceDescriptor:
    source_type: str
    value: str
    is_local_path: bool = False


class BundleScaffoldError(RuntimeError):
    pass


def is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def infer_case_id_default(source: str | None, *, explicit_id: str | None = None) -> str:
    if explicit_id is not None and explicit_id.strip():
        return explicit_id.strip()
    if source is None:
        return "new-case"
    path = Path(source).expanduser()
    if path.name:
        name = path.name
    else:
        parsed = urlparse(source)
        name = Path(parsed.path).name or "new-case"
    if name.endswith(".git"):
        name = name[:-4]
    for suffix in _ARCHIVE_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    normalized = safe_name(name or "new-case")
    return normalized or "new-case"


def prompt_case_id(*, default: str) -> str:
    while True:
        value = str(typer.prompt("Case ID", default=default)).strip()
        if value:
            return value
        print_console("[red]case id must not be empty[/red]")


def print_authoring_wizard_header(*, case_id_hint: str | None = None) -> None:
    print_console("")
    print_console("[bold cyan]ArtEvalBench Case Authoring Wizard[/bold cyan]")
    if case_id_hint:
        print_console(
            f"[dim]Default case id: {case_id_hint}. Press Enter to accept defaults, then expand "
            "the generated placeholder oracle manually, or opt into the quick output check "
            "shortcut if the case is simple enough.[/dim]"
        )
    else:
        print_console(
            "[dim]Press Enter to accept defaults, then fill in oracles/*.py and refs/ manually, "
            "or opt into the quick output check shortcut if the case is simple enough.[/dim]"
        )
    print_console("")


def prompt_authoring_answers(bundle_dir: Path, *, show_header: bool = True) -> AuthoringAnswers:
    case = load_case_spec(bundle_dir)
    artifact_dir = bundle_dir / "artifact"
    instruction_default, used_fallback = _instruction_path_default(case, artifact_dir)
    if show_header:
        print_authoring_wizard_header(case_id_hint=case.id)
    print_console("[bold]Runtime[/bold]")
    if used_fallback:
        print_console(
            "[yellow]No README.md found under artifact/; defaulting instructions.path to README.md.[/yellow]"
        )
    runtime_mode = RuntimeMode(
        typer.prompt(
            "Runtime mode",
            default=case.run.runtime.mode.value,
            type=click.Choice([mode.value for mode in RuntimeMode], case_sensitive=False),
            show_choices=True,
        )
    )
    runtime_image: str | None = None
    if runtime_mode == RuntimeMode.DOCKER:
        image_default = case.run.runtime.image or default_docker_image()
        runtime_image = typer.prompt("Docker runtime image", default=image_default).strip()
    print_console("")
    print_console("[bold]Artifact Instructions[/bold]")
    instruction_path = _prompt_relative_path(
        "Instruction path inside artifact/",
        default=instruction_default,
    )
    print_console("")
    print_console("[bold]Oracle Bootstrap[/bold]")
    quick_check = typer.confirm(
        "Generate a quick output-path/text check starter",
        default=False,
    )
    expected_output_path: str | None = None
    expected_output_text: str | None = None
    if quick_check:
        expected_output_path = _prompt_relative_path(
            "Expected output file path in workspace",
            default=_DEFAULT_OUTPUT_PATH,
        )
        expected_output_text = typer.prompt(
            "Expected output text",
            default=_default_expected_output_text(case.id),
        )
    print_console("")
    print_console("[bold]Case Card[/bold]")
    core_claim = typer.prompt(
        "Core claim",
        default=_default_text(
            case.case_card.core_claim,
            f"Validate the artifact evaluation contract for {case.id}.",
        ),
    )
    acceptable_evidence_default = (
        f"The oracle accepts the workspace when {expected_output_path} exists and matches refs/{_EXPECTED_RESULT_FILENAME}."
        if quick_check and expected_output_path
        else f"The case is successful when the oracle phases under oracles/ pass for {case.id}."
    )
    acceptable_evidence = typer.prompt(
        "Acceptable evidence",
        default=_default_text(
            case.case_card.acceptable_evidence,
            acceptable_evidence_default,
        ),
    )
    allowed_tolerance = typer.prompt(
        "Allowed tolerance",
        default=_default_text(case.case_card.allowed_tolerance, "n/a"),
    )
    return AuthoringAnswers(
        core_claim=core_claim.strip(),
        acceptable_evidence=acceptable_evidence.strip(),
        allowed_tolerance=allowed_tolerance.strip(),
        runtime_mode=runtime_mode,
        instruction_path=instruction_path,
        quick_check=quick_check,
        expected_output_path=expected_output_path,
        expected_output_text=expected_output_text,
        runtime_image=runtime_image.strip() if runtime_image else None,
    )


def apply_authoring_answers(
    bundle_dir: Path,
    answers: AuthoringAnswers,
    *,
    write_instructions: bool,
) -> "CaseSpec":
    from ...models import CaseSpec, OracleFailureMode, OraclePhaseName, OracleScoreMode

    case = load_case_spec(bundle_dir)
    payload = case.model_dump(mode="json")
    payload["case_card"] = {
        "core_claim": answers.core_claim,
        "acceptable_evidence": answers.acceptable_evidence,
        "allowed_tolerance": answers.allowed_tolerance,
    }
    payload["run"]["instructions"]["path"] = answers.instruction_path
    payload["run"]["runtime"]["mode"] = answers.runtime_mode.value
    payload["run"]["runtime"]["image"] = (
        answers.runtime_image if answers.runtime_mode == RuntimeMode.DOCKER else None
    )
    if answers.quick_check:
        payload["oracle"] = {
            "expected_score": 4,
            "phases": _canonical_oracle_phases(),
            "score_mode": OracleScoreMode.PHASE_COUNT.value,
            "failure_mode": OracleFailureMode.FAIL_FAST.value,
            "placeholder": False,
            "notes": (
                "Generated quick-check oracle. Expand files under oracles/ and refs/ if the case "
                "needs richer validation."
            ),
        }
    else:
        payload["oracle"] = {
            "placeholder": True,
            "notes": (
                "Generated placeholder oracle. Add phase implementations under oracles/ and populate "
                "refs/ manually when the case contract is ready."
            ),
        }
    updated_case = CaseSpec.model_validate(payload)
    write_case_spec(bundle_dir / "case.toml", updated_case)
    if write_instructions:
        if answers.quick_check:
            _write_quick_check_instructions_file(
                bundle_dir / "artifact",
                instruction_path=answers.instruction_path,
                expected_output_path=answers.expected_output_path or _DEFAULT_OUTPUT_PATH,
                expected_output_text=answers.expected_output_text
                or _default_expected_output_text(case.id),
            )
        else:
            _write_manual_instructions_file(
                bundle_dir / "artifact",
                instruction_path=answers.instruction_path,
            )
    if answers.quick_check:
        (bundle_dir / REFS_DIRNAME).mkdir(parents=True, exist_ok=True)
        (bundle_dir / REFS_DIRNAME / _EXPECTED_RESULT_FILENAME).write_text(
            (answers.expected_output_text or _default_expected_output_text(case.id)) + "\n",
            encoding="utf-8",
        )
        _write_starter_oracle_files(
            bundle_dir / ORACLE_DIRNAME,
            instruction_path=answers.instruction_path,
            expected_output_path=answers.expected_output_path or _DEFAULT_OUTPUT_PATH,
        )
    return updated_case


def scaffold_case_bundle(
    source: str,
    case_id: str,
    *,
    project_state: ProjectConfigState,
    target_root: Path | None = None,
    artifact_mode: ArtifactMode | None = None,
    ref: str | None = None,
    runtime_mode: RuntimeMode = RuntimeMode.LOCAL,
    image: str | None = None,
    timeout_ms: int | None = None,
    instruction_path: str = "README.md",
    prompt_profile: PromptProfile = PromptProfile.ARTIFACT_EVAL_V1,
) -> Path:
    from ...models import CaseSpec, UpstreamSourceType, UpstreamSpec

    bundle_root = (
        target_root or project_state.config.resolve_bundles_dir(project_state.root)
    ).resolve()
    bundle_root.mkdir(parents=True, exist_ok=True)
    (bundle_root / "__init__.py").touch(exist_ok=True)
    bundle_dir = bundle_root / safe_name(case_id)
    if bundle_dir.exists():
        raise BundleScaffoldError(f"bundle already exists: {bundle_dir}")
    bundle_dir.mkdir(parents=True, exist_ok=False)
    (bundle_dir / "__init__.py").write_text(
        '"""Bundle content package for the case."""\n', encoding="utf-8"
    )
    oracle_dir = bundle_dir / ORACLE_DIRNAME
    refs_dir = bundle_dir / REFS_DIRNAME
    oracle_dir.mkdir(parents=True, exist_ok=False)
    refs_dir.mkdir(parents=True, exist_ok=False)

    descriptor = _classify_source(source)
    selected_mode = artifact_mode or project_state.config.artifact_mode
    upstream = _build_upstream_spec(descriptor)
    if descriptor.source_type == UpstreamSourceType.GIT:
        resolved = ensure_git_checkout(
            descriptor.value,
            ref,
            project_state=project_state,
            protected_paths=protected_git_checkout_paths(project_state),
        )
        if selected_mode in {ArtifactMode.VENDOR, ArtifactMode.HYBRID}:
            artifact_dir = bundle_dir / "artifact"
            artifact_dir.mkdir(parents=True, exist_ok=False)
            _materialize_source(
                descriptor,
                artifact_dir,
                project_state.root,
                ref=resolved.resolved_ref,
                project_state=project_state,
            )
        upstream = upstream.model_copy(
            update={
                "ref": resolved.resolved_ref,
                "requested_ref": ref,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "artifact_mode": selected_mode,
                "overlay_artifact": selected_mode == ArtifactMode.HYBRID,
            }
        )
    else:
        if selected_mode in {ArtifactMode.VENDOR, ArtifactMode.HYBRID}:
            artifact_dir = bundle_dir / "artifact"
            artifact_dir.mkdir(parents=True, exist_ok=False)
            _materialize_source(
                descriptor,
                artifact_dir,
                project_state.root,
                project_state=project_state,
            )
        upstream = upstream.model_copy(
            update={
                "artifact_mode": selected_mode,
                "overlay_artifact": selected_mode == ArtifactMode.HYBRID,
            }
        )
    _write_placeholder_oracle(oracle_dir, case_id)
    case = CaseSpec.model_validate(
        {
            "id": case_id,
            "case_card": _todo_case_card(case_id),
            "run": {
                "id": case_id,
                "instructions": {"path": instruction_path},
                "runtime": {
                    "mode": runtime_mode.value,
                    "image": image,
                    "timeout_ms": timeout_ms if timeout_ms is not None else default_timeout_ms(),
                },
                "prompt": {"profile": prompt_profile.value},
            },
            "oracle": {
                "placeholder": True,
                "notes": "Replace the placeholder oracle stubs under oracles/ with real phase implementations.",
            },
            "upstream": upstream.model_dump(mode="json"),
        }
    )
    write_case_spec(bundle_dir / "case.toml", case)
    register_case_bundle(case.id, bundle_dir, project_state=project_state)
    return bundle_dir


def _classify_source(source: str) -> SourceDescriptor:
    from ...models import UpstreamSourceType

    path = Path(source).expanduser()
    if path.exists():
        resolved = path.resolve()
        if resolved.is_dir():
            return SourceDescriptor(UpstreamSourceType.LOCAL, str(resolved), is_local_path=True)
        if resolved.is_file() and _looks_like_archive(resolved.name):
            return SourceDescriptor(UpstreamSourceType.ARCHIVE, str(resolved), is_local_path=True)
        raise BundleScaffoldError(f"unsupported local source: {resolved}")
    parsed = urlparse(source)
    if parsed.scheme in {"file", "http", "https", "ssh"}:
        if _looks_like_archive(parsed.path):
            return SourceDescriptor(UpstreamSourceType.ARCHIVE, source)
        return SourceDescriptor(UpstreamSourceType.GIT, source)
    if source.endswith(".git") or source.startswith("git@"):
        return SourceDescriptor(UpstreamSourceType.GIT, source)
    raise BundleScaffoldError(f"could not classify source: {source}")


def _build_upstream_spec(descriptor: SourceDescriptor) -> "UpstreamSpec":
    from ...models import UpstreamSourceType, UpstreamSpec

    if descriptor.source_type == UpstreamSourceType.LOCAL:
        return UpstreamSpec(source_type=descriptor.source_type, path=descriptor.value)
    if descriptor.source_type == UpstreamSourceType.GIT:
        return UpstreamSpec(source_type=descriptor.source_type, url=descriptor.value)
    if descriptor.is_local_path:
        return UpstreamSpec(source_type=descriptor.source_type, path=descriptor.value)
    return UpstreamSpec(source_type=descriptor.source_type, url=descriptor.value)


def _materialize_source(
    descriptor: SourceDescriptor,
    artifact_dir: Path,
    project_root: Path,
    *,
    ref: str | None = None,
    project_state: ProjectConfigState | None = None,
) -> None:
    from ...models import UpstreamSourceType

    with tempfile.TemporaryDirectory(prefix="ae_bundle_materialize_") as tmpdir:
        temp_root = Path(tmpdir) / "materialized"
        if descriptor.source_type == UpstreamSourceType.LOCAL:
            resolved = prepare_local_source(LocalSource(path=descriptor.value), project_root)
        elif descriptor.source_type == UpstreamSourceType.GIT:
            resolved = ensure_git_checkout(
                descriptor.value,
                ref,
                project_state=project_state,
                protected_paths=set(),
            ).checkout_path
        else:
            archive_source = (
                ArchiveSource(path=descriptor.value)
                if descriptor.is_local_path
                else ArchiveSource(url=descriptor.value)
            )
            resolved = prepare_archive_source(archive_source, project_root, temp_root)
        _copy_contents(resolved, artifact_dir)


def _copy_contents(source_dir: Path, target_dir: Path) -> None:
    for entry in source_dir.iterdir():
        target = target_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)


def _write_placeholder_oracle(oracle_dir: Path, case_id: str) -> None:
    (oracle_dir / "__init__.py").write_text(
        '"""Oracle package for the case bundle."""\n', encoding="utf-8"
    )
    write_placeholder_oracle_package(oracle_dir, case_id)


def _write_starter_oracle_files(
    oracle_dir: Path,
    *,
    instruction_path: str,
    expected_output_path: str,
) -> None:
    oracle_dir.mkdir(parents=True, exist_ok=True)
    init_path = oracle_dir / "__init__.py"
    if not init_path.exists():
        init_path.write_text('"""Case-local oracle package."""\n', encoding="utf-8")
    for relative_path, content in render_starter_oracle_files(
        instruction_path=instruction_path,
        expected_output_path=expected_output_path,
    ).items():
        (oracle_dir / relative_path).write_text(content, encoding="utf-8")


def _looks_like_archive(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES)


def _prompt_relative_path(label: str, *, default: str) -> str:
    while True:
        value = typer.prompt(label, default=default).strip()
        try:
            return _normalize_relative_path(value)
        except ValueError as exc:
            print_console(f"[red]{exc}[/red]")


def _normalize_relative_path(path_text: str) -> str:
    path = Path(path_text.strip())
    if not path_text.strip():
        raise ValueError("path must not be empty")
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path must stay within the workspace")
    return path.as_posix()


def _instruction_path_default(case: "CaseSpec", artifact_dir: Path) -> tuple[str, bool]:
    configured = case.run.instructions.path.strip()
    if configured and configured != "README.md":
        return configured, False
    detected = _detect_readme_path(artifact_dir)
    if detected is not None:
        return detected, False
    return "README.md", True


def _detect_readme_path(artifact_dir: Path) -> str | None:
    root_readme = artifact_dir / "README.md"
    if root_readme.is_file():
        return "README.md"
    if not artifact_dir.exists():
        return None
    candidates = [
        path.relative_to(artifact_dir).as_posix()
        for path in artifact_dir.rglob("*")
        if path.is_file() and path.name.lower() == "readme.md"
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda value: (value.count("/"), len(value), value))[0]


def _default_text(existing: str, fallback: str) -> str:
    value = existing.strip()
    return fallback if not value or value.startswith("TODO:") else value


def _default_expected_output_text(case_id: str) -> str:
    return f"{case_id} minimal case ready"


def _canonical_oracle_phases() -> list[str]:
    from ...models import OraclePhaseName

    return [
        OraclePhaseName.ENV_SETUP.value,
        OraclePhaseName.ARTIFACT_BUILD.value,
        OraclePhaseName.BENCHMARK_PREP.value,
        OraclePhaseName.EXPERIMENT_RUNS.value,
    ]


def _todo_case_card(case_id: str) -> dict[str, str]:
    return {
        "core_claim": f"TODO: summarize the core clean-baseline claim for {case_id}.",
        "acceptable_evidence": (
            f"TODO: describe the evidence that should count as success for {case_id}."
        ),
        "allowed_tolerance": (
            f"TODO: describe the allowed tolerance or write 'n/a' for {case_id}."
        ),
    }


def _write_quick_check_instructions_file(
    artifact_dir: Path,
    *,
    instruction_path: str,
    expected_output_path: str,
    expected_output_text: str,
) -> None:
    instructions_file = artifact_dir / instruction_path
    instructions_file.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Starter Artifact Instructions", "", "Follow these steps exactly:", ""]
    output_parent = Path(expected_output_path).parent
    step = 1
    if output_parent != Path("."):
        lines.append(
            f"{step}. Create the directory `{output_parent.as_posix()}` if it does not already exist."
        )
        step += 1
    lines.extend(
        [
            f"{step}. Write the exact text `{expected_output_text}` to `{expected_output_path}`.",
            f"{step + 1}. Print the contents of `{expected_output_path}`.",
            f"{step + 2}. Verify the file exists and the contents match exactly.",
            "",
            "Keep all generated files inside the artifact workspace.",
            "",
        ]
    )
    instructions_file.write_text("\n".join(lines), encoding="utf-8")


def _write_manual_instructions_file(
    artifact_dir: Path,
    *,
    instruction_path: str,
) -> None:
    instructions_file = artifact_dir / instruction_path
    instructions_file.parent.mkdir(parents=True, exist_ok=True)
    instructions_file.write_text(
        "\n".join(
            [
                "# Artifact Instructions",
                "",
                "Describe the real reproduction flow for this case.",
                "",
                "Include at least:",
                "- setup or dependency steps the runtime should perform",
                "- the commands that should be run inside the artifact workspace",
                "- the concrete outputs, logs, or files the oracle should validate",
                "- any reference files that must be added under refs/",
                "",
                "Then replace the placeholder oracle stubs under oracles/*.py with",
                "real phase implementations.",
                "",
            ]
        ),
        encoding="utf-8",
    )
