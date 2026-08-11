"""Tests for the request logger interceptor."""

from __future__ import annotations

from unittest.mock import MagicMock

import grpc
import pytest

from grpc_server_kit.aio.interceptors.request_logger import AsyncRequestLoggerInterceptor, _sanitize_peer

from .conftest import RESPONSE, FailingMethodFactory, MakeContext, MakeRpcError, OkMethod, RunUnary

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        (None, "unknown"),
        ("ipv4:127.0.0.1:50051", "ipv4"),
        ("ipv6:[::1]:50051", "ipv6"),
        ("unix:/tmp/sock", "unix"),
        ("garbage", "unknown"),
    ],
)
def test__sanitize_peer__various_peer_formats__extracts_protocol_or_unknown(
    peer: str | None,
    expected: str,
) -> None:
    # Act
    result = _sanitize_peer(peer)

    # Assert
    assert result == expected


async def test__request_logger__health_method__returns_response(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    interceptor = AsyncRequestLoggerInterceptor()
    ctx = make_context()

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), ctx, "/grpc.health.v1.Health/Check")

    # Assert
    assert result == RESPONSE


async def test__request_logger__log_peer_enabled__returns_response(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    interceptor = AsyncRequestLoggerInterceptor(log_peer=True)
    ctx = make_context(peer="ipv4:10.0.0.1:1234")

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), ctx, "/svc/Method")

    # Assert
    assert result == RESPONSE


@pytest.mark.parametrize(
    ("exc_code", "context_code"),
    [
        pytest.param(grpc.StatusCode.NOT_FOUND, None, id="client_error"),
        pytest.param(grpc.StatusCode.INTERNAL, None, id="server_error"),
        # exc.code() == UNKNOWN falls back to the context's code.
        pytest.param(grpc.StatusCode.UNKNOWN, grpc.StatusCode.NOT_FOUND, id="unknown_recovers_from_context"),
    ],
)
async def test__request_logger__rpc_error__reraised(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
    make_rpc_error: MakeRpcError,
    exc_code: grpc.StatusCode,
    context_code: grpc.StatusCode | None,
) -> None:
    # Arrange
    interceptor = AsyncRequestLoggerInterceptor()
    ctx = make_context(code=context_code)

    # Act & Assert
    with pytest.raises(grpc.RpcError):
        await run_unary(interceptor, failing_method(make_rpc_error(exc_code)), MagicMock(), ctx)


async def test__request_logger__abort_error__reraised(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
) -> None:
    # Arrange
    # grpc.aio.AbortError is not a grpc.RpcError subclass: it is handled by its
    # own except branch (no stack trace logged — the abort was deliberate).
    interceptor = AsyncRequestLoggerInterceptor()
    ctx = make_context(code=grpc.StatusCode.INVALID_ARGUMENT)

    # Act & Assert
    with pytest.raises(grpc.aio.AbortError):
        await run_unary(interceptor, failing_method(grpc.aio.AbortError()), MagicMock(), ctx)


@pytest.mark.parametrize("log_request_on_error", [False, True])
async def test__request_logger__unexpected_exception__reraised(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
    log_request_on_error: bool,
) -> None:
    # Arrange
    interceptor = AsyncRequestLoggerInterceptor(log_request_on_error=log_request_on_error)
    ctx = make_context()

    # Act & Assert
    with pytest.raises(RuntimeError):
        await run_unary(interceptor, failing_method(RuntimeError("boom")), MagicMock(), ctx)
