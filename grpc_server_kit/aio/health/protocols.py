"""Protocols for health checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from grpc_health.v1 import health_pb2


class AsyncHealthChecker(Protocol):
    """Protocol for component health checks (DB, Redis, external service, ...).

    One shape (``async check() -> bool``) serves the kit's gRPC health servicer
    and any external health registry alike.
    """

    async def check(self) -> bool:
        """Check component health. Returns True if healthy."""
        ...


class AsyncHealthCacheProtocol(Protocol):
    """Protocol for a health status cache with TTL support.

    Implementations must be safe for concurrent coroutine access and must
    ensure that only one concurrent task performs the underlying check on a
    cache miss (double-checked locking or equivalent).

    Note: Cache instances should not be shared across different event loops.
    """

    async def get_or_set(
        self,
        check_func: Callable[[], Awaitable[health_pb2.HealthCheckResponse.ServingStatus]],
        now: float | None = None,
    ) -> health_pb2.HealthCheckResponse.ServingStatus:
        """Return the cached health status or perform the check and cache the result."""
        ...


__all__ = ["AsyncHealthCacheProtocol", "AsyncHealthChecker"]
