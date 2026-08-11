"""Async observability wiring (OpenTelemetry instrumentation for the aio gRPC server)."""

from .tracing import instrument_aio_server, uninstrument_aio_server

__all__ = ["instrument_aio_server", "uninstrument_aio_server"]
