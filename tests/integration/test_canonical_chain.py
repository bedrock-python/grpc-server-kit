"""End-to-end test of the CANONICAL chain order over a real grpc.aio server.

Regression for the ordering defect where the Sentry interceptor sat outside the
exception handler and therefore only ever saw ``AbortError`` — meaning zero
captured events in production.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import grpc
import pytest

from grpc_server_kit import GrpcServerConfig
from grpc_server_kit.aio import create_async_grpc_server
from grpc_server_kit.aio.interceptors import (
    GRPC_DEFAULT_ERROR_STATUS_MAP,
    AsyncContextInterceptor,
    AsyncExceptionHandlerInterceptor,
    AsyncMetricsInterceptor,
    AsyncRequestLoggerInterceptor,
    AsyncSentryInterceptor,
    AsyncTracingInterceptor,
    HeaderConfig,
    find_mapped_status,
)
from grpc_server_kit.interceptors import is_server_error

from .conftest import SERVICE, RecordingMetrics, make_echo_generic_handler

pytestmark = pytest.mark.integration


@dataclass
class RecordingReporter:
    """In-memory ErrorReporterProtocol implementation."""

    captured: list[Exception] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)

    def isolate(self) -> contextlib.AbstractContextManager[object]:
        return contextlib.nullcontext()

    def capture_exception(self, error: Exception) -> None:
        self.captured.append(error)

    def add_breadcrumb(
        self,
        message: str,
        category: str = "default",
        level: str = "info",
        data: dict[str, object] | None = None,
    ) -> None:
        return None

    def set_tags(self, **tags: str) -> None:
        self.tags.update(tags)


class RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}
        self.exceptions: list[Exception] = []

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def record_exception(self, exception: Exception) -> None:
        self.exceptions.append(exception)

    def __enter__(self) -> RecordingSpan:
        return self

    def __exit__(self, *args: object) -> None:
        return None


@dataclass
class RecordingTracer:
    spans: list[RecordingSpan] = field(default_factory=list)

    @contextlib.contextmanager
    def start_as_current_span(self, _name: str) -> Iterator[RecordingSpan]:
        span = RecordingSpan()
        self.spans.append(span)
        yield span


@dataclass
class ChainFixture:
    port: int
    metrics: RecordingMetrics
    reporter: RecordingReporter
    tracer: RecordingTracer

    def target(self) -> str:
        return f"127.0.0.1:{self.port}"


@pytest.fixture
async def canonical_server() -> AsyncIterator[ChainFixture]:
    """A real server running the full canonical chain with recording seams."""
    metrics = RecordingMetrics()
    reporter = RecordingReporter()
    tracer = RecordingTracer()

    def server_errors_only(exc: Exception) -> bool:
        return is_server_error(find_mapped_status(type(exc), GRPC_DEFAULT_ERROR_STATUS_MAP))

    interceptors: list[grpc.aio.ServerInterceptor] = [
        AsyncMetricsInterceptor(metrics=metrics, service_name=SERVICE),
        AsyncContextInterceptor([HeaderConfig("x-request-id", "request_id")], bind_structlog=False),
        AsyncRequestLoggerInterceptor(),
        AsyncTracingInterceptor(service_name=SERVICE, tracer=tracer),  # type: ignore[arg-type]
        AsyncExceptionHandlerInterceptor(),
        # Inside the exception handler; captures only server-class errors.
        AsyncSentryInterceptor(sentry=reporter, capture_filter=server_errors_only),
    ]
    server = create_async_grpc_server(
        interceptors=interceptors,
        settings=GrpcServerConfig(host="127.0.0.1", port=0),
        register_servicers=lambda raw: raw.add_generic_rpc_handlers((make_echo_generic_handler(),)),
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield ChainFixture(port=port, metrics=metrics, reporter=reporter, tracer=tracer)
    finally:
        await server.stop(grace=None)


@pytest.fixture
async def channel(canonical_server: ChainFixture) -> AsyncIterator[grpc.aio.Channel]:
    """An open channel to the canonical-chain server."""
    async with grpc.aio.insecure_channel(canonical_server.target()) as chan:
        yield chan


async def test__canonical_chain__unmapped_handler_exception__sentry_captures_raw_exception(
    channel: grpc.aio.Channel,
    canonical_server: ChainFixture,
) -> None:
    # Act
    fail = channel.unary_unary(f"/{SERVICE}/FailRuntimeError")
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await fail(b"x")

    # Assert
    assert exc_info.value.code() == grpc.StatusCode.INTERNAL
    # The raw RuntimeError reached Sentry BEFORE the exception handler mapped
    # it into an abort — this is the whole point of the canonical order.
    assert [type(e).__name__ for e in canonical_server.reporter.captured] == ["RuntimeError"]


async def test__canonical_chain__deliberate_abort__sentry_not_captured(
    channel: grpc.aio.Channel,
    canonical_server: ChainFixture,
) -> None:
    # Act
    abort = channel.unary_unary(f"/{SERVICE}/AbortDirectly")
    with pytest.raises(grpc.aio.AioRpcError):
        await abort(b"x")

    # Assert
    # Deliberate aborts are never captured — they never reach the Sentry
    # interceptor's capture filter at all.
    assert canonical_server.reporter.captured == []


async def test__canonical_chain__client_mapped_exception__sentry_not_captured(
    channel: grpc.aio.Channel,
    canonical_server: ChainFixture,
) -> None:
    # Act
    client_error = channel.unary_unary(f"/{SERVICE}/FailValueError")
    with pytest.raises(grpc.aio.AioRpcError):
        await client_error(b"x")

    # Assert
    # Client-mapped errors (ValueError -> INVALID_ARGUMENT) are excluded by the
    # server-errors-only capture filter.
    assert canonical_server.reporter.captured == []


async def test__canonical_chain__unmapped_handler_exception__tracing_span_records_original_exception(
    channel: grpc.aio.Channel,
    canonical_server: ChainFixture,
) -> None:
    # Act
    fail = channel.unary_unary(f"/{SERVICE}/FailRuntimeError")
    with pytest.raises(grpc.aio.AioRpcError):
        await fail(b"x")

    # Assert
    spans = canonical_server.tracer.spans
    assert len(spans) == 1
    # The span records the ORIGINAL RuntimeError (recovered from the abort's
    # exception chain), with the INTERNAL status code attribute.
    assert spans[0].attributes["rpc.grpc.status_code"] == grpc.StatusCode.INTERNAL.value[0]
    assert [type(e).__name__ for e in spans[0].exceptions] == ["RuntimeError"]


async def test__canonical_chain__success_path__all_interceptors_record_success(
    channel: grpc.aio.Channel,
    canonical_server: ChainFixture,
) -> None:
    # Act
    echo = channel.unary_unary(f"/{SERVICE}/Echo")
    response = await echo(b"ping", metadata=(("x-request-id", "req-9"),))

    # Assert
    assert response == b"ping"
    record = canonical_server.metrics.for_method(f"/{SERVICE}/Echo")
    assert record["status"] == "success"
    assert canonical_server.reporter.captured == []
    assert canonical_server.reporter.tags["request_id"] == "req-9"
    assert len(canonical_server.tracer.spans) == 1
    assert canonical_server.tracer.spans[0].attributes["rpc.grpc.status_code"] == grpc.StatusCode.OK.value[0]
