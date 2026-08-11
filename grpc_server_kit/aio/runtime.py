"""Runtime helpers for gRPC server lifecycle (async)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from ..constants import DEFAULT_GRACE_PERIOD
from ..signals import SignalManager

if TYPE_CHECKING:
    from types import TracebackType

    from ..protocols import GrpcAsyncServerProtocol

logger = logging.getLogger(__name__)

# After a successful stop() the server's termination waiter resolves promptly;
# this cap only guards against non-conforming server implementations.
_POST_STOP_TERMINATION_TIMEOUT = 1.0


class ServerLifecycleManager:
    """Manages the lifecycle of an async gRPC server.

    Args:
        server: The async gRPC server to manage.
        address: Human-readable bind address used for logging.
        grace_period: Graceful shutdown grace period in seconds. ``None`` maps
            to gRPC's own semantics: all in-flight RPCs are aborted immediately.
        signal_manager: Optional custom signal manager.
    """

    def __init__(
        self,
        server: GrpcAsyncServerProtocol,
        address: str,
        grace_period: float | None = DEFAULT_GRACE_PERIOD,
        signal_manager: SignalManager | None = None,
    ) -> None:
        if grace_period is not None and grace_period < 0:
            raise ValueError(f"grace_period must be non-negative, got {grace_period}")

        self._server = server
        self._address = address
        self._grace_period = grace_period
        self._signal_manager = signal_manager or SignalManager()
        self._stop_event = asyncio.Event()
        self._stop_reason = "terminated"
        self._handlers_set_up = False

    async def __aenter__(self) -> GrpcAsyncServerProtocol:
        """Async context manager entry: start the server."""
        await self._server.start()
        logger.info("gRPC server started", extra={"address": self._address})
        return self._server

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit: stop the server."""
        await self.stop()

    def request_shutdown(self, reason: str = "requested") -> None:
        """Request a graceful shutdown, honored by the running or the next :meth:`run` loop.

        Idempotent: repeated requests are ignored and the FIRST reason is kept,
        since that is what actually triggered the shutdown. A request made
        before :meth:`run` starts is remembered, so a signal racing startup is
        never lost.

        Safe to call from the event loop (e.g. from a handler or a background
        task); from other threads use ``loop.call_soon_threadsafe``.
        """
        if self._stop_event.is_set():
            logger.debug("Shutdown already requested, ignoring", extra={"reason": reason})
            return
        self._stop_reason = reason
        self._stop_event.set()

    async def run(self, setup_signals: bool = True) -> None:
        """Run the server until termination, shutdown request, or signal."""
        self._handlers_set_up = setup_signals
        if not self._stop_event.is_set():
            # Fresh run: forget why the PREVIOUS run stopped. When a shutdown is
            # already pending for this run, its reason is kept instead.
            self._stop_reason = "terminated"

        # Install signal handlers BEFORE starting the server so a setup failure
        # can never leave a started server running with no way to stop it.
        if setup_signals:
            self._signal_manager.setup(self._on_signal)

        try:
            await self._server.start()
        except BaseException:
            if setup_signals:
                await self._signal_manager.reset_async()
            raise
        logger.info("gRPC server started", extra={"address": self._address})

        wait_task: asyncio.Task[bool] = asyncio.create_task(
            self._server.wait_for_termination(), name=f"grpc-server-wait:{self._address}"
        )
        stop_task = asyncio.create_task(self._stop_event.wait(), name=f"grpc-server-stop-event:{self._address}")

        try:
            done, _ = await asyncio.wait(
                [wait_task, stop_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in done:
                t.result()
        except asyncio.CancelledError:
            self._stop_reason = "cancelled"
            raise
        except KeyboardInterrupt:
            self._stop_reason = "keyboard_interrupt"
            raise
        finally:
            stop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_task

            # Stop the server BEFORE touching wait_task: cancelling a task that
            # awaits the server's shared termination future would poison that
            # future and break stop() itself. After a successful stop the
            # termination waiter completes on its own.
            try:
                await self.stop()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                    await asyncio.wait_for(wait_task, timeout=_POST_STOP_TERMINATION_TIMEOUT)
            finally:
                if not wait_task.done():
                    wait_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await wait_task

                # Re-arm AFTER this run has fully stopped (never at the start of
                # the next one): a shutdown requested between runs must be
                # honored by the next run, not silently discarded. The stop
                # reason survives for post-run introspection.
                self._stop_event = asyncio.Event()

    async def stop(self) -> None:
        """Stop the server gracefully, then restore signal handlers.

        Signal handlers stay installed through the whole drain so a repeated
        SIGINT/SIGTERM during the grace period is absorbed (logged and ignored)
        instead of killing the process mid-drain.
        """
        logger.info("Shutting down gRPC server...", extra={"reason": self._stop_reason})

        # Use a task and shield to ensure stop() completes even if this task is cancelled
        stop_task: asyncio.Task[None] = asyncio.create_task(self._server.stop(grace=self._grace_period))
        try:
            await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            # If the waiter is cancelled, re-await the task so shutdown still
            # completes, then let the cancellation propagate.
            await stop_task
            raise
        finally:
            if self._handlers_set_up:
                self._handlers_set_up = False
                await self._signal_manager.reset_async()

        logger.info("gRPC server stopped")

    async def _on_signal(self, sig_name: str) -> None:
        """Signal handler."""
        self.request_shutdown(f"signal_{sig_name}")


async def run_async_grpc_server(
    server: GrpcAsyncServerProtocol,
    *,
    address: str,
    grace_period: float | None = DEFAULT_GRACE_PERIOD,
    setup_signals: bool = True,
    signal_manager: SignalManager | None = None,
) -> None:
    """Start gRPC server and handle graceful shutdown lifecycle.

    Uses ServerLifecycleManager to orchestrate the server runtime.

    Args:
        server: The async gRPC server to run.
        address: Human-readable bind address used for logging.
        grace_period: Graceful shutdown grace period in seconds. ``None`` maps
            to gRPC's own semantics: all in-flight RPCs are aborted immediately.
        setup_signals: Whether to install SIGINT/SIGTERM handlers.
        signal_manager: Optional custom signal manager (a fresh one is created if omitted).
    """
    manager = ServerLifecycleManager(
        server=server,
        address=address,
        grace_period=grace_period,
        signal_manager=signal_manager,
    )
    await manager.run(setup_signals=setup_signals)
