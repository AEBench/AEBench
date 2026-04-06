from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import click
import typer

from constants import DEFAULT_DOCKER_IMAGE
from evaluator.constants import ORACLE_DIRNAME, REFS_DIRNAME
from log import print_console
from models import (
    CasePlan,
    CaseConfig,
    OracleFailureMode,
    OraclePhaseName,
    OracleScoreMode,
    OracleConfig,
    RuntimeMode,
)
from utils import safe_name

from ..loader import load_case_spec
from .case_spec import write_case_spec
from .templates import render_starter_oracle_files


_EXPECTED_RESULT_FILENAME = "expected_result.txt"
_DEFAULT_OUTPUT_PATH = "demo-output/result.txt"
_ARCHIVE_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar", ".tgz", ".zip")
_CANONICAL_ORACLE_PHASES = [
    OraclePhaseName.ENV_SETUP.value,
    OraclePhaseName.ARTIFACT_BUILD.value,
    OraclePhaseName.BENCHMARK_PREP.value,
    OraclePhaseName.EXPERIMENT_RUNS.value,
]


@dataclass(frozen=True, slots=True)
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


def is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def infer_case_id_default(source: str | None, *, explicit_id: str | None = None) -> str:
    if explicit_id is not None and explicit_id.strip():
        return explicit_id.strip()

    if source is None:
        return "new-case"

    path = Path(source).expanduser()
    name = path.name or Path(urlparse(source).path).name or "new-case"
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


def prompt_authoring_answers(bundle_dir: Path, *, show_header: bool = True) -> AuthoringAnswers:
    case = load_case_spec(bundle_dir)
    artifact_dir = bundle_dir / "artifact"
    instruction_default, used_fallback = _instruction_path_default(case, artifact_dir)

    if show_header:
        _print_authoring_header()

    runtime_mode, runtime_image = _prompt_runtime(case, used_fallback)
    instruction_path = _prompt_instruction_path(instruction_default)
    quick_check, expected_output_path, expected_output_text = _prompt_oracle_setup(case)
    core_claim, acceptable_evidence, allowed_tolerance = _prompt_case_brief(
        case=case,
        quick_check=quick_check,
        expected_output_path=expected_output_path,
    )

    return AuthoringAnswers(
        core_claim=core_claim,
        acceptable_evidence=acceptable_evidence,
        allowed_tolerance=allowed_tolerance,
        runtime_mode=runtime_mode,
        instruction_path=instruction_path,
        quick_check=quick_check,
        expected_output_path=expected_output_path,
        expected_output_text=expected_output_text,
        runtime_image=runtime_image,
    )


def apply_authoring_answers(bundle_dir: Path, answers: AuthoringAnswers, *, write_instructions: bool) -> CaseConfig:
    case = load_case_spec(bundle_dir)
    updated_case = _build_updated_case(case, answers)
    write_case_spec(updated_case, bundle_dir)

    if write_instructions:
        _write_instruction_file(bundle_dir / "artifact", answers, case.id)

    if answers.quick_check:
        _write_quick_check_scaffold(bundle_dir, answers, case.id)

    return updated_case


def _print_authoring_header() -> None:
    print_console("")
    print_console("[bold cyan]AEBench Case Authoring Wizard[/bold cyan]")
    print_console("")


