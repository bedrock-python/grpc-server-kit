"""Tests for Prometheus gRPC server metrics."""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry

from grpc_server_kit.observability.metrics import (
    DEFAULT_GRPC_BUCKETS,
    GrpcServerMetrics,
    get_grpc_server_metrics,
    make_metric_name,
)

pytestmark = pytest.mark.unit

LABELS = {"service": "s", "method": "m", "status": "success", "grpc_code": "OK"}


@pytest.fixture
def registry() -> CollectorRegistry:
    """Fresh, isolated Prometheus registry (avoids the process-global registry)."""
    return CollectorRegistry()


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        (None, "grpc_requests_total"),
        ("myapp", "myapp_grpc_requests_total"),
    ],
)
def test__make_metric_name__prefix__builds_prefixed_or_bare_name(prefix: str | None, expected: str) -> None:
    # Act
    name = make_metric_name("grpc_requests_total", prefix)

    # Assert
    assert name == expected


def test__make_metric_name__invalid_prefix__raises() -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="Invalid metric prefix"):
        make_metric_name("x", "bad-prefix")


def test__grpc_server_metrics__record_request__increments_counter_and_histogram(registry: CollectorRegistry) -> None:
    # Arrange
    metrics = GrpcServerMetrics(registry=registry)

    # Act
    metrics.record_request(service="s", method="m", status="success", grpc_code="OK", duration=0.1)

    # Assert
    assert registry.get_sample_value("grpc_requests_total", LABELS) == 1.0
    assert registry.get_sample_value("grpc_request_duration_seconds_count", {"service": "s", "method": "m"}) == 1.0


def test__grpc_server_metrics__prefix_configured__prefixes_metric_names(registry: CollectorRegistry) -> None:
    # Arrange
    metrics = GrpcServerMetrics(prefix="myapp", registry=registry)

    # Act
    metrics.record_request(service="s", method="m", status="success", grpc_code="OK", duration=0.1)

    # Assert
    assert registry.get_sample_value("myapp_grpc_requests_total", LABELS) == 1.0


def test__get_grpc_server_metrics__same_prefix_called_twice__returns_cached_instance() -> None:
    # Act
    first = get_grpc_server_metrics(prefix="cache_test")
    second = get_grpc_server_metrics(prefix="cache_test")

    # Assert
    assert first is second


def test__get_grpc_server_metrics__conflicting_buckets__raises() -> None:
    # Silently returning the cached instance would record latencies into the
    # wrong histogram bounds; a conflict must fail loudly.
    # Arrange
    get_grpc_server_metrics(prefix="bucket_conflict_test")

    # Act & Assert
    with pytest.raises(ValueError, match="already exists with buckets"):
        get_grpc_server_metrics(prefix="bucket_conflict_test", buckets=(0.1, 1.0))


def test__get_grpc_server_metrics__same_buckets_called_twice__returns_cached_instance() -> None:
    # Act
    first = get_grpc_server_metrics(prefix="bucket_same_test", buckets=DEFAULT_GRPC_BUCKETS)
    second = get_grpc_server_metrics(prefix="bucket_same_test", buckets=DEFAULT_GRPC_BUCKETS)

    # Assert
    assert first is second
