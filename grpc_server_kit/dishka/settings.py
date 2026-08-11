"""Dishka provider exposing gRPC server settings + service name to the container.

Shared (sync) provider: it only holds an already-built settings object, so it lives at
the top level rather than under ``aio``.
"""

from __future__ import annotations

from dishka import Provider, Scope, provide

from grpc_server_kit.protocols import GrpcServerSettingsProtocol, GrpcServiceName


class GrpcServerSettingsProvider(Provider):
    """Provide the gRPC server settings (as ``GrpcServerSettingsProtocol``) and service name.

    Construct with the service's settings object (anything structurally satisfying
    ``GrpcServerSettingsProtocol`` — e.g. ``BaseGrpcServerSettings``) and the fully-qualified
    gRPC service name. Replaces the per-service settings-aliasing boilerplate that the other
    kit providers depend on.
    """

    scope = Scope.APP

    def __init__(self, settings: GrpcServerSettingsProtocol, *, service_name: str) -> None:
        super().__init__()
        self._settings = settings
        self._service_name = service_name

    @provide
    def settings(self) -> GrpcServerSettingsProtocol:
        """Provide the gRPC server settings."""
        return self._settings

    @provide
    def service_name(self) -> GrpcServiceName:
        """Provide the fully-qualified gRPC service name (typed DI key)."""
        return GrpcServiceName(self._service_name)


__all__ = ["GrpcServerSettingsProvider"]
