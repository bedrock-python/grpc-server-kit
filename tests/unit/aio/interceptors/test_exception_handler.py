"""Tests for the exception-handler interceptor (exception -> gRPC status mapping)."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import grpc
import pytest

from grpc_server_kit.aio.interceptors.exception_handler import (
    GRPC_SAFE_ERROR_MESSAGES,
    AsyncExceptionHandlerInterceptor,
    default_error_detail,
)

from .conftest import RESPONSE, FailingMethodFactory, MakeContext, OkMethod, RunUnary

pytestmark = pytest.mark.unit


async def test__exception_handler__no_exception__returns_response(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    interceptor = AsyncExceptionHandlerInterceptor()

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), make_context())

    # Assert
    assert result == RESPONSE


@pytest.mark.parametrize(
    "exc",
    [
        # The handler already aborted with its own status: no re-mapping, no second abort.
        pytest.param(grpc.aio.AbortError(), id="abort_error"),
        pytest.param(asyncio.CancelledError(), id="cancelled"),
    ],
)
async def test__exception_handler__passthrough_exception__reraised_without_abort(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
    exc: BaseException,
) -> None:
    # Arrange
    interceptor = AsyncExceptionHandlerInterceptor()
    ctx = make_context()

    # Act & Assert
    with pytest.raises(type(exc)):
        await run_unary(interceptor, failing_method(exc), MagicMock(), ctx)
    ctx.abort.assert_not_called()


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        pytest.param(ValueError("bad"), grpc.StatusCode.INVALID_ARGUMENT, id="value_error"),
        pytest.param(KeyError("x"), grpc.StatusCode.INTERNAL, id="unmapped_key_error"),
        # TypeError almost always signals a server-side programming bug; it must
        # surface as INTERNAL (logged with a stack trace), not INVALID_ARGUMENT.
        pytest.param(TypeError("wrong arity"), grpc.StatusCode.INTERNAL, id="type_error_is_server_bug"),
    ],
)
async def test__exception_handler__default_map__aborts_with_mapped_status(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
    exc: Exception,
    expected_status: grpc.StatusCode,
) -> None:
    # Arrange
    interceptor = AsyncExceptionHandlerInterceptor()
    ctx = make_context()

    # Act & Assert
    with pytest.raises(grpc.aio.AbortError):
        await run_unary(interceptor, failing_method(exc), MagicMock(), ctx)
    assert ctx.abort.call_args.args[0] == expected_status
    assert ctx.abort.call_args.args[1] == GRPC_SAFE_ERROR_MESSAGES[expected_status]


def _outbound_rpc_error(code: grpc.StatusCode, details: str) -> grpc.aio.AioRpcError:
    """The error an outbound call made by the handler raises when the other service fails."""
    return grpc.aio.AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details=details)


@pytest.mark.parametrize(
    "downstream_code",
    [
        pytest.param(grpc.StatusCode.INTERNAL, id="downstream_internal"),
        # The downstream status describes this server's call, not the caller's request:
        # forwarding PERMISSION_DENIED would blame the caller for this server's credentials.
        pytest.param(grpc.StatusCode.PERMISSION_DENIED, id="downstream_permission_denied"),
    ],
)
async def test__exception_handler__outbound_rpc_error__aborts_internal_without_downstream_details(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
    caplog: pytest.LogCaptureFixture,
    downstream_code: grpc.StatusCode,
) -> None:
    # Arrange
    interceptor = AsyncExceptionHandlerInterceptor()
    ctx = make_context()
    error = _outbound_rpc_error(downstream_code, "ledger dsn=postgres://ledger:s3cret@10.0.3.7/ledger")

    # Act
    with caplog.at_level(logging.ERROR), pytest.raises(grpc.aio.AbortError):
        await run_unary(interceptor, failing_method(error), MagicMock(), ctx)

    # Assert
    ctx.abort.assert_awaited_once_with(grpc.StatusCode.INTERNAL, "Internal server error")
    # The real failure goes to the log with its traceback, as for any other server error.
    (record,) = caplog.records
    assert record.exc_info is not None
    assert record.exc_info[1] is error


async def test__exception_handler__rpc_error_in_status_map__aborts_with_mapped_status(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
) -> None:
    # Arrange
    interceptor = AsyncExceptionHandlerInterceptor({grpc.RpcError: grpc.StatusCode.UNAVAILABLE})
    ctx = make_context()
    error = _outbound_rpc_error(grpc.StatusCode.UNAVAILABLE, "connection refused")

    # Act & Assert
    with pytest.raises(grpc.aio.AbortError):
        await run_unary(interceptor, failing_method(error), MagicMock(), ctx)
    ctx.abort.assert_awaited_once_with(grpc.StatusCode.UNAVAILABLE, "Service unavailable")


async def test__exception_handler__custom_status_map__overrides_default_mapping(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
) -> None:
    # Arrange
    class MyError(Exception):
        pass

    interceptor = AsyncExceptionHandlerInterceptor({MyError: grpc.StatusCode.NOT_FOUND})
    ctx = make_context()

    # Act & Assert
    with pytest.raises(grpc.aio.AbortError):
        await run_unary(interceptor, failing_method(MyError()), MagicMock(), ctx)
    assert ctx.abort.call_args.args[0] == grpc.StatusCode.NOT_FOUND


async def test__exception_handler__merge_defaults_false__unmapped_value_error_becomes_internal(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
) -> None:
    # Arrange
    interceptor = AsyncExceptionHandlerInterceptor({}, merge_defaults=False)
    ctx = make_context()

    # Act & Assert
    with pytest.raises(grpc.aio.AbortError):
        await run_unary(interceptor, failing_method(ValueError("x")), MagicMock(), ctx)
    # ValueError not mapped (defaults disabled) -> INTERNAL
    assert ctx.abort.call_args.args[0] == grpc.StatusCode.INTERNAL


async def test__exception_handler__custom_detail_factory__used_for_abort_details(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
) -> None:
    # Arrange
    interceptor = AsyncExceptionHandlerInterceptor(detail_factory=lambda exc, _status: f"detail:{type(exc).__name__}")
    ctx = make_context()

    # Act & Assert
    with pytest.raises(grpc.aio.AbortError):
        await run_unary(interceptor, failing_method(ValueError("x")), MagicMock(), ctx)
    assert ctx.abort.call_args.args[1] == "detail:ValueError"


async def test__exception_handler__exception_subclass__resolved_via_mro(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
) -> None:
    # Arrange
    class MyValueError(ValueError):
        pass

    interceptor = AsyncExceptionHandlerInterceptor()
    ctx = make_context()

    # Act & Assert
    with pytest.raises(grpc.aio.AbortError):
        await run_unary(interceptor, failing_method(MyValueError("x")), MagicMock(), ctx)
    assert ctx.abort.call_args.args[0] == grpc.StatusCode.INVALID_ARGUMENT


async def test__exception_handler__abort_itself_raises__propagates_that_failure(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
) -> None:
    # Arrange
    # A context whose abort() itself blows up: the failure surfaces instead of hanging.
    interceptor = AsyncExceptionHandlerInterceptor()
    ctx = make_context()
    ctx.abort.side_effect = RuntimeError("context closed")

    # Act & Assert
    with pytest.raises(RuntimeError, match="context closed"):
        await run_unary(interceptor, failing_method(ValueError("x")), MagicMock(), ctx)


async def test__exception_handler__abort_returns_instead_of_raising__raises_runtimeerror(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
) -> None:
    # Arrange
    # abort() must never return; a non-conforming context triggers the guard.
    interceptor = AsyncExceptionHandlerInterceptor()
    ctx = make_context()
    ctx.abort.side_effect = None
    ctx.abort.return_value = None

    # Act & Assert
    with pytest.raises(RuntimeError, match="returned instead of raising"):
        await run_unary(interceptor, failing_method(ValueError("x")), MagicMock(), ctx)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (grpc.StatusCode.INVALID_ARGUMENT, GRPC_SAFE_ERROR_MESSAGES[grpc.StatusCode.INVALID_ARGUMENT]),
        # A status code with no configured safe message falls back to the generic one.
        (grpc.StatusCode.CANCELLED, "Request processing failed"),
    ],
)
def test__default_error_detail__status_code__returns_safe_or_fallback_message(
    status: grpc.StatusCode,
    expected: str,
) -> None:
    # Act
    detail = default_error_detail(ValueError(), status)

    # Assert
    assert detail == expected
