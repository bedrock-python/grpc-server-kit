"""Tests for OpenTelemetry gRPC server instrumentation helpers."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest

from grpc_server_kit.aio.observability.tracing import instrument_aio_server, uninstrument_aio_server

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("target", "instrumentor_method", "kwargs"),
    [
        (instrument_aio_server, "instrument", {"tracer_provider": "tp"}),
        (uninstrument_aio_server, "uninstrument", {}),
    ],
    ids=["instrument", "uninstrument"],
)
def test__tracing__available_dependency__forwards_kwargs_to_instrumentor(
    target: Callable[..., None],
    instrumentor_method: str,
    kwargs: dict[str, str],
) -> None:
    # Act
    with patch("grpc_server_kit.aio.observability.tracing.GrpcAioInstrumentorServer") as instrumentor_cls:
        target(**kwargs)

    # Assert
    getattr(instrumentor_cls.return_value, instrumentor_method).assert_called_once_with(**kwargs)


@pytest.mark.parametrize(
    "target",
    [instrument_aio_server, uninstrument_aio_server],
    ids=["instrument", "uninstrument"],
)
def test__tracing__missing_dependency__raises(target: Callable[[], None]) -> None:
    # Act & Assert
    with (
        patch("grpc_server_kit.aio.observability.tracing.HAS_OTEL_GRPC", False),
        pytest.raises(ImportError, match=r"grpc-server-kit\[tracing\]"),
    ):
        target()
