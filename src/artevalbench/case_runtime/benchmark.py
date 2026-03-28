from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Sequence, TypeGuard

from ..application.benchmark_service import (
 BenchmarkRunResult,
 BenchmarkRunService,
 summarize_case_output_dirs,
)
from ..display import BaseDashboardDisplay, DisplayConfig, create_dashboard_display
from ..infrastructure.config import AppContext, resolve_settings
from ..models import LiveLayoutMode, LiveViewMode, PromptProfile, RunOptions, UiMode
from ..project_config import ProjectConfigState, load_project_config
from ..run_control import RunControl, activate_interrupt_handler

if TYPE_CHECKING:
	from ..display_textual import TextualDashboardDisplay


def run_benchmark(
 case_refs: Sequence[str],
 *,
 model_name: str | None = None,
 interactive: bool = False,
 prompt_profile: PromptProfile | None = None,
 prompt_append: str | None = None,
 cleanup_workspace: bool = False,
 live_view: LiveViewMode = LiveViewMode.AUTO,
 live_layout: LiveLayoutMode = LiveLayoutMode.AUTO,
 ui: UiMode = UiMode.RICH,
 output_dir: Path | None = None,
 project_state: ProjectConfigState | None = None,
) -> BenchmarkRunResult:
	state = project_state or load_project_config(Path.cwd())
	context = AppContext(project_state=state, settings=resolve_settings(state))
	service = BenchmarkRunService(context)
	options = RunOptions(
	 model_name=model_name or context.settings.default_model,
	 interactive=interactive,
	 prompt_profile=prompt_profile,
	 prompt_append=prompt_append,
	 cleanup_workspace=cleanup_workspace,
	)
	run_control = RunControl()
	dashboard = _maybe_dashboard(
	 selected_refs=list(case_refs or state.registry.cases.keys()),
	 live_view=live_view,
	 live_layout=live_layout,
	 ui=ui,
	 interactive=interactive,
	 run_control=run_control,
	)
	display_context = nullcontext(dashboard) if dashboard is None else dashboard

	def _run_with_sink(active_dashboard: BaseDashboardDisplay | None) -> BenchmarkRunResult:
		return service.run(
		 list(case_refs),
		 options=options,
		 output_dir=output_dir,
		 sink=active_dashboard,
		 run_control=run_control,
		)

	with activate_interrupt_handler(run_control):
		if _is_textual_dashboard(dashboard):
			return dashboard.run_worker(lambda: _run_with_sink(dashboard))
		with display_context as active_dashboard:
			return _run_with_sink(
			 active_dashboard if isinstance(active_dashboard, BaseDashboardDisplay) else None
			)


def summarize_case_outputs(
 case_output_inputs: Sequence[Path],
 *,
 output_dir: Path,
 model_name: str | None = None,
 agent_kind: str | None = None,
 prompt_profile: str | None = None,
 run_label: str | None = None,
 expected_case_ids: Sequence[str] | None = None,
 project_state: ProjectConfigState | None = None,
) -> BenchmarkRunResult:
	state = project_state or load_project_config(Path.cwd())
	return summarize_case_output_dirs(
	 case_output_inputs,
	 output_dir=output_dir,
	 project_state=state,
	 model_name=model_name,
	 agent_kind=agent_kind,
	 prompt_profile=prompt_profile,
	 run_label=run_label,
	 expected_case_ids=expected_case_ids,
	)


def _maybe_dashboard(
 selected_refs: list[str],
 *,
 live_view: LiveViewMode,
 live_layout: LiveLayoutMode,
 ui: UiMode,
 interactive: bool,
 run_control: RunControl,
) -> BaseDashboardDisplay | None:
	return create_dashboard_display(
	 config=DisplayConfig(
	  view=live_view,
	  layout=live_layout,
	  interactive=interactive,
	  ui=ui,
	 ),
	 title="ArtEvalBench Run",
	 selected_cases=selected_refs,
	 run_control=run_control,
	)


def _is_textual_dashboard(
 dashboard: BaseDashboardDisplay | None,
) -> TypeGuard["TextualDashboardDisplay"]:
	if dashboard is None:
		return False
	try:
		from ..display_textual import TextualDashboardDisplay
	except ImportError:
		return False
	return isinstance(dashboard, TextualDashboardDisplay)
