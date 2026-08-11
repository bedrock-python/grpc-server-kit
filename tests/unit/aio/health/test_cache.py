"""Tests for the TTL health-status cache."""

from __future__ import annotations

import asyncio

import pytest
from grpc_health.v1 import health_pb2
from pytest_lazy_fixtures import lf

from grpc_server_kit.aio.health.cache import HealthCache

pytestmark = pytest.mark.unit

SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING

TTL = 0.05


class RecordingCheck:
    """Health check recording how many times it ran."""

    def __init__(self, *, duration: float = 0.0, statuses: list[int] | None = None) -> None:
        self._duration = duration
        self._statuses = statuses
        self.calls = 0

    async def __call__(self) -> health_pb2.HealthCheckResponse.ServingStatus:
        self.calls += 1
        if self._duration:
            await asyncio.sleep(self._duration)
        if self._statuses:
            return self._statuses.pop(0)
        return SERVING


@pytest.fixture
def cache() -> HealthCache:
    return HealthCache(ttl=TTL)


@pytest.fixture
def instant_check() -> RecordingCheck:
    """Check that completes well within the TTL."""
    return RecordingCheck()


@pytest.fixture
def slow_check() -> RecordingCheck:
    """Check that takes longer than the TTL to complete."""
    return RecordingCheck(duration=TTL * 3)


def test__health_cache__non_positive_ttl__raises() -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="TTL must be positive"):
        HealthCache(ttl=0)


async def test__health_cache__first_call__runs_check(cache: HealthCache, instant_check: RecordingCheck) -> None:
    # Act
    status = await cache.get_or_set(instant_check)

    # Assert
    assert status == SERVING
    assert instant_check.calls == 1


async def test__health_cache__second_call_within_ttl__serves_from_cache(
    cache: HealthCache,
    instant_check: RecordingCheck,
) -> None:
    # Arrange
    await cache.get_or_set(instant_check)

    # Act
    status = await cache.get_or_set(instant_check)

    # Assert
    assert status == SERVING
    assert instant_check.calls == 1


async def test__health_cache__call_after_ttl_expiry__runs_check_again(cache: HealthCache) -> None:
    # Arrange
    check = RecordingCheck(statuses=[SERVING, NOT_SERVING])
    assert await cache.get_or_set(check) == SERVING

    # Act
    await asyncio.sleep(TTL * 2)
    status = await cache.get_or_set(check)

    # Assert
    assert status == NOT_SERVING
    assert check.calls == 2


@pytest.mark.parametrize("check", [lf("instant_check"), lf("slow_check")])
async def test__health_cache__concurrent_callers__runs_check_once(cache: HealthCache, check: RecordingCheck) -> None:
    # Act
    results = await asyncio.gather(*(cache.get_or_set(check) for _ in range(10)))

    # Assert
    assert results == [SERVING] * 10
    assert check.calls == 1


async def test__health_cache__check_slower_than_ttl__entry_is_fresh_at_completion(
    cache: HealthCache,
    slow_check: RecordingCheck,
) -> None:
    # Arrange
    await cache.get_or_set(slow_check)

    # Act
    status = await cache.get_or_set(slow_check)

    # Assert
    assert status == SERVING
    assert slow_check.calls == 1


async def test__health_cache__waiters_behind_slow_check__reuse_its_result(
    cache: HealthCache,
    slow_check: RecordingCheck,
) -> None:
    # Arrange
    first = asyncio.create_task(cache.get_or_set(slow_check))
    await asyncio.sleep(TTL * 1.5)

    # Act
    second = await cache.get_or_set(slow_check)

    # Assert
    assert await first == SERVING
    assert second == SERVING
    assert slow_check.calls == 1
