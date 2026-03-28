from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import click
import typer

from ..constants import default_docker_image
from ..log import print_console
from ..models import RuntimeMode
from ..utils import safe_name
from .loader import load_case_spec
from .manifest import write_case_spec
from .models import CaseSpec, OracleFailureMode, OraclePhaseName, OracleScoreMode
from .oracle_templates import render_oracle_template_set

_EXPECTED_RESULT_FILENAME = "expected_result.txt"
_DEFAULT_OUTPUT_PATH = "demo-output/result.txt"
_ARCHIVE_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar", ".tgz", ".zip")


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
		 "[dim]Press Enter to accept defaults, then fill in oracle/*.py and refs/ manually, "
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
	 else f"The case is successful when the decorated oracle phases under oracle/ pass for {case.id}."
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
) -> CaseSpec:
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
		  "Generated quick-check oracle. Expand files under oracle/ and refs/ if the case "
		  "needs richer validation."
		 ),
		}
	else:
		payload["oracle"] = {
		 "placeholder": True,
		 "notes": (
		  "Generated placeholder oracle. Add decorated phases under oracle/ and populate "
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
		(bundle_dir / "refs").mkdir(parents=True, exist_ok=True)
		(bundle_dir / "refs" / _EXPECTED_RESULT_FILENAME).write_text(
		 (answers.expected_output_text or _default_expected_output_text(case.id)) + "\n",
		 encoding="utf-8",
		)
		_write_starter_oracle_template(
		 bundle_dir / "oracle",
		 instruction_path=answers.instruction_path,
		 expected_output_path=answers.expected_output_path or _DEFAULT_OUTPUT_PATH,
		)
	return updated_case


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


def _instruction_path_default(case: CaseSpec, artifact_dir: Path) -> tuple[str, bool]:
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
	return [
	 OraclePhaseName.ENV_SETUP.value,
	 OraclePhaseName.ARTIFACT_BUILD.value,
	 OraclePhaseName.BENCHMARK_PREP.value,
	 OraclePhaseName.EXPERIMENT_RUNS.value,
	]


def _write_starter_oracle_template(
 oracle_dir: Path,
 *,
 instruction_path: str,
 expected_output_path: str,
) -> None:
	for relative_path, content in _render_starter_oracle_files(
	 instruction_path=instruction_path,
	 expected_output_path=expected_output_path,
	).items():
		(oracle_dir / relative_path).write_text(content, encoding="utf-8")


def _render_starter_oracle_files(
 *,
 instruction_path: str,
 expected_output_path: str,
) -> dict[str, str]:
	return render_oracle_template_set(
	 "starter",
	 replacements={
	  "__INSTRUCTION_PATH__": repr(instruction_path),
	  "__EXPECTED_OUTPUT_PATH__": repr(expected_output_path),
	 },
	)


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
	  "This starter case is intentionally minimal. Update this file,",
	  "`refs/expected_result.txt`, and `oracle/*.py` if the case needs richer",
	  "setup or validation.",
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
	   "Then replace the placeholder oracle under oracle/*.py with decorated phases.",
	   "",
	  ]
	 ),
	 encoding="utf-8",
	)
