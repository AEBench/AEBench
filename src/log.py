from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, TextIO, cast

import structlog

if TYPE_CHECKING:
	from structlog.typing import FilteringBoundLogger


def _stderr() -> TextIO:
	stream = sys.__stderr__
	if stream is None:
		raise RuntimeError("sys.__stderr__ is not available")
	return cast(TextIO, stream)


def configure_logging(*, log_level: str = "info", log_renderer: str = "console") -> None:
	level = _to_logging_level(log_level)
	renderer: structlog.typing.Processor = (
		structlog.processors.JSONRenderer()
		if log_renderer == "json"
		else structlog.dev.ConsoleRenderer()
	)
	logging.basicConfig(
		level=level,
		handlers=[logging.StreamHandler(_stderr())],
		force=True,
	)
	structlog.configure(
		processors=[
			structlog.contextvars.merge_contextvars,
			structlog.processors.add_log_level,
			structlog.processors.TimeStamper(fmt="iso"),
			structlog.processors.StackInfoRenderer(),
			structlog.processors.UnicodeDecoder(),
			structlog.processors.format_exc_info,
			renderer,
		],
		wrapper_class=structlog.make_filtering_bound_logger(level),
		logger_factory=structlog.PrintLoggerFactory(file=_stderr()),
		cache_logger_on_first_use=True,
	)


def get_logger(name: str = "") -> FilteringBoundLogger:
	return cast("FilteringBoundLogger", structlog.get_logger(name))


def _to_logging_level(log_level: str) -> int:
	return {
		"debug": logging.DEBUG,
		"info": logging.INFO,
		"warning": logging.WARNING,
		"error": logging.ERROR,
	}.get(log_level.lower(), logging.INFO)
