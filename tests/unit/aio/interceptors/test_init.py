"""Tests for the aio.interceptors package's public API surface."""

from __future__ import annotations

import pytest

from grpc_server_kit.aio import interceptors

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", interceptors.__all__)
def test__aio_interceptors_public_api__exported_name__is_importable(name: str) -> None:
    # Assert
    assert hasattr(interceptors, name), f"missing aio.interceptors export: {name}"


@pytest.mark.parametrize(
    "name",
    [
        "AsyncContextInterceptor",
        "AsyncExceptionHandlerInterceptor",
        "AsyncMetricsInterceptor",
        "AsyncRequestLoggerInterceptor",
        "AsyncSentryInterceptor",
        "AsyncTracingInterceptor",
    ],
)
def test__aio_interceptors_public_api__key_interceptor__is_callable(name: str) -> None:
    # Assert
    assert callable(getattr(interceptors, name))
