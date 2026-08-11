"""Tracing interceptor (OpenTelemetry-compatible seam).

Creates one span per RPC with OTel semantic-convention attributes
(``rpc.system``, ``rpc.service``, ``rpc.method``, ``rpc.grpc.status_code``).
Tracing is delegated to any object satisfying ``TracerProtocol`` — the
OpenTelemetry tracer fits structurally.

Note:
    This interceptor is a lightweight seam and does not extract the incoming
    trace context (``traceparent``) from metadata. For full distributed-tracing
    propagation use the official instrumentation via
    :func:`grpc_server_kit.aio.observability.instrument_aio_server` (the
    ``[tracing]`` extra) — the two approaches compose.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import grpc

from grpc_server_kit.interceptors.constants import (
    OTEL_REQUEST_ID,
    OTEL_RPC_GRPC_STATUS_CODE,
    OTEL_RPC_METHOD,
    OTEL_RPC_SERVICE,
    OTEL_RPC_SYSTEM,
    SKIPPED_HEALTH_METHODS,
    X_REQUEST_ID,
)
from grpc_server_kit.interceptors.utils import is_server_error, resolve_status_code

from .base import AsyncServerInterceptor, RpcCall, split_method_name
from .metadata import get_metadata_dict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection

    from .protocols import SpanProtocol, TracerProtocol

__all__ = ["AsyncTracingInterceptor"]


class AsyncTracingInterceptor(AsyncServerInterceptor):
    """Interceptor that adds a tracing span to each gRPC call.

    The span covers the whole RPC (including full response streams), is named
    after the RPC method, and carries OTel semconv attributes. Health-check
    methods are skipped by default. Exceptions are recorded against the span
    for server-side errors — including errors already mapped to an abort by an
    inner exception handler (recovered from ``AbortError.__context__``).
    """

    def __init__(
        self,
        service_name: str,
        tracer: TracerProtocol | None = None,
        *,
        skip_methods: Collection[str] = SKIPPED_HEALTH_METHODS,
    ) -> None:
        """Initialize interceptor with the service name and tracer.

        Args:
            service_name: Fallback service name for span attributes when the RPC
                method name cannot be parsed.
            tracer: A ``TracerProtocol`` implementation or None (no-op).
            skip_methods: Full RPC method names to exclude from tracing
                (defaults to the gRPC health-check methods).

        Raises:
            ValueError: If ``service_name`` is empty.
        """
        super().__init__(skip_methods=skip_methods)
        if not service_name:
            raise ValueError("service_name cannot be empty")
        self._service_name = service_name
        self.tracer = tracer

    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        """Wrap the RPC in a span with semconv attributes."""
        if self.tracer is None:
            yield
            return

        service, method = split_method_name(call.method_name)
        span_name = call.method_name.removeprefix("/") or call.method_name

        with self.tracer.start_as_current_span(span_name) as span:
            span.set_attribute(OTEL_RPC_SYSTEM, "grpc")
            span.set_attribute(OTEL_RPC_SERVICE, service or self._service_name)
            span.set_attribute(OTEL_RPC_METHOD, method or call.method_name)

            metadata = get_metadata_dict(call.context)
            if X_REQUEST_ID in metadata:
                span.set_attribute(OTEL_REQUEST_ID, metadata[X_REQUEST_ID])

            try:
                yield
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                self._set_status(span, grpc.StatusCode.CANCELLED)
                raise
            except grpc.aio.AbortError as exc:
                status = resolve_status_code(None, call.context)
                self._set_status(span, status)
                if is_server_error(status):
                    # An inner exception handler already mapped the original
                    # exception into this abort; recover it from the chain so
                    # server-error spans still carry the real exception event.
                    cause = exc.__cause__ or exc.__context__
                    if isinstance(cause, Exception) and not isinstance(cause, grpc.aio.AbortError):
                        span.record_exception(cause)
                raise
            except grpc.RpcError as exc:
                status = resolve_status_code(exc, call.context)
                self._set_status(span, status)
                if is_server_error(status):
                    span.record_exception(exc)
                raise
            except Exception as exc:
                self._set_status(span, resolve_status_code(None, call.context, default=grpc.StatusCode.INTERNAL))
                span.record_exception(exc)
                raise
            else:
                self._set_status(span, resolve_status_code(None, call.context, default=grpc.StatusCode.OK))

    @staticmethod
    def _set_status(span: SpanProtocol, status: grpc.StatusCode) -> None:
        """Set the numeric ``rpc.grpc.status_code`` semconv attribute."""
        span.set_attribute(OTEL_RPC_GRPC_STATUS_CODE, status.value[0])
