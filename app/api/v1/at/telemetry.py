"""OpenTelemetry scaffolding for AT endpoints.

W3C trace context propagation, structured metrics, and span helpers.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Context var for request trace context propagation
_trace_ctx: ContextVar[dict[str, str]] = ContextVar("at_trace_ctx", default={})


@dataclass(slots=True)
class ATMetrics:
    """In-process metrics collector for AT endpoints.

    Collects counters and histograms for SLO monitoring.
    Production: replace with OpenTelemetry SDK meter provider.
    """

    # Counters
    voice_callbacks_total: int = 0
    sms_callbacks_total: int = 0
    outbound_calls_total: int = 0
    outbound_sms_total: int = 0
    auth_failures_total: int = 0
    rate_limit_hits_total: int = 0
    idempotency_replays_total: int = 0

    # Histograms (recent latency samples)
    _call_setup_latencies: list[float] = field(default_factory=list)
    _sms_response_latencies: list[float] = field(default_factory=list)

    def record_call_setup_latency(self, seconds: float) -> None:
        """Record call setup latency (AT accept → first AI audio)."""
        self._call_setup_latencies.append(seconds)
        # Keep bounded
        if len(self._call_setup_latencies) > 1000:
            self._call_setup_latencies = self._call_setup_latencies[-500:]

    def record_sms_response_latency(self, seconds: float) -> None:
        """Record SMS response latency (callback → AI reply sent)."""
        self._sms_response_latencies.append(seconds)
        if len(self._sms_response_latencies) > 1000:
            self._sms_response_latencies = self._sms_response_latencies[-500:]

    def snapshot(self) -> dict[str, Any]:
        """Return a metrics snapshot for the health/readiness endpoint."""
        return {
            "voice_callbacks_total": self.voice_callbacks_total,
            "sms_callbacks_total": self.sms_callbacks_total,
            "outbound_calls_total": self.outbound_calls_total,
            "outbound_sms_total": self.outbound_sms_total,
            "auth_failures_total": self.auth_failures_total,
            "rate_limit_hits_total": self.rate_limit_hits_total,
            "idempotency_replays_total": self.idempotency_replays_total,
            "call_setup_latency_samples": len(self._call_setup_latencies),
            "sms_response_latency_samples": len(self._sms_response_latencies),
        }


# Singleton metrics instance
metrics = ATMetrics()


def extract_trace_context(headers: dict[str, str]) -> dict[str, str]:
    """Extract W3C trace context from request headers.

    Looks for `traceparent` and `tracestate` headers per W3C spec.
    Returns dict with extracted fields for log enrichment.
    """
    ctx: dict[str, str] = {}
    traceparent = headers.get("traceparent", "")
    if traceparent:
        parts = traceparent.split("-")
        if len(parts) >= 4:
            ctx["trace_id"] = parts[1]
            ctx["span_id"] = parts[2]
    tracestate = headers.get("tracestate", "")
    if tracestate:
        ctx["tracestate"] = tracestate
    return ctx


def set_trace_context(ctx: dict[str, str]) -> None:
    """Set trace context for the current request."""
    _trace_ctx.set(ctx)


def get_trace_context() -> dict[str, str]:
    """Get trace context for the current request."""
    return _trace_ctx.get()


def structured_log(
    level: str,
    message: str,
    *,
    tenant_id: str = "",
    company_id: str = "",
    route: str = "",
    status: str = "",
    **extra: Any,
) -> None:
    """Emit a structured log line with trace context and tenant labels.

    All AT logs should go through this to ensure consistent fields.
    """
    ctx = get_trace_context()
    log_data = {
        "route": route,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "status": status,
        **ctx,
        **extra,
    }
    # Remove empty values
    log_data = {k: v for k, v in log_data.items() if v}

    log_fn = getattr(logger, level, logger.info)
    log_fn(message, extra=log_data)
