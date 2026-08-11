"""Tests for overall health-check orchestration."""

from __future__ import annotations

import asyncio

import pytest
from grpc_health.v1 import health_pb2
from pytest_lazy_fixtures import lf

from grpc_server_kit.aio.health.cache import HealthCache
from grpc_server_kit.aio.health.orchestrator import _perform_checks, check_async_overall_health

from .conftest import FakeHealthChecker

pytestmark = pytest.mark.unit

SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING


@pytest.mark.parametrize("checkers", [None, []], ids=["none", "empty_list"])
async def test__check_async_overall_health__no_checkers__returns_serving(
    checkers: list[FakeHealthChecker] | None,
) -> None:
    # Act
    result = await check_async_overall_health(checkers=checkers)

    # Assert
    assert result == SERVING


@pytest.mark.parametrize(
    ("checkers", "expected"),
    [
        ([lf("healthy_checker"), lf("healthy_checker")], SERVING),
        ([lf("healthy_checker"), lf("failing_checker")], NOT_SERVING),
    ],
    ids=["all_healthy", "one_failing"],
)
async def test__check_async_overall_health__checker_results__aggregates_status(
    checkers: list[FakeHealthChecker],
    expected: health_pb2.HealthCheckResponse.ServingStatus,
) -> None:
    # Act
    result = await check_async_overall_health(checkers=checkers)

    # Assert
    assert result == expected


async def test__check_async_overall_health__checker_raises_unexpected_error__returns_not_serving() -> None:
    # Arrange
    checker = FakeHealthChecker(exc=RuntimeError("boom"))

    # Act
    result = await check_async_overall_health(checkers=[checker])

    # Assert
    assert result == NOT_SERVING


async def test__check_async_overall_health__cache_provided__returns_serving(
    healthy_checker: FakeHealthChecker,
) -> None:
    # Arrange
    cache = HealthCache(ttl=10)

    # Act
    result = await check_async_overall_health(checkers=[healthy_checker], cache=cache)

    # Assert
    assert result == SERVING


async def test__check_async_overall_health__checks_exceed_timeout__returns_not_serving() -> None:
    # Arrange
    class _SlowChecker:
        async def check(self) -> bool:
            await asyncio.sleep(1)
            return True

    # Act
    result = await check_async_overall_health(checkers=[_SlowChecker()], timeout=0.01)

    # Assert
    assert result == NOT_SERVING


async def test__perform_checks__checker_raises_base_exception__propagates() -> None:
    # Arrange
    class _CancellingChecker:
        async def check(self) -> bool:
            raise asyncio.CancelledError

    # Act & Assert
    with pytest.raises(asyncio.CancelledError):
        await _perform_checks([_CancellingChecker()], timeout=1.0)
