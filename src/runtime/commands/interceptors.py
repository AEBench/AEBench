"""Built-in interceptors.

None are enabled by default. Denying commands changes the task the agent is
performing and would break comparability with baseline traces, so policy
interceptors ship dormant until the evidence layer has proven itself on real
runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .runner import BaseInterceptor
from .types import CommandRecord, CommandRequest, Decision


@dataclass(slots=True)
class PatternDenyPolicy(BaseInterceptor):
	"""Denies commands whose shell source or argv matches a pattern.

	Not enabled anywhere. It exists so the deny path has a real implementation
	to exercise, and as the shape future policies should take.
	"""

	patterns: tuple[re.Pattern[str], ...] = ()
	reason: str = "denied by AEBench policy"
	name: str = "pattern_deny"

	@classmethod
	def from_strings(
		cls, patterns: tuple[str, ...], *, reason: str | None = None
	) -> "PatternDenyPolicy":
		"""Builds a policy from regular-expression strings."""
		compiled = tuple(re.compile(pattern) for pattern in patterns)
		if reason is None:
			return cls(patterns=compiled)
		return cls(patterns=compiled, reason=reason)

	def inspect(self, request: CommandRequest) -> Decision:
		"""Denies when any pattern matches the command text."""
		haystack = request.shell_source or " ".join(request.argv)
		for pattern in self.patterns:
			if pattern.search(haystack):
				return Decision.deny(f"{self.reason}: {pattern.pattern}")
		return Decision.permit()


@dataclass(slots=True)
class RecordCollector(BaseInterceptor):
	"""Keeps every record in memory. Intended for tests and short-lived runs."""

	records: list[CommandRecord] = field(default_factory=list)
	name: str = "record_collector"

	def observe(self, record: CommandRecord) -> None:
		"""Appends the record."""
		self.records.append(record)


__all__ = ["PatternDenyPolicy", "RecordCollector"]