def _prompt_runtime(case: CaseConfig, used_fallback_instruction_path: bool) -> tuple[RuntimeMode, str | None]:
    print_console("[bold]Runtime[/bold]")
    if used_fallback_instruction_path:
        print_console(
            "[yellow]No README.md found under artifact/; defaulting instructions to README.md.[/yellow]"
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
        image_default = case.run.runtime.image or DEFAULT_DOCKER_IMAGE
        runtime_image = typer.prompt("Docker runtime image", default=image_default).strip() or None

    return runtime_mode, runtime_image


def _prompt_instruction_path(default_instruction_path: str) -> str:
    print_console("")
    print_console("[bold]Artifact Instructions[/bold]")
    return _prompt_relative_path("Instruction path inside artifact/", default=default_instruction_path)


def _prompt_oracle_setup(case: CaseConfig) -> tuple[bool, str | None, str | None]:
    print_console("")
    print_console("[bold]Oracle Setup[/bold]")

    quick_check = typer.confirm("Generate a starter output-file check", default=False)
    if not quick_check:
        return False, None, None

    expected_output_path = _prompt_relative_path(
        "Expected output file path in workspace",
        default=_DEFAULT_OUTPUT_PATH,
    )
    expected_output_text = typer.prompt(
        "Expected output text",
        default=_default_expected_output_text(case.id),
    ).strip()

    return True, expected_output_path, expected_output_text


def _prompt_case_brief(
    *,
    case: CaseConfig,
    quick_check: bool,
    expected_output_path: str | None,
) -> tuple[str, str, str]:
    print_console("")
    print_console("[bold]Case Brief[/bold]")

    core_claim = typer.prompt(
        "Core claim",
        default=_default_text(
            case.case_brief.core_claim,
            f"Validate the artifact evaluation contract for {case.id}.",
        ),
    ).strip()

    acceptable_evidence = typer.prompt(
        "Acceptable evidence",
        default=_default_text(
            case.case_brief.acceptable_evidence,
            _acceptable_evidence_default(case.id, quick_check, expected_output_path),
        ),
    ).strip()

    allowed_tolerance = typer.prompt(
        "Allowed tolerance",
        default=_default_text(case.case_brief.allowed_tolerance, "n/a"),
    ).strip()

    return core_claim, acceptable_evidence, allowed_tolerance


def _acceptable_evidence_default(
    case_id: str,
    quick_check: bool,
    expected_output_path: str | None,
) -> str:
    if quick_check and expected_output_path:
        return (
            f"The case passes when {expected_output_path} exists and matches "
            f"refs/{_EXPECTED_RESULT_FILENAME}."
        )
    return f"The case passes when the four oracle phases succeed for {case_id}."


def _build_updated_case(case: CaseConfig, answers: AuthoringAnswers) -> CaseConfig:
    updated_runtime = case.run.runtime.model_copy(
        update={
            "mode": answers.runtime_mode,
            "image": answers.runtime_image if answers.runtime_mode == RuntimeMode.DOCKER else None,
        }
    )
    updated_run = case.run.model_copy(
        update={
            "instructions_path": answers.instruction_path,
            "runtime": updated_runtime,
        }
    )

    updated_case_brief = CasePlan(
        core_claim=answers.core_claim,
        acceptable_evidence=answers.acceptable_evidence,
        allowed_tolerance=answers.allowed_tolerance,
    )

    updated_oracle = _build_oracle_spec(answers)

    return case.model_copy(
        update={
            "case_brief": updated_case_brief,
            "run": updated_run,
            "oracle": updated_oracle,
        }
    )


def _build_oracle_spec(answers: AuthoringAnswers) -> OracleConfig:
    if answers.quick_check:
        return OracleConfig(
            expected_score=4,
            phases=list(_CANONICAL_ORACLE_PHASES),
            score_mode=OracleScoreMode.PHASE_COUNT,
            failure_mode=OracleFailureMode.FAIL_FAST,
            placeholder=False,
            notes=(
                "Generated starter oracle scaffold. Expand the checks under oracle/ "
                "and populate refs/ if the case needs richer validation."
            ),
        )

    return OracleConfig(
        placeholder=True,
        notes=(
            "Generated placeholder oracle scaffold. Replace the stubs under oracle/ "
            "with real phase implementations and add reference data under refs/."
        ),
    )


def _write_instruction_file(artifact_dir: Path, answers: AuthoringAnswers, case_id: str) -> None:
    if answers.quick_check:
        _write_quick_check_instructions_file(
            artifact_dir,
            instruction_path=answers.instruction_path,
            expected_output_path=answers.expected_output_path or _DEFAULT_OUTPUT_PATH,
            expected_output_text=answers.expected_output_text or _default_expected_output_text(case_id),
        )
        return

    _write_manual_instructions_file(
        artifact_dir,
        instruction_path=answers.instruction_path,
    )


def _write_quick_check_scaffold(bundle_dir: Path, answers: AuthoringAnswers, case_id: str) -> None:
    refs_dir = bundle_dir / REFS_DIRNAME
    refs_dir.mkdir(parents=True, exist_ok=True)
    (refs_dir / _EXPECTED_RESULT_FILENAME).write_text(
        (answers.expected_output_text or _default_expected_output_text(case_id)) + "\n",
        encoding="utf-8",
    )

    oracle_dir = bundle_dir / ORACLE_DIRNAME
    oracle_dir.mkdir(parents=True, exist_ok=True)
    _ensure_oracle_package(oracle_dir)

    starter_files = render_starter_oracle_files(
        instruction_path=answers.instruction_path,
        expected_output_path=answers.expected_output_path or _DEFAULT_OUTPUT_PATH,
    )
    for relative_path, content in starter_files.items():
        target = oracle_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _ensure_oracle_package(oracle_dir: Path) -> None:
    init_path = oracle_dir / "__init__.py"
    if not init_path.exists():
        init_path.write_text('"""Case-local oracle package."""\n', encoding="utf-8")


def _prompt_relative_path(label: str, *, default: str) -> str:
    while True:
        value = typer.prompt(label, default=default).strip()
        if not value:
            print_console("[red]path must not be empty[/red]")
            continue
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            print_console("[red]path must stay within the workspace[/red]")
            continue
        return path.as_posix()


def _instruction_path_default(case: CaseConfig, artifact_dir: Path) -> tuple[str, bool]:
    configured = case.run.instructions_path.strip()
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


def _write_quick_check_instructions_file(
    artifact_dir: Path,
    *,
    instruction_path: str,
    expected_output_path: str,
    expected_output_text: str,
) -> None:
    instructions_file = artifact_dir / instruction_path
    instructions_file.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Starter Artifact Instructions",
        "",
        "Follow these steps exactly:",
        "",
    ]

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
            f"{step + 2}. Verify that the file exists and matches exactly.",
            "",
            "Keep all generated files inside the artifact workspace.",
            "",
        ]
    )

    instructions_file.write_text("\n".join(lines), encoding="utf-8")


def _write_manual_instructions_file(artifact_dir: Path, *, instruction_path: str) -> None:
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
                "- commands that should be run inside the artifact workspace",
                "- concrete outputs, logs, or files the oracle should validate",
                "- any reference files that must be added under refs/",
                "",
                "Then replace the scaffolded oracle files under oracle/*.py with",
                "real phase implementations that return typed requirements.",
                "",
            ]
        ),
        encoding="utf-8",
    )