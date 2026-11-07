"""Traceparent helpers for Nasiko agents (part of the demo-agents MCP kit).

The platform authorizes MCP `tools/call` and LLM-router calls by the W3C `traceparent` the
agent received with the A2A request: its trace id names the live flow. Three helpers:

- `extract_traceparent(context)` — the inbound header from an a2a-sdk `RequestContext`
  (duck-typed, so this module does not import a2a).
- `suppressed_http_instrumentation()` — OTel's httpx auto-instrumentation overwrites any
  `traceparent` already on a request with one derived from the ambient span, which deep in a
  tool loop is not the inbound flow; the gateway then answers 403. Suppressing instrumentation
  for the MCP post keeps our header (proven live in comms-followup's mcp_client.py).
- `bound_traceparent(tp)` — makes the inbound trace the current OTel context for an LLM call,
  so whatever the instrumentation injects carries the same trace id, while the GenAI span (token
  counts) is still recorded. Suppressing there would lose token observability.

OpenTelemetry is optional: without it every helper is a no-op and the explicit header is sent.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import context as _otel_context
    from opentelemetry import trace as _otel_trace
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
except ImportError:  # pragma: no cover - exercised only without OTel installed
    _otel_context = None
    _otel_trace = None
    TraceContextTextMapPropagator = None

SUPPRESS_KEY: Any = None
if _otel_context is not None:
    SUPPRESS_KEY = getattr(_otel_context, "_SUPPRESS_INSTRUMENTATION_KEY", None)
    if SUPPRESS_KEY is None:  # pragma: no cover - older opentelemetry-api
        try:
            from opentelemetry.instrumentation.utils import _SUPPRESS_INSTRUMENTATION_KEY

            SUPPRESS_KEY = _SUPPRESS_INSTRUMENTATION_KEY
        except ImportError:
            SUPPRESS_KEY = None


def extract_traceparent(context: Any) -> str | None:
    """The CURRENT request's `traceparent` from `context.call_context.state["headers"]`.

    Read it per request, never from a process-wide variable: a resumed task can run in a
    worker created by an earlier request.
    """
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
def suppressed_http_instrumentation() -> Iterator[None]:
    """Stop OTel auto-instrumentation from touching requests made inside this block."""
    if _otel_context is None or SUPPRESS_KEY is None:
        yield
        return
    token = _otel_context.attach(_otel_context.set_value(SUPPRESS_KEY, True))
    try:
        yield
    finally:
        _otel_context.detach(token)


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
