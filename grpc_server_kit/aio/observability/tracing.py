"""OpenTelemetry tracing wiring for the async gRPC server (``[tracing]`` extra).

Thin helpers over ``GrpcAioInstrumentorServer``. Call :func:`instrument_aio_server` once
at startup (optionally passing ``tracer_provider=...``, ``filter_=...`` etc.).
"""

from __future__ import annotations

from typing import Any

try:
    from opentelemetry.instrumentation.grpc import GrpcAioInstrumentorServer

    HAS_OTEL_GRPC = True
except ImportError:  # pragma: no cover - exercised only when the [tracing] extra is absent
    HAS_OTEL_GRPC = False

__all__ = ["instrument_aio_server", "uninstrument_aio_server"]

_MISSING_MESSAGE = (
    "Install grpc-server-kit[tracing] (opentelemetry-instrumentation-grpc) "
    "to instrument the gRPC server with OpenTelemetry"
)


def instrument_aio_server(**kwargs: Any) -> None:
    """Instrument the async gRPC server with OpenTelemetry.

    Keyword arguments are forwarded to ``GrpcAioInstrumentorServer().instrument()``
    (e.g. ``tracer_provider``, ``filter_``).

    Raises:
        ImportError: If the ``[tracing]`` extra is not installed.
    """
    if not HAS_OTEL_GRPC:
        raise ImportError(_MISSING_MESSAGE)
    GrpcAioInstrumentorServer().instrument(**kwargs)


def uninstrument_aio_server() -> None:
    """Remove OpenTelemetry instrumentation from the async gRPC server.

    Raises:
        ImportError: If the ``[tracing]`` extra is not installed.
    """
    if not HAS_OTEL_GRPC:
        raise ImportError(_MISSING_MESSAGE)
    GrpcAioInstrumentorServer().uninstrument()
