"""Tests for the metrics interceptor (backend-neutral recorder seam)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import grpc
import pytest
from pytest_lazy_fixtures import lf

from grpc_server_kit.aio.interceptors.metrics import AsyncMetricsInterceptor

from .conftest import RESPONSE, FailingMethodFactory, MakeContext, MakeRpcError, OkMethod, RunUnary

pytestmark = pytest.mark.unit


@pytest.fixture
def rpc_error_not_found(make_rpc_error: MakeRpcError) -> grpc.RpcError:
    """RpcError double reporting NOT_FOUND, e.g. surfaced by a downstream client call."""
    return make_rpc_error(grpc.StatusCode.NOT_FOUND)


def test__metrics_interceptor__empty_service_name__raises() -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="service_name cannot be empty"):
        AsyncMetricsInterceptor(metrics=MagicMock(), service_name="")


async def test__metrics_interceptor__no_metrics_configured__returns_response(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    interceptor = AsyncMetricsInterceptor(metrics=None, service_name="svc")

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), make_context())

    # Assert
    assert result == RESPONSE


async def test__metrics_interceptor__health_method__not_recorded(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    metrics = MagicMock()
    interceptor = AsyncMetricsInterceptor(metrics=metrics, service_name="svc")

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), make_context(), "/grpc.health.v1.Health/Check")

    # Assert
    assert result == RESPONSE
    metrics.record_request.assert_not_called()


async def test__metrics_interceptor__success__records_success_status(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    metrics = MagicMock()
    interceptor = AsyncMetricsInterceptor(metrics=metrics, service_name="svc")

    # Act
    await run_unary(interceptor, ok_method, MagicMock(), make_context())

    # Assert
    metrics.record_request.assert_called_once()
    kwargs = metrics.record_request.call_args.kwargs
    assert kwargs["status"] == "success"
    assert kwargs["grpc_code"] == grpc.StatusCode.OK.name
    assert kwargs["service"] == "svc"


@pytest.mark.parametrize(
    ("exc", "context_code", "expected_code"),
    [
        pytest.param(lf("rpc_error_not_found"), None, grpc.StatusCode.NOT_FOUND, id="rpc_error"),
        pytest.param(
            grpc.aio.AbortError(),
            grpc.StatusCode.PERMISSION_DENIED,
            grpc.StatusCode.PERMISSION_DENIED,
            # Deliberate abort: the context (not the exception) carries the real status.
            id="abort_error",
        ),
        pytest.param(asyncio.CancelledError(), None, grpc.StatusCode.CANCELLED, id="cancelled"),
        # An exception with no explicit status at all is an internal error.
        pytest.param(RuntimeError("boom"), None, grpc.StatusCode.INTERNAL, id="unexpected_exception"),
    ],
)
async def test__metrics_interceptor__failed_rpc__records_error_status(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
    exc: BaseException,
    context_code: grpc.StatusCode | None,
    expected_code: grpc.StatusCode,
) -> None:
    # Arrange
    metrics = MagicMock()
    interceptor = AsyncMetricsInterceptor(metrics=metrics, service_name="svc")
    ctx = make_context(code=context_code)

    # Act & Assert
    with pytest.raises(type(exc)):
        await run_unary(interceptor, failing_method(exc), MagicMock(), ctx)
    kwargs = metrics.record_request.call_args.kwargs
    assert kwargs["status"] == "error"
    assert kwargs["grpc_code"] == expected_code.name


async def test__metrics_interceptor__record_request_raises__request_still_succeeds(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    metrics = MagicMock()
    metrics.record_request.side_effect = RuntimeError("metrics backend down")
    interceptor = AsyncMetricsInterceptor(metrics=metrics, service_name="svc")

    # Act
    # The request still succeeds even though recording the metric failed.
    result = await run_unary(interceptor, ok_method, MagicMock(), make_context())

    # Assert
    assert result == RESPONSE
