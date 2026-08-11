"""SSL/TLS credentials loading for gRPC server."""

import logging
import os
import stat
from pathlib import Path

import grpc

from .protocols import GrpcSslSettingsProtocol

logger = logging.getLogger(__name__)

DEFAULT_MAX_CERT_SIZE = 1024 * 1024  # 1MB


def load_server_credentials(settings: GrpcSslSettingsProtocol, strict: bool = True) -> grpc.ServerCredentials:
    """Load TLS credentials for secure gRPC server.

    Args:
        settings: SSL/TLS configuration protocol
        strict: If True (default), fail on insecure file permissions (Unix only)

    Returns:
        Configured gRPC ServerCredentials

    Raises:
        ValueError: If configuration is invalid or files are too large/invalid
        FileNotFoundError: If certificate/key files are missing
        PermissionError: If files cannot be accessed due to permissions
        OSError: If other I/O errors occur
    """
    if not settings.ssl_cert_file or not settings.ssl_key_file:
        raise ValueError("SSL enabled but ssl_cert_file or ssl_key_file not provided")

    max_size = settings.ssl_max_cert_size or DEFAULT_MAX_CERT_SIZE

    cert = _read_cert_file(settings.ssl_cert_file, "certificate", max_size=max_size, strict=strict)
    key = _read_cert_file(settings.ssl_key_file, "private key", max_size=max_size, strict=strict)

    ca_cert = None
    if settings.ssl_ca_file:
        ca_cert = _read_cert_file(settings.ssl_ca_file, "CA certificate", max_size=max_size, strict=strict)

    return grpc.ssl_server_credentials(
        [(key, cert)],
        root_certificates=ca_cert,
        require_client_auth=settings.ssl_client_auth,
    )


def _read_cert_file(path_str: str, label: str, max_size: int = DEFAULT_MAX_CERT_SIZE, strict: bool = True) -> bytes:
    """Read and validate certificate/key file."""
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"{label.capitalize()} file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    _check_permissions(path, label, strict=strict)

    stats = path.stat()
    if stats.st_size > max_size:
        raise ValueError(f"{label.capitalize()} file too large ({stats.st_size} bytes): {path}")

    try:
        data = path.read_bytes()
        _validate_pem(data, label)
    except PermissionError as e:
        raise PermissionError(f"Permission denied reading {label} file {path}: {e}") from e
    except OSError as e:
        raise OSError(f"Unexpected error reading {label} file {path}: {e}") from e
    else:
        return data


def _validate_pem(data: bytes, label: str) -> None:
    """Light sanity check that the file contains a PEM block.

    Deliberately permissive: accepts preambles before the block (e.g. ``openssl``
    text output), any PEM type (PKCS#8, PKCS#1, SEC1, encrypted keys), and CA
    bundles with multiple blocks. Real validation belongs to the TLS stack —
    this only catches obviously wrong files (empty, DER, JSON, ...).
    """
    if not data.strip():
        raise ValueError(f"Empty {label} file")
    if b"-----BEGIN " not in data or b"-----END " not in data:
        raise ValueError(f"{label.capitalize()} file does not contain a PEM block")


def _check_permissions(path: Path, label: str, strict: bool = True) -> None:
    """Check file permissions for security (Unix only)."""
    if os.name == "nt":
        if label == "private key":
            logger.debug(
                f"Skipping permission check for {label} on Windows. "
                "Ensure only the service user has access to this file.",
                extra={"path": str(path)},
            )
        return

    mode = path.stat().st_mode
    # Fail if world-readable or group-readable private keys on Unix
    if label == "private key" and (mode & stat.S_IRWXG or mode & stat.S_IRWXO):
        msg = (
            f"Sensitive file {path} has too permissive permissions ({oct(mode & 0o777)}). "
            "Private keys must be readable only by owner (chmod 600)."
        )
        if strict:
            raise PermissionError(msg)
        else:
            logger.warning(msg, extra={"path": str(path)})
