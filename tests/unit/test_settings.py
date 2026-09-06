"""Tests for the pydantic-based gRPC server and health settings models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from grpc_server_kit.settings import BaseGrpcServerSettings, BaseHealthSettings, GrpcCompressionAlgorithm

pytestmark = pytest.mark.unit


def test__base_grpc_server_settings__defaults__match_documented_values() -> None:
    # Act
    settings = BaseGrpcServerSettings()

    # Assert
    assert settings.host == "[::]"
    assert settings.port == 50051
    assert settings.ssl_enabled is False
    assert isinstance(settings.health, BaseHealthSettings)
    assert settings.health.cache_ttl == 5.0


def test__base_grpc_server_settings__custom_values__override_defaults() -> None:
    # Act
    settings = BaseGrpcServerSettings(host="0.0.0.0", port=9090, compression_algorithm="gzip")

    # Assert
    assert settings.host == "0.0.0.0"
    assert settings.port == 9090
    assert settings.compression_algorithm == "gzip"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"ssl_enabled": True}, "ssl_cert_file is required"),
        ({"ssl_enabled": True, "ssl_cert_file": "/path/cert.pem"}, "ssl_key_file is required"),
        (
            {
                "ssl_enabled": True,
                "ssl_cert_file": "/path/cert.pem",
                "ssl_key_file": "/path/key.pem",
                "ssl_client_auth": True,
            },
            "ssl_ca_file is required",
        ),
    ],
)
def test__base_grpc_server_settings__incomplete_ssl_settings__raises(
    kwargs: dict[str, str | bool],
    match: str,
) -> None:
    # Act & Assert
    with pytest.raises(ValidationError, match=match):
        BaseGrpcServerSettings(**kwargs)


def test__base_grpc_server_settings__complete_mtls_settings__accepted() -> None:
    # Act
    settings = BaseGrpcServerSettings(
        ssl_enabled=True,
        ssl_cert_file="/path/cert.pem",
        ssl_key_file="/path/key.pem",
        ssl_ca_file="/path/ca.pem",
        ssl_client_auth=True,
    )

    # Assert
    assert settings.ssl_client_auth is True
    assert settings.ssl_ca_file == "/path/ca.pem"


@pytest.mark.parametrize("invalid_port", [70000, -1])
def test__base_grpc_server_settings__invalid_port__raises(invalid_port: int) -> None:
    # Act & Assert
    with pytest.raises(ValidationError):
        BaseGrpcServerSettings(port=invalid_port)


def test__base_grpc_server_settings__port_zero__accepted_as_ephemeral() -> None:
    # Act
    settings = BaseGrpcServerSettings(port=0)

    # Assert
    assert settings.port == 0


@pytest.mark.parametrize("algorithm", ["none", "deflate", "gzip"])
def test__base_grpc_server_settings__supported_compression_algorithm__accepted(
    algorithm: GrpcCompressionAlgorithm,
) -> None:
    # Act
    settings = BaseGrpcServerSettings(compression_algorithm=algorithm)

    # Assert
    assert settings.compression_algorithm == algorithm


def test__base_grpc_server_settings__invalid_compression_algorithm__raises() -> None:
    # Act & Assert
    with pytest.raises(ValidationError):
        BaseGrpcServerSettings(compression_algorithm="bzip2")  # type: ignore[arg-type]


def test__base_grpc_server_settings__empty_host__raises() -> None:
    # Act & Assert
    with pytest.raises(ValidationError):
        BaseGrpcServerSettings(host="")


def test__base_health_settings__defaults__match_documented_values() -> None:
    # Act
    health = BaseHealthSettings()

    # Assert
    assert health.cache_ttl == 5.0
    assert health.check_timeout == 10.0


def test__base_health_settings__cache_ttl_zero__means_no_caching() -> None:
    # 0 is a valid, documented value ("disable caching") end-to-end.
    # Act
    health = BaseHealthSettings(cache_ttl=0)

    # Assert
    assert health.cache_ttl == 0


def test__base_health_settings__non_positive_check_timeout__raises() -> None:
    # Act & Assert
    with pytest.raises(ValidationError):
        BaseHealthSettings(check_timeout=0.0)
