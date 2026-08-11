"""Metrics interceptor for gRPC (backend-neutral recorder seam)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import grpc

from grpc_server_kit.interceptors.constants import SKIPPED_HEALTH_METHODS
from grpc_server_kit.interceptors.utils import resolve_status_code

from .base import AsyncServerInterceptor, RpcCall

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection

    from .protocols import GrpcServerMetricsProtocol

__all__ = ["AsyncMetricsInterceptor"]

logger = logging.getLogger(__name__)


class AsyncMetricsInterceptor(AsyncServerInterceptor):
    """Interceptor that records gRPC request metrics via a metrics recorder.

    Recording is delegated to any object satisfying ``GrpcServerMetricsProtocol``,
    so the kit never imports a metrics backend directly. Skips gRPC health check
    methods by default, and metric-recording failures never fail the request.
    For streaming RPCs the recorded duration covers the whole stream.
    """

    def __init__(
        self,
        metrics: GrpcServerMetricsProtocol | None = None,
        service_name: str = "unknown",
        *,
        skip_methods: Collection[str] = SKIPPED_HEALTH_METHODS,
    ) -> None:
        """Initialize interceptor with the metrics recorder and service name.

        Args:
            metrics: A ``GrpcServerMetricsProtocol`` implementation or None.
            service_name: The name of the service to include in labels.
            skip_methods: Full RPC method names to exclude from metrics
                (defaults to the gRPC health-check methods).

        Raises:
            ValueError: If ``service_name`` is empty.
        """
        super().__init__(skip_methods=skip_methods)
        if not service_name:
            raise ValueError("service_name cannot be empty")
        self._service_name = service_name
        self._metrics = metrics

    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        """Record request count and duration around the RPC."""
        if self._metrics is None:
            yield
            return

        start_time = time.perf_counter()
        status = "success"
        grpc_code = grpc.StatusCode.OK

        try:
            yield
        except BaseException as exc:
            status = "error"
            grpc_code = self._classify_error(exc, call)
            raise
        else:
            grpc_code = resolve_status_code(None, call.context, default=grpc.StatusCode.OK)
        finally:
            duration = time.perf_counter() - start_time
            try:
                self._metrics.record_request(
                    service=self._service_name,
                    method=call.method_name,
                    status=status,
                    grpc_code=grpc_code.name,
                    duration=duration,
                )
            except Exception:
                # Metrics errors should not fail the request
                logger.exception("Failed to record gRPC server metrics")

    def _classify_error(self, exc: BaseException, call: RpcCall) -> grpc.StatusCode:
        """Resolve the status code to record for a failed RPC."""
        if isinstance(exc, asyncio.CancelledError | GeneratorExit):
            # Task cancellation / client gone mid-stream.
            return grpc.StatusCode.CANCELLED
        if isinstance(exc, grpc.aio.AbortError):
            # Deliberate abort — the context carries the real status.
            return resolve_status_code(None, call.context)
        code = resolve_status_code(exc if isinstance(exc, grpc.RpcError) else None, call.context)
        if code in (grpc.StatusCode.OK, grpc.StatusCode.UNKNOWN) and not isinstance(exc, grpc.RpcError):
            # An exception with no explicit status is an internal error.
            return grpc.StatusCode.INTERNAL
        return code
