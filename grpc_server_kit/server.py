"""Common gRPC server utilities (sync and async)."""

import logging
import string

from .credentials import load_server_credentials
from .protocols import GrpcAsyncServerProtocol, GrpcServerProtocol, GrpcServerSettingsProtocol

logger = logging.getLogger(__name__)

# gRPC service names are dot-separated identifiers up to 253 characters
# (per the gRPC naming and Health Checking Protocol conventions). One shared
# validator serves reflection registration and the health servicer alike.
SERVICE_NAME_MAX_LENGTH = 253
_SERVICE_NAME_ALLOWED_CHARS = frozenset(string.ascii_letters + string.digits + "._-/")


def is_valid_service_name(service: str) -> bool:
    """Check a fully-qualified gRPC service name (character whitelist, no regex)."""
    if not service or len(service) > SERVICE_NAME_MAX_LENGTH:
        return False
    return all(c in _SERVICE_NAME_ALLOWED_CHARS for c in service)


def bind_server_port(
    server: GrpcServerProtocol | GrpcAsyncServerProtocol,
    settings: GrpcServerSettingsProtocol,
) -> int:
    """Bind gRPC server to the configured port with or without TLS.

    Compatible with both sync and async gRPC servers.

    Note:
        If settings.port is 0, the OS will choose an available ephemeral port.
        The actual port bound is returned by this function.

    Args:
        server: gRPC server instance (sync or async)
        settings: Server settings including host, port, and SSL configuration

    Returns:
        The actual bound port number.
    """
    if not (0 <= settings.port <= 65535):
        raise ValueError(f"Invalid port number: {settings.port}")

    if not settings.host:
        raise ValueError("Host cannot be empty")

    bind_address = f"{settings.host}:{settings.port}"
    if settings.ssl_enabled:
        credentials = load_server_credentials(settings)
        port = server.add_secure_port(bind_address, credentials)
        logger.info("gRPC server with TLS", extra={"address": bind_address, "port": port})
    else:
        port = server.add_insecure_port(bind_address)
        logger.info("gRPC server (insecure)", extra={"address": bind_address, "port": port})

    return port
