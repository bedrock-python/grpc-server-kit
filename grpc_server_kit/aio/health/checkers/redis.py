"""Redis health checker (async).

The checker is fully duck-typed: it only requires the passed client to expose an
async ``ping()`` (the redis-py async interface), so this module imports nothing
Redis-specific. Install the ``[redis]`` extra to get ``redis`` for your client.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from typing import Protocol

from grpc_server_kit.constants import DEFAULT_CHECKER_TIMEOUT

from .utils import FunctionalHealthChecker, handle_check_exceptions

DEFAULT_REDIS_CHECK_TIMEOUT = DEFAULT_CHECKER_TIMEOUT


class RedisClientProtocol(Protocol):
    """Protocol for a Redis client compatible with the redis-py async interface.

    Supports both single instance and clustered Redis clients (any object exposing
    an async ``ping``).
    """

    def ping(self, **kwargs: object) -> Awaitable[bool | dict[str, bool]] | bool | dict[str, bool]:
        """Ping the Redis server to check connectivity.

        Returns:
            For a single instance: True if ping was successful.
            For clustered clients: a dict mapping node names to their ping results.
        """
        ...

    async def aclose(self) -> None:
        """Close the Redis client."""
        ...


class RedisHealthChecker(FunctionalHealthChecker):
    """Health checker for Redis connectivity.

    Performs a connectivity check by pinging Redis with a configurable timeout.
    Compatible with both single instance and clustered Redis deployments.
    """

    def __init__(self, redis_client: RedisClientProtocol, timeout: float = DEFAULT_REDIS_CHECK_TIMEOUT) -> None:
        """Initialize Redis health checker.

        Args:
            redis_client: Redis client instance implementing RedisClientProtocol.
            timeout: Maximum time to wait for Redis response in seconds.

        Raises:
            ValueError: If timeout is not positive.
        """
        super().__init__(check_async_redis_health, redis_client, timeout)


@handle_check_exceptions("Redis")
async def check_async_redis_health(
    redis_client: RedisClientProtocol | None,
    timeout: float = DEFAULT_REDIS_CHECK_TIMEOUT,
) -> bool:
    """Check async Redis connectivity with a timeout via PING.

    Args:
        redis_client: Redis client to check. If None, returns True (no Redis configured).
        timeout: Maximum time to wait for Redis response in seconds.

    Returns:
        True if Redis is healthy or redis_client is None, False otherwise.
    """
    if redis_client is None:
        return True

    async with asyncio.timeout(timeout):
        result = redis_client.ping()
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            # Clustered clients return per-node results; every node must be up.
            # An empty dict means no reachable nodes — unhealthy.
            return bool(result) and all(result.values())
        return bool(result)
