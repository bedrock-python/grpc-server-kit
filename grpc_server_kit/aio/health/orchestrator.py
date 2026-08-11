"""Overall health-check orchestration (async)."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import TYPE_CHECKING

from grpc_health.v1 import health_pb2

from grpc_server_kit.constants import DEFAULT_HEALTH_CHECK_TIMEOUT

from .protocols import AsyncHealthCacheProtocol, AsyncHealthChecker

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


async def check_async_overall_health(
    checkers: list[AsyncHealthChecker] | None = None,
    cache: AsyncHealthCacheProtocol | None = None,
    timeout: float = DEFAULT_HEALTH_CHECK_TIMEOUT,
) -> health_pb2.HealthCheckResponse.ServingStatus:
    """Check overall service health including all dependencies (async)."""
    if not checkers:
        return health_pb2.HealthCheckResponse.SERVING

    if cache is not None:
        return await cache.get_or_set(
            check_func=functools.partial(_perform_checks, checkers, timeout=timeout),
        )

    return await _perform_checks(checkers, timeout=timeout)


async def _perform_checks(
    checkers: Sequence[AsyncHealthChecker], timeout: float = DEFAULT_HEALTH_CHECK_TIMEOUT
) -> health_pb2.HealthCheckResponse.ServingStatus:
    """Perform all health checks in parallel and aggregate results.

    Uses asyncio.gather with return_exceptions to collect pass/fail per checker
    without using exceptions for control flow.

    Args:
        checkers: Sequence of health checkers to execute. Must not be empty.
        timeout: Maximum seconds to wait for all health checks to complete.

    Returns:
        SERVING if all checks pass, NOT_SERVING if any check fails or unexpected error occurs.
    """
    loop = asyncio.get_running_loop()
    start_time = loop.time()
    checker_count = len(checkers)
    checker_names = [type(c).__name__ for c in checkers]

    try:
        async with asyncio.timeout(timeout):
            results = await asyncio.gather(
                *(c.check() for c in checkers),
                return_exceptions=True,
            )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except TimeoutError:
        elapsed = loop.time() - start_time
        logger.warning(
            "Health checks timed out",
            extra={
                "checker_count": checker_count,
                "checkers": checker_names,
                "timeout": timeout,
                "elapsed_seconds": round(elapsed, 3),
            },
        )
        return health_pb2.HealthCheckResponse.NOT_SERVING

    failed_checkers: list[str] = []
    unexpected_errors: list[tuple[str, Exception]] = []

    for checker, outcome in zip(checkers, results, strict=True):
        name = type(checker).__name__
        if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
            raise outcome
        if outcome is False:
            failed_checkers.append(name)
        elif isinstance(outcome, Exception):
            unexpected_errors.append((name, outcome))

    elapsed = loop.time() - start_time
    extra = {
        "checker_count": checker_count,
        "elapsed_seconds": round(elapsed, 3),
    }

    if failed_checkers:
        logger.warning(
            "Health checks failed",
            extra={**extra, "failed_count": len(failed_checkers), "failed_checkers": failed_checkers},
        )
        return health_pb2.HealthCheckResponse.NOT_SERVING

    if unexpected_errors:
        # Log each error WITH its own traceback: logger.exception outside an
        # except block has no active exception and would log "NoneType: None".
        for name, error in unexpected_errors:
            logger.error(
                "Health checker raised unexpected error",
                extra={**extra, "checker": name, "error_type": type(error).__name__},
                exc_info=error,
            )
        return health_pb2.HealthCheckResponse.NOT_SERVING

    # DEBUG, not INFO: this fires for every Check probe / Watch iteration and
    # carries no information when everything is healthy.
    logger.debug(
        "Health checks passed",
        extra={**extra, "checkers": checker_names},
    )
    return health_pb2.HealthCheckResponse.SERVING
