"""Request-scoped metadata for safe Tesla API transport logs."""

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class TeslaAPILogContext:
    """Non-secret fields that connect transport events to an application operation."""

    correlation_id: str | None = None
    vehicle_id: str | None = None
    source: str | None = None
    flow_phase: str | None = None
    flow_iteration: int | None = None
    attempt: int | None = None


_CONTEXT: ContextVar[TeslaAPILogContext | None] = ContextVar(
    "tesla_api_log_context",
    default=None,
)


def configure_json_logging() -> None:
    """Emit each JSON application event as one complete logging line."""

    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    logging.basicConfig(level=level, format="%(message)s")


@contextmanager
def tesla_api_log_context(
    *,
    correlation_id: str | None = None,
    vehicle_id: str | None = None,
    source: str | None = None,
    flow_phase: str | None = None,
    flow_iteration: int | None = None,
    attempt: int | None = None,
) -> Iterator[None]:
    """Temporarily add safe request metadata to all Tesla transport calls."""

    current = _CONTEXT.get() or TeslaAPILogContext()
    updated = replace(
        current,
        correlation_id=correlation_id if correlation_id is not None else current.correlation_id,
        vehicle_id=vehicle_id if vehicle_id is not None else current.vehicle_id,
        source=source if source is not None else current.source,
        flow_phase=flow_phase if flow_phase is not None else current.flow_phase,
        flow_iteration=(flow_iteration if flow_iteration is not None else current.flow_iteration),
        attempt=attempt if attempt is not None else current.attempt,
    )
    token = _CONTEXT.set(updated)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def current_tesla_api_log_context() -> TeslaAPILogContext:
    """Return the context attached to the current thread/task."""

    return _CONTEXT.get() or TeslaAPILogContext()
