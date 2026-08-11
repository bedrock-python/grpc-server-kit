"""Tests for async gRPC server lifecycle management."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from grpc_server_kit.aio import ServerLifecycleManager, run_async_grpc_server

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_server() -> AsyncMock:
    return AsyncMock()


async def test__run_async_grpc_server__server_terminates__starts_and_stops_with_grace_period(
    mock_server: AsyncMock,
) -> None:
    # Arrange
    start_event = asyncio.Event()
    mock_server.start.side_effect = start_event.set
    term_event = asyncio.Event()
    mock_server.wait_for_termination.side_effect = term_event.wait
    mock_server.stop = AsyncMock()

    # Act
    task = asyncio.create_task(
        run_async_grpc_server(mock_server, address="localhost:50051", grace_period=5.0, setup_signals=False)
    )
    await asyncio.wait_for(start_event.wait(), timeout=1.0)
    term_event.set()
    await task

    # Assert
    mock_server.start.assert_called_once()
    mock_server.stop.assert_called_once_with(grace=5.0)


async def test__run_async_grpc_server__setup_signals_true__configures_signal_manager(mock_server: AsyncMock) -> None:
    # Arrange
    mock_server.wait_for_termination.return_value = True
    mock_server.stop = AsyncMock()
    mock_signal_manager = MagicMock()
    mock_signal_manager.reset_async = AsyncMock()

    # Act
    await run_async_grpc_server(
        mock_server,
        address="localhost:50051",
        setup_signals=True,
        signal_manager=mock_signal_manager,
    )

    # Assert
    mock_signal_manager.setup.assert_called_once()
    mock_signal_manager.reset_async.assert_called_once()


async def test__server_lifecycle_manager__signal_received__stops_server(mock_server: AsyncMock) -> None:
    # Arrange
    term_event = asyncio.Event()
    mock_server.wait_for_termination.side_effect = term_event.wait

    async def mock_stop(grace: float | None) -> None:
        # A real server resolves wait_for_termination once stopped.
        term_event.set()

    mock_server.stop = AsyncMock(side_effect=mock_stop)
    manager = ServerLifecycleManager(server=mock_server, address="localhost:50051", grace_period=5.0)

    # Act
    task = asyncio.create_task(manager.run(setup_signals=False))
    await asyncio.sleep(0.1)
    await manager._on_signal("SIGINT")
    await task

    # Assert
    mock_server.stop.assert_called_once()


def test__server_lifecycle_manager_init__negative_grace_period__raises(mock_server: AsyncMock) -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="grace_period must be non-negative"):
        ServerLifecycleManager(server=mock_server, address="localhost:50051", grace_period=-1.0)


async def test__run_async_grpc_server__cancelled__stops_server_and_reraises(mock_server: AsyncMock) -> None:
    # Arrange
    mock_server.wait_for_termination.side_effect = asyncio.CancelledError
    mock_server.stop = AsyncMock()

    # Act & Assert
    with pytest.raises(asyncio.CancelledError):
        await run_async_grpc_server(mock_server, address="localhost:50051", grace_period=5.0, setup_signals=False)

    mock_server.stop.assert_called_once()


async def test__server_lifecycle_manager__used_as_context_manager__starts_and_stops_server(
    mock_server: AsyncMock,
) -> None:
    # Arrange
    mock_server.stop = AsyncMock()
    manager = ServerLifecycleManager(server=mock_server, address="localhost:50051", grace_period=2.0)

    # Act
    async with manager as server:
        assert server == mock_server
        mock_server.start.assert_called_once()

    # Assert
    mock_server.stop.assert_called_once_with(grace=2.0)


async def test__server_lifecycle_manager_run__setup_signals_true__configures_signal_manager(
    mock_server: AsyncMock,
) -> None:
    # Arrange
    mock_server.wait_for_termination.return_value = True
    mock_signal_manager = MagicMock()
    mock_signal_manager.reset_async = AsyncMock()
    manager = ServerLifecycleManager(server=mock_server, address="localhost:50051", signal_manager=mock_signal_manager)

    # Act
    await manager.run(setup_signals=True)

    # Assert
    mock_signal_manager.setup.assert_called_once()
    mock_signal_manager.reset_async.assert_called_once()


@pytest.mark.parametrize(
    ("exception", "stop_reason"),
    [
        (KeyboardInterrupt, "keyboard_interrupt"),
        (asyncio.CancelledError, "cancelled"),
    ],
)
async def test__server_lifecycle_manager_run__interrupted_while_waiting__sets_reason_and_stops(
    mock_server: AsyncMock,
    exception: type[BaseException],
    stop_reason: str,
) -> None:
    # Arrange
    mock_server.start = AsyncMock()
    mock_server.stop = AsyncMock()
    manager = ServerLifecycleManager(server=mock_server, address="localhost:50051")

    # Act & Assert
    with patch("asyncio.wait", side_effect=exception), pytest.raises(exception):
        await manager.run(setup_signals=False)

    assert manager._stop_reason == stop_reason
    mock_server.stop.assert_called_once()


async def test__server_lifecycle_manager_run__called_again_after_shutdown__serves_again(mock_server: AsyncMock) -> None:
    # A second run() must serve again instead of exiting immediately on the
    # previous run's stop event.

    # Arrange
    term_event = asyncio.Event()
    mock_server.wait_for_termination.side_effect = term_event.wait

    async def mock_stop(grace: float | None) -> None:
        term_event.set()

    mock_server.stop = AsyncMock(side_effect=mock_stop)
    manager = ServerLifecycleManager(server=mock_server, address="localhost:50051")

    # Act
    first_run = asyncio.create_task(manager.run(setup_signals=False))
    await asyncio.sleep(0.05)
    manager.request_shutdown("first")
    await asyncio.wait_for(first_run, timeout=5)

    term_event.clear()
    second_run = asyncio.create_task(manager.run(setup_signals=False))
    await asyncio.sleep(0.05)

    # Assert
    # Still serving: the previous run's stop event was re-armed instead of
    # short-circuiting this run.
    assert not second_run.done()

    manager.request_shutdown("second")
    await asyncio.wait_for(second_run, timeout=5)
    assert mock_server.stop.call_count == 2


async def test__server_lifecycle_manager__shutdown_requested_between_runs__honored_by_next_run(
    mock_server: AsyncMock,
) -> None:
    # Mirror image of the re-arm test above: a request arriving while no run is
    # active must be honored by the next run, not discarded by the re-arm.

    # Arrange
    term_event = asyncio.Event()
    mock_server.wait_for_termination.side_effect = term_event.wait

    async def mock_stop(grace: float | None) -> None:
        term_event.set()

    mock_server.stop = AsyncMock(side_effect=mock_stop)
    manager = ServerLifecycleManager(server=mock_server, address="localhost:50051")
    manager.request_shutdown("early")

    # Act
    await asyncio.wait_for(manager.run(setup_signals=False), timeout=5)

    # Assert
    mock_server.stop.assert_called_once()


def test__server_lifecycle_manager__repeated_shutdown_requests__keeps_first_reason(mock_server: AsyncMock) -> None:
    # The first reason is what actually triggered the shutdown; later ones are noise.

    # Arrange
    manager = ServerLifecycleManager(server=mock_server, address="localhost:50051")

    # Act
    manager.request_shutdown("first")
    manager.request_shutdown("second")

    # Assert
    assert manager._stop_reason == "first"


async def test__server_lifecycle_manager_run__signal_setup_fails__never_starts_server(mock_server: AsyncMock) -> None:
    # Signals are installed BEFORE start; a failing setup must not leave a
    # started server running with no way to stop it.

    # Arrange
    mock_signal_manager = MagicMock()
    mock_signal_manager.setup.side_effect = ValueError("main thread only")
    mock_signal_manager.reset_async = AsyncMock()
    manager = ServerLifecycleManager(server=mock_server, address="localhost:50051", signal_manager=mock_signal_manager)

    # Act & Assert
    with pytest.raises(ValueError, match="main thread only"):
        await manager.run(setup_signals=True)

    mock_server.start.assert_not_called()


async def test__server_lifecycle_manager_stop__cancelled_while_waiting__reawaits_and_reraises(
    mock_server: AsyncMock,
) -> None:
    # stop() re-awaits the shielded stop_task on CancelledError.

    # Arrange
    stop_event = asyncio.Event()

    async def mock_stop(grace: float) -> None:
        if not stop_event.is_set():
            stop_event.set()
            raise asyncio.CancelledError()
        return

    mock_server.stop.side_effect = mock_stop
    manager = ServerLifecycleManager(server=mock_server, address="localhost:50051")

    # Act
    stop_task = asyncio.create_task(manager.stop())
    await stop_event.wait()
    stop_task.cancel()

    # Act & Assert
    with pytest.raises(asyncio.CancelledError):
        await stop_task

    assert mock_server.stop.call_count == 1
