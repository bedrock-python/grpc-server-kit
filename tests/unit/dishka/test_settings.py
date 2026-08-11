"""Tests for the shared gRPC server settings Dishka provider."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from dishka import AsyncContainer, make_async_container

from grpc_server_kit.dishka import GrpcServerSettingsProvider
from grpc_server_kit.protocols import GrpcServerSettingsProtocol, GrpcServiceName
from grpc_server_kit.settings import BaseGrpcServerSettings

pytestmark = pytest.mark.unit


@pytest.fixture
async def container() -> AsyncIterator[AsyncContainer]:
    """Container wired with ``GrpcServerSettingsProvider`` for a fixed test settings/service name."""
    settings = BaseGrpcServerSettings(port=12345)
    async_container = make_async_container(GrpcServerSettingsProvider(settings, service_name="svc.X"))
    yield async_container
    await async_container.close()


async def test__grpc_server_settings_provider__container__resolves_settings_and_service_name(
    container: AsyncContainer,
) -> None:
    # Act
    settings = await container.get(GrpcServerSettingsProtocol)
    service_name = await container.get(GrpcServiceName)

    # Assert
    assert settings.port == 12345
    assert service_name == "svc.X"
