"""Internal utilities for health checkers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec

from grpc_server_kit.constants import DEFAULT_CHECKER_TIMEOUT

logger = logging.getLogger(__name__)

P = ParamSpec("P")


def handle_check_exceptions(
    check_name: str,
) -> Callable[[Callable[P, Awaitable[bool]]], Callable[P, Awaitable[bool]]]:
    """Decorator for consistent exception handling in health check functions.

    This decorator provides uniform error handling for all health check implementations:
    - Logs timeout errors with warning level
    - Propagates CancelledError and KeyboardInterrupt for proper shutdown
    - Catches and logs all other exceptions, returning False (unhealthy)

    Args:
        check_name: Human-readable name of the check for logging (e.g. "Database", "Redis").

    Returns:
        Decorator function that wraps health check implementations.
    """

    def decorator(func: Callable[P, Awaitable[bool]]) -> Callable[P, Awaitable[bool]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> bool:
            try:
                return await func(*args, **kwargs)
            except TimeoutError:
                timeout = kwargs.get("timeout", "unknown")
                logger.warning(f"{check_name} health check timed out", extra={"check": check_name, "timeout": timeout})
                return False
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except (ConnectionError, OSError) as e:
                # Common networking errors for health checks (e.g. Redis down, Postgres unreachable)
                logger.warning(
                    f"{check_name} health check failed (network/OS error)",
                    extra={"check": check_name, "error": str(e), "error_type": type(e).__name__},
                )
                return False
            except Exception:
                logger.exception(f"{check_name} health check failed with unexpected error", extra={"check": check_name})
                return False

        return wrapper

    return decorator


class FunctionalHealthChecker:
    """Generic health checker that wraps a health check function.

    This class provides a common wrapper for any health check function that follows
    the `check_func(resource, timeout=...)` pattern.
    """

    def __init__(
        self,
        check_func: Callable[..., Awaitable[bool]],
        resource: object | None = None,
        timeout: float = DEFAULT_CHECKER_TIMEOUT,
    ) -> None:
        """Initialize health checker.

        Args:
            check_func: Asynchronous function that performs the health check.
            resource: The resource (e.g. session maker, client) to check.
            timeout: Maximum time to wait for the check to complete in seconds.

        Raises:
            ValueError: If timeout is not positive.
        """
        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")
        self._check_func = check_func
        self._resource = resource
        self._timeout = timeout

    async def check(self) -> bool:
        """Execute the wrapped health check function.

        Returns:
            True if healthy, False otherwise.
        """
        return await self._check_func(self._resource, timeout=self._timeout)
