"""Tests for the Postgres and Redis health checkers."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from unittest.mock import patch

import pytest
from pytest_lazy_fixtures import lf

from grpc_server_kit.aio.health.checkers.postgres import DatabaseHealthChecker, check_async_database_health
from grpc_server_kit.aio.health.checkers.redis import RedisHealthChecker, check_async_redis_health
from grpc_server_kit.aio.health.checkers.utils import FunctionalHealthChecker, handle_check_exceptions

pytestmark = pytest.mark.unit


def test__functional_health_checker__non_positive_timeout__raises() -> None:
    async def _check(_resource: object, timeout: float = 1.0) -> bool:
        return True

    # Act & Assert
    with pytest.raises(ValueError, match="timeout must be positive"):
        FunctionalHealthChecker(_check, resource=None, timeout=0)


async def test__handle_check_exceptions__check_succeeds__returns_result() -> None:
    @handle_check_exceptions("Test")
    async def _check(_resource: object, timeout: float = 1.0) -> bool:
        return True

    # Act
    result = await _check(None)

    # Assert
    assert result is True


@pytest.mark.parametrize(
    "raised",
    [TimeoutError(), ConnectionError("down"), RuntimeError("boom")],
    ids=["timeout", "connection_error", "unexpected_error"],
)
async def test__handle_check_exceptions__check_raises_handled_error__returns_false(raised: Exception) -> None:
    @handle_check_exceptions("Test")
    async def _check(_resource: object, timeout: float = 1.0) -> bool:
        raise raised

    # Act
    result = await _check(None)

    # Assert
    assert result is False


async def test__handle_check_exceptions__check_raises_cancelled_error__reraises() -> None:
    @handle_check_exceptions("Test")
    async def _check(_resource: object, timeout: float = 1.0) -> bool:
        raise asyncio.CancelledError

    # Act & Assert
    with pytest.raises(asyncio.CancelledError):
        await _check(None)


class _FakeSession:
    def __init__(self, exc: BaseException | None = None) -> None:
        self._exc = exc

    async def execute(self, _stmt: object) -> object:
        if self._exc is not None:
            raise self._exc
        return object()


class _HangingSession:
    async def execute(self, _stmt: object) -> object:
        await asyncio.sleep(30)
        return object()


def _session_maker(session: _FakeSession) -> Callable[[], AbstractAsyncContextManager[_FakeSession]]:
    @contextlib.asynccontextmanager
    async def _maker() -> AsyncIterator[_FakeSession]:
        yield session

    return _maker


async def test__database_health_checker__healthy_session__returns_true() -> None:
    # Arrange
    checker = DatabaseHealthChecker(session_maker=_session_maker(_FakeSession()))

    # Act
    result = await checker.check()

    # Assert
    assert result is True


async def test__check_async_database_health__no_session_maker__returns_true() -> None:
    # Act
    result = await check_async_database_health(None)

    # Assert
    assert result is True


async def test__check_async_database_health__execute_raises__returns_false() -> None:
    # Arrange
    session_maker = _session_maker(_FakeSession(exc=ConnectionError("db down")))

    # Act
    result = await check_async_database_health(session_maker)

    # Assert
    assert result is False


async def test__check_async_database_health__sqlalchemy_not_installed__returns_true() -> None:
    # Act
    with patch("grpc_server_kit.aio.health.checkers.postgres.SQLALCHEMY_AVAILABLE", False):
        result = await check_async_database_health(_session_maker(_FakeSession()))

    # Assert
    assert result is True


async def test__check_async_database_health__query_exceeds_timeout__returns_false() -> None:
    # Act
    result = await check_async_database_health(_session_maker(_HangingSession()), timeout=0.05)  # type: ignore[arg-type]

    # Assert
    assert result is False


async def test__check_async_database_health__cleanup_hangs_after_success__returns_true_without_blocking() -> None:
    # Arrange
    # Session __aexit__ hanging on a dead connection must neither hold the
    # check past its budget nor convert a successful SELECT 1 into a failure.
    class _HangingCleanupCM:
        async def __aenter__(self) -> _FakeSession:
            return _FakeSession()

        async def __aexit__(self, *args: object) -> None:
            await asyncio.sleep(30)

    # Act
    with patch("grpc_server_kit.aio.health.checkers.postgres._CLEANUP_TIMEOUT", 0.05):
        result = await asyncio.wait_for(
            check_async_database_health(_HangingCleanupCM, timeout=1.0),  # type: ignore[arg-type]
            timeout=5,
        )

    # Assert
    assert result is True


class _FakeRedis:
    def __init__(self, *, result: object = True, exc: BaseException | None = None, awaitable: bool = True) -> None:
        self._result = result
        self._exc = exc
        self._awaitable = awaitable

    def ping(self, **_kwargs: object) -> object:
        if self._awaitable:

            async def _ping() -> object:
                if self._exc is not None:
                    raise self._exc
                return self._result

            return _ping()
        if self._exc is not None:
            raise self._exc
        return self._result

    async def aclose(self) -> None:
        return None


@pytest.fixture
def cluster_ping_all_up() -> dict[str, bool]:
    return {"node1": True, "node2": True}


async def test__redis_health_checker__healthy_client__returns_true() -> None:
    # Arrange
    checker = RedisHealthChecker(redis_client=_FakeRedis(result=True))

    # Act
    result = await checker.check()

    # Assert
    assert result is True


async def test__check_async_redis_health__no_client__returns_true() -> None:
    # Act
    result = await check_async_redis_health(None)

    # Assert
    assert result is True


async def test__check_async_redis_health__connection_error__returns_false() -> None:
    # Arrange
    client = _FakeRedis(exc=ConnectionError("redis down"))

    # Act
    result = await check_async_redis_health(client)

    # Assert
    assert result is False


async def test__check_async_redis_health__non_awaitable_ping__returns_true() -> None:
    # Arrange
    # A client whose ping() returns a plain value (not awaitable) is still supported.
    client = _FakeRedis(result=True, awaitable=False)

    # Act
    result = await check_async_redis_health(client)

    # Assert
    assert result is True


@pytest.mark.parametrize(
    ("ping_result", "expected"),
    [
        (lf("cluster_ping_all_up"), True),
        ({"node1": True, "node2": False}, False),
        ({"node1": False, "node2": False}, False),
        ({}, False),
    ],
    ids=["all_up", "one_node_down", "all_down", "empty"],
)
async def test__check_async_redis_health__cluster_ping_result__reports_health(
    ping_result: dict[str, bool],
    expected: bool,
) -> None:
    # Clustered clients return a dict of per-node results. A non-empty dict is
    # truthy, but a failed node must still mean unhealthy, and an empty dict
    # (no reachable nodes) is unhealthy too.
    # Arrange
    client = _FakeRedis(result=ping_result)

    # Act
    result = await check_async_redis_health(client)

    # Assert
    assert result is expected
