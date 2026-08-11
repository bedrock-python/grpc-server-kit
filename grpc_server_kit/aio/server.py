"""gRPC server creation and configuration helpers (async)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import grpc

from ..options import build_grpc_options
from ..protocols import GrpcAsyncServerProtocol, GrpcServerSettingsProtocol
from ..server import is_valid_service_name

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

try:
    from grpc_reflection.v1alpha import reflection
except ImportError:
    reflection = None

try:
    from grpc_channelz.v1 import channelz
except ImportError:
    channelz = None

logger = logging.getLogger(__name__)


class AsyncServer(GrpcAsyncServerProtocol):
    """Thin, typed wrapper over :class:`grpc.aio.Server`.

    Exposes the surface the kit needs (ports, lifecycle, servicer registration)
    with precise types; everything else is reachable via :attr:`raw_server`.
    Generated ``add_*Servicer_to_server`` functions accept this wrapper directly.
    """

    def __init__(self, server: grpc.aio.Server) -> None:
        self._server = server

    @property
    def raw_server(self) -> grpc.aio.Server:
        """Get the underlying gRPC server."""
        return self._server

    def add_generic_rpc_handlers(self, generic_rpc_handlers: Sequence[grpc.GenericRpcHandler]) -> None:
        """Register generic RPC handlers (used by generated registration functions)."""
        self._server.add_generic_rpc_handlers(generic_rpc_handlers)

    def add_registered_method_handlers(self, service_name: str, method_handlers: dict[str, Any]) -> None:
        """Register per-method handlers (used by newer generated registration functions)."""
        self._server.add_registered_method_handlers(service_name, method_handlers)

    def add_insecure_port(self, address: str) -> int:
        """Add insecure port; delegates to underlying server and returns int."""
        return int(self._server.add_insecure_port(address))

    def add_secure_port(self, address: str, credentials: grpc.ServerCredentials) -> int:
        """Add secure port; delegates to underlying server and returns int."""
        return int(self._server.add_secure_port(address, credentials))

    async def start(self) -> None:
        """Start the server."""
        await self._server.start()

    async def stop(self, grace: float | None) -> None:
        """Stop the server."""
        if grace is not None and grace < 0:
            raise ValueError(f"grace must be non-negative or None, got {grace}")
        await self._server.stop(grace)

    async def wait_for_termination(self, timeout: float | None = None) -> bool:
        """Wait for server termination.

        Returns:
            True if the server has terminated, False if the timeout expired before termination.
        """
        result = await self._server.wait_for_termination(timeout)
        return result if isinstance(result, bool) else bool(result)


def create_async_grpc_server(
    *,
    interceptors: list[grpc.aio.ServerInterceptor],
    settings: GrpcServerSettingsProtocol,
    register_servicers: Callable[[grpc.aio.Server], None] | None = None,
    enable_reflection: bool = False,
    reflection_service_names: Sequence[str] | None = None,
    enable_channelz: bool = False,
) -> AsyncServer:
    """Create async gRPC server, register servicers, and optionally enable debug services.

    Args:
        interceptors: List of async gRPC interceptors
        settings: gRPC server settings
        register_servicers: Optional callback to register gRPC servicers (servicers can
            also be registered later on the returned server)
        enable_reflection: Whether to enable gRPC reflection
        reflection_service_names: List of service names to expose via reflection
        enable_channelz: Whether to enable gRPC channelz

    Returns:
        Configured async gRPC server ready to be started

    Raises:
        ValueError: If reflection is enabled but service names are not provided
        ImportError: If reflection or channelz packages are missing when enabled
    """
    server_wrapper = create_base_async_grpc_server(interceptors, settings)
    if register_servicers is not None:
        # register_servicers expects the raw grpc.aio.Server
        register_servicers(server_wrapper.raw_server)

    if enable_reflection:
        if reflection is None:
            raise ImportError(
                "grpcio-reflection is not installed. "
                "Please install it with 'pip install grpcio-reflection' "
                "or use 'grpc-server-kit[reflection]' optional dependency."
            )

        validated_names = validate_reflection_service_names(reflection_service_names)
        reflection.enable_server_reflection(
            [*validated_names, reflection.SERVICE_NAME],
            server_wrapper.raw_server,
        )
        logger.info("gRPC reflection enabled")

    if enable_channelz:
        if channelz is None:
            raise ImportError(
                "grpcio-channelz is not installed. "
                "Please install it with 'pip install grpcio-channelz' "
                "or use 'grpc-server-kit[channelz]' optional dependency."
            )

        channelz.add_channelz_servicer(server_wrapper.raw_server)
        logger.info("Channelz enabled")

    return server_wrapper


def validate_reflection_service_names(service_names: Sequence[str] | None) -> list[str]:
    """Validate and normalize (strip, dedupe) reflection service names.

    Raises:
        ValueError: If the list is empty or contains invalid service names.
    """
    if not service_names:
        raise ValueError("reflection_service_names must be provided when reflection is enabled")

    validated_names: list[str] = []
    for name in service_names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid service name: {name}")

        clean_name = name.strip()
        if not is_valid_service_name(clean_name):
            raise ValueError(f"Invalid gRPC service name format: {clean_name}")

        if clean_name not in validated_names:
            validated_names.append(clean_name)

    return validated_names


def create_base_async_grpc_server(
    interceptors: list[grpc.aio.ServerInterceptor],
    settings: GrpcServerSettingsProtocol,
) -> AsyncServer:
    """Create and configure basic async gRPC server.

    Args:
        interceptors: List of async gRPC interceptors
        settings: gRPC server settings

    Returns:
        Configured but not started async gRPC server wrapper
    """
    if settings.max_concurrent_rpcs is not None and settings.max_concurrent_rpcs < 1:
        raise ValueError(
            f"max_concurrent_rpcs must be >= 1 (or None for unlimited), got {settings.max_concurrent_rpcs}"
        )

    server = grpc.aio.server(
        interceptors=interceptors,
        options=build_grpc_options(settings),
        maximum_concurrent_rpcs=settings.max_concurrent_rpcs,
    )

    return AsyncServer(server)
