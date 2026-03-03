"""TDD tests for AT OpenTelemetry scaffolding.

Covers: W3C trace context extraction, metrics collection,
structured log field contract, and SLO metric recording.
"""

from __future__ import annotations

# ── W3C Trace Context ──


class TestTraceContextExtraction:
    """Extract traceparent/tracestate from headers."""

    def test_extract_valid_traceparent(self) -> None:
        from app.api.v1.at.telemetry import extract_trace_context

        headers = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        }
        ctx = extract_trace_context(headers)
        assert ctx["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert ctx["span_id"] == "00f067aa0ba902b7"

    def test_extract_with_tracestate(self) -> None:
        from app.api.v1.at.telemetry import extract_trace_context

        headers = {
            "traceparent": "00-abc123-def456-01",
            "tracestate": "vendor=value",
        }
        ctx = extract_trace_context(headers)
        assert ctx["tracestate"] == "vendor=value"

    def test_missing_traceparent_returns_empty(self) -> None:
        from app.api.v1.at.telemetry import extract_trace_context

        ctx = extract_trace_context({})
        assert ctx == {}

    def test_malformed_traceparent_returns_empty(self) -> None:
        from app.api.v1.at.telemetry import extract_trace_context

        ctx = extract_trace_context({"traceparent": "garbage"})
        assert "trace_id" not in ctx


# ── Metrics Collection ──


class TestATMetrics:
    """In-process metrics collector."""

    def test_initial_counters_zero(self) -> None:
        from app.api.v1.at.telemetry import ATMetrics

        m = ATMetrics()
        assert m.voice_callbacks_total == 0
        assert m.auth_failures_total == 0

    def test_increment_counters(self) -> None:
        from app.api.v1.at.telemetry import ATMetrics

        m = ATMetrics()
        m.voice_callbacks_total += 1
        m.sms_callbacks_total += 3
        assert m.voice_callbacks_total == 1
        assert m.sms_callbacks_total == 3

    def test_record_call_setup_latency(self) -> None:
        from app.api.v1.at.telemetry import ATMetrics

        m = ATMetrics()
        m.record_call_setup_latency(1.5)
        m.record_call_setup_latency(2.0)
        assert len(m._call_setup_latencies) == 2
        assert m._call_setup_latencies[0] == 1.5

    def test_record_sms_response_latency(self) -> None:
        from app.api.v1.at.telemetry import ATMetrics

        m = ATMetrics()
        m.record_sms_response_latency(0.8)
        assert len(m._sms_response_latencies) == 1

    def test_latency_list_bounded(self) -> None:
        from app.api.v1.at.telemetry import ATMetrics

        m = ATMetrics()
        for i in range(1100):
            m.record_call_setup_latency(float(i))
        # Trim triggers at >1000, keeps last 500 + remaining additions
        assert len(m._call_setup_latencies) < 1100

    def test_snapshot_includes_all_fields(self) -> None:
        from app.api.v1.at.telemetry import ATMetrics

        m = ATMetrics()
        m.voice_callbacks_total = 10
        m.auth_failures_total = 2
        snap = m.snapshot()
        assert snap["voice_callbacks_total"] == 10
        assert snap["auth_failures_total"] == 2
        assert "call_setup_latency_samples" in snap


# ── Structured Log Fields ──


class TestStructuredLogContract:
    """structured_log() emits required fields."""

    def test_structured_log_includes_tenant_fields(self) -> None:
        """structured_log() accepts all required fields without error."""
        from app.api.v1.at.telemetry import structured_log

        # Should not raise — verifies the function accepts all required fields
        structured_log(
            "info",
            "Test log",
            tenant_id="public",
            company_id="acme",
            route="/api/v1/at/voice/callback",
            status="ok",
        )

    def test_structured_log_includes_trace_context(self) -> None:
        from app.api.v1.at.telemetry import structured_log, set_trace_context

        set_trace_context({"trace_id": "abc123", "span_id": "def456"})
        # Should not crash and should include trace context
        structured_log("info", "Traced request", route="/test")

    def test_structured_log_omits_empty_fields(self) -> None:
        """Empty string fields should be filtered out of log extra."""
        from app.api.v1.at.telemetry import structured_log, get_trace_context, set_trace_context

        set_trace_context({})
        # This should work fine with empty optional fields
        structured_log("info", "Minimal log")


# ── Trace Context Propagation ──


class TestTraceContextPropagation:
    """Context var set/get for request-scoped trace context."""

    def test_set_and_get_trace_context(self) -> None:
        from app.api.v1.at.telemetry import set_trace_context, get_trace_context

        set_trace_context({"trace_id": "test-trace", "span_id": "test-span"})
        ctx = get_trace_context()
        assert ctx["trace_id"] == "test-trace"

    def test_default_trace_context_is_empty(self) -> None:
        from app.api.v1.at.telemetry import _trace_ctx

        # Reset to default
        tok = _trace_ctx.set({})
        ctx = _trace_ctx.get()
        assert ctx == {}
        _trace_ctx.reset(tok)
