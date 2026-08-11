"""End-to-end tests of the interceptor chain over a real grpc.aio server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import grpc
import pytest

from .conftest import SERVICE, RunningServer

pytestmark = pytest.mark.integration


async def _invoke_echo(channel: grpc.aio.Channel) -> bytes:
    echo = channel.unary_unary(f"/{SERVICE}/Echo")
    return await echo(b"hello", metadata=(("x-request-id", "req-1"),))


async def _invoke_stream(channel: grpc.aio.Channel) -> list[bytes]:
    stream = channel.unary_stream(f"/{SERVICE}/Stream")
    return [item async for item in stream(b"go")]


async def _invoke_sum_lengths(channel: grpc.aio.Channel) -> bytes:
    sum_lengths = channel.stream_unary(f"/{SERVICE}/SumLengths")
    return await sum_lengths(iter([b"ab", b"cde"]))


async def _invoke_chat_upper(channel: grpc.aio.Channel) -> list[bytes]:
    chat = channel.stream_stream(f"/{SERVICE}/ChatUpper")
    return [item async for item in chat(iter([b"ab", b"cd"]))]


@dataclass
class MappedErrorCase:
    """A handler exception and the gRPC status/details the client must observe."""

    method: str
    expected_code: grpc.StatusCode
    expected_details: str


@pytest.mark.parametrize(
    ("method", "invoke", "expected_response"),
    [
        pytest.param("Echo", _invoke_echo, b"hello", id="unary_unary"),
        pytest.param("Stream", _invoke_stream, [b"one", b"two", b"three"], id="unary_stream"),
        pytest.param("SumLengths", _invoke_sum_lengths, b"5", id="stream_unary"),
        pytest.param("ChatUpper", _invoke_chat_upper, [b"AB", b"CD"], id="stream_stream"),
    ],
)
async def test__interceptor_chain__rpc_kind_success__passes_through_and_records_metrics(
    channel: grpc.aio.Channel,
    running_server: RunningServer,
    method: str,
    invoke: Callable[[grpc.aio.Channel], Awaitable[bytes | list[bytes]]],
    expected_response: bytes | list[bytes],
) -> None:
    # Act
    response = await invoke(channel)

    # Assert
    assert response == expected_response

    record = running_server.metrics.for_method(f"/{SERVICE}/{method}")
    assert record["status"] == "success"
    assert record["grpc_code"] == "OK"


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            MappedErrorCase("FailValueError", grpc.StatusCode.INVALID_ARGUMENT, "Invalid request data"),
            id="default_map_value_error",
        ),
        pytest.param(
            MappedErrorCase("FailDomainError", grpc.StatusCode.ALREADY_EXISTS, "Resource already exists"),
            id="custom_map_domain_error",
        ),
        pytest.param(
            MappedErrorCase("FailRuntimeError", grpc.StatusCode.INTERNAL, "Internal server error"),
            id="unmapped_error_defaults_to_internal",
        ),
    ],
)
async def test__interceptor_chain__handler_raises_mapped_exception__client_gets_safe_status(
    channel: grpc.aio.Channel,
    running_server: RunningServer,
    case: MappedErrorCase,
) -> None:
    # Arrange
    call = channel.unary_unary(f"/{SERVICE}/{case.method}")

    # Act
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await call(b"x")

    # Assert
    # The safe mapped detail is sent, never the exception's own message.
    assert exc_info.value.code() == case.expected_code
    assert exc_info.value.details() == case.expected_details

    record = running_server.metrics.for_method(f"/{SERVICE}/{case.method}")
    assert record["status"] == "error"
    assert record["grpc_code"] == case.expected_code.name


async def test__interceptor_chain__handler_aborts_directly__passes_through_untouched(
    channel: grpc.aio.Channel,
    running_server: RunningServer,
) -> None:
    # Arrange
    # context.abort() raises grpc.aio.AbortError; the chain must not re-map it.
    abort = channel.unary_unary(f"/{SERVICE}/AbortDirectly")

    # Act
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await abort(b"x")

    # Assert
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED
    assert exc_info.value.details() == "handler-abort"

    record = running_server.metrics.for_method(f"/{SERVICE}/AbortDirectly")
    assert record["status"] == "error"
    assert record["grpc_code"] == "PERMISSION_DENIED"


async def test__interceptor_chain__unary_stream_error_mid_stream__maps_status_after_partial_delivery(
    channel: grpc.aio.Channel,
    running_server: RunningServer,
) -> None:
    # Arrange
    # The failure happens AFTER the first item was already delivered.
    stream = channel.unary_stream(f"/{SERVICE}/StreamFailMid")
    call = stream(b"go")
    items: list[bytes] = []

    # Act
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        async for item in call:
            items.append(item)

    # Assert
    assert items == [b"one"]
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert exc_info.value.details() == "Invalid request data"

    record = running_server.metrics.for_method(f"/{SERVICE}/StreamFailMid")
    assert record["status"] == "error"
    assert record["grpc_code"] == "INVALID_ARGUMENT"
