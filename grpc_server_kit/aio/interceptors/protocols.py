"""Structural protocols for optional interceptor dependencies (duck-typing seams).

The cross-cutting observability seams (span/tracer, error reporter) are owned by
:mod:`grpc_server_kit.observability.protocols` and re-exported here for
interceptor consumers. The protocols defined locally are gRPC-specific shapes
that belong to this package.
"""

from __future__ import annotations

from typing import Protocol

from grpc_server_kit.observability.protocols import (
    ErrorReporterProtocol,
    SpanAttributeValue,
    SpanProtocol,
    TracerProtocol,
)


class GrpcServerMetricsProtocol(Protocol):
    """Protocol for a gRPC server metrics recorder.

    The kit's prometheus-backed ``GrpcServerMetrics`` satisfies this
    structurally, as does any recorder with the same shape.
    """

    def record_request(
        self,
        service: str,
        method: str,
        status: str,
        grpc_code: str,
        duration: float,
    ) -> None:
        """Record a completed gRPC request.

        Args:
            service: Name of the service.
            method: Name of the method.
            status: Status of the request (success, error).
            grpc_code: gRPC status code name.
            duration: Request duration in seconds.
        """
        ...


__all__ = [
    "ErrorReporterProtocol",
    "GrpcServerMetricsProtocol",
    "SpanAttributeValue",
    "SpanProtocol",
    "TracerProtocol",
]
