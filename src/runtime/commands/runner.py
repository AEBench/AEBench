"""Policy and observation pipeline for monitored commands.

The runner never executes anything. It decides, and it fans records out to
observers; the shim owns the process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from .types import CommandRecord, CommandRequest, Decision

logger = logging.getLogger(__name__)


class BaseInterceptor:
	"""Base for anything that inspects or observes commands.

	Both hooks default to doing nothing, so a subclass overrides only the one
	it cares about.
	"""

	name: str = "interceptor"

	def inspect(self, request: CommandRequest) -> Decision:
		"""Allows the command."""
		_ = request
		return Decision.permit()

	def observe(self, record: CommandRecord) -> None:
		"""Ignores the record."""
		_ = record


@dataclass(frozen=True, slots=True)
class Verdict:
	"""A merged decision plus the interceptor that denied, if any."""

	decision: Decision
	denied_by: str | None = None


class CommandRunner:
	"""Merges interceptor verdicts and broadcasts completed records."""

	def __init__(self, interceptors: Sequence[BaseInterceptor] = ()) -> None:
		"""Initializes the runner with an ordered interceptor chain."""
		self._interceptors = tuple(interceptors)

	@property
	def interceptors(self) -> tuple[BaseInterceptor, ...]:
		"""Returns the configured interceptor chain."""
		return self._interceptors

	def decide(self, request: CommandRequest) -> Verdict:
		"""Runs the chain and returns the resulting verdict.

		The first deny wins and short-circuits. An interceptor that raises is
		skipped: a broken policy must not stop an artifact from building.
		"""
		for interceptor in self._interceptors:
			try:
				decision = interceptor.inspect(request)
			except Exception:
				logger.exception("interceptor %s failed to inspect; allowing", interceptor.name)
				continue

			if not decision.allow:
				return Verdict(decision=decision, denied_by=interceptor.name)
		return Verdict(decision=Decision.permit())

	def observe(self, record: CommandRecord) -> None:
		"""Broadcasts a record to every interceptor."""
		for interceptor in self._interceptors:
			try:
				interceptor.observe(record)
			except Exception:
				logger.exception(
					"interceptor %s failed to observe %s", interceptor.name, record.command_id
				)


__all__ = ["BaseInterceptor", "CommandRunner", "Verdict"]
