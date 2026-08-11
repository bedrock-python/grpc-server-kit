"""End-to-end tests for the GrpcApp facade."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from grpc_server_kit import GrpcApp

from .conftest import SERVICE, make_echo_generic_handler

pytestmark = pytest.mark.integration


@dataclass
class RunningApp:
    """A GrpcApp serving in the background via ``run()``, with an open channel to it."""

    app: GrpcApp
    run_task: asyncio.Task[None]
    channel: grpc.aio.Channel


@pytest.fixture
async def running_app() -> AsyncIterator[RunningApp]:
    """A GrpcApp with the echo handler and health enabled, running via app.run()."""
    app = GrpcApp(host="127.0.0.1", port=0)
    app.register(lambda raw: raw.add_generic_rpc_handlers((make_echo_generic_handler(),)))
    app.enable_health()
    app.build()

    run_task = asyncio.create_task(app.run(setup_signals=False))
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{app.bound_port}") as channel:
            await asyncio.wait_for(channel.channel_ready(), timeout=5)
            yield RunningApp(app=app, run_task=run_task, channel=channel)
    finally:
        # request_shutdown() is idempotent, so teardown never has to check
        # whether the test already stopped the app (such a check would be racy).
        app.request_shutdown("test-teardown")
        await asyncio.wait_for(run_task, timeout=10)


async def test__grpc_app__run__serves_registered_handler(running_app: RunningApp) -> None:
    # Act
    echo = running_app.channel.unary_unary(f"/{SERVICE}/Echo")
    response = await echo(b"ping")

    # Assert
    assert response == b"ping"


async def test__grpc_app__health_enabled__check_reports_serving(running_app: RunningApp) -> None:
    # Act
    health = health_pb2_grpc.HealthStub(running_app.channel)
    response = await health.Check(health_pb2.HealthCheckRequest(service=""))

    # Assert
    assert response.status == health_pb2.HealthCheckResponse.SERVING


async def test__grpc_app__shutdown_requested__stops_gracefully(running_app: RunningApp) -> None:
    # Act
    running_app.app.request_shutdown("test-done")

    # Assert
    await asyncio.wait_for(running_app.run_task, timeout=10)


async def test__grpc_app__used_as_context_manager__serves_within_block() -> None:
    # Arrange
    app = GrpcApp(host="127.0.0.1", port=0)
    app.register(lambda raw: raw.add_generic_rpc_handlers((make_echo_generic_handler(),)))

    # Act
    async with app, grpc.aio.insecure_channel(f"127.0.0.1:{app.bound_port}") as channel:
        echo = channel.unary_unary(f"/{SERVICE}/Echo")
        response = await echo(b"ping")

    # Assert
    assert response == b"ping"


async def test__grpc_app__run_called_while_already_running__raises_runtime_error() -> None:
    # Arrange
    app = GrpcApp(host="127.0.0.1", port=0)
    app.register(lambda raw: raw.add_generic_rpc_handlers((make_echo_generic_handler(),)))
    app.build()
    run_task = asyncio.create_task(app.run(setup_signals=False))
    try:
        await asyncio.sleep(0.05)

        # Act & Assert
        with pytest.raises(RuntimeError, match="already running"):
            await app.run(setup_signals=False)
    finally:
        app.request_shutdown()
        await asyncio.wait_for(run_task, timeout=10)
