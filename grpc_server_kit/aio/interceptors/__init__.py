"""Async gRPC server interceptors.

All interceptors are streaming-aware: they wrap the whole RPC (including full
response streams), so timings, error mapping, and cleanup cover the entire
call. Build custom interceptors by subclassing
:class:`~grpc_server_kit.aio.interceptors.base.AsyncServerInterceptor`.

Canonical execution order (outermost first):
1. ``AsyncMetricsInterceptor``            (metrics — measures everything below)
2. ``AsyncContextInterceptor``            (extract metadata into context vars)
3. ``AsyncRequestLoggerInterceptor``      (request/response logging)
4. ``AsyncTracingInterceptor``            (tracing span)
5. ``AsyncSentryInterceptor``             (error tracking)
6. ``AsyncExceptionHandlerInterceptor``   (exception → gRPC status mapping)
"""

from .base import AsyncServerInterceptor, RpcCall, split_method_name
from .context import AsyncContextInterceptor, HeaderConfig
from .exception_handler import (
    GRPC_DEFAULT_ERROR_STATUS_MAP,
    GRPC_SAFE_ERROR_MESSAGES,
    AsyncExceptionHandlerInterceptor,
    ErrorDetailFactory,
    default_error_detail,
    find_mapped_status,
)
from .metadata import get_metadata_dict
from .metrics import AsyncMetricsInterceptor
from .protocols import (
    ErrorReporterProtocol,
    GrpcServerMetricsProtocol,
    SpanAttributeValue,
    SpanProtocol,
    TracerProtocol,
)
from .request_logger import AsyncRequestLoggerInterceptor
from .sentry import AsyncSentryInterceptor
from .tracing import AsyncTracingInterceptor

__all__ = [
    "GRPC_DEFAULT_ERROR_STATUS_MAP",
    "GRPC_SAFE_ERROR_MESSAGES",
    "AsyncContextInterceptor",
    "AsyncExceptionHandlerInterceptor",
    "AsyncMetricsInterceptor",
    "AsyncRequestLoggerInterceptor",
    "AsyncSentryInterceptor",
    "AsyncServerInterceptor",
    "AsyncTracingInterceptor",
    "ErrorDetailFactory",
    "ErrorReporterProtocol",
    "GrpcServerMetricsProtocol",
    "HeaderConfig",
    "RpcCall",
    "SpanAttributeValue",
    "SpanProtocol",
    "TracerProtocol",
    "default_error_detail",
    "find_mapped_status",
    "get_metadata_dict",
    "split_method_name",
]
