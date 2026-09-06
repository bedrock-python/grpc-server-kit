"""Tests for the context interceptor (header metadata to context-variable binding)."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock, patch

import grpc
import pytest

from grpc_server_kit.aio.interceptors.context import AsyncContextInterceptor, HeaderConfig
from grpc_server_kit.interceptors.constants import SKIPPED_HEALTH_METHODS

from .conftest import RESPONSE, MakeContext, OkMethod, RunUnary

pytestmark = pytest.mark.unit

HEALTH_CHECK_METHOD = "/grpc.health.v1.Health/Check"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        pytest.param({"header_name": "", "context_var_name": "x"}, "header_name cannot be empty", id="header_name"),
        pytest.param(
            {"header_name": "x", "context_var_name": ""}, "context_var_name cannot be empty", id="context_var_name"
        ),
    ],
)
def test__header_config__required_field_empty__raises(kwargs: dict[str, str], match: str) -> None:
    # Act & Assert
    with pytest.raises(ValueError, match=match):
        HeaderConfig(**kwargs)


async def test__context_interceptor__header_present__returns_response(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    config = HeaderConfig(header_name="x-req", context_var_name="request_id")
    interceptor = AsyncContextInterceptor([config], bind_structlog=False)
    ctx = make_context(metadata={"x-req": "r1"})

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), ctx)

    # Assert
    assert result == RESPONSE


async def test__context_interceptor__context_setter_configured__calls_setter_then_remover(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    calls: list[tuple[str, str]] = []

    def setter(value: str) -> Callable[[], None]:
        calls.append(("set", value))

        def remover() -> None:
            calls.append(("remove", value))

        return remover

    config = HeaderConfig(header_name="x-req", context_var_name="request_id", context_setter=setter)
    interceptor = AsyncContextInterceptor([config], bind_structlog=False)

    # Act
    await run_unary(interceptor, ok_method, MagicMock(), make_context(metadata={"x-req": "r1"}))

    # Assert
    assert calls == [("set", "r1"), ("remove", "r1")]


async def test__context_interceptor__header_missing_with_default_factory__binds_default_value(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    seen: list[str] = []
    config = HeaderConfig(
        header_name="x-req",
        context_var_name="request_id",
        default_factory=lambda: "generated",
        context_setter=seen.append,
    )
    interceptor = AsyncContextInterceptor([config], bind_structlog=False)

    # Act
    await run_unary(interceptor, ok_method, MagicMock(), make_context(metadata={}))

    # Assert
    assert seen == ["generated"]


async def test__context_interceptor__optional_header_missing__skipped_without_abort(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    config = HeaderConfig(header_name="x-req", context_var_name="req", required=False)
    interceptor = AsyncContextInterceptor([config], bind_structlog=False)
    ctx = make_context(metadata={})

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), ctx)

    # Assert
    assert result == RESPONSE
    ctx.abort.assert_not_called()


@pytest.mark.parametrize(
    ("config_kwargs", "metadata"),
    [
        pytest.param({"required": True}, {}, id="missing"),
        pytest.param({"required": True, "allow_empty": False}, {"x-req": ""}, id="empty"),
        pytest.param({"required": True, "validator": lambda _v: False}, {"x-req": "bad"}, id="fails_validator"),
    ],
)
async def test__context_interceptor__required_header_invalid__aborts_invalid_argument(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
    config_kwargs: dict[str, object],
    metadata: dict[str, str],
) -> None:
    # Arrange
    config = HeaderConfig(header_name="x-req", context_var_name="req", **config_kwargs)
    interceptor = AsyncContextInterceptor([config], bind_structlog=False)
    ctx = make_context(metadata=metadata)

    # Act & Assert
    with pytest.raises(grpc.aio.AbortError):
        await run_unary(interceptor, ok_method, MagicMock(), ctx)
    assert ctx.abort.call_args.args[0] == grpc.StatusCode.INVALID_ARGUMENT


async def test__context_interceptor__bind_structlog_true__binds_then_unbinds_contextvars(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    mock_structlog = MagicMock()
    config = HeaderConfig(header_name="x-req", context_var_name="request_id")
    interceptor = AsyncContextInterceptor([config], bind_structlog=True)
    ctx = make_context(metadata={"x-req": "r1"})

    # Act
    with patch("grpc_server_kit.aio.interceptors.context.structlog", mock_structlog):
        await run_unary(interceptor, ok_method, MagicMock(), ctx)

    # Assert
    mock_structlog.contextvars.bind_contextvars.assert_called_once()
    mock_structlog.contextvars.unbind_contextvars.assert_called_once()


async def test__context_interceptor__remover_raises__request_still_succeeds(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    def setter(_value: str) -> Callable[[], None]:
        def remover() -> None:
            raise RuntimeError("cleanup failed")

        return remover

    config = HeaderConfig(header_name="x-req", context_var_name="req", context_setter=setter)
    interceptor = AsyncContextInterceptor([config], bind_structlog=False)

    # Act
    # The request still completes even though the remover raised.
    result = await run_unary(interceptor, ok_method, MagicMock(), make_context(metadata={"x-req": "r1"}))

    # Assert
    assert result == RESPONSE


async def test__context_interceptor__skipped_method__does_not_bind_context(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    bound: list[str] = []
    config = HeaderConfig(header_name="x-req", context_var_name="req", context_setter=bound.append)
    interceptor = AsyncContextInterceptor([config], bind_structlog=False, skip_methods=SKIPPED_HEALTH_METHODS)

    # Act
    await run_unary(
        interceptor,
        ok_method,
        MagicMock(),
        make_context(metadata={"x-req": "r1"}),
        HEALTH_CHECK_METHOD,
    )

    # Assert
    assert bound == []


async def test__context_interceptor__skipped_method_missing_required_header__does_not_abort(
    make_context: MakeContext,
    ok_method: OkMethod,
    run_unary: RunUnary,
) -> None:
    # Arrange
    config = HeaderConfig(header_name="x-req", context_var_name="req", required=True)
    interceptor = AsyncContextInterceptor([config], bind_structlog=False, skip_methods=SKIPPED_HEALTH_METHODS)

    # Act
    result = await run_unary(interceptor, ok_method, MagicMock(), make_context(), HEALTH_CHECK_METHOD)

    # Assert
    assert result == RESPONSE
