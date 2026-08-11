"""Dishka provider for gRPC server Prometheus metrics (``[metrics]`` extra)."""

from __future__ import annotations

import logging

from dishka import Provider, Scope, provide

from grpc_server_kit.aio.interceptors.protocols import GrpcServerMetricsProtocol
from grpc_server_kit.observability.metrics import HAS_PROMETHEUS, GrpcServerMetrics, get_grpc_server_metrics

logger = logging.getLogger(__name__)


class PrometheusGrpcServerMetricsProvider(Provider):
    """Provide ``GrpcServerMetrics`` (and its protocol view) for the metrics interceptor.

    Degrades gracefully like the sibling Sentry/tracer seam providers: when
    ``enabled`` is False OR the ``[metrics]`` extra is absent, provides ``None``
    (the metrics interceptor then no-ops), so the provider can always be
    registered.
    """

    scope = Scope.APP

    def __init__(self, *, enabled: bool = True, prefix: str | None = None) -> None:
        super().__init__()
        self._enabled = enabled
        self._prefix = prefix

    @provide
    def metrics(self) -> GrpcServerMetrics | None:
        """Provide the concrete metrics instance (cached by prefix), or None when disabled/absent."""
        if not self._enabled:
            return None
        if not HAS_PROMETHEUS:
            logger.warning(
                "gRPC server metrics are enabled but prometheus-client is not installed; "
                "metrics are disabled (install grpc-server-kit[metrics] to enable them)"
            )
            return None
        return get_grpc_server_metrics(self._prefix)

    @provide
    def metrics_protocol(self, metrics: GrpcServerMetrics | None) -> GrpcServerMetricsProtocol | None:
        """Provide the protocol-typed view consumed by ``AsyncMetricsInterceptor``."""
        return metrics


__all__ = ["PrometheusGrpcServerMetricsProvider"]
