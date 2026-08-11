"""Tests for the streaming-aware interceptor base class."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import grpc
import pytest

from grpc_server_kit.aio.interceptors.base import AsyncServerInterceptor, RpcCall, split_method_name

pytestmark = pytest.mark.unit


class _Recorder(AsyncServerInterceptor):
    """Interceptor recording setup/teardown/error around each call."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.events: list[str] = []
        self.calls: list[RpcCall] = []

    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        self.calls.append(call)
        self.events.append("setup")
        try:
            yield
        except Exception as exc:
            self.events.append(f"error:{type(exc).__name__}")
            raise
        else:
            self.events.append("teardown")


def _details(method: str = "/svc/M") -> MagicMock:
    details = MagicMock()
    details.method = method
    return details


def _continuation(handler: grpc.RpcMethodHandler | None) -> Any:
    async def continuation(_details: Any) -> grpc.RpcMethodHandler | None:
        return handler

    return continuation


@pytest.mark.parametrize(
    ("method_name", "expected"),
    [
        ("/pkg.Service/Method", ("pkg.Service", "Method")),
        ("/Service/Method", ("Service", "Method")),
        ("garbage", ("garbage", "")),
        ("", ("", "")),
    ],
)
def test__split_method_name__various_formats__splits_into_service_and_method(
    method_name: str,
    expected: tuple[str, str],
) -> None:
    # Act
    result = split_method_name(method_name)

    # Assert
    assert result == expected


async def test__base_interceptor__unary_unary__wraps_and_runs_around_call() -> None:
    # Arrange
    async def behavior(request: Any, _context: Any) -> str:
        return f"echo:{request}"

    handler = grpc.unary_unary_rpc_method_handler(behavior)
    interceptor = _Recorder()

    # Act
    wrapped = await interceptor.intercept_service(_continuation(handler), _details("/svc/Echo"))
    assert wrapped is not None
    result = await wrapped.unary_unary("hi", MagicMock())

    # Assert
    assert result == "echo:hi"
    assert interceptor.events == ["setup", "teardown"]
    call = interceptor.calls[0]
    assert call.method_name == "/svc/Echo"
    assert call.request == "hi"
    assert call.request_streaming is False
    assert call.response_streaming is False


async def test__base_interceptor__unary_unary_error__flows_through_around_call() -> None:
    # Arrange
    async def behavior(_request: Any, _context: Any) -> str:
        raise ValueError("boom")

    handler = grpc.unary_unary_rpc_method_handler(behavior)
    interceptor = _Recorder()
    wrapped = await interceptor.intercept_service(_continuation(handler), _details())
    assert wrapped is not None

    # Act & Assert
    with pytest.raises(ValueError, match="boom"):
        await wrapped.unary_unary("hi", MagicMock())
    assert interceptor.events == ["setup", "error:ValueError"]


async def test__base_interceptor__unary_stream__around_call_covers_whole_stream() -> None:
    # Arrange
    async def behavior(_request: Any, _context: Any) -> AsyncIterator[int]:
        yield 1
        yield 2

    handler = grpc.unary_stream_rpc_method_handler(behavior)
    interceptor = _Recorder()

    # Act
    wrapped = await interceptor.intercept_service(_continuation(handler), _details())
    assert wrapped is not None
    items = [item async for item in wrapped.unary_stream("hi", MagicMock())]

    # Assert
    assert items == [1, 2]
    assert interceptor.events == ["setup", "teardown"]
    assert interceptor.calls[0].response_streaming is True


async def test__base_interceptor__unary_stream_error_mid_stream__flows_through_around_call() -> None:
    # Arrange
    async def behavior(_request: Any, _context: Any) -> AsyncIterator[int]:
        yield 1
        raise ValueError("mid-stream")

    handler = grpc.unary_stream_rpc_method_handler(behavior)
    interceptor = _Recorder()
    wrapped = await interceptor.intercept_service(_continuation(handler), _details())
    assert wrapped is not None
    items: list[int] = []

    # Act & Assert
    with pytest.raises(ValueError, match="mid-stream"):
        async for item in wrapped.unary_stream("hi", MagicMock()):
            items.append(item)
    assert items == [1]
    assert interceptor.events == ["setup", "error:ValueError"]


async def test__base_interceptor__stream_unary__passes_request_iterator_through() -> None:
    # Arrange
    async def behavior(request_iterator: Any, _context: Any) -> str:
        received = [item async for item in request_iterator]
        return ",".join(received)

    handler = grpc.stream_unary_rpc_method_handler(behavior)
    interceptor = _Recorder()

    async def request_iter() -> AsyncIterator[str]:
        yield "a"
        yield "b"

    # Act
    wrapped = await interceptor.intercept_service(_continuation(handler), _details())
    assert wrapped is not None
    result = await wrapped.stream_unary(request_iter(), MagicMock())

    # Assert
    assert result == "a,b"
    assert interceptor.calls[0].request_streaming is True
    assert interceptor.calls[0].response_streaming is False


async def test__base_interceptor__stream_stream__wraps_and_runs_around_call() -> None:
    # Arrange
    async def behavior(request_iterator: Any, _context: Any) -> AsyncIterator[str]:
        async for item in request_iterator:
            yield item.upper()

    handler = grpc.stream_stream_rpc_method_handler(behavior)
    interceptor = _Recorder()

    async def request_iter() -> AsyncIterator[str]:
        yield "a"
        yield "b"

    # Act
    wrapped = await interceptor.intercept_service(_continuation(handler), _details())
    assert wrapped is not None
    items = [item async for item in wrapped.stream_stream(request_iter(), MagicMock())]

    # Assert
    assert items == ["A", "B"]
    assert interceptor.events == ["setup", "teardown"]


