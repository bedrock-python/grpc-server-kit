"""Type-safe fluent builder for async gRPC servers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from .server import create_async_grpc_server

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import grpc

    from ..protocols import GrpcServerSettingsProtocol
    from .server import AsyncServer


class AsyncGrpcServerBuilder:
    """Fluent builder for an :class:`AsyncServer`.

    Advanced API — for the common path prefer :class:`grpc_server_kit.aio.GrpcApp`.
    """

    def __init__(self, settings: GrpcServerSettingsProtocol) -> None:
        self._settings = settings
        self._interceptors: list[grpc.aio.ServerInterceptor] = []
        self._register_servicers: Callable[[grpc.aio.Server], None] | None = None
        self._enable_reflection = False
        self._reflection_service_names: Sequence[str] | None = None
        self._enable_channelz = False

    def with_interceptors(self, interceptors: list[grpc.aio.ServerInterceptor]) -> Self:
        """Add interceptors to the server."""
        self._interceptors.extend(interceptors)
        return self

    def with_servicers(self, register_servicers: Callable[[grpc.aio.Server], None]) -> Self:
        """Set the servicer registration callback (optional — servicers can also be
        registered later on the built server)."""
        self._register_servicers = register_servicers
        return self

    def with_reflection(self, service_names: Sequence[str]) -> Self:
        """Enable reflection for the given services."""
        self._enable_reflection = True
        self._reflection_service_names = service_names
        return self

    def with_channelz(self) -> Self:
        """Enable channelz for the server."""
        self._enable_channelz = True
        return self

    def build(self) -> AsyncServer:
        """Build and return the configured AsyncServer."""
        return create_async_grpc_server(
            interceptors=self._interceptors,
            settings=self._settings,
            register_servicers=self._register_servicers,
            enable_reflection=self._enable_reflection,
            reflection_service_names=self._reflection_service_names,
            enable_channelz=self._enable_channelz,
        )
