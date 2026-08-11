"""Tests for gRPC interceptor status-code utilities."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import grpc
import pytest

from grpc_server_kit.interceptors.utils import is_server_error, resolve_status_code

pytestmark = pytest.mark.unit


class _RpcError(grpc.RpcError):
    """Client-style ``RpcError`` exposing a fixed status code."""

    def __init__(self, code: grpc.StatusCode) -> None:
        self._code = code

    def code(self) -> grpc.StatusCode:
        return self._code


@pytest.fixture
def make_rpc_error() -> Callable[[grpc.StatusCode], grpc.RpcError]:
    """Factory building an ``RpcError`` whose ``code()`` returns the given status."""

    def _make(code: grpc.StatusCode) -> grpc.RpcError:
        return _RpcError(code)

    return _make


@pytest.fixture
def make_status_context() -> Callable[[grpc.StatusCode | None], MagicMock]:
    """Factory building a mock servicer context whose ``code()`` returns the given status."""

    def _make(code: grpc.StatusCode | None) -> MagicMock:
        ctx = MagicMock()
        ctx.code.return_value = code
        return ctx

    return _make


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.INTERNAL, True),
        (grpc.StatusCode.UNKNOWN, True),
        (grpc.StatusCode.DATA_LOSS, True),
        (grpc.StatusCode.UNIMPLEMENTED, True),
        (grpc.StatusCode.UNAVAILABLE, True),
        (grpc.StatusCode.DEADLINE_EXCEEDED, True),
        (grpc.StatusCode.OK, False),
        (grpc.StatusCode.INVALID_ARGUMENT, False),
        (grpc.StatusCode.NOT_FOUND, False),
        (grpc.StatusCode.PERMISSION_DENIED, False),
    ],
)
def test__is_server_error__status_code__classifies_server_vs_client_errors(
    code: grpc.StatusCode,
    expected: bool,
) -> None:
    # Act
    result = is_server_error(code)

    # Assert
    assert result is expected


def test__resolve_status_code__exception_and_context_disagree__prefers_exception_code(
    make_rpc_error: Callable[[grpc.StatusCode], grpc.RpcError],
    make_status_context: Callable[[grpc.StatusCode | None], MagicMock],
) -> None:
    # Arrange
    exc = make_rpc_error(grpc.StatusCode.NOT_FOUND)
    context = make_status_context(grpc.StatusCode.INTERNAL)

    # Act
    result = resolve_status_code(exc, context)

    # Assert
    assert result is grpc.StatusCode.NOT_FOUND


def test__resolve_status_code__exception_code_unknown__recovers_from_context(
    make_rpc_error: Callable[[grpc.StatusCode], grpc.RpcError],
    make_status_context: Callable[[grpc.StatusCode | None], MagicMock],
) -> None:
    # Arrange
    exc = make_rpc_error(grpc.StatusCode.UNKNOWN)
    context = make_status_context(grpc.StatusCode.NOT_FOUND)

    # Act
    result = resolve_status_code(exc, context)

    # Assert
    assert result is grpc.StatusCode.NOT_FOUND


@pytest.mark.parametrize("context_code", [None, grpc.StatusCode.OK])
def test__resolve_status_code__exception_code_unknown_and_context_has_nothing_better__keeps_unknown(
    make_rpc_error: Callable[[grpc.StatusCode], grpc.RpcError],
    make_status_context: Callable[[grpc.StatusCode | None], MagicMock],
    context_code: grpc.StatusCode | None,
) -> None:
    # Arrange
    exc = make_rpc_error(grpc.StatusCode.UNKNOWN)
    context = make_status_context(context_code)

    # Act
    result = resolve_status_code(exc, context)

    # Assert
    assert result is grpc.StatusCode.UNKNOWN


def test__resolve_status_code__no_exception__falls_back_to_context_code(
    make_status_context: Callable[[grpc.StatusCode | None], MagicMock],
) -> None:
    # Arrange
    context = make_status_context(grpc.StatusCode.PERMISSION_DENIED)

    # Act
    result = resolve_status_code(None, context)

    # Assert
    assert result is grpc.StatusCode.PERMISSION_DENIED


def test__resolve_status_code__context_ok_and_no_exception__returns_ok(
    make_status_context: Callable[[grpc.StatusCode | None], MagicMock],
) -> None:
    # Arrange
    context = make_status_context(grpc.StatusCode.OK)

    # Act
    result = resolve_status_code(None, context, default=grpc.StatusCode.OK)

    # Assert
    assert result is grpc.StatusCode.OK


@pytest.mark.parametrize(
    ("default", "expected"),
    [
        (grpc.StatusCode.UNKNOWN, grpc.StatusCode.UNKNOWN),
        (grpc.StatusCode.INTERNAL, grpc.StatusCode.INTERNAL),
    ],
)
def test__resolve_status_code__nothing_available__returns_default(
    make_status_context: Callable[[grpc.StatusCode | None], MagicMock],
    default: grpc.StatusCode,
    expected: grpc.StatusCode,
) -> None:
    # Arrange
    context = make_status_context(None)

    # Act
    result = resolve_status_code(None, context, default=default)

    # Assert
    assert result is expected


def test__resolve_status_code__exception_missing_code_method__returns_unknown(
    make_status_context: Callable[[grpc.StatusCode | None], MagicMock],
) -> None:
    # Arrange
    context = make_status_context(None)

    # Act
    result = resolve_status_code(RuntimeError("boom"), context)

    # Assert
    assert result is grpc.StatusCode.UNKNOWN


def test__resolve_status_code__context_missing_code_attribute__returns_unknown() -> None:
    # Arrange
    class _NoCode:
        """Context-like object exposing no ``code`` attribute at all."""

    # Act
    result = resolve_status_code(None, _NoCode())

    # Assert
    assert result is grpc.StatusCode.UNKNOWN


def test__resolve_status_code__context_code_raises__falls_back_to_default() -> None:
    # Arrange
    context = MagicMock()
    context.code.side_effect = RuntimeError("broken context")

    # Act
    result = resolve_status_code(None, context)

    # Assert
    assert result is grpc.StatusCode.UNKNOWN


def test__resolve_status_code__exception_code_not_a_status__is_ignored(
    make_status_context: Callable[[grpc.StatusCode | None], MagicMock],
) -> None:
    # Arrange
    exc = MagicMock()
    exc.code.return_value = "not-a-status"
    context = make_status_context(None)

    # Act
    result = resolve_status_code(exc, context)

    # Assert
    assert result is grpc.StatusCode.UNKNOWN
