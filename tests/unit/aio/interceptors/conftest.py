"""Shared fixtures for interceptor tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from grpc_server_kit.aio.interceptors import AsyncServerInterceptor, RpcCall

RESPONSE = "RESPONSE"

RunUnary = Callable[..., Awaitable[Any]]
MakeContext = Callable[..., MagicMock]
OkMethod = Callable[[Any, Any], Awaitable[str]]
FailingMethodFactory = Callable[[BaseException], Callable[[Any, Any], Awaitable[Any]]]


def make_call(
    context: Any,
    method_name: str = "/svc/M",
    request: Any = None,
    *,
    request_streaming: bool = False,
    response_streaming: bool = False,
) -> RpcCall:
    """Build an RpcCall for direct around() testing."""
    return RpcCall(
        method_name=method_name,
        request=request if request is not None else MagicMock(),
        context=context,
        request_streaming=request_streaming,
        response_streaming=response_streaming,
    )


class _RpcError(grpc.RpcError):
    """grpc.RpcError double whose code() returns a fixed status (or None, like a bare RpcError)."""

    def __init__(self, code: grpc.StatusCode | None = None) -> None:
        self._code = code

    def code(self) -> grpc.StatusCode | None:
        return self._code


MakeRpcError = Callable[..., grpc.RpcError]


@pytest.fixture
def make_context() -> MakeContext:
    """Factory building a mock async ServicerContext.

    ``abort()`` raises ``grpc.aio.AbortError`` like the real aio context does;
    the call is still recorded for assertions via ``ctx.abort.call_args``.
    """

    def _make(
        metadata: Mapping[str, str | bytes] | None = None,
        peer: str | None = None,
        code: grpc.StatusCode | None = None,
    ) -> MagicMock:
        ctx = MagicMock()
        ctx.invocation_metadata.return_value = list((metadata or {}).items())
        ctx.peer.return_value = peer
        ctx.code.return_value = code

        async def _abort(_status: grpc.StatusCode, _details: str = "") -> None:
            raise grpc.aio.AbortError

        ctx.abort = AsyncMock(side_effect=_abort)
        return ctx

    return _make


@pytest.fixture
def run_unary() -> RunUnary:
    """Run an interceptor around a unary-unary method, like the base plumbing does."""

    async def _run(
        interceptor: AsyncServerInterceptor,
        method: Callable[[Any, Any], Awaitable[Any]],
        request: Any,
        context: Any,
        method_name: str = "/svc/M",
    ) -> Any:
        call = make_call(context, method_name=method_name, request=request)
        async with interceptor.around(call):
            return await method(request, context)

    return _run


@pytest.fixture
def ok_method() -> OkMethod:
    """An async gRPC method that returns a fixed response."""

    async def _method(_request: Any, _context: Any) -> str:
        return RESPONSE

    return _method


@pytest.fixture
def failing_method() -> FailingMethodFactory:
    """Factory building an async gRPC method that raises the given exception."""

    def _factory(exc: BaseException) -> Callable[[Any, Any], Awaitable[Any]]:
        async def _method(_request: Any, _context: Any) -> Any:
            raise exc

        return _method

    return _factory


@pytest.fixture
def make_rpc_error() -> MakeRpcError:
    """Factory building a grpc.RpcError double with an optional fixed status code.

    Consolidates the ``_RpcError`` test double that used to be redefined (with
    slightly different shapes) in every interceptor test module.
    """
    return _RpcError
