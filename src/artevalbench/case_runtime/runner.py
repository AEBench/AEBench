from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, TypeGuard

from ..application.case_service import CaseRunService
from ..display import BaseDashboardDisplay, DisplayConfig, create_dashboard_display
from ..infrastructure.config import AppContext, resolve_settings
from ..models import LiveLayoutMode, LiveViewMode, PromptProfile, RunOptions, UiMode
from ..project_config import ProjectConfigState, load_project_config
from ..run_control import RunControl, activate_interrupt_handler
from ..domain.models import CaseRunResult

if TYPE_CHECKING:
	from ..display_textual import TextualDashboardDisplay


def run_case(
 case_dir: Path,
 *,
 model_name: str | None = None,
 save_path: Path | None = None,
 interactive: bool = False,
 prompt_profile: PromptProfile | None = None,
 prompt_append: str | None = None,
 cleanup_workspace: bool = False,
 live_view: LiveViewMode = LiveViewMode.AUTO,
 live_layout: LiveLayoutMode = LiveLayoutMode.AUTO,
 ui: UiMode = UiMode.RICH,
 project_state: ProjectConfigState | None = None,
 dashboard: BaseDashboardDisplay | None = None,
 run_control: RunControl | None = None,
) -> CaseRunResult:
	state = project_state or load_project_config(case_dir)
	context = AppContext(project_state=state, settings=resolve_settings(state))
	service = CaseRunService(context)
	options = RunOptions(
	 model_name=model_name or context.settings.default_model,
	 interactive=interactive,
	 prompt_profile=prompt_profile,
	 prompt_append=prompt_append,
	 cleanup_workspace=cleanup_workspace,
	)
	active_run_control = run_control or RunControl()
	created_dashboard = None
	if dashboard is None:
		created_dashboard = _maybe_dashboard(
		 title=f"AE Case Run: {case_dir.resolve().name}",
		 live_view=live_view,
		 live_layout=live_layout,
		 ui=ui,
		 interactive=interactive,
		 selected_cases=None,
		 run_control=active_run_control,
		)
	dashboard_context = (
	 nullcontext(dashboard)
	 if dashboard is not None
	 else created_dashboard
	 if created_dashboard is not None
	 else nullcontext(None)
	)
	interrupt_context = (
	 nullcontext() if run_control is not None else activate_interrupt_handler(active_run_control)
	)

	def _run_with_sink(active_dashboard: BaseDashboardDisplay | None) -> CaseRunResult:
		return service.run(
		 case_dir.resolve(),
		 save_path=save_path,
		 options=options,
		 sink=active_dashboard,
		 run_control=active_run_control,
		)

	with interrupt_context:
		if _is_textual_dashboard(created_dashboard):
			return created_dashboard.run_worker(lambda: _run_with_sink(created_dashboard))
		if _is_textual_dashboard(dashboard):
			return dashboard.run_worker(lambda: _run_with_sink(dashboard))
		with dashboard_context as active_dashboard:
			return _run_with_sink(
			 active_dashboard if isinstance(active_dashboard, BaseDashboardDisplay) else None
			)


def _maybe_dashboard(
 *,
 title: str,
 live_view: LiveViewMode,
 live_layout: LiveLayoutMode,
 ui: UiMode,
 interactive: bool,
 selected_cases: list[str] | None,
 run_control: RunControl,
) -> BaseDashboardDisplay | None:
	return create_dashboard_display(
	 config=DisplayConfig(
	  view=live_view,
	  layout=live_layout,
	  interactive=interactive,
	  ui=ui,
	 ),
	 title=title,
	 selected_cases=selected_cases,
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
