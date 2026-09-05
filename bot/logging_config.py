"""Structured JSON logging via structlog.

Call ``configure_logging()`` once at program start. After that, anything that
calls ``structlog.get_logger(__name__)`` will emit JSON lines on stdout with
timestamp, level, logger, and any structured kwargs passed to the call.

Why structlog?  It's the path of least resistance for a bot that needs to be
operated: every event is a JSON object that can be grep'd, filtered by Loki,
or fed straight into a feature store for later analysis.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Optional

import structlog


def _add_logger_name(logger: Any, method_name: str, event_dict: dict) -> dict:
    """Stamp the logger's name (or a fallback) into the event dict.

    ``structlog.stdlib.add_logger_name`` requires a stdlib logger; with
    PrintLoggerFactory we don't have one, so we attach the logger name via
    the bound ``_name`` attribute set by :func:`get_logger`.
    """
    event_dict.setdefault("logger", getattr(logger, "_name", logger.__class__.__name__))
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Configure root logger + structlog.

    Safe to call multiple times — re-running just re-binds the processors.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging (e.g. pydantic, urllib3) through the same pipeline.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )


def get_logger(name: Optional[str] = None):
    """Convenience wrapper so callers don't have to import structlog."""
    return structlog.get_logger(name)