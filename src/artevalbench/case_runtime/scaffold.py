from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..cache import ensure_git_checkout, protected_git_checkout_paths
from ..constants import default_timeout_ms
from ..models import ArchiveSource, LocalSource, PromptProfile, RuntimeMode
from ..project_config import ArtifactMode, ProjectConfigState
from ..sources import prepare_archive_source, prepare_local_source
from ..utils import safe_name
from .manifest import write_case_spec
from .models import CaseSpec, UpstreamSourceType, UpstreamSpec
from .registry import register_case_bundle, write_placeholder_oracle_package

_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz")


@dataclass(frozen=True)
class SourceDescriptor:
	source_type: UpstreamSourceType
	value: str
	is_local_path: bool = False


class BundleScaffoldError(RuntimeError):
	pass


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
	oracle_dir = bundle_dir / "oracle"
	refs_dir = bundle_dir / "refs"
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
	   "notes": "Replace the placeholder oracle package with case-specific decorated phases and refs.",
	  },
	  "upstream": upstream.model_dump(mode="json"),
	 }
	)
	write_case_spec(bundle_dir / "case.toml", case)
	register_case_bundle(case.id, bundle_dir, project_state=project_state)
	return bundle_dir


def _classify_source(source: str) -> SourceDescriptor:
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


def _build_upstream_spec(descriptor: SourceDescriptor) -> UpstreamSpec:
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


def _looks_like_archive(name: str) -> bool:
	return any(name.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES)


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
