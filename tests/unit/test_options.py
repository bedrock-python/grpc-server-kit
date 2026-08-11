"""Tests for gRPC channel option building."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from grpc_server_kit.options import build_grpc_options

pytestmark = pytest.mark.unit


@pytest.fixture
def settings() -> MagicMock:
    """Settings with every tuning field set to a valid value and no optional limits."""
    mock = MagicMock()
    mock.max_send_message_length = 1024
    mock.max_receive_message_length = 2048
    mock.max_metadata_size = 512
    mock.keepalive_time_ms = 1000
    mock.keepalive_timeout_ms = 500
    mock.keepalive_permit_without_calls = False
    mock.http2_min_recv_ping_interval_without_data_ms = 100
    mock.http2_max_pings_without_data = 5
    mock.initial_stream_window_size = 65535
    mock.initial_connection_window_size = 65535
    mock.max_concurrent_rpcs = None
    mock.max_connection_idle_ms = None
    mock.max_connection_age_ms = None
    mock.max_connection_age_grace_ms = None
    mock.compression_algorithm = None
    return mock


def test__build_grpc_options__all_fields_set__maps_every_field(settings: MagicMock) -> None:
    # Arrange
    settings.keepalive_permit_without_calls = True
    settings.max_concurrent_rpcs = 50
    settings.max_connection_idle_ms = 3000
    settings.max_connection_age_ms = 6000
    settings.max_connection_age_grace_ms = 1000

    # Act
    options = dict(build_grpc_options(settings))

    # Assert
    assert options == {
        "grpc.max_send_message_length": 1024,
        "grpc.max_receive_message_length": 2048,
        "grpc.max_metadata_size": 512,
        "grpc.keepalive_time_ms": 1000,
        "grpc.keepalive_timeout_ms": 500,
        "grpc.keepalive_permit_without_calls": 1,
        "grpc.http2.min_recv_ping_interval_without_data_ms": 100,
        "grpc.http2.max_pings_without_data": 5,
        "grpc.http2.initial_stream_window_size": 65535,
        "grpc.http2.initial_connection_window_size": 65535,
        "grpc.max_concurrent_streams": 50,
        "grpc.max_connection_idle_ms": 3000,
        "grpc.max_connection_age_ms": 6000,
        "grpc.max_connection_age_grace_ms": 1000,
    }


@pytest.mark.parametrize(
    "option_name",
    [
        "grpc.max_concurrent_streams",
        "grpc.max_connection_idle_ms",
        "grpc.max_connection_age_ms",
        "grpc.max_connection_age_grace_ms",
        "grpc.default_compression_algorithm",
    ],
)
def test__build_grpc_options__optional_field_unset__omits_option(settings: MagicMock, option_name: str) -> None:
    # Act
    options = dict(build_grpc_options(settings))

    # Assert
    assert option_name not in options


def test__build_grpc_options__field_is_none__falls_back_to_default(settings: MagicMock) -> None:
    # Arrange
    settings.max_send_message_length = None

    # Act
    options = dict(build_grpc_options(settings))

    # Assert
    assert options["grpc.max_send_message_length"] == 4 * 1024 * 1024


@pytest.mark.parametrize(
    ("algorithm", "expected_code"),
    [
        ("gzip", 2),
        ("GZIP", 2),
        ("deflate", 1),
        ("none", 0),
    ],
)
def test__build_grpc_options__supported_compression__maps_to_algorithm_code(
    settings: MagicMock,
    algorithm: str,
    expected_code: int,
) -> None:
    # Arrange
    settings.compression_algorithm = algorithm

    # Act
    options = dict(build_grpc_options(settings))

    # Assert
    assert options["grpc.default_compression_algorithm"] == expected_code


def test__build_grpc_options__unsupported_compression__raises(settings: MagicMock) -> None:
    # Arrange
    settings.compression_algorithm = "invalid"

    # Act & Assert
    with pytest.raises(ValueError, match="Unsupported compression_algorithm"):
        build_grpc_options(settings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_send_message_length", -100),
        ("max_metadata_size", -1),
        ("keepalive_time_ms", -1),
        ("max_connection_idle_ms", -5),
        ("max_connection_age_ms", -5),
    ],
)
def test__build_grpc_options__negative_value__raises(settings: MagicMock, field: str, value: int) -> None:
    # Arrange
    setattr(settings, field, value)

    # Act & Assert
    with pytest.raises(ValueError, match=f"{field} must be non-negative"):
        build_grpc_options(settings)
