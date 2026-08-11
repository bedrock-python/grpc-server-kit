"""Dishka provider building the configured ``AsyncServer`` (``[dishka]`` extra)."""

from __future__ import annotations

from collections.abc import Sequence

import grpc
from dishka import Provider, Scope, provide

from grpc_server_kit.aio.builder import AsyncGrpcServerBuilder
from grpc_server_kit.aio.server import AsyncServer
from grpc_server_kit.protocols import GrpcServerSettingsProtocol, GrpcServiceName


class AsyncGrpcServerProvider(Provider):
    """Provide an ``AsyncServer`` wired with interceptors, options, and reflection/channelz.

    The server is returned WITHOUT servicers registered: register your servicers on
    the resolved server after resolving it (servicer registration is service-specific and
    usually needs other APP-scoped singletons). Reflection (when ``settings.enable_reflection``)
    defaults to advertising only the service name — pass ``reflection_service_names``
    explicitly to advertise more (e.g. the health service, once you register its servicer).
    """

    scope = Scope.APP

    def __init__(self, *, reflection_service_names: Sequence[str] | None = None) -> None:
        super().__init__()
        self._reflection_service_names = list(reflection_service_names) if reflection_service_names else None

    @provide
    def server(
        self,
        settings: GrpcServerSettingsProtocol,
        interceptors: list[grpc.aio.ServerInterceptor],
        service_name: GrpcServiceName,
    ) -> AsyncServer:
        """Build the server from settings + the injected interceptor chain."""
        builder = AsyncGrpcServerBuilder(settings).with_interceptors(interceptors)
        if settings.enable_reflection:
            # Only names whose servicers this bundle actually registers may be
            # advertised by default — advertising the health service without a
            # registered health servicer would reflect an UNIMPLEMENTED service.
            names = self._reflection_service_names or [str(service_name)]
            builder.with_reflection(names)
        if settings.enable_channelz:
            builder.with_channelz()
        return builder.build()


__all__ = ["AsyncGrpcServerProvider"]
