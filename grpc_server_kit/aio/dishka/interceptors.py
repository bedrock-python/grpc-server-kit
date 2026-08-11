"""Dishka provider assembling the canonical gRPC server interceptor chain (``[dishka]`` extra).

Replaces the per-service hand-written ``create_interceptors`` list. The order is fixed and
encodes the documented rationale: metrics outermost (measures everything below); the
exception handler maps raw exceptions to gRPC statuses; Sentry sits INSIDE the exception
handler so it observes raw handler exceptions BEFORE they are converted into
``grpc.aio.AbortError`` (outside it would never capture anything); the request-scoped
Dishka interceptor is innermost. Individual interceptors can be toggled off and extra
ones slotted at the outer/inner ends.
"""

from __future__ import annotations

from collections.abc import Sequence

import grpc
from dishka import AsyncContainer, Provider, Scope, provide
from dishka.integrations.grpcio import DishkaAioInterceptor

from grpc_server_kit.aio.interceptors import (
    GRPC_DEFAULT_ERROR_STATUS_MAP,
    AsyncContextInterceptor,
    AsyncExceptionHandlerInterceptor,
    AsyncMetricsInterceptor,
    AsyncRequestLoggerInterceptor,
    AsyncSentryInterceptor,
    AsyncTracingInterceptor,
    ErrorDetailFactory,
    HeaderConfig,
)
from grpc_server_kit.aio.interceptors.exception_handler import find_mapped_status
from grpc_server_kit.aio.interceptors.protocols import (
    ErrorReporterProtocol,
    GrpcServerMetricsProtocol,
    TracerProtocol,
)
from grpc_server_kit.interceptors.utils import is_server_error
from grpc_server_kit.protocols import GrpcServiceName


class GrpcServerInterceptorsProvider(Provider):
    """Provide the ordered ``list[grpc.aio.ServerInterceptor]`` for the server.

    Depends on the metrics/sentry/tracer seams (``... | None``) — register the matching
    seam providers (or use :func:`grpc_server_kit.aio.dishka.grpc_server_providers`).
    """

    scope = Scope.APP

    def __init__(
        self,
        *,
        error_status_map: dict[type[Exception], grpc.StatusCode] | None = None,
        detail_factory: ErrorDetailFactory | None = None,
        header_configs: Sequence[HeaderConfig] | None = None,
        include_metrics: bool = True,
        include_context: bool = True,
        include_request_logger: bool = True,
        include_tracing: bool = True,
        include_sentry: bool = True,
        include_exception_handler: bool = True,
        extra_outer: Sequence[grpc.aio.ServerInterceptor] | None = None,
        extra_inner: Sequence[grpc.aio.ServerInterceptor] | None = None,
    ) -> None:
        super().__init__()
        self._error_status_map = error_status_map
        self._detail_factory = detail_factory
        self._header_configs = list(header_configs) if header_configs is not None else []
        self._include_metrics = include_metrics
        self._include_context = include_context
        self._include_request_logger = include_request_logger
        self._include_tracing = include_tracing
        self._include_sentry = include_sentry
        self._include_exception_handler = include_exception_handler
        self._extra_outer = list(extra_outer or [])
        self._extra_inner = list(extra_inner or [])

    @provide
    def interceptors(
        self,
        container: AsyncContainer,
        service_name: GrpcServiceName,
        metrics: GrpcServerMetricsProtocol | None,
        sentry: ErrorReporterProtocol | None,
        tracer: TracerProtocol | None,
    ) -> list[grpc.aio.ServerInterceptor]:
        """Build the interceptor chain (outermost first; Dishka request-scope interceptor last)."""
        chain: list[grpc.aio.ServerInterceptor] = list(self._extra_outer)
        if self._include_metrics:
            chain.append(AsyncMetricsInterceptor(metrics=metrics, service_name=service_name))
        if self._include_context:
            chain.append(AsyncContextInterceptor(header_configs=self._header_configs))
        if self._include_request_logger:
            chain.append(AsyncRequestLoggerInterceptor())
        if self._include_tracing:
            chain.append(AsyncTracingInterceptor(service_name=service_name, tracer=tracer))
        if self._include_exception_handler:
            chain.append(AsyncExceptionHandlerInterceptor(self._error_status_map, detail_factory=self._detail_factory))
        if self._include_sentry:
            # Inside the exception handler: sees raw handler exceptions before
            # they are mapped into AbortError (which Sentry must not capture).
            # The capture filter reuses the SAME status map, so client-mapped
            # errors (e.g. ValueError -> INVALID_ARGUMENT) are not reported.
            merged_map = dict(GRPC_DEFAULT_ERROR_STATUS_MAP)
            merged_map.update(self._error_status_map or {})

            def _server_errors_only(exc: Exception, _map: dict = merged_map) -> bool:  # type: ignore[type-arg]
                return is_server_error(find_mapped_status(type(exc), _map))

            chain.append(AsyncSentryInterceptor(sentry=sentry, capture_filter=_server_errors_only))
        chain.extend(self._extra_inner)
        chain.append(DishkaAioInterceptor(container))  # innermost: opens the REQUEST scope per RPC
        return chain


__all__ = ["GrpcServerInterceptorsProvider"]
