"""Tests for signal handling used for graceful gRPC server shutdown."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from grpc_server_kit.aio import reset_signal_handlers
from grpc_server_kit.signals import (
    SignalManager,
    reset_signal_handlers_async,
    setup_signal_handlers,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def cleanup_signals() -> Generator[None, None, None]:
    reset_signal_handlers()
    yield
    reset_signal_handlers()


@pytest.fixture
def manager() -> SignalManager:
    return SignalManager()


@pytest.fixture
def running_loop() -> MagicMock:
    """Event loop mock that is running and executes call_soon_threadsafe callbacks immediately."""
    loop = MagicMock()
    loop.is_running.return_value = True
    loop.call_soon_threadsafe.side_effect = lambda f: f()
    return loop


@pytest.fixture
def mock_default_manager() -> Generator[MagicMock, None, None]:
    with patch("grpc_server_kit.signals._default_manager") as mock_manager:
        mock_manager.reset_async = AsyncMock()
        yield mock_manager


def test__signal_manager__setup_async_callback_on_linux_with_loop__registers_handlers_for_both_signals(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # Act
    with patch("sys.platform", "linux"), patch("asyncio.get_running_loop", return_value=running_loop):
        manager.setup(AsyncMock())

    # Assert
    assert running_loop.add_signal_handler.call_count == 2


async def test__signal_manager__loop_handler_triggered_with_async_callback__creates_task_for_coroutine(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # Arrange
    mock_callback = AsyncMock()

    # Act
    with patch("sys.platform", "linux"), patch("asyncio.get_running_loop", return_value=running_loop):
        manager.setup(mock_callback)
        handler = running_loop.add_signal_handler.call_args_list[0][0][1]
        handler()

    # Assert
    running_loop.create_task.assert_called_once()
    # Await the coroutine created by AsyncMock to avoid RuntimeWarning
    await running_loop.create_task.call_args[0][0]


def test__signal_manager__reset_after_loop_handlers_setup__removes_handlers_for_both_signals(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # Arrange
    with patch("sys.platform", "linux"), patch("asyncio.get_running_loop", return_value=running_loop):
        manager.setup(AsyncMock())

    # Act
    manager.reset()

    # Assert
    assert running_loop.remove_signal_handler.call_count == 2


def test__signal_manager__setup_called_twice__does_not_double_register_handlers(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # Act
    with patch("sys.platform", "linux"), patch("asyncio.get_running_loop", return_value=running_loop):
        manager.setup(MagicMock())
        manager.setup(MagicMock())

    # Assert
    assert running_loop.add_signal_handler.call_count == 2


def test__signal_manager__reset_with_pending_background_task__cancels_it(manager: SignalManager) -> None:
    # Arrange
    mock_task = MagicMock()
    mock_task.done.return_value = False
    manager._background_tasks.add(mock_task)
    manager._handlers_set_up = True

    # Act
    manager.reset()

    # Assert
    mock_task.cancel.assert_called_once()


def test__signal_manager__setup_on_win32__installs_standard_handler_for_sigint(manager: SignalManager) -> None:
    # Act
    with (
        patch("sys.platform", "win32"),
        patch("signal.signal") as mock_signal,
        patch("asyncio.get_running_loop", side_effect=RuntimeError),
    ):
        manager.setup(MagicMock())

    # Assert
    assert mock_signal.call_count >= 1


def test__signal_manager__win32_handler_triggered__schedules_callback_via_loop(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # Arrange
    mock_callback = MagicMock()

    # Act
    with (
        patch("sys.platform", "win32"),
        patch("signal.signal") as mock_signal,
        patch("asyncio.get_running_loop", return_value=running_loop),
    ):
        manager.setup(mock_callback)
        handler = mock_signal.call_args_list[0][0][1]
        handler(signal.SIGINT, None)

    # Assert
    running_loop.call_soon_threadsafe.assert_called_once()
    mock_callback.assert_called_once()


def test__signal_manager__loop_handler_triggered_with_sync_callback__calls_it_via_loop(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # Arrange
    mock_callback = MagicMock()

    # Act
    with patch("sys.platform", "linux"), patch("asyncio.get_running_loop", return_value=running_loop):
        manager.setup(mock_callback)
        handler = running_loop.add_signal_handler.call_args_list[0][0][1]
        handler()

    # Assert
    mock_callback.assert_called_once()


@pytest.mark.parametrize(("platform", "expected_handler_count"), [("linux", 2), ("win32", 1)])
def test__signal_manager__no_running_loop__falls_back_to_standard_handlers(
    manager: SignalManager,
    platform: str,
    expected_handler_count: int,
) -> None:
    # Act
    with (
        patch("sys.platform", platform),
        patch("asyncio.get_running_loop", side_effect=RuntimeError),
        patch("signal.signal") as mock_signal,
    ):
        manager.setup(MagicMock())

    # Assert
    assert mock_signal.call_count == expected_handler_count


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test__signal_manager__standard_handler_triggered_without_loop__calls_callback(
    manager: SignalManager,
    platform: str,
) -> None:
    # Arrange
    mock_callback = MagicMock()

    # Act
    with (
        patch("sys.platform", platform),
        patch("asyncio.get_running_loop", side_effect=RuntimeError),
        patch("signal.signal") as mock_signal,
    ):
        manager.setup(mock_callback)
        handler = mock_signal.call_args_list[0][0][1]
        handler(signal.SIGINT, None)

    # Assert
    mock_callback.assert_called_once()


def test__signal_manager__async_callback_with_no_running_loop__does_not_raise(manager: SignalManager) -> None:
    # Arrange
    async def async_callback(sig: str) -> None:
        pass

    # Act
    with (
        patch("sys.platform", "linux"),
        patch("asyncio.get_running_loop", side_effect=RuntimeError),
        patch("signal.signal") as mock_signal,
    ):
        manager.setup(async_callback)
        handler = mock_signal.call_args_list[0][0][1]
        # Should not raise even if loop is missing for async callback
        handler(signal.SIGINT, None)


def test__signal_manager__second_signal_while_shutdown_in_progress__ignored(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # Arrange
    mock_callback = MagicMock()

    # Act
    with patch("sys.platform", "linux"), patch("asyncio.get_running_loop", return_value=running_loop):
        manager.setup(mock_callback)
        handler = running_loop.add_signal_handler.call_args_list[0][0][1]
        handler()
        first_call_count = mock_callback.call_count
        # Second signal is ignored (shutdown already in progress)
        handler()

    # Assert
    assert first_call_count == 1
    assert mock_callback.call_count == 1


def test__signal_manager__async_callback_raises__logs_exception_instead_of_propagating(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # Arrange
    async def faulty_callback(sig: str) -> None:
        raise ValueError("test error")

    # Act
    with (
        patch("sys.platform", "linux"),
        patch("asyncio.get_running_loop", return_value=running_loop),
        patch("grpc_server_kit.signals.logger") as mock_logger,
    ):
        manager.setup(faulty_callback)
        handler = running_loop.add_signal_handler.call_args_list[0][0][1]
        handler()

        # The mock loop never runs the scheduled coroutine; close it to avoid a
        # "coroutine was never awaited" RuntimeWarning at GC time.
        running_loop.create_task.call_args[0][0].close()

        task = running_loop.create_task.return_value
        done_cb = task.add_done_callback.call_args_list[0][0][0]
        task.result.side_effect = ValueError("test error")
        done_cb(task)

    # Assert
    mock_logger.exception.assert_called_once_with("Error in async shutdown callback", extra={"signal": "SIGINT"})


def test__signal_manager__add_signal_handler_unsupported__falls_back_to_standard_handlers(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # Arrange
    running_loop.add_signal_handler.side_effect = ValueError("not supported")

    # Act
    with (
        patch("sys.platform", "linux"),
        patch("asyncio.get_running_loop", return_value=running_loop),
        patch("signal.signal") as mock_signal,
    ):
        manager.setup(MagicMock())

    # Assert
    assert mock_signal.call_count >= 1


async def test__signal_manager__reset_async_with_pending_task__removes_handlers_and_awaits_task(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # Arrange
    future: asyncio.Future[None] = asyncio.Future()
    future.set_result(None)
    manager._background_tasks.add(future)
    manager._handlers_set_up = True
    manager._used_loop_handlers = True
    manager._loop = running_loop

    # Act
    with patch("sys.platform", "linux"):
        await manager.reset_async()

    # Assert
    assert running_loop.remove_signal_handler.call_count == 2
    assert future.done()
    assert len(manager._background_tasks) == 0


def test__signal_manager__reset__restores_previous_std_handler_not_default(manager: SignalManager) -> None:
    # The handlers saved at setup time must be restored — NOT SIG_DFL.
    # Arrange
    sentinel_previous = MagicMock(name="previous_handler")

    # Act
    with (
        patch("sys.platform", "win32"),
        patch("signal.signal") as mock_signal,
        patch("asyncio.get_running_loop", side_effect=RuntimeError),
    ):
        mock_signal.return_value = sentinel_previous
        manager.setup(MagicMock())
        mock_signal.reset_mock()
        manager.reset()
        restored = {call.args[0]: call.args[1] for call in mock_signal.call_args_list}

    # Assert
    assert restored == {signal.SIGINT: sentinel_previous}


def test__signal_manager__signal_signal_unavailable__setup_warns_instead_of_raising(manager: SignalManager) -> None:
    # signal.signal raises ValueError outside the main thread: setup must warn,
    # not crash the caller.
    # Act
    with (
        patch("sys.platform", "win32"),
        patch("signal.signal", side_effect=ValueError("main thread only")),
        patch("asyncio.get_running_loop", side_effect=RuntimeError),
        patch("grpc_server_kit.signals.logger") as mock_logger,
    ):
        manager.setup(MagicMock())

    # Assert
    mock_logger.warning.assert_called_once()

    # No handlers were saved, so reset must be a clean no-op.
    manager.reset()
    assert manager._handlers_set_up is False


def test__signal_manager__setup_after_reset__handles_next_signal(
    manager: SignalManager,
    running_loop: MagicMock,
) -> None:
    # A reused manager must handle the NEXT run's first signal instead of
    # dropping it with "shutdown already in progress".
    # Arrange
    first_callback = MagicMock()
    second_callback = MagicMock()

    # Act
    with patch("sys.platform", "linux"), patch("asyncio.get_running_loop", return_value=running_loop):
        manager.setup(first_callback)
        first_handler = running_loop.add_signal_handler.call_args_list[0][0][1]
        first_handler()
        manager.reset()
        shutdown_flag_after_reset = manager._shutdown_in_progress
        manager.setup(second_callback)
        second_handler = running_loop.add_signal_handler.call_args_list[-1][0][1]
        second_handler()

    # Assert
    assert first_callback.call_count == 1
    assert shutdown_flag_after_reset is False
    assert second_callback.call_count == 1


def test__setup_signal_handlers__global_wrapper__delegates_to_default_manager(
    mock_default_manager: MagicMock,
) -> None:
    # Arrange
    mock_callback = MagicMock()

    # Act
    setup_signal_handlers(mock_callback)

    # Assert
    mock_default_manager.setup.assert_called_once_with(mock_callback)


async def test__reset_signal_handlers_async__global_wrapper__delegates_to_default_manager(
    mock_default_manager: MagicMock,
) -> None:
    # Act
    await reset_signal_handlers_async()

    # Assert
    mock_default_manager.reset_async.assert_called_once()


def test__signal_manager__reset_when_never_set_up__is_noop(manager: SignalManager) -> None:
    # Act
    manager.reset()

    # Assert
    assert manager._handlers_set_up is False
