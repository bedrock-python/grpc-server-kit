"""Tests for the zero-dependency gRPC server configuration dataclass."""

from __future__ import annotations

import pytest

from grpc_server_kit.config import GrpcServerConfig
from grpc_server_kit.options import build_grpc_options

pytestmark = pytest.mark.unit


def test__grpc_server_config__defaults__match_documented_values() -> None:
    # Act
    config = GrpcServerConfig()

    # Assert
    assert config.host == "[::]"
    assert config.port == 50051
    assert config.ssl_enabled is False
    assert config.enable_reflection is False
    assert config.grace_period == 5.0


def test__grpc_server_config__default_instance__satisfies_options_protocol() -> None:
    # The zero-dep config must be usable everywhere settings are accepted.
    # Act
    options = dict(build_grpc_options(GrpcServerConfig()))

    # Assert
    assert options["grpc.max_send_message_length"] == 4 * 1024 * 1024


def test__grpc_server_config__empty_host__raises() -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="host cannot be empty"):
        GrpcServerConfig(host="")


@pytest.mark.parametrize("invalid_port", [-1, 70000])
def test__grpc_server_config__invalid_port__raises(invalid_port: int) -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="port must be within"):
        GrpcServerConfig(port=invalid_port)


def test__grpc_server_config__port_zero__accepted_as_ephemeral() -> None:
    # Act
    config = GrpcServerConfig(port=0)

    # Assert
    assert config.port == 0


def test__grpc_server_config__negative_grace_period__raises() -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="grace_period must be non-negative"):
        GrpcServerConfig(grace_period=-1.0)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"ssl_enabled": True}, "ssl_cert_file is required"),
        ({"ssl_enabled": True, "ssl_cert_file": "/cert.pem"}, "ssl_key_file is required"),
        (
            {
                "ssl_enabled": True,
                "ssl_cert_file": "/cert.pem",
                "ssl_key_file": "/key.pem",
                "ssl_client_auth": True,
            },
            "ssl_ca_file is required",
        ),
    ],
)
def test__grpc_server_config__incomplete_ssl_settings__raises(kwargs: dict[str, str | bool], match: str) -> None:
    # Act & Assert
    with pytest.raises(ValueError, match=match):
        GrpcServerConfig(**kwargs)
