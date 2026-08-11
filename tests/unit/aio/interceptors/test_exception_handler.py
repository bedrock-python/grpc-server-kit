"""Tests for the exception-handler interceptor (exception -> gRPC status mapping)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import grpc
import pytest
from pytest_lazy_fixtures import lf

from grpc_server_kit.aio.interceptors.exception_handler import (
    GRPC_SAFE_ERROR_MESSAGES,
    AsyncExceptionHandlerInterceptor,
    default_error_detail,
)

from .conftest import RESPONSE, FailingMethodFactory, MakeContext, MakeRpcError, OkMethod, RunUnary

pytestmark = pytest.mark.unit


@pytest.fixture
def rpc_error(make_rpc_error: MakeRpcError) -> grpc.RpcError:
    """A bare RpcError, e.g. one surfaced by an upstream call — not this interceptor's concern."""
    return make_rpc_error()


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
        pytest.param(lf("rpc_error"), id="rpc_error"),
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
