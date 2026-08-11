"""Signal handling for graceful gRPC server shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
import signal
import sys
import types
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

logger = logging.getLogger(__name__)

_HANDLED_SIGNALS = (signal.SIGINT, signal.SIGTERM)

# Handler value used by the standard `signal` module (SIG_DFL/SIG_IGN/callable/None).
type _StdSignalHandler = Callable[[int, types.FrameType | None], Any] | int | signal.Handlers | None


class SignalManager:
    """Manager for signal handlers to avoid global state.

    A manager can be reused across sequential runs: :meth:`reset` /
    :meth:`reset_async` restore the signal handlers that were installed before
    :meth:`setup` (not the OS defaults) and re-arm the manager for the next
    setup/signal cycle.
    """

    def __init__(self) -> None:
        self._handlers_set_up = False
        self._used_loop_handlers = False
        self._saved_std_handlers: dict[int, _StdSignalHandler] = {}
        self._background_tasks: set[asyncio.Future[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown_in_progress = False

    def setup(
        self,
        shutdown_callback: Callable[[str], None] | Callable[[str], Awaitable[None]],
    ) -> None:
        """Setup signal handlers for graceful shutdown.

        Supports both synchronous and asynchronous callbacks. Degrades to a
        warning (instead of crashing) when handlers cannot be installed — e.g.
        when called from a non-main thread, where Python allows no signal
        handling at all.
        """
        if self._handlers_set_up:
            return

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        is_async = inspect.iscoroutinefunction(shutdown_callback)

        def handle_signal(sig_name: str) -> None:
            if self._shutdown_in_progress:
                logger.debug("Shutdown already in progress, ignoring signal", extra={"signal": sig_name})
                return

            self._shutdown_in_progress = True
            logger.info("Received signal, starting shutdown", extra={"signal": sig_name})

            if is_async:
                # Always try to schedule on the captured loop if it exists
                if self._loop and self._loop.is_running():

                    def _schedule_callback() -> None:
                        coro = shutdown_callback(sig_name)
                        if self._loop is None:
                            return

                        task: asyncio.Task[None] = self._loop.create_task(cast("Coroutine[Any, Any, None]", coro))
                        self._background_tasks.add(task)

                        def _done_cb(t: asyncio.Task[None]) -> None:
                            self._background_tasks.discard(t)
                            try:
                                t.result()
                            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                                pass
                            except Exception:
                                logger.exception("Error in async shutdown callback", extra={"signal": sig_name})

                        task.add_done_callback(_done_cb)

                    self._loop.call_soon_threadsafe(_schedule_callback)
                else:
                    # No running loop, fallback to sync if possible (unlikely for async callback)
                    logger.warning("No running event loop to schedule async shutdown callback")
            else:
                # shutdown_callback is Callable[[str], None]
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(lambda: shutdown_callback(sig_name))
                else:
                    shutdown_callback(sig_name)

        if self._setup_loop_handlers(handle_signal):
            self._used_loop_handlers = True
        else:
            self._setup_standard_handlers(handle_signal)

        self._handlers_set_up = True

    def _setup_loop_handlers(self, handle_signal: Callable[[str], None]) -> bool:
        """Setup signal handlers using asyncio loop.

        Returns:
            True if handlers were successfully set up via loop, False otherwise.
        """
        if sys.platform != "win32" and self._loop:
            try:
                for sig in _HANDLED_SIGNALS:
                    self._loop.add_signal_handler(sig, functools.partial(handle_signal, sig.name))
            except (RuntimeError, ValueError):
                return False
            else:
                return True

        return False

    def _setup_standard_handlers(self, handle_signal: Callable[[str], None]) -> None:
        """Setup handlers via the standard signal module, saving the previous ones.

        ``signal.signal`` raises ValueError outside the main thread — degrade to
        a warning instead of crashing the caller after the server has started.
        """

        def _handler(signum: int, _frame: types.FrameType | None) -> None:
            sig_name = f"signal {signum}"
            with contextlib.suppress(ValueError, AttributeError):
                sig_name = signal.Signals(signum).name
            handle_signal(sig_name)

        signals = _HANDLED_SIGNALS if sys.platform != "win32" else (signal.SIGINT,)
        for sig in signals:
            try:
                previous = signal.signal(sig, _handler)
            except (ValueError, OSError):
                logger.warning(
                    "Cannot install signal handler (non-main thread?); "
                    "graceful shutdown by signal is disabled for this run",
                    extra={"signal": sig.name},
                )
                return
            self._saved_std_handlers[sig] = previous

    def _restore_handlers(self) -> None:
        """Restore the signal handlers that were in place before setup()."""
        if self._used_loop_handlers and self._loop and self._loop.is_running():
            for sig in _HANDLED_SIGNALS:
                with contextlib.suppress(RuntimeError, ValueError):
                    self._loop.remove_signal_handler(sig)

        # Restore the exact previous handlers (NOT SIG_DFL: Python's default
        # SIGINT handler is default_int_handler, and the host application may
        # have installed its own handlers before us).
        for signum, previous in self._saved_std_handlers.items():
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(signum, previous)

        self._saved_std_handlers.clear()
        self._used_loop_handlers = False
        self._handlers_set_up = False
        self._shutdown_in_progress = False
        self._loop = None

    async def reset_async(self) -> None:
        """Restore previous signal handlers and await pending shutdown callbacks."""
        if not self._handlers_set_up:
            return

        pending = list(self._background_tasks)
        self._restore_handlers()
        if pending:
            # We don't cancel them, we wait for them to finish naturally (shutdown callbacks)
            await asyncio.gather(*pending, return_exceptions=True)
        self._background_tasks.clear()

    def reset(self) -> None:
        """Restore previous signal handlers synchronously.

        Note: This cancels pending background tasks. Use reset_async to wait
        for them instead.
        """
        if not self._handlers_set_up:
            return

        self._restore_handlers()
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        self._background_tasks.clear()


# Default instance for convenience, but allows creating independent managers
_default_manager = SignalManager()


def setup_signal_handlers(
    shutdown_callback: Callable[[str], None] | Callable[[str], Awaitable[None]],
) -> None:
    """Setup signal handlers using the default manager."""
    _default_manager.setup(shutdown_callback)


def reset_signal_handlers() -> None:
    """Reset signal handlers using the default manager.

    Note: This is synchronous and cancels pending tasks.
    """
    _default_manager.reset()


async def reset_signal_handlers_async() -> None:
    """Reset signal handlers asynchronously using the default manager.

    Awaits all pending background tasks.
    """
    await _default_manager.reset_async()
