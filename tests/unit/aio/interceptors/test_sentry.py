"""Tests for the Sentry (error-tracking) interceptor."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import grpc
import pytest

from grpc_server_kit.aio.interceptors.sentry import AsyncSentryInterceptor

from .conftest import RESPONSE, FailingMethodFactory, MakeContext, OkMethod, RunUnary, make_call

pytestmark = pytest.mark.unit


async def test__sentry_interceptor__no_sentry_configured__returns_response(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    interceptor = AsyncSentryInterceptor(sentry=None)

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), make_context())

    # Assert
    assert result == RESPONSE


async def test__sentry_interceptor__success__sets_grpc_method_and_request_id_tags(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    sentry = MagicMock()
    interceptor = AsyncSentryInterceptor(sentry=sentry)
    ctx = make_context(metadata={"x-request-id": "req-1"})

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), ctx)

    # Assert
    assert result == RESPONSE
    sentry.set_tags.assert_called_once_with(grpc_method="/svc/M", request_id="req-1")


async def test__sentry_interceptor__success__adds_breadcrumb(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    sentry = MagicMock()
    interceptor = AsyncSentryInterceptor(sentry=sentry)
    ctx = make_context(metadata={"x-request-id": "req-1"})

    # Act
    await run_unary(interceptor, ok_method, MagicMock(), ctx)

    # Assert
    sentry.add_breadcrumb.assert_called_once()


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(grpc.RpcError(), id="rpc_error"),
        pytest.param(grpc.aio.AbortError(), id="abort_error"),
        pytest.param(asyncio.CancelledError(), id="cancelled"),
    ],
)
async def test__sentry_interceptor__non_capturable_exception__not_captured(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
    exc: BaseException,
) -> None:
    # Arrange
    sentry = MagicMock()
    interceptor = AsyncSentryInterceptor(sentry=sentry)

    # Act & Assert
    with pytest.raises(type(exc)):
        await run_unary(interceptor, failing_method(exc), MagicMock(), make_context())
    sentry.capture_exception.assert_not_called()


async def test__sentry_interceptor__unexpected_exception__captured(
    make_context: MakeContext,
    failing_method: FailingMethodFactory,
    run_unary: RunUnary,
) -> None:
    # Arrange
    sentry = MagicMock()
    interceptor = AsyncSentryInterceptor(sentry=sentry)
    exc = RuntimeError("boom")

    # Act & Assert
    with pytest.raises(RuntimeError):
        await run_unary(interceptor, failing_method(exc), MagicMock(), make_context())
    sentry.capture_exception.assert_called_once_with(exc)


async def test__sentry_interceptor__any_rpc__runs_inside_isolated_scope(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    # Tags/breadcrumbs must be written inside reporter.isolate() so concurrent
    # RPCs cannot contaminate each other's reports.
    sentry = MagicMock()
    interceptor = AsyncSentryInterceptor(sentry=sentry)

    # Act
    await run_unary(interceptor, ok_method, MagicMock(), make_context())

    # Assert
    sentry.isolate.assert_called_once()
    sentry.isolate.return_value.__enter__.assert_called_once()
    sentry.isolate.return_value.__exit__.assert_called_once()


async def test__sentry_interceptor__health_method__skipped_entirely(make_context: MakeContext) -> None:
    # Arrange
    sentry = MagicMock()
    interceptor = AsyncSentryInterceptor(sentry=sentry)
    call = make_call(make_context(), method_name="/grpc.health.v1.Health/Watch")

    # Act
    async with interceptor.around(call):
        pass

    # Assert
    sentry.set_tags.assert_not_called()
    sentry.add_breadcrumb.assert_not_called()
