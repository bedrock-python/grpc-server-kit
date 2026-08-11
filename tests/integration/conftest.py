"""Shared fixtures for integration tests: a real grpc.aio server + channel.

The test service uses generic bytes-in/bytes-out handlers, so no compiled
protos are needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import grpc
import pytest

from grpc_server_kit import GrpcServerConfig
from grpc_server_kit.aio import AsyncServer, create_async_grpc_server
from grpc_server_kit.aio.interceptors import (
    AsyncContextInterceptor,
    AsyncExceptionHandlerInterceptor,
    AsyncMetricsInterceptor,
    AsyncRequestLoggerInterceptor,
    HeaderConfig,
)

SERVICE = "test.Echo"


class DomainError(Exception):
    """Custom domain error mapped to ALREADY_EXISTS in the test chain."""


@dataclass
class RecordingMetrics:
    """In-memory GrpcServerMetricsProtocol implementation for assertions."""

    records: list[dict[str, Any]] = field(default_factory=list)

    def record_request(self, service: str, method: str, status: str, grpc_code: str, duration: float) -> None:
        self.records.append(
            {"service": service, "method": method, "status": status, "grpc_code": grpc_code, "duration": duration}
        )

    def for_method(self, method: str) -> dict[str, Any]:
        matching = [r for r in self.records if r["method"] == method]
        assert len(matching) == 1, f"expected exactly one metric record for {method}, got {matching}"
        return matching[0]


async def _echo(request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    return request


async def _fail_value_error(_request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    raise ValueError("secret internals must not leak")


async def _fail_domain_error(_request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    raise DomainError("duplicate thing")


async def _fail_runtime_error(_request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    raise RuntimeError("boom")


async def _abort_directly(_request: bytes, context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    await context.abort(grpc.StatusCode.PERMISSION_DENIED, "handler-abort")
    raise AssertionError("unreachable")


async def _stream(_request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> AsyncIterator[bytes]:
    yield b"one"
    yield b"two"
    yield b"three"


async def _stream_fail_mid(_request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> AsyncIterator[bytes]:
    yield b"one"
    raise ValueError("mid-stream failure")


async def _sum_lengths(request_iterator: Any, _context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    total = 0
    async for chunk in request_iterator:
        total += len(chunk)
    return str(total).encode()


async def _chat_upper(request_iterator: Any, _context: grpc.aio.ServicerContext[bytes, bytes]) -> AsyncIterator[bytes]:
    async for chunk in request_iterator:
        yield chunk.upper()


def make_echo_generic_handler() -> grpc.GenericRpcHandler:
    """Build the generic handler exposing the test service methods."""
    return grpc.method_handlers_generic_handler(
        SERVICE,
        {
            "Echo": grpc.unary_unary_rpc_method_handler(_echo),
            "FailValueError": grpc.unary_unary_rpc_method_handler(_fail_value_error),
            "FailDomainError": grpc.unary_unary_rpc_method_handler(_fail_domain_error),
            "FailRuntimeError": grpc.unary_unary_rpc_method_handler(_fail_runtime_error),
            "AbortDirectly": grpc.unary_unary_rpc_method_handler(_abort_directly),
            "Stream": grpc.unary_stream_rpc_method_handler(_stream),
            "StreamFailMid": grpc.unary_stream_rpc_method_handler(_stream_fail_mid),
            "SumLengths": grpc.stream_unary_rpc_method_handler(_sum_lengths),
            "ChatUpper": grpc.stream_stream_rpc_method_handler(_chat_upper),
        },
    )


@dataclass
class RunningServer:
    server: AsyncServer
    port: int
    metrics: RecordingMetrics

    def target(self) -> str:
        return f"127.0.0.1:{self.port}"


@pytest.fixture
async def running_server() -> AsyncIterator[RunningServer]:
    """A started grpc.aio server with the full canonical interceptor chain."""
    metrics = RecordingMetrics()
    interceptors: list[grpc.aio.ServerInterceptor] = [
        AsyncMetricsInterceptor(metrics=metrics, service_name=SERVICE),
        AsyncContextInterceptor([HeaderConfig("x-request-id", "request_id")], bind_structlog=False),
        AsyncRequestLoggerInterceptor(),
        AsyncExceptionHandlerInterceptor({DomainError: grpc.StatusCode.ALREADY_EXISTS}),
    ]
    server = create_async_grpc_server(
        interceptors=interceptors,
        settings=GrpcServerConfig(host="127.0.0.1", port=0),
        register_servicers=lambda raw: raw.add_generic_rpc_handlers((make_echo_generic_handler(),)),
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield RunningServer(server=server, port=port, metrics=metrics)
    finally:
        await server.stop(grace=None)


@pytest.fixture
async def channel(running_server: RunningServer) -> AsyncIterator[grpc.aio.Channel]:
    """An open channel to the running test server."""
    async with grpc.aio.insecure_channel(running_server.target()) as chan:
        yield chan
