"""Tests for the async gRPC server Dishka provider bundle."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from unittest.mock import MagicMock, patch

import grpc
import pytest
from dishka import AsyncContainer, Provider, make_async_container
from dishka.integrations.grpcio import DishkaAioInterceptor

from grpc_server_kit.aio.dishka import (
    AsyncGrpcServerProvider,
    GrpcServerInterceptorsProvider,
    PrometheusGrpcServerMetricsProvider,
    SentryAdapterProvider,
    TracerAdapterProvider,
    grpc_server_providers,
)
from grpc_server_kit.aio.dishka.observability import _OtelTracerAdapter
from grpc_server_kit.aio.interceptors.protocols import (
    ErrorReporterProtocol,
    GrpcServerMetricsProtocol,
    TracerProtocol,
)
from grpc_server_kit.aio.server import AsyncServer
from grpc_server_kit.dishka import GrpcServerSettingsProvider
from grpc_server_kit.observability.metrics import GrpcServerMetrics
from grpc_server_kit.protocols import GrpcServiceName
from grpc_server_kit.settings import BaseGrpcServerSettings

pytestmark = pytest.mark.unit

SERVICE = "my.pkg.MyService"
INTERCEPTORS_KEY = list[grpc.aio.ServerInterceptor]

ContainerFactory = Callable[..., Awaitable[AsyncContainer]]


@pytest.fixture
def make_settings() -> Callable[..., BaseGrpcServerSettings]:
    """Factory building ``BaseGrpcServerSettings`` for tests (bypasses strict kwarg typing)."""

    def _make(**kwargs: object) -> BaseGrpcServerSettings:
        return BaseGrpcServerSettings(**kwargs)  # type: ignore[arg-type]

    return _make


@pytest.fixture
async def container_factory() -> AsyncIterator[ContainerFactory]:
    """Build async containers from providers, closing every one on teardown."""
    containers: list[AsyncContainer] = []

    async def _make(*providers: Provider) -> AsyncContainer:
        container = make_async_container(*providers)
        containers.append(container)
        return container

    yield _make

    for container in containers:
        await container.close()


async def test__grpc_server_providers__default_bundle__wires_service_name_chain_and_server(
    container_factory: ContainerFactory,
    make_settings: Callable[..., BaseGrpcServerSettings],
) -> None:
    # Arrange
    container = await container_factory(
        *grpc_server_providers(
            make_settings(),
            service_name=SERVICE,
            metrics_enabled=False,
            sentry_enabled=False,
            tracing_enabled=False,
        )
    )

    # Act
    service_name = await container.get(GrpcServiceName)
    chain = await container.get(INTERCEPTORS_KEY)
    server = await container.get(AsyncServer)

    # Assert
    assert service_name == SERVICE

    names = [type(i).__name__ for i in chain]
    # Sentry sits INSIDE the exception handler (later in the list) so it
    # observes raw handler exceptions before they become AbortError.
    assert names == [
        "AsyncMetricsInterceptor",
        "AsyncContextInterceptor",
        "AsyncRequestLoggerInterceptor",
        "AsyncTracingInterceptor",
        "AsyncExceptionHandlerInterceptor",
        "AsyncSentryInterceptor",
        "DishkaAioInterceptor",
    ]
    assert isinstance(chain[-1], DishkaAioInterceptor)

    assert isinstance(server, AsyncServer)
    assert server.raw_server is not None


async def test__grpc_server_interceptors_provider__sentry_and_tracing_disabled__drops_them_from_chain(
    container_factory: ContainerFactory,
    make_settings: Callable[..., BaseGrpcServerSettings],
) -> None:
    # Arrange
    container = await container_factory(
        GrpcServerSettingsProvider(make_settings(), service_name=SERVICE),
        PrometheusGrpcServerMetricsProvider(enabled=False),
        SentryAdapterProvider(enabled=False),
        TracerAdapterProvider(enabled=False),
        GrpcServerInterceptorsProvider(include_sentry=False, include_tracing=False),
    )

    # Act
    chain = await container.get(INTERCEPTORS_KEY)

    # Assert
    names = [type(i).__name__ for i in chain]
    assert names == [
        "AsyncMetricsInterceptor",
        "AsyncContextInterceptor",
        "AsyncRequestLoggerInterceptor",
        "AsyncExceptionHandlerInterceptor",
        "DishkaAioInterceptor",
    ]


async def test__grpc_server_interceptors_provider__extra_outer_and_inner__slot_at_chain_ends(
    container_factory: ContainerFactory,
    make_settings: Callable[..., BaseGrpcServerSettings],
) -> None:
    # Arrange
    marker_outer = object()
    marker_inner = object()
    container = await container_factory(
        GrpcServerSettingsProvider(make_settings(), service_name=SERVICE),
        PrometheusGrpcServerMetricsProvider(enabled=False),
        SentryAdapterProvider(enabled=False),
        TracerAdapterProvider(enabled=False),
        GrpcServerInterceptorsProvider(
            include_metrics=False,
            include_context=False,
            include_request_logger=False,
            include_tracing=False,
            include_sentry=False,
            include_exception_handler=False,
            extra_outer=[marker_outer],  # type: ignore[list-item]
            extra_inner=[marker_inner],  # type: ignore[list-item]
        ),
    )

    # Act
    chain = await container.get(INTERCEPTORS_KEY)

    # Assert
    assert chain[0] is marker_outer
    assert chain[-2] is marker_inner
    assert isinstance(chain[-1], DishkaAioInterceptor)
    assert len(chain) == 3


@pytest.mark.parametrize("enabled", [True, False])
async def test__prometheus_grpc_server_metrics_provider__enabled_flag__toggles_metrics_instance(
    container_factory: ContainerFactory,
    enabled: bool,
) -> None:
    # Arrange
    container = await container_factory(
        PrometheusGrpcServerMetricsProvider(enabled=enabled, prefix="dishka_metrics_test")
    )

    # Act
    metrics = await container.get(GrpcServerMetrics | None)
    protocol_view = await container.get(GrpcServerMetricsProtocol | None)

    # Assert
    assert isinstance(metrics, GrpcServerMetrics) is enabled
    assert protocol_view is metrics


async def test__prometheus_grpc_server_metrics_provider__prometheus_missing__provides_none(
    container_factory: ContainerFactory,
) -> None:
    # Missing [metrics] extra degrades to None + warning like the sibling
    # sentry/tracer seams — never an ImportError crash at container resolution.
    # Arrange
    container = await container_factory(PrometheusGrpcServerMetricsProvider(enabled=True))

    # Act
    with patch("grpc_server_kit.aio.dishka.metrics.HAS_PROMETHEUS", False):
        metrics = await container.get(GrpcServerMetrics | None)

    # Assert
    assert metrics is None


@pytest.mark.parametrize(
    ("explicit_metrics_enabled", "expected_enabled"),
    [
        (None, False),  # None defers to settings.metrics_enabled (False here)
        (True, True),  # explicit argument wins over settings
    ],
)
def test__grpc_server_providers__metrics_enabled_argument__resolves_against_settings_field(
    make_settings: Callable[..., BaseGrpcServerSettings],
    explicit_metrics_enabled: bool | None,
    expected_enabled: bool,
) -> None:
    # Act
    providers = grpc_server_providers(
        make_settings(metrics_enabled=False),
        service_name=SERVICE,
        metrics_enabled=explicit_metrics_enabled,
        sentry_enabled=False,
        tracing_enabled=False,
    )

    # Assert
    metrics_provider = next(p for p in providers if isinstance(p, PrometheusGrpcServerMetricsProvider))
    assert metrics_provider._enabled is expected_enabled


def test__otel_tracer_adapter__start_as_current_span__disables_sdk_auto_recording() -> None:
    # Arrange
    inner = MagicMock()
    adapter = _OtelTracerAdapter(inner)

    # Act
    adapter.start_as_current_span("span-name")

    # Assert
    inner.start_as_current_span.assert_called_once_with(
        "span-name", record_exception=False, set_status_on_exception=False
    )


@pytest.mark.parametrize("enabled", [True, False])
async def test__sentry_adapter_provider__enabled_flag__toggles_error_reporter(
    container_factory: ContainerFactory,
    enabled: bool,
) -> None:
    # Arrange
    container = await container_factory(SentryAdapterProvider(enabled=enabled))

    # Act
    reporter = await container.get(ErrorReporterProtocol | None)

    # Assert
    assert (reporter is not None) is enabled


@pytest.mark.parametrize("enabled", [True, False])
async def test__tracer_adapter_provider__enabled_flag__toggles_tracer(
    container_factory: ContainerFactory,
    make_settings: Callable[..., BaseGrpcServerSettings],
    enabled: bool,
) -> None:
    # Arrange
    container = await container_factory(
        GrpcServerSettingsProvider(make_settings(), service_name=SERVICE),
        TracerAdapterProvider(enabled=enabled),
    )

    # Act
    tracer = await container.get(TracerProtocol | None)

    # Assert
    assert (tracer is not None) is enabled


async def test__async_grpc_server_provider__reflection_enabled_via_bundle__builds_server(
    container_factory: ContainerFactory,
    make_settings: Callable[..., BaseGrpcServerSettings],
) -> None:
    # Arrange
    container = await container_factory(
        *grpc_server_providers(
            make_settings(enable_reflection=True),
            service_name=SERVICE,
            metrics_enabled=False,
            sentry_enabled=False,
            tracing_enabled=False,
        )
    )

    # Act
    server = await container.get(AsyncServer)

    # Assert
    assert isinstance(server, AsyncServer)


async def test__async_grpc_server_provider__standalone_registration__builds_server(
    container_factory: ContainerFactory,
    make_settings: Callable[..., BaseGrpcServerSettings],
) -> None:
    # AsyncGrpcServerProvider only needs settings + interceptors + service_name.
    # Arrange
    container = await container_factory(
        GrpcServerSettingsProvider(make_settings(), service_name=SERVICE),
        PrometheusGrpcServerMetricsProvider(enabled=False),
        SentryAdapterProvider(enabled=False),
        TracerAdapterProvider(enabled=False),
        GrpcServerInterceptorsProvider(),
        AsyncGrpcServerProvider(),
    )

    # Act
    server = await container.get(AsyncServer)

    # Assert
    assert isinstance(server, AsyncServer)