async def test__base_interceptor__read_write_api_handler_returning_none__yields_no_items() -> None:
    # Arrange
    async def behavior(_request: Any, _context: Any) -> None:
        # Handlers using the read/write API return None instead of yielding.
        return None

    handler = grpc.unary_stream_rpc_method_handler(behavior)
    interceptor = _Recorder()

    # Act
    wrapped = await interceptor.intercept_service(_continuation(handler), _details())
    assert wrapped is not None
    items = [item async for item in wrapped.unary_stream("hi", MagicMock())]

    # Assert
    assert items == []
    assert interceptor.events == ["setup", "teardown"]


async def test__base_interceptor__unimplemented_method__passthrough_without_wrapping() -> None:
    # Arrange
    interceptor = _Recorder()

    # Act
    result = await interceptor.intercept_service(_continuation(None), _details())

    # Assert
    assert result is None
    assert interceptor.events == []


async def test__base_interceptor__around__reusable_across_multiple_calls() -> None:
    # Arrange
    interceptor = _Recorder()
    ctx = MagicMock()
    call = RpcCall(method_name="/svc/M", request="r", context=ctx, request_streaming=False, response_streaming=False)

    # Act
    async with interceptor.around(call):
        pass
    async with interceptor.around(call):
        pass

    # Assert
    assert interceptor.events == ["setup", "teardown", "setup", "teardown"]


async def test__base_interceptor__skip_methods__intercept_service_returns_handler_unwrapped() -> None:
    # Arrange
    async def behavior(request: Any, _context: Any) -> str:
        return f"echo:{request}"

    handler = grpc.unary_unary_rpc_method_handler(behavior)
    interceptor = _Recorder(skip_methods={"/svc/Skipped"})

    # Act
    returned = await interceptor.intercept_service(_continuation(handler), _details("/svc/Skipped"))

    # Assert
    # The original handler passes through untouched — zero interception overhead.
    assert returned is handler
    assert interceptor.events == []


async def test__base_interceptor__skip_methods__around_is_noop() -> None:
    # Arrange
    interceptor = _Recorder(skip_methods={"/svc/Skipped"})
    ctx = MagicMock()
    call = RpcCall(
        method_name="/svc/Skipped",
        request="r",
        context=ctx,
        request_streaming=False,
        response_streaming=False,
    )

    # Act
    async with interceptor.around(call):
        pass

    # Assert
    assert interceptor.events == []


async def test__base_interceptor__same_handler_registered_twice__wrapped_handler_is_reused() -> None:
    # Arrange
    async def behavior(request: Any, _context: Any) -> str:
        return f"echo:{request}"

    handler = grpc.unary_unary_rpc_method_handler(behavior)
    interceptor = _Recorder()

    # Act
    first = await interceptor.intercept_service(_continuation(handler), _details("/svc/Echo"))
    second = await interceptor.intercept_service(_continuation(handler), _details("/svc/Echo"))

    # Assert
    assert first is second


async def test__base_interceptor__different_handler_same_method__cache_is_invalidated() -> None:
    # Arrange
    async def behavior(request: Any, _context: Any) -> str:
        return f"echo:{request}"

    handler = grpc.unary_unary_rpc_method_handler(behavior)
    interceptor = _Recorder()
    cached = await interceptor.intercept_service(_continuation(handler), _details("/svc/Echo"))

    # Act
    # A DIFFERENT handler under the same method name (dynamic generic handlers)
    # must invalidate the cache.
    other_handler = grpc.unary_unary_rpc_method_handler(behavior)
    rewrapped = await interceptor.intercept_service(_continuation(other_handler), _details("/svc/Echo"))

    # Assert
    assert rewrapped is not cached


async def test__base_interceptor__cached_wrapped_handler__still_runs_around_call_per_rpc() -> None:
    # Arrange
    async def behavior(request: Any, _context: Any) -> str:
        return f"echo:{request}"

    handler = grpc.unary_unary_rpc_method_handler(behavior)
    interceptor = _Recorder()
    wrapped = await interceptor.intercept_service(_continuation(handler), _details("/svc/Echo"))
    assert wrapped is not None

    # Act
    await wrapped.unary_unary("a", MagicMock())
    await wrapped.unary_unary("b", MagicMock())

    # Assert
    assert interceptor.events == ["setup", "teardown", "setup", "teardown"]


async def test__base_interceptor__stream_consumer_exits_early__closes_inner_generator() -> None:
    # Arrange
    closed = asyncio.Event()

    async def behavior(_request: Any, _context: Any) -> AsyncIterator[int]:
        try:
            yield 1
            yield 2
        finally:
            closed.set()

    handler = grpc.unary_stream_rpc_method_handler(behavior)
    interceptor = _Recorder()
    wrapped = await interceptor.intercept_service(_continuation(handler), _details())
    assert wrapped is not None
    outer = wrapped.unary_stream("hi", MagicMock())
    assert await anext(outer) == 1

    # Act
    # Consumer walks away mid-stream: closing the outer generator must close
    # the servicer generator NOW (its finally runs inside the RPC), instead of
    # deferring it to the event loop's async-generator finalizer.
    await outer.aclose()

    # Assert
    assert closed.is_set()
    # GeneratorExit passes through around_call; a recorder without finally sees
    # only the setup phase.
    assert interceptor.events == ["setup"]
