"""Traceparent helpers (copied in from the agent kit; owned by this agent).

The platform authorizes LLM-router calls by the W3C `traceparent` the agent received with the A2A
request: its trace id names the live flow.

- `extract_traceparent(context)`: the inbound header from an a2a-sdk `RequestContext` (duck-typed).
- `bound_traceparent(tp)`: makes the inbound trace the current OTel context for an LLM call, so the
  header the instrumentation injects carries the same trace id while the GenAI span is still recorded.

OpenTelemetry is optional: without it every helper is a no-op and the explicit header is sent.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import context as _otel_context
    from opentelemetry import trace as _otel_trace
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )
except ImportError:  # pragma: no cover - exercised only without OTel installed
    _otel_context = None
    _otel_trace = None
    TraceContextTextMapPropagator = None


def extract_traceparent(context: Any) -> str | None:
    """The CURRENT request's `traceparent` from `context.call_context.state["headers"]`."""
    call_context = getattr(context, "call_context", None)
    state = getattr(call_context, "state", None)
    if not isinstance(state, dict):
        return None
    headers = state.get("headers")
    if not isinstance(headers, dict):
        return None
    value = headers.get("traceparent")
    return value if isinstance(value, str) and value else None


@contextmanager
def bound_traceparent(traceparent: str) -> Iterator[None]:
    """Make the inbound trace the current OTel context for the duration of the block."""
    if _otel_context is None or TraceContextTextMapPropagator is None:
        yield
        return
    extracted = TraceContextTextMapPropagator().extract({"traceparent": traceparent})
    if not _otel_trace.get_current_span(extracted).get_span_context().is_valid:
        yield  # malformed header: keep the ambient context rather than an empty one
        return
    token = _otel_context.attach(extracted)
    try:
        yield
    finally:
        _otel_context.detach(token)
