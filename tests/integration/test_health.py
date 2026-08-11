"""Integration tests for the gRPC Health Checking Protocol v1 servicer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from grpc_server_kit import GrpcServerConfig
from grpc_server_kit.aio import create_async_grpc_server
from grpc_server_kit.aio.health import AsyncDynamicHealthServicer, HealthCache

pytestmark = pytest.mark.integration


class FlakyChecker:
    """Health checker whose result can be flipped by the test."""

    def __init__(self) -> None:
        self.healthy = True

    async def check(self) -> bool:
        return self.healthy


@dataclass
class HealthServer:
    """A running health-only server and the checker driving its status."""

    port: int
    checker: FlakyChecker

    def target(self) -> str:
        return f"127.0.0.1:{self.port}"


@pytest.fixture
async def health_server() -> AsyncIterator[HealthServer]:
    """A started grpc.aio server exposing only the gRPC Health service."""
    checker = FlakyChecker()
    servicer = AsyncDynamicHealthServicer(
        checkers=[checker],
        cache=HealthCache(ttl=0.05),
        check_interval=0.05,
        heartbeat_interval=60.0,
    )
    server = create_async_grpc_server(
        interceptors=[],
        settings=GrpcServerConfig(host="127.0.0.1", port=0),
        register_servicers=lambda raw: health_pb2_grpc.add_HealthServicer_to_server(servicer, raw),
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield HealthServer(port=port, checker=checker)
    finally:
        await server.stop(grace=None)


@pytest.fixture
async def channel(health_server: HealthServer) -> AsyncIterator[grpc.aio.Channel]:
    """An open channel to the running health server."""
    async with grpc.aio.insecure_channel(health_server.target()) as chan:
        yield chan


async def test__health_check__all_checkers_healthy__reports_serving(channel: grpc.aio.Channel) -> None:
    # Arrange
    stub = health_pb2_grpc.HealthStub(channel)

    # Act
    response = await stub.Check(health_pb2.HealthCheckRequest(service=""))

    # Assert
    assert response.status == health_pb2.HealthCheckResponse.SERVING


async def test__health_check__checker_unhealthy__reports_not_serving(
    channel: grpc.aio.Channel,
    health_server: HealthServer,
) -> None:
    # Arrange
    health_server.checker.healthy = False
    stub = health_pb2_grpc.HealthStub(channel)
    await asyncio.sleep(0.1)  # let the cache TTL expire

    # Act
    response = await stub.Check(health_pb2.HealthCheckRequest(service=""))

    # Assert
    assert response.status == health_pb2.HealthCheckResponse.NOT_SERVING


async def test__health_check__unknown_service__reports_service_unknown(channel: grpc.aio.Channel) -> None:
    # Arrange
    stub = health_pb2_grpc.HealthStub(channel)

    # Act
    response = await stub.Check(health_pb2.HealthCheckRequest(service="no.such.Service"))

    # Assert
    assert response.status == health_pb2.HealthCheckResponse.SERVICE_UNKNOWN


async def test__health_watch__checker_status_changes__streams_updated_statuses(
    channel: grpc.aio.Channel,
    health_server: HealthServer,
) -> None:
    # Arrange
    stub = health_pb2_grpc.HealthStub(channel)
    call = stub.Watch(health_pb2.HealthCheckRequest(service=""))

    # Act
    first = await asyncio.wait_for(call.read(), timeout=5)
    health_server.checker.healthy = False
    second = await asyncio.wait_for(call.read(), timeout=5)
    call.cancel()

    # Assert
    assert first.status == health_pb2.HealthCheckResponse.SERVING
    assert second.status == health_pb2.HealthCheckResponse.NOT_SERVING
