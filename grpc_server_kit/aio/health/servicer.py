"""Dynamic health check servicer with real-time dependency checks (async)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, NoReturn

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from grpc_server_kit.constants import (
    DEFAULT_HEALTH_CHECK_INTERVAL,
    DEFAULT_HEALTH_CHECK_TIMEOUT,
    DEFAULT_HEALTH_HEARTBEAT_INTERVAL,
)
from grpc_server_kit.server import is_valid_service_name

from .orchestrator import check_async_overall_health

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from .protocols import AsyncHealthCacheProtocol, AsyncHealthChecker

logger = logging.getLogger(__name__)


class AsyncDynamicHealthServicer(health_pb2_grpc.HealthServicer):
    """Async health servicer with dynamic dependency checks.

    Implements the standard gRPC Health Checking Protocol v1 with real-time
    dependency verification. Supports both Check (unary) and Watch (streaming) modes.
    """

    def __init__(
        self,
        checkers: list[AsyncHealthChecker] | None = None,
        cache: AsyncHealthCacheProtocol | None = None,
        service_names: list[str] | None = None,
        check_interval: float = DEFAULT_HEALTH_CHECK_INTERVAL,
        heartbeat_interval: float = DEFAULT_HEALTH_HEARTBEAT_INTERVAL,
        check_timeout: float = DEFAULT_HEALTH_CHECK_TIMEOUT,
    ) -> None:
        """Initialize health servicer.

        Args:
            checkers: Optional list of asynchronous health checkers. If None or empty,
                the service always returns SERVING (no dependencies to check).
            cache: Optional health status cache following AsyncHealthCacheProtocol.
                Strongly recommended with multiple Watch clients: watchers share
                one real dependency check per TTL instead of each running their own.
            service_names: Optional list of additional service names to support.
                The empty string "" is always supported and represents overall health.
            check_interval: Frequency of dependency checks in Watch mode (seconds). Must be positive.
            heartbeat_interval: Interval to send status even if unchanged in Watch (seconds).
                Must be >= check_interval.
            check_timeout: Maximum seconds to wait for each health check run. Must be positive.

        Raises:
            ValueError: If check_interval, heartbeat_interval or check_timeout are invalid.
        """
        if check_interval <= 0:
            raise ValueError(f"check_interval must be positive, got {check_interval}")
        if heartbeat_interval < check_interval:
            raise ValueError(f"heartbeat_interval ({heartbeat_interval}) must be >= check_interval ({check_interval})")
        if check_timeout <= 0:
            raise ValueError(f"check_timeout must be positive, got {check_timeout}")

        self._checkers = checkers or []
        self._cache = cache
        self._service_names = set(service_names or [])
        self._service_names.add("")  # Always support overall health check
        self._check_interval = check_interval
        self._heartbeat_interval = heartbeat_interval
        self._check_timeout = check_timeout

    def _validate_service_name(self, service: str) -> bool:
        """Validate gRPC service name ("" is the overall-health wildcard)."""
        if not service:
            return True
        return is_valid_service_name(service)

    def _is_service_known(self, service: str) -> bool:
        """Check if the requested service name is registered."""
        return service in self._service_names

    async def _perform_health_check(self, service: str = "") -> health_pb2.HealthCheckResponse.ServingStatus:
        """Perform health check with error handling.

        Returns:
            SERVING if all checks pass, NOT_SERVING on any failure or exception.
        """
        try:
            return await check_async_overall_health(
                cache=self._cache,
                checkers=self._checkers,
                timeout=self._check_timeout,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except (RuntimeError, ValueError, TimeoutError) as e:
            # Expected/recoverable errors during health check execution
            logger.warning("Health check execution failed (recoverable)", extra={"service": service, "error": str(e)})
            return health_pb2.HealthCheckResponse.NOT_SERVING
        except Exception:
            logger.exception("Health check failed due to unexpected error", extra={"service": service})
            return health_pb2.HealthCheckResponse.NOT_SERVING

    def _should_send_update(
        self,
        status: health_pb2.HealthCheckResponse.ServingStatus,
        last_status: health_pb2.HealthCheckResponse.ServingStatus | None,
        last_heartbeat: float,
        now: float,
    ) -> bool:
        """Determine if a status update should be sent in Watch mode.

        Returns:
            True if update should be sent (status changed or heartbeat interval reached).
        """
        is_changed = status != last_status
        is_heartbeat = (now - last_heartbeat) >= self._heartbeat_interval
        return is_changed or is_heartbeat

    async def Check(
        self,
        request: health_pb2.HealthCheckRequest,
        _context: grpc.aio.ServicerContext[health_pb2.HealthCheckRequest, health_pb2.HealthCheckResponse],
    ) -> health_pb2.HealthCheckResponse:
        """Check service health with real-time dependency checks (unary).

        Implements the Check RPC from gRPC Health Checking Protocol v1.

        Returns:
            Health check response with status (SERVING, NOT_SERVING, or SERVICE_UNKNOWN).
        """
        if not self._validate_service_name(request.service):
            logger.warning("Health check with invalid service name", extra={"service": request.service})
            return health_pb2.HealthCheckResponse(status=health_pb2.HealthCheckResponse.SERVICE_UNKNOWN)

        if not self._is_service_known(request.service):
            logger.debug("Health check for unknown service", extra={"service": request.service})
            return health_pb2.HealthCheckResponse(status=health_pb2.HealthCheckResponse.SERVICE_UNKNOWN)

        status = await self._perform_health_check(service=request.service)
        logger.debug(
            "Health check completed",
            extra={
                "service": request.service,
                "status": health_pb2.HealthCheckResponse.ServingStatus.Name(status),
            },
        )
        return health_pb2.HealthCheckResponse(status=status)

    async def Watch(
        self,
        request: health_pb2.HealthCheckRequest,
        context: grpc.aio.ServicerContext[health_pb2.HealthCheckRequest, health_pb2.HealthCheckResponse],
    ) -> AsyncGenerator[health_pb2.HealthCheckResponse, None]:
        """Watch service health status with streaming updates.

        Implements the Watch RPC from gRPC Health Checking Protocol v1.
        Sends updates when status changes or on heartbeat interval. Per the
        protocol a Watch stream never completes normally: internal failures
        abort the stream with INTERNAL so clients can distinguish a broken
        watch from a deliberate server shutdown.

        Yields:
            Streaming health check responses as status changes.
        """
        if not self._validate_service_name(request.service):
            logger.warning("Health watch with invalid service name", extra={"service": request.service})
            yield health_pb2.HealthCheckResponse(status=health_pb2.HealthCheckResponse.SERVICE_UNKNOWN)
            return

        if not self._is_service_known(request.service):
            logger.debug("Health watch for unknown service", extra={"service": request.service})
            yield health_pb2.HealthCheckResponse(status=health_pb2.HealthCheckResponse.SERVICE_UNKNOWN)
            return

        logger.info(
            "Health watch started",
            extra={
                "service": request.service,
                "check_interval": self._check_interval,
                "heartbeat_interval": self._heartbeat_interval,
            },
        )

        last_status: health_pb2.HealthCheckResponse.ServingStatus | None = None
        last_heartbeat = 0.0

        try:
            while not context.done():
                status = await self._perform_health_check(service=request.service)
                now = asyncio.get_running_loop().time()

                if self._should_send_update(status, last_status, last_heartbeat, now):
                    last_status = status
                    last_heartbeat = now
                    yield health_pb2.HealthCheckResponse(status=status)

                if context.done():
                    break

                await asyncio.sleep(self._check_interval)

        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            logger.info("Health watch cancelled", extra={"service": request.service})
            raise
        except grpc.aio.AbortError:
            raise
        except (RuntimeError, ValueError, TimeoutError) as e:
            logger.warning("Health watch failed (recoverable)", extra={"service": request.service, "error": str(e)})
            await self._abort_watch(context)
        except Exception:
            logger.exception("Health watch failed due to unexpected error", extra={"service": request.service})
            await self._abort_watch(context)
        finally:
            logger.info("Health watch stopped", extra={"service": request.service})

    @staticmethod
    async def _abort_watch(
        context: grpc.aio.ServicerContext[health_pb2.HealthCheckRequest, health_pb2.HealthCheckResponse],
    ) -> NoReturn:
        """Terminate a broken Watch stream with an error status (never OK EOF)."""
        await context.abort(grpc.StatusCode.INTERNAL, "Health watch failed")
        raise RuntimeError("ServicerContext.abort() returned instead of raising")
