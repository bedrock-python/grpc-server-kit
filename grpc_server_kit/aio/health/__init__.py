"""gRPC Health Checking Protocol v1 implementation (async).

Gated behind the ``[health]`` extra (``grpcio-health-checking``). The Postgres and
Redis checkers additionally require the ``[postgres]`` / ``[redis]`` extras.
"""

from .cache import HealthCache
from .checkers import (
    DatabaseHealthChecker,
    FunctionalHealthChecker,
    RedisClientProtocol,
    RedisHealthChecker,
    SessionMakerProtocol,
    check_async_database_health,
    check_async_redis_health,
    handle_check_exceptions,
)
from .orchestrator import check_async_overall_health
from .protocols import AsyncHealthCacheProtocol, AsyncHealthChecker
from .servicer import AsyncDynamicHealthServicer

__all__ = [
    "AsyncDynamicHealthServicer",
    "AsyncHealthCacheProtocol",
    "AsyncHealthChecker",
    "DatabaseHealthChecker",
    "FunctionalHealthChecker",
    "HealthCache",
    "RedisClientProtocol",
    "RedisHealthChecker",
    "SessionMakerProtocol",
    "check_async_database_health",
    "check_async_overall_health",
    "check_async_redis_health",
    "handle_check_exceptions",
]
