"""Dishka providers for the Sentry / Tracer interceptor seams.

These supply the protocol-typed ``... | None`` objects the Sentry/Tracing interceptors
accept; they DO NOT initialize the SDKs (``sentry_sdk.init`` / OpenTelemetry provider
setup are process-wide concerns owned by the application runtime, not DI). Each degrades
to ``None`` when its extra is absent or disabled (the interceptor then no-ops).
"""

from __future__ import annotations

from typing import Any, cast

try:
    from opentelemetry import trace

    HAS_OTEL = True
except ImportError:  # pragma: no cover - exercised only when the [tracing] extra is absent
    HAS_OTEL = False

from dishka import Provider, Scope, provide

from grpc_server_kit.aio.interceptors.protocols import ErrorReporterProtocol, TracerProtocol
from grpc_server_kit.observability.sentry import HAS_SENTRY, SentrySdkAdapter
from grpc_server_kit.protocols import GrpcServiceName


class SentryAdapterProvider(Provider):
    """Provide an ``ErrorReporterProtocol`` seam for ``AsyncSentryInterceptor`` (None when disabled/absent)."""

    scope = Scope.APP

    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__()
        self._enabled = enabled

    @provide
    def sentry(self) -> ErrorReporterProtocol | None:
        """Provide the Sentry adapter, or None when disabled or ``[sentry]`` is absent."""
        if not self._enabled or not HAS_SENTRY:
            return None
        return SentrySdkAdapter()


class _OtelTracerAdapter:
    """Adapt an OTel tracer to ``TracerProtocol`` with SDK auto-recording disabled.

    OTel's ``start_as_current_span`` defaults to ``record_exception=True`` /
    ``set_status_on_exception=True``, which would record a junk ``AbortError``
    event (and force ERROR status) for every deliberate abort passing through
    the span, and double-record genuine server errors. The kit's tracing
    interceptor records exceptions selectively, so the SDK automation is off.
    """

    def __init__(self, tracer: Any) -> None:
        self._tracer = tracer

    def start_as_current_span(self, name: str) -> Any:
        return self._tracer.start_as_current_span(
            name,
            record_exception=False,
            set_status_on_exception=False,
        )


class TracerAdapterProvider(Provider):
    """Provide a ``TracerProtocol`` seam for ``AsyncTracingInterceptor`` (None when disabled/absent).

    When enabled and ``[tracing]`` is installed, supplies the process OpenTelemetry tracer
    named after the service.
    """

    scope = Scope.APP

    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__()
        self._enabled = enabled

    @provide
    def tracer(self, service_name: GrpcServiceName) -> TracerProtocol | None:
        """Provide the OTel tracer, or None when disabled or ``[tracing]`` is absent."""
        if not self._enabled or not HAS_OTEL:
            return None
        return cast("TracerProtocol", _OtelTracerAdapter(trace.get_tracer(service_name)))


__all__ = ["SentryAdapterProvider", "TracerAdapterProvider"]
