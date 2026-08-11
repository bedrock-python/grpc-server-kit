"""Sentry interceptor."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import grpc

from grpc_server_kit.interceptors.constants import (
    METHOD_KEY,
    REQUEST_ID_KEY,
    SENTRY_GRPC_METHOD,
    SENTRY_REQUEST_ID,
    SKIPPED_HEALTH_METHODS,
    X_REQUEST_ID,
)

from .base import AsyncServerInterceptor, RpcCall
from .metadata import get_metadata_dict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Collection

    from .protocols import ErrorReporterProtocol

__all__ = ["AsyncSentryInterceptor"]


class AsyncSentryInterceptor(AsyncServerInterceptor):
    """Interceptor that adds error tracking and breadcrumbs.

    Each RPC runs inside the reporter's isolated scope (``reporter.isolate()``),
    so tags and breadcrumbs of concurrent RPCs never contaminate each other's
    reports. Health-check methods are skipped by default.

    Place this interceptor INSIDE the exception handler in the chain (later in
    the interceptor list) so it observes raw handler exceptions before they are
    mapped into ``grpc.aio.AbortError``. Deliberate aborts and gRPC errors are
    re-raised without capture.
    """

    def __init__(
        self,
        sentry: ErrorReporterProtocol | None = None,
        *,
        capture_filter: Callable[[Exception], bool] | None = None,
        skip_methods: Collection[str] = SKIPPED_HEALTH_METHODS,
    ) -> None:
        """Initialize interceptor.

        Args:
            sentry: An ``ErrorReporterProtocol`` implementation or None (no-op).
            capture_filter: Predicate deciding whether an unexpected exception
                is captured. None captures everything. The dishka bundle wires
                a filter capturing only server-class errors (per the same
                mapping the exception handler uses), so client-mapped errors
                like ``ValueError`` do not spam the error tracker.
            skip_methods: Full RPC method names to exclude from error tracking
                (defaults to the gRPC health-check methods).
        """
        super().__init__(skip_methods=skip_methods)
        self._sentry = sentry
        self._capture_filter = capture_filter

    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        """Tag the isolated scope, add a breadcrumb, and capture unexpected exceptions."""
        if self._sentry is None:
            yield
            return

        metadata = get_metadata_dict(call.context)
        request_id = str(metadata.get(X_REQUEST_ID, ""))

        with self._sentry.isolate():
            self._sentry.set_tags(
                **{
                    SENTRY_GRPC_METHOD: call.method_name,
                    SENTRY_REQUEST_ID: request_id,
                }
            )
            self._sentry.add_breadcrumb(
                message=f"gRPC call: {call.method_name}",
                category="grpc",
                level="info",
                data={METHOD_KEY: call.method_name, REQUEST_ID_KEY: request_id},
            )

            try:
                yield
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except grpc.aio.AbortError:
                # Deliberate abort with an explicit status — not an unexpected error.
                raise
            except grpc.RpcError:
                # Re-raised gRPC errors are handled by the exception handler interceptor.
                raise
            except Exception as exc:
                if self._capture_filter is None or self._capture_filter(exc):
                    self._sentry.capture_exception(exc)
                raise
