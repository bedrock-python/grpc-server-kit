"""Tests for the async gRPC server builder."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from grpc_server_kit.aio.builder import AsyncGrpcServerBuilder

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_create_server() -> Generator[MagicMock, None, None]:
    with patch("grpc_server_kit.aio.builder.create_async_grpc_server") as mock:
        yield mock


def test__builder_build__no_servicers_configured__passes_none(
    mock_settings: MagicMock,
    mock_create_server: MagicMock,
) -> None:
    # Act
    AsyncGrpcServerBuilder(mock_settings).with_interceptors([]).build()

    # Assert
    assert mock_create_server.call_args.kwargs["register_servicers"] is None


def test__builder_build__fully_configured__passes_all_options_through(
    mock_settings: MagicMock,
    mock_create_server: MagicMock,
) -> None:
    # Arrange
    mock_interceptor = MagicMock()
    mock_servicer_reg = MagicMock()
    builder = (
        AsyncGrpcServerBuilder(mock_settings)
        .with_interceptors([mock_interceptor])
        .with_servicers(mock_servicer_reg)
        .with_reflection(["service.Name"])
        .with_channelz()
    )

    # Act
    builder.build()

    # Assert
    mock_create_server.assert_called_once_with(
        interceptors=[mock_interceptor],
        settings=mock_settings,
        register_servicers=mock_servicer_reg,
        enable_reflection=True,
        reflection_service_names=["service.Name"],
        enable_channelz=True,
    )
