"""Tests for the GrpcApp facade."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from grpc_server_kit.aio.app import GrpcApp
from grpc_server_kit.config import GrpcServerConfig

pytestmark = pytest.mark.unit


@pytest.fixture
def app() -> GrpcApp:
    return GrpcApp(port=0)


@pytest.fixture
def mock_create_server() -> Generator[MagicMock, None, None]:
    with patch("grpc_server_kit.aio.app.create_async_grpc_server") as mock:
        yield mock


@pytest.fixture
def mock_bind_port() -> Generator[MagicMock, None, None]:
    with patch("grpc_server_kit.aio.app.bind_server_port", return_value=1) as mock:
        yield mock


@pytest.fixture
def built_app(app: GrpcApp, mock_create_server: MagicMock, mock_bind_port: MagicMock) -> GrpcApp:
    """App already built once; server creation stays patched for further build() calls."""
    app.build()
    return app


async def _restart_via_run(app: GrpcApp) -> None:
    await app.run(setup_signals=False)


async def _restart_via_build(app: GrpcApp) -> None:
    app.build()


@pytest.fixture
async def finished_app(app: GrpcApp, mock_create_server: MagicMock, mock_bind_port: MagicMock) -> GrpcApp:
    """App that has already run() to completion once; run()/build() must now reject it as single-use."""
    with patch("grpc_server_kit.aio.app.ServerLifecycleManager") as mock_manager_cls:
        mock_manager_cls.return_value.run = AsyncMock()
        await app.run(setup_signals=False)
    return app


def test__grpc_app_init__settings_and_host_port__raises() -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="either settings or host/port"):
        GrpcApp(GrpcServerConfig(), port=1234)


def test__grpc_app_init__host_port_shortcuts__builds_settings_with_values() -> None:
    # Act
    app = GrpcApp(host="127.0.0.1", port=1234)

    # Assert
    assert app.settings.host == "127.0.0.1"
    assert app.settings.port == 1234


@pytest.mark.parametrize(
    ("attribute", "match"),
    [
        ("server", "not built yet"),
        ("bound_port", "not bound yet"),
    ],
)
def test__grpc_app_property__before_build__raises(app: GrpcApp, attribute: str, match: str) -> None:
    # Act & Assert
    with pytest.raises(RuntimeError, match=match):
        getattr(app, attribute)


def test__request_shutdown__never_started__does_not_raise(app: GrpcApp) -> None:
    # Stopping something that is not running is not an error: any caller-side
    # "is it running?" check would be racy anyway.

    # Act & Assert
    app.request_shutdown()


async def test__request_shutdown__after_run_finished__does_not_raise(finished_app: GrpcApp) -> None:
    # Act & Assert
    finished_app.request_shutdown()


async def test__request_shutdown__before_run__honored_once_serving_starts(
    app: GrpcApp,
    mock_create_server: MagicMock,
    mock_bind_port: MagicMock,
) -> None:
    # Arrange
    # A SIGTERM racing a slow startup must not be lost.
    app.request_shutdown("early")

    # Act
    with patch("grpc_server_kit.aio.app.ServerLifecycleManager") as mock_manager_cls:
        mock_manager_cls.return_value.run = AsyncMock()
        await app.run(setup_signals=False)

    # Assert
    mock_manager_cls.return_value.request_shutdown.assert_called_once_with("early")


async def test__request_shutdown__while_serving__forwards_to_lifecycle_manager(
    app: GrpcApp,
    mock_create_server: MagicMock,
    mock_bind_port: MagicMock,
) -> None:
    # Arrange
    with patch("grpc_server_kit.aio.app.ServerLifecycleManager") as mock_manager_cls:
        serving = asyncio.Event()
        released = asyncio.Event()

        async def _serve(**_kwargs: object) -> None:
            serving.set()
            await released.wait()

        mock_manager_cls.return_value.run = _serve
        run_task = asyncio.create_task(app.run(setup_signals=False))
        await serving.wait()

        # Act
        app.request_shutdown("now")

        # Assert
        mock_manager_cls.return_value.request_shutdown.assert_called_once_with("now")
        released.set()
        await run_task


def test__build__creates_and_binds_server__returns_bound_server(
    app: GrpcApp,
    mock_create_server: MagicMock,
    mock_bind_port: MagicMock,
) -> None:
    # Arrange
    mock_bind_port.return_value = 54321

    # Act
    server = app.build()

    # Assert
    assert server is mock_create_server.return_value
    assert app.bound_port == 54321
    mock_bind_port.assert_called_once_with(server, app.settings)


def test__build__registered_servicers__aggregated_registrar_invokes_all(
    app: GrpcApp,
    mock_create_server: MagicMock,
    mock_bind_port: MagicMock,
) -> None:
    # Arrange
    servicer = MagicMock()
    registrar = MagicMock()
    callback = MagicMock()
    app.add_servicer(servicer, registrar)
    app.register(callback)
    app.build()
    register_all = mock_create_server.call_args.kwargs["register_servicers"]
    raw_server = MagicMock()

    # Act
    register_all(raw_server)

    # Assert
    registrar.assert_called_once_with(servicer, raw_server)
    callback.assert_called_once_with(raw_server)


def test__build__called_twice__returns_same_server_and_creates_once(
    built_app: GrpcApp,
    mock_create_server: MagicMock,
) -> None:
    # Arrange
    first = built_app.server

    # Act
    second = built_app.build()

    # Assert
    assert first is second
    mock_create_server.assert_called_once()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("add_servicer", (MagicMock(), MagicMock())),
        ("add_interceptors", ([MagicMock()],)),
    ],
    ids=["add_servicer", "add_interceptors"],
)
def test__grpc_app__configure_after_build__raises(
    built_app: GrpcApp, method_name: str, args: tuple[object, ...]
) -> None:
    # Act & Assert
    with pytest.raises(RuntimeError, match="already built"):
        getattr(built_app, method_name)(*args)


def test__build__reflection_enabled__passes_names_to_server_creation(
    app: GrpcApp,
    mock_create_server: MagicMock,
    mock_bind_port: MagicMock,
) -> None:
    # Arrange
    app.enable_reflection(["pkg.Svc"])

    # Act
    app.build()

    # Assert
    kwargs = mock_create_server.call_args.kwargs
    assert kwargs["enable_reflection"] is True
    assert kwargs["reflection_service_names"] == ["pkg.Svc"]


def test__build__reflection_enabled_without_names__raises() -> None:
    # Arrange
    app = GrpcApp(GrpcServerConfig(port=0, enable_reflection=True))

    # Act & Assert
    with pytest.raises(ValueError, match="no service names are configured"):
        app.build()


def test__build__health_enabled_with_reflection__adds_health_service_name(
    app: GrpcApp,
    mock_create_server: MagicMock,
    mock_bind_port: MagicMock,
) -> None:
    # Arrange
    app.enable_reflection(["pkg.Svc"])
    app.enable_health()

    # Act
    app.build()

    # Assert
    kwargs = mock_create_server.call_args.kwargs
    assert kwargs["reflection_service_names"] == ["pkg.Svc", "grpc.health.v1.Health"]


def test__build__channelz_enabled__passes_flag_to_server_creation(
    app: GrpcApp,
    mock_create_server: MagicMock,
    mock_bind_port: MagicMock,
) -> None:
    # Arrange
    app.enable_channelz()

    # Act
    app.build()

    # Assert
    assert mock_create_server.call_args.kwargs["enable_channelz"] is True


def test__grpc_app__failed_bind__stays_retryable(app: GrpcApp, mock_create_server: MagicMock) -> None:
    # Nothing is cached on a failed bind: fixing the cause and retrying works,
    # instead of the app wedging on a stale unbound server.

    # Act & Assert
    with patch(
        "grpc_server_kit.aio.app.bind_server_port",
        side_effect=[RuntimeError("port busy"), 4242],
    ):
        with pytest.raises(RuntimeError, match="port busy"):
            app.build()

        # The failure cached nothing...
        with pytest.raises(RuntimeError, match="not built yet"):
            _ = app.server

        # ...so the retry builds a fresh server and binds successfully.
        server = app.build()

    assert server is mock_create_server.return_value
    assert app.bound_port == 4242
    assert mock_create_server.call_count == 2


@pytest.mark.parametrize("restart", [_restart_via_run, _restart_via_build], ids=["run", "build"])
async def test__grpc_app__after_finished__restart_raises(
    finished_app: GrpcApp,
    restart: Callable[[GrpcApp], Awaitable[None]],
) -> None:
    # Act & Assert
    with pytest.raises(RuntimeError, match="cannot restart"):
        await restart(finished_app)


def test__enable_health__zero_cache_ttl__disables_caching(app: GrpcApp) -> None:
    # A settings-valid cache_ttl=0 must disable caching, not crash build().

    # Act
    app.enable_health(cache_ttl=0)

    # Assert
    assert app._health_kwargs is not None
    assert app._health_kwargs["cache_ttl"] is None


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"cache_ttl": -1}, "cache_ttl must be non-negative"),
        ({"check_timeout": 0}, "check_timeout must be positive"),
    ],
    ids=["negative_cache_ttl", "non_positive_check_timeout"],
)
def test__enable_health__invalid_value__raises(app: GrpcApp, kwargs: dict[str, float], match: str) -> None:
    # Act & Assert
    with pytest.raises(ValueError, match=match):
        app.enable_health(**kwargs)


def test__enable_health__settings_with_health_block__reads_defaults() -> None:
    # A settings object carrying a `health` block (like BaseGrpcServerSettings)
    # supplies enable_health defaults automatically.

    # Arrange
    class _Health:
        cache_ttl = 7.5
        check_timeout = 3.0

    class _SettingsWithHealth:
        def __init__(self, inner: GrpcServerConfig) -> None:
            self._inner = inner
            self.health = _Health()

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    app = GrpcApp(_SettingsWithHealth(GrpcServerConfig(port=0)))  # type: ignore[arg-type]

    # Act
    app.enable_health()

    # Assert
    assert app._health_kwargs is not None
    assert app._health_kwargs["cache_ttl"] == 7.5
    assert app._health_kwargs["check_timeout"] == 3.0
