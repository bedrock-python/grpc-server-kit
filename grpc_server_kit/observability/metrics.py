"""Prometheus metric definitions for the gRPC server (``[metrics]`` extra).

``GrpcServerMetrics`` structurally satisfies
:class:`grpc_server_kit.aio.interceptors.protocols.GrpcServerMetricsProtocol`, so it can
be passed straight to ``AsyncMetricsInterceptor``.
"""

from __future__ import annotations

from grpc_server_kit.observability.naming import make_metric_name

try:
    from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

    HAS_PROMETHEUS = True
except ImportError:  # pragma: no cover - exercised only when the [metrics] extra is absent
    HAS_PROMETHEUS = False

__all__ = [
    "DEFAULT_GRPC_BUCKETS",
    "GrpcServerMetrics",
    "get_grpc_server_metrics",
    "make_metric_name",
]

# Default histogram buckets for gRPC request duration (seconds).
DEFAULT_GRPC_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_MISSING_MESSAGE = "Install grpc-server-kit[metrics] (prometheus-client) to use gRPC server metrics"


class GrpcServerMetrics:
    """gRPC server metrics.

    Metrics:
        - ``grpc_requests_total`` (Counter) — labels: service, method, status, grpc_code
        - ``grpc_request_duration_seconds`` (Histogram) — labels: service, method
    """

    def __init__(
        self,
        prefix: str | None = None,
        buckets: tuple[float, ...] = DEFAULT_GRPC_BUCKETS,
        registry: CollectorRegistry | None = None,
    ) -> None:
        """Initialize gRPC server metrics.

        Args:
            prefix: Optional metric name prefix.
            buckets: Histogram buckets for request duration.
            registry: Collector registry to register with (defaults to the global one).

        Raises:
            ImportError: If prometheus-client is not installed.
        """
        if not HAS_PROMETHEUS:
            raise ImportError(_MISSING_MESSAGE)

        self.buckets: tuple[float, ...] = tuple(buckets)
        reg = registry if registry is not None else REGISTRY
        self.requests_total = Counter(
            make_metric_name("grpc_requests_total", prefix),
            "Total number of gRPC requests",
            ["service", "method", "status", "grpc_code"],
            registry=reg,
        )
        self.request_duration = Histogram(
            make_metric_name("grpc_request_duration_seconds", prefix),
            "gRPC request duration in seconds",
            ["service", "method"],
            buckets=list(buckets),
            registry=reg,
        )

    def record_request(
        self,
        service: str,
        method: str,
        status: str,
        grpc_code: str,
        duration: float,
    ) -> None:
        """Record a completed gRPC request."""
        self.requests_total.labels(service=service, method=method, status=status, grpc_code=grpc_code).inc()
        self.request_duration.labels(service=service, method=method).observe(duration)


_SERVER_METRICS_CACHE: dict[str | None, GrpcServerMetrics] = {}


def get_grpc_server_metrics(
    prefix: str | None = None,
    buckets: tuple[float, ...] | None = None,
) -> GrpcServerMetrics:
    """Get (or lazily create) a cached ``GrpcServerMetrics`` for the given prefix.

    Caching by prefix avoids Prometheus "duplicated timeseries" errors when the helper
    is called more than once for the same prefix.

    Raises:
        ValueError: If the prefix is already cached with DIFFERENT buckets —
            silently returning the old instance would record latencies into the
            wrong histogram bounds with no error.
    """
    requested = tuple(buckets) if buckets is not None else DEFAULT_GRPC_BUCKETS
    cached = _SERVER_METRICS_CACHE.get(prefix)
    if cached is not None:
        if buckets is not None and requested != cached.buckets:
            raise ValueError(
                f"GrpcServerMetrics for prefix {prefix!r} already exists with buckets "
                f"{cached.buckets}; cannot re-create it with {requested}"
            )
        return cached
    metrics = GrpcServerMetrics(prefix=prefix, buckets=requested)
    _SERVER_METRICS_CACHE[prefix] = metrics
    return metrics
