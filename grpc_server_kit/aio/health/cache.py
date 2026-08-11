"""Health status cache (async)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from grpc_health.v1 import health_pb2

from grpc_server_kit.constants import DEFAULT_HEALTH_CACHE_TTL

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class HealthCache:
    """TTL cache for health status with single-flight semantics.

    :meth:`get_or_set` returns the cached status while it is fresh; on a cache
    miss it runs ``check_func`` under a lock (double-checked locking), so only
    one concurrent task performs the actual health check.

    Note: This cache is designed for single event loop usage. Do not share
    instances across different event loops.
    """

    def __init__(self, ttl: float = DEFAULT_HEALTH_CACHE_TTL) -> None:
        """Initialize health cache.

        Args:
            ttl: Time-to-live for cached status in seconds. Must be positive.

        Raises:
            ValueError: If ttl is not positive.
        """
        if ttl <= 0:
            raise ValueError(f"TTL must be positive, got {ttl}")
        self._ttl = ttl
        self._status: health_pb2.HealthCheckResponse.ServingStatus | None = None
        self._last_check: float = 0.0
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        # Created lazily so the cache can be constructed outside the event loop.
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @staticmethod
    def _now(now: float | None) -> float:
        return now if now is not None else asyncio.get_running_loop().time()

    def _get_fresh(self, now: float) -> health_pb2.HealthCheckResponse.ServingStatus | None:
        if self._status is None:
            return None
        if (now - self._last_check) < self._ttl:
            return self._status
        return None

    async def get_or_set(
        self,
        check_func: Callable[[], Awaitable[health_pb2.HealthCheckResponse.ServingStatus]],
        now: float | None = None,
    ) -> health_pb2.HealthCheckResponse.ServingStatus:
        """Get the cached status or perform the check and cache the result.

        The clock is re-read after waiting on the lock and again after the
        check completes: waiters queued behind a slow check must validate
        freshness against the check's COMPLETION time, otherwise a check
        slower than the TTL produces entries that are expired at birth and the
        cache degenerates into serialized re-checks.

        Args:
            check_func: Coroutine function performing the actual health check.
            now: Optional fixed time (event-loop clock) — intended for tests.
                When omitted, the clock is re-read at each step.
        """
        if (cached := self._get_fresh(self._now(now))) is not None:
            return cached

        async with self._get_lock():
            if (cached := self._get_fresh(self._now(now))) is not None:
                return cached

            status = await check_func()
            self._status = status
            # Stamp AFTER the check so the entry's age starts at completion.
            self._last_check = self._now(now)
            return status
