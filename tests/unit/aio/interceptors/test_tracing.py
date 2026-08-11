"""Tests for the tracing interceptor (OpenTelemetry-compatible seam)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import grpc
import pytest
from pytest_lazy_fixtures import lf

from grpc_server_kit.aio.interceptors.tracing import AsyncTracingInterceptor
from grpc_server_kit.interceptors.constants import (
    OTEL_REQUEST_ID,
    OTEL_RPC_GRPC_STATUS_CODE,
    OTEL_RPC_METHOD,
    OTEL_RPC_SERVICE,
)

from .conftest import RESPONSE, FailingMethodFactory, MakeContext, MakeRpcError, OkMethod, RunUnary, make_call

pytestmark = pytest.mark.unit


def _make_tracer() -> tuple[MagicMock, MagicMock]:
    span = MagicMock()
    tracer = MagicMock()
    cm = tracer.start_as_current_span.return_value
    cm.__enter__.return_value = span
    cm.__exit__.return_value = None
    return tracer, span


def _attrs(span: MagicMock) -> dict[str, Any]:
    return {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}


@pytest.fixture
def rpc_error_internal(make_rpc_error: MakeRpcError) -> grpc.RpcError:
    """RpcError double reporting INTERNAL — a server-side failure."""
    return make_rpc_error(grpc.StatusCode.INTERNAL)


@pytest.fixture
def rpc_error_not_found(make_rpc_error: MakeRpcError) -> grpc.RpcError:
    """RpcError double reporting NOT_FOUND — a client-side failure."""
    return make_rpc_error(grpc.StatusCode.NOT_FOUND)


def test__tracing_interceptor__empty_service_name__raises() -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="service_name cannot be empty"):
        AsyncTracingInterceptor(service_name="", tracer=MagicMock())


async def test__tracing_interceptor__no_tracer_configured__returns_response(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    interceptor = AsyncTracingInterceptor(service_name="svc", tracer=None)

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), make_context())

    # Assert
    assert result == RESPONSE


async def test__tracing_interceptor__success__sets_semconv_attributes(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    tracer, span = _make_tracer()
    interceptor = AsyncTracingInterceptor(service_name="fallback", tracer=tracer)
    ctx = make_context(metadata={"x-request-id": "req-1"})

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), ctx, "/pkg.Svc/M")

    # Assert
    assert result == RESPONSE
    tracer.start_as_current_span.assert_called_once_with("pkg.Svc/M")
    attrs = _attrs(span)
    assert attrs[OTEL_RPC_SERVICE] == "pkg.Svc"
    assert attrs[OTEL_RPC_METHOD] == "M"
    assert attrs[OTEL_RPC_GRPC_STATUS_CODE] == grpc.StatusCode.OK.value[0]
    assert attrs[OTEL_REQUEST_ID] == "req-1"


@pytest.mark.parametrize(
    ("exc", "context_code", "expected_status", "expect_recorded"),
    [
        pytest.param(lf("rpc_error_internal"), None, grpc.StatusCode.INTERNAL, True, id="rpc_error_server_side"),
        pytest.param(lf("rpc_error_not_found"), None, grpc.StatusCode.NOT_FOUND, False, id="rpc_error_client_side"),
        pytest.param(
            grpc.aio.AbortError(),
            grpc.StatusCode.PERMISSION_DENIED,
            grpc.StatusCode.PERMISSION_DENIED,
            False,
            # AbortError with no cause chain: nothing to recover, nothing to record.
            id="abort_error_uses_context_code",
        ),
        pytest.param(RuntimeError("boom"), None, grpc.StatusCode.INTERNAL, True, id="unexpected_exception"),
    ],
)
async def test__tracing_interceptor__failed_rpc__records_exception_for_server_errors_only(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
    exc: BaseException,
    context_code: grpc.StatusCode | None,
    expected_status: grpc.StatusCode,
    expect_recorded: bool,
) -> None:
    # Arrange
    tracer, span = _make_tracer()
    interceptor = AsyncTracingInterceptor(service_name="svc", tracer=tracer)
    ctx = make_context(code=context_code)

    # Act & Assert
    with pytest.raises(type(exc)):
        await run_unary(interceptor, failing_method(exc), MagicMock(), ctx)
    assert _attrs(span)[OTEL_RPC_GRPC_STATUS_CODE] == expected_status.value[0]
    if expect_recorded:
        span.record_exception.assert_called_once_with(exc)
    else:
        span.record_exception.assert_not_called()


async def test__tracing_interceptor__abort_error_with_original_cause__records_original_exception(
    make_context: MakeContext,
    run_unary: RunUnary,
) -> None:
    # Arrange
    # An inner exception handler maps RuntimeError -> abort(INTERNAL); the span
    # must still carry the ORIGINAL exception, recovered from AbortError.__context__.
    tracer, span = _make_tracer()
    interceptor = AsyncTracingInterceptor(service_name="svc", tracer=tracer)
    ctx = make_context(code=grpc.StatusCode.INTERNAL)
    original = RuntimeError("boom")

    async def aborting_method(_request: object, _context: object) -> object:
        try:
            raise original
        except RuntimeError:
            raise grpc.aio.AbortError from None  # __context__ keeps the original

    # Act & Assert
    with pytest.raises(grpc.aio.AbortError):
        await run_unary(interceptor, aborting_method, MagicMock(), ctx)
    assert _attrs(span)[OTEL_RPC_GRPC_STATUS_CODE] == grpc.StatusCode.INTERNAL.value[0]
    span.record_exception.assert_called_once_with(original)


async def test__tracing_interceptor__health_method__span_not_created(make_context: MakeContext) -> None:
    # Arrange
    tracer, span = _make_tracer()
    interceptor = AsyncTracingInterceptor(service_name="svc", tracer=tracer)
    call = make_call(make_context(), method_name="/grpc.health.v1.Health/Check")

    # Act
    async with interceptor.around(call):
        pass

    # Assert
    tracer.start_as_current_span.assert_not_called()
    span.set_attribute.assert_not_called()
