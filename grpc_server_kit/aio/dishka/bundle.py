"""One-call bundle of the standard gRPC server Dishka providers (``[dishka]`` extra)."""

from __future__ import annotations

from collections.abc import Sequence

import grpc
from dishka import Provider

from grpc_server_kit.aio.dishka.interceptors import GrpcServerInterceptorsProvider
from grpc_server_kit.aio.dishka.metrics import PrometheusGrpcServerMetricsProvider
from grpc_server_kit.aio.dishka.observability import SentryAdapterProvider, TracerAdapterProvider
from grpc_server_kit.aio.dishka.server import AsyncGrpcServerProvider
from grpc_server_kit.aio.interceptors import ErrorDetailFactory, HeaderConfig
from grpc_server_kit.dishka.settings import GrpcServerSettingsProvider
from grpc_server_kit.protocols import GrpcServerSettingsProtocol


def grpc_server_providers(
    settings: GrpcServerSettingsProtocol,
    *,
    service_name: str,
    error_status_map: dict[type[Exception], grpc.StatusCode] | None = None,
    detail_factory: ErrorDetailFactory | None = None,
    header_configs: Sequence[HeaderConfig] | None = None,
    metrics_enabled: bool | None = None,
    metrics_prefix: str | None = None,
    sentry_enabled: bool = True,
    tracing_enabled: bool = True,
    reflection_service_names: Sequence[str] | None = None,
) -> tuple[Provider, ...]:
    """Return the standard set of gRPC server providers for one-line registration.

    Register the result in a Dishka container together with ``GrpcioProvider()`` and your
    servicer/domain providers::

        from grpc_server_kit.dishka import GrpcioProvider
        from grpc_server_kit.aio.dishka import grpc_server_providers

        container = make_async_container(
            *grpc_server_providers(settings.grpc, service_name="my.pkg.MyService"),
            GrpcioProvider(),
            MyDomainProvider(),
        )

    Then resolve ``AsyncServer`` from the container, register your servicers on it,
    ``bind_server_port(server, settings.grpc)`` and ``run_async_grpc_server(server, address=...)``.

    Args:
        metrics_enabled: Explicit metrics toggle. When None (default), read from
            ``settings.metrics_enabled`` if the settings object has that field,
            else True.
    """
    if metrics_enabled is None:
        metrics_enabled = bool(getattr(settings, "metrics_enabled", True))
    return (
        GrpcServerSettingsProvider(settings, service_name=service_name),
        PrometheusGrpcServerMetricsProvider(enabled=metrics_enabled, prefix=metrics_prefix),
        SentryAdapterProvider(enabled=sentry_enabled),
        TracerAdapterProvider(enabled=tracing_enabled),
        GrpcServerInterceptorsProvider(
            error_status_map=error_status_map,
            detail_factory=detail_factory,
            header_configs=header_configs,
        ),
        AsyncGrpcServerProvider(reflection_service_names=reflection_service_names),
    )


__all__ = ["grpc_server_providers"]
