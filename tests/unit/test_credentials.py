"""Tests for TLS credential loading and certificate/key file validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pytest_lazy_fixtures import lf

from grpc_server_kit.credentials import (
    _check_permissions,
    _read_cert_file,
    _validate_pem,
    load_server_credentials,
)

pytestmark = pytest.mark.unit

PEM_CERT = b"-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----"
PEM_KEY = b"-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----"
PEM_CA = b"-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----"


@pytest.fixture
def cert_path(tmp_path: Path) -> Path:
    path = tmp_path / "server.crt"
    path.write_bytes(PEM_CERT)
    return path


@pytest.fixture
def key_path(tmp_path: Path) -> Path:
    """Private key file with the restrictive permissions TLS requires."""
    path = tmp_path / "server.key"
    path.write_bytes(PEM_KEY)
    path.chmod(0o600)
    return path


@pytest.fixture
def ca_path(tmp_path: Path) -> Path:
    path = tmp_path / "ca.crt"
    path.write_bytes(PEM_CA)
    return path


@pytest.fixture
def ssl_settings(cert_path: Path, key_path: Path, ca_path: Path) -> MagicMock:
    """Settings with valid cert/key/CA files and mTLS client auth enabled."""
    mock = MagicMock()
    mock.ssl_cert_file = str(cert_path)
    mock.ssl_key_file = str(key_path)
    mock.ssl_ca_file = str(ca_path)
    mock.ssl_client_auth = True
    mock.ssl_max_cert_size = None
    return mock


@pytest.fixture
def ssl_settings_without_ca(cert_path: Path, key_path: Path) -> MagicMock:
    """Settings with valid cert/key but no CA file and no client auth."""
    mock = MagicMock()
    mock.ssl_cert_file = str(cert_path)
    mock.ssl_key_file = str(key_path)
    mock.ssl_ca_file = None
    mock.ssl_client_auth = False
    mock.ssl_max_cert_size = None
    return mock


@pytest.fixture
def world_readable_key_path() -> MagicMock:
    """Mock Path whose stat() reports world/group-readable permissions."""
    path = MagicMock()
    path.stat.return_value.st_mode = 0o777
    return path


def test__load_server_credentials__missing_cert_and_key__raises() -> None:
    # Arrange
    settings = MagicMock()
    settings.ssl_cert_file = None
    settings.ssl_key_file = None

    # Act & Assert
    with pytest.raises(ValueError, match="SSL enabled but ssl_cert_file or ssl_key_file not provided"):
        load_server_credentials(settings)


@pytest.mark.parametrize(
    ("settings", "expected_root_certificates", "expected_require_client_auth"),
    [
        (lf("ssl_settings"), PEM_CA, True),
        (lf("ssl_settings_without_ca"), None, False),
    ],
)
def test__load_server_credentials__valid_files__builds_credentials_from_files(
    settings: MagicMock,
    expected_root_certificates: bytes | None,
    expected_require_client_auth: bool,
) -> None:
    # Act
    with patch("grpc.ssl_server_credentials") as mock_ssl:
        load_server_credentials(settings)
        args, kwargs = mock_ssl.call_args

    # Assert
    mock_ssl.assert_called_once()
    assert args[0] == [(PEM_KEY, PEM_CERT)]
    assert kwargs["root_certificates"] == expected_root_certificates
    assert kwargs["require_client_auth"] is expected_require_client_auth


def test__load_server_credentials__cert_file_missing__raises_file_not_found() -> None:
    # Arrange
    settings = MagicMock()
    settings.ssl_cert_file = "non_existent.crt"
    settings.ssl_key_file = "non_existent.key"
    settings.ssl_max_cert_size = None

    # Act & Assert
    with pytest.raises(FileNotFoundError):
        load_server_credentials(settings)


def test__load_server_credentials__cert_file_exceeds_max_size__raises(tmp_path: Path) -> None:
    # Arrange
    large_file = tmp_path / "large.crt"
    large_file.write_bytes(PEM_CERT + (b"0" * (1024 * 1024)))  # > 1MB

    settings = MagicMock()
    settings.ssl_cert_file = str(large_file)
    settings.ssl_key_file = str(large_file)
    settings.ssl_max_cert_size = 1024  # 1KB limit

    # Act & Assert
    with pytest.raises(ValueError, match="Certificate file too large"):
        load_server_credentials(settings)


def test__check_permissions__permissive_private_key_non_strict__logs_warning(
    world_readable_key_path: MagicMock,
) -> None:
    # Act
    with patch("os.name", "posix"), patch("grpc_server_kit.credentials.logger") as mock_logger:
        _check_permissions(world_readable_key_path, "private key", strict=False)

    # Assert
    mock_logger.warning.assert_called_once()


def test__check_permissions__permissive_private_key_strict__raises(world_readable_key_path: MagicMock) -> None:
    # Act & Assert
    with patch("os.name", "posix"), pytest.raises(PermissionError, match="too permissive permissions"):
        _check_permissions(world_readable_key_path, "private key", strict=True)


@pytest.mark.parametrize(
    ("side_effect", "expected_exception", "match"),
    [
        (PermissionError("denied"), PermissionError, "Permission denied reading"),
        (OSError("unexpected"), OSError, "Unexpected error reading"),
    ],
)
def test__read_cert_file__read_bytes_fails__reraises_with_context(
    cert_path: Path,
    side_effect: OSError,
    expected_exception: type[OSError],
    match: str,
) -> None:
    # Act & Assert
    with patch("pathlib.Path.read_bytes", side_effect=side_effect), pytest.raises(expected_exception, match=match):
        _read_cert_file(str(cert_path), "test")


def test__read_cert_file__path_is_a_directory__raises(tmp_path: Path) -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="Path is not a file"):
        _read_cert_file(str(tmp_path), "test")


def test__read_cert_file__invalid_pem_content__propagates_value_error(tmp_path: Path) -> None:
    # Arrange
    file_path = tmp_path / "test.crt"
    file_path.write_bytes(b"not a pem")

    # Act & Assert
    with pytest.raises(ValueError, match="does not contain a PEM block"):
        _read_cert_file(str(file_path), "test")


def test__validate_pem__no_pem_markers__raises() -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="does not contain a PEM block"):
        _validate_pem(b"not a pem", "test")


def test__validate_pem__empty_data__raises() -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="Empty test file"):
        _validate_pem(b"", "test")


@pytest.mark.parametrize(
    ("data", "label"),
    [
        (
            b"subject=CN=example\nissuer=CN=ca\n-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----",
            "certificate",
        ),
        (b"-----BEGIN ENCRYPTED PRIVATE KEY-----\nkey\n-----END ENCRYPTED PRIVATE KEY-----", "private key"),
        (PEM_CA + b"\n" + PEM_CA, "CA certificate"),
    ],
    ids=["preamble_before_block", "encrypted_private_key", "ca_bundle_multiple_blocks"],
)
def test__validate_pem__well_formed_variants__accepted(data: bytes, label: str) -> None:
    # Act
    _validate_pem(data, label)
