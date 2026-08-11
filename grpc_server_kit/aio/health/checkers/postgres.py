"""Database health checker (async).

Depends only on raw SQLAlchemy (the ``[postgres]`` extra). Compatible with any
async session maker following the ``async_sessionmaker`` pattern.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Protocol

from grpc_server_kit.constants import DEFAULT_CHECKER_TIMEOUT

from .utils import FunctionalHealthChecker, handle_check_exceptions

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

try:
    from sqlalchemy import text

    SQLALCHEMY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when the [postgres] extra is absent
    SQLALCHEMY_AVAILABLE = False

logger = logging.getLogger(__name__)

DEFAULT_DB_CHECK_TIMEOUT = DEFAULT_CHECKER_TIMEOUT

# Session cleanup (rollback/close) gets its own short deadline: a dead
# connection must not hold the health check past its budget, and a slow pool
# return must not convert a successful probe into a timeout.
_CLEANUP_TIMEOUT = 1.0


class SessionMakerProtocol(Protocol):
    """Protocol for SQLAlchemy async session makers.

    Follows the standard async_sessionmaker pattern from SQLAlchemy. Compatible
    with any callable that returns an async context manager providing an AsyncSession.
    """

    def __call__(self) -> contextlib.AbstractAsyncContextManager[AsyncSession]:
        """Create a new async database session."""
        ...


class DatabaseHealthChecker(FunctionalHealthChecker):
    """Health checker for SQLAlchemy async databases.

    Performs a connectivity check by executing a simple ``SELECT 1`` query with a
    configurable timeout.
    """

    def __init__(self, session_maker: SessionMakerProtocol, timeout: float = DEFAULT_DB_CHECK_TIMEOUT) -> None:
        """Initialize database health checker.

        Args:
            session_maker: SQLAlchemy async session maker.
            timeout: Maximum time to wait for database response in seconds.

        Raises:
            ValueError: If timeout is not positive.
        """
        super().__init__(check_async_database_health, session_maker, timeout)


@handle_check_exceptions("Database")
async def check_async_database_health(
    session_maker: SessionMakerProtocol | None,
    timeout: float = DEFAULT_DB_CHECK_TIMEOUT,
) -> bool:
    """Check async database connectivity with timeout.

    Executes a simple ``SELECT 1`` query to verify the database is reachable and
    responsive. The timeout covers session acquisition and the query; session
    cleanup runs under its own short deadline so a dead connection cannot hang
    the check and a slow cleanup cannot fail a successful probe.

    Args:
        session_maker: SQLAlchemy session maker to check. If None, returns True (no database configured).
        timeout: Maximum time to wait for database response in seconds.

    Returns:
        True if database is healthy or session_maker is None, False otherwise.

    Note:
        If SQLAlchemy is not installed, this function logs a debug message and returns True,
        allowing the service to run without database health checks.
    """
    if session_maker is None:
        return True

    if not SQLALCHEMY_AVAILABLE:
        logger.debug("SQLAlchemy not installed, skipping database health check")
        return True

    session_cm = session_maker()
    async with asyncio.timeout(timeout):
        session = await session_cm.__aenter__()

    try:
        async with asyncio.timeout(timeout):
            await session.execute(text("SELECT 1"))
        return True
    finally:
        try:
            async with asyncio.timeout(_CLEANUP_TIMEOUT):
                await session_cm.__aexit__(None, None, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Database health-check session cleanup failed", exc_info=True)
