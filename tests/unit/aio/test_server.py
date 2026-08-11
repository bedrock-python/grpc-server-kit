"""Tests for async gRPC server creation, port binding, and the AsyncServer wrapper."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from grpc_server_kit import bind_server_port
from grpc_server_kit.aio import (
    AsyncServer,
    create_async_grpc_server,
    create_base_async_grpc_server,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_server() -> MagicMock:
    """Bare server double satisfying GrpcServerProtocol/GrpcAsyncServerProtocol for bind_server_port."""
    return MagicMock()


@pytest.fixture
def mock_create_base() -> Generator[MagicMock, None, None]:
    with patch("grpc_server_kit.aio.server.create_base_async_grpc_server") as mock:
        mock.return_value = MagicMock()
        yield mock


@pytest.fixture
def mock_grpc_server() -> AsyncMock:
    """Raw grpc.aio.Server double; port/handler registration methods are sync in the real API."""
    server = AsyncMock()
    server.add_insecure_port = MagicMock(return_value=50051)
    server.add_secure_port = MagicMock(return_value=50052)
    server.add_generic_rpc_handlers = MagicMock()
    server.add_registered_method_handlers = MagicMock()
    return server


@pytest.fixture
def async_server(mock_grpc_server: AsyncMock) -> AsyncServer:
    return AsyncServer(mock_grpc_server)


def test__bind_server_port__ssl_disabled__adds_insecure_port(
    mock_server: MagicMock,
    mock_settings: MagicMock,
) -> None:
    # Arrange
    mock_server.add_insecure_port.return_value = 50051
    mock_settings.host = "127.0.0.1"
    mock_settings.port = 50051
    mock_settings.ssl_enabled = False

    # Act
    port = bind_server_port(mock_server, mock_settings)

    # Assert
    assert port == 50051
    mock_server.add_insecure_port.assert_called_once_with("127.0.0.1:50051")


def test__bind_server_port__ssl_enabled__adds_secure_port(mock_server: MagicMock, mock_settings: MagicMock) -> None:
    # Arrange
    mock_server.add_secure_port.return_value = 50051
    mock_settings.host = "localhost"
    mock_settings.port = 50051
    mock_settings.ssl_enabled = True

    # Act
    with patch("grpc_server_kit.server.load_server_credentials", return_value=MagicMock()):
        port = bind_server_port(mock_server, mock_settings)

    # Assert
    assert port == 50051
    mock_server.add_secure_port.assert_called_once()


@pytest.mark.parametrize(
    ("host", "port", "match"),
    [
        ("127.0.0.1", 70000, "Invalid port number"),
        ("", 50051, "Host cannot be empty"),
    ],
    ids=["invalid_port", "empty_host"],
)
def test__bind_server_port__invalid_settings__raises(
    mock_server: MagicMock,
    mock_settings: MagicMock,
    host: str,
    port: int,
    match: str,
) -> None:
    # Arrange
    mock_settings.host = host
    mock_settings.port = port
    mock_settings.ssl_enabled = False

    # Act & Assert
    with pytest.raises(ValueError, match=match):
        bind_server_port(mock_server, mock_settings)


@patch("grpc.aio.server")
@patch("grpc_server_kit.aio.server.build_grpc_options")
def test__create_base_async_grpc_server__valid_settings__configures_grpc_server(
    mock_build_options: MagicMock,
    mock_grpc_server_factory: MagicMock,
    mock_settings: MagicMock,
) -> None:
    # Arrange
    mock_settings.max_concurrent_rpcs = 100
    mock_build_options.return_value = [("opt", 1)]

    # Act
    server = create_base_async_grpc_server(interceptors=[], settings=mock_settings)

    # Assert
    mock_grpc_server_factory.assert_called_once_with(interceptors=[], options=[("opt", 1)], maximum_concurrent_rpcs=100)
    assert server.raw_server is mock_grpc_server_factory.return_value


def test__create_base_async_grpc_server__invalid_max_concurrent_rpcs__raises(mock_settings: MagicMock) -> None:
    # Arrange
    mock_settings.max_concurrent_rpcs = 0

    # Act & Assert
    with pytest.raises(ValueError, match="max_concurrent_rpcs must be >= 1"):
        create_base_async_grpc_server([], mock_settings)


@pytest.mark.parametrize(
    ("patch_target", "build_kwargs"),
    [
        (
            "grpc_reflection.v1alpha.reflection.enable_server_reflection",
            {"enable_reflection": True, "reflection_service_names": ["test.Service"]},
        ),
        (
            "grpc_channelz.v1.channelz.add_channelz_servicer",
            {"enable_channelz": True},
        ),
    ],
    ids=["reflection", "channelz"],
)
def test__create_async_grpc_server__debug_service_enabled__registers_it(
    mock_settings: MagicMock,
    mock_create_base: MagicMock,
    patch_target: str,
    build_kwargs: dict[str, Any],
) -> None:
    # Arrange
    mock_settings.max_concurrent_rpcs = 100

    # Act
    with patch(patch_target) as mock_integration:
        create_async_grpc_server(
            interceptors=[],
            settings=mock_settings,
            register_servicers=lambda s: None,
            **build_kwargs,
        )

    # Assert
    mock_integration.assert_called_once()


def test__create_async_grpc_server__no_register_servicers__servicers_stay_optional(
    mock_settings: MagicMock,
    mock_create_base: MagicMock,
) -> None:
    # Servicers are optional at creation time; they can be registered later.

    # Arrange
    mock_settings.max_concurrent_rpcs = 100

    # Act
    server = create_async_grpc_server(interceptors=[], settings=mock_settings)

    # Assert
    assert server is mock_create_base.return_value


@pytest.mark.parametrize(
    ("reflection_service_names", "match"),
    [
        ([], "reflection_service_names must be provided"),
        ([123], "Invalid service name"),
        ([" "], "Invalid service name"),
        (["bad name!"], "Invalid gRPC service name format"),
        (["a" * 254], "Invalid gRPC service name format"),
    ],
    ids=["empty_list", "non_string_name", "blank_name", "illegal_characters", "over_length_cap"],
)
def test__create_async_grpc_server__invalid_reflection_names__raises(
    mock_settings: MagicMock,
    mock_create_base: MagicMock,
    reflection_service_names: list[Any],
    match: str,
) -> None:
    # Arrange
    mock_settings.max_concurrent_rpcs = 100

    # Act & Assert
    with (
        patch("grpc_reflection.v1alpha.reflection.enable_server_reflection"),
        pytest.raises(ValueError, match=match),
    ):
        create_async_grpc_server(
            interceptors=[],
            settings=mock_settings,
            register_servicers=lambda s: None,
            enable_reflection=True,
            reflection_service_names=reflection_service_names,
        )


def test__create_async_grpc_server__duplicate_and_whitespace_reflection_names__normalizes_them(
    mock_settings: MagicMock,
    mock_create_base: MagicMock,
) -> None:
    # Dashes are legal per the shared service-name validator; duplicates and
    # surrounding whitespace are normalized rather than rejected.

    # Arrange
    mock_settings.max_concurrent_rpcs = 100

    # Act
    with patch("grpc_reflection.v1alpha.reflection.enable_server_reflection") as mock_enable_reflection:
        create_async_grpc_server(
            interceptors=[],
            settings=mock_settings,
            register_servicers=lambda s: None,
            enable_reflection=True,
            reflection_service_names=["test.Svc", " test.Svc ", "other-svc.Name"],
        )

    # Assert
    registered_names = mock_enable_reflection.call_args.args[0]
    assert registered_names[:-1] == ["test.Svc", "other-svc.Name"]


@pytest.mark.parametrize(
    ("patch_target", "build_kwargs", "match"),
    [
        (
            "grpc_server_kit.aio.server.reflection",
            {"enable_reflection": True, "reflection_service_names": ["test.Svc"]},
            "grpcio-reflection is not installed",
        ),
        (
            "grpc_server_kit.aio.server.channelz",
            {"enable_channelz": True},
            "grpcio-channelz is not installed",
        ),
    ],
    ids=["reflection", "channelz"],
)
def test__create_async_grpc_server__optional_extra_not_installed__raises(
    mock_settings: MagicMock,
    mock_create_base: MagicMock,
    patch_target: str,
    build_kwargs: dict[str, Any],
    match: str,
) -> None:
    # Arrange
    mock_settings.max_concurrent_rpcs = 100

    # Act & Assert
    with patch(patch_target, None), pytest.raises(ImportError, match=match):
        create_async_grpc_server(
            interceptors=[],
            settings=mock_settings,
            register_servicers=lambda s: None,
            **build_kwargs,
        )


def test__async_server__add_insecure_port__delegates_to_raw_server(
    async_server: AsyncServer,
    mock_grpc_server: AsyncMock,
) -> None:
    # Act
    port = async_server.add_insecure_port("localhost:0")

    # Assert
    assert port == 50051
    mock_grpc_server.add_insecure_port.assert_called_once_with("localhost:0")


def test__async_server__add_secure_port__delegates_to_raw_server(
    async_server: AsyncServer,
    mock_grpc_server: AsyncMock,
) -> None:
    # Act
    port = async_server.add_secure_port("localhost:0", MagicMock())

    # Assert
    assert port == 50052
    mock_grpc_server.add_secure_port.assert_called_once()


def test__async_server__add_generic_rpc_handlers__delegates_to_raw_server(
    async_server: AsyncServer,
    mock_grpc_server: AsyncMock,
) -> None:
    # Arrange
    handlers = (MagicMock(),)

    # Act
    async_server.add_generic_rpc_handlers(handlers)

    # Assert
    mock_grpc_server.add_generic_rpc_handlers.assert_called_once_with(handlers)


def test__async_server__add_registered_method_handlers__delegates_to_raw_server(
    async_server: AsyncServer,
    mock_grpc_server: AsyncMock,
) -> None:
    # Arrange
    method_handlers = {"M": MagicMock()}

    # Act
    async_server.add_registered_method_handlers("svc", method_handlers)

    # Assert
    mock_grpc_server.add_registered_method_handlers.assert_called_once_with("svc", method_handlers)


async def test__async_server__start__delegates_to_raw_server(
    async_server: AsyncServer,
    mock_grpc_server: AsyncMock,
) -> None:
    # Act
    await async_server.start()

    # Assert
    mock_grpc_server.start.assert_called_once()


async def test__async_server__stop__delegates_to_raw_server(
    async_server: AsyncServer,
    mock_grpc_server: AsyncMock,
) -> None:
    # Act
    await async_server.stop(grace=1.0)

    # Assert
    mock_grpc_server.stop.assert_called_once_with(1.0)


async def test__async_server__wait_for_termination__delegates_to_raw_server(
    async_server: AsyncServer,
    mock_grpc_server: AsyncMock,
) -> None:
    # Arrange
    mock_grpc_server.wait_for_termination.return_value = True

    # Act
    result = await async_server.wait_for_termination(timeout=1.0)

    # Assert
    assert result is True
    mock_grpc_server.wait_for_termination.assert_called_once_with(1.0)


def test__async_server__raw_server_property__returns_wrapped_server(
    async_server: AsyncServer,
    mock_grpc_server: AsyncMock,
) -> None:
    # Act
    raw = async_server.raw_server

    # Assert
    assert raw == mock_grpc_server


def test__async_server__unknown_attribute__raises_attribute_error(async_server: AsyncServer) -> None:
    # No __getattr__ magic: unknown attributes fail loudly; raw_server is the escape hatch.

    # Act & Assert
    with pytest.raises(AttributeError):
        _ = async_server.another_method  # type: ignore[attr-defined]


async def test__async_server__stop_negative_grace__raises(async_server: AsyncServer) -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="grace must be non-negative"):
        await async_server.stop(grace=-1.0)


async def test__async_server__wait_for_termination_non_bool_result__coerces_to_bool(
    async_server: AsyncServer,
    mock_grpc_server: AsyncMock,
) -> None:
    # Arrange
    mock_grpc_server.wait_for_termination.return_value = 1  # truthy non-bool

    # Act
    result = await async_server.wait_for_termination()

    # Assert
    assert result is True
