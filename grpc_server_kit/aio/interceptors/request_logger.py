"""Request logger interceptor."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import grpc

from grpc_server_kit.interceptors.constants import SKIPPED_HEALTH_METHODS
from grpc_server_kit.interceptors.utils import is_server_error, resolve_status_code

from .base import AsyncServerInterceptor, RpcCall

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection

__all__ = ["AsyncRequestLoggerInterceptor"]

logger = logging.getLogger(__name__)


def _sanitize_peer(peer: str | None) -> str:
    """Sanitize peer address to avoid logging internal IPs."""
    if not peer:
        return "unknown"
    # Log only the protocol, hide the actual IP
    for proto in ("ipv4", "ipv6", "unix"):
        if peer.startswith(f"{proto}:"):
            return proto
    return "unknown"


class AsyncRequestLoggerInterceptor(AsyncServerInterceptor):
    """Interceptor that logs start and completion of gRPC requests.

    Logs via standard logging with ``extra=`` for method and optional peer protocol.
    Skips logging for health check methods. For streaming RPCs the completion log
    is emitted after the full stream has been produced.
    """

    def __init__(
        self,
        *,
        log_peer: bool = False,
        log_request_on_error: bool = False,
        skip_methods: Collection[str] = SKIPPED_HEALTH_METHODS,
    ) -> None:
        """Initialize interceptor.

        Args:
            log_peer: Whether to log the peer protocol of the gRPC request.
            log_request_on_error: Whether to include ``str(request)`` in server-error
                logs. Off by default — request payloads may contain sensitive data.
            skip_methods: Full RPC method names to exclude from logging
                (defaults to the gRPC health-check methods).
        """
        super().__init__(skip_methods=skip_methods)
        self._log_peer = log_peer
        self._log_request_on_error = log_request_on_error

    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        """Log request start/completion/failure around the RPC."""
        log_data: dict[str, Any] = {"method": call.method_name}
        if self._log_peer:
            log_data["peer_protocol"] = _sanitize_peer(call.context.peer())

        logger.info("gRPC request started", extra=log_data)
        try:
            yield
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            logger.info("gRPC request cancelled", extra={"method": call.method_name})
            raise
        except grpc.aio.AbortError:
            # An interceptor or the handler aborted with an explicit status; the
            # context carries the real code. No stack trace — the abort was deliberate.
            status = resolve_status_code(None, call.context)
            self._log_failure(call, status, exc_info=False)
            raise
        except grpc.RpcError as exc:
            status = resolve_status_code(exc, call.context)
            self._log_failure(call, status, exc_info=is_server_error(status))
            raise
        except Exception:
            # Unexpected exceptions that are not gRPC errors yet: log with stack trace.
            extra: dict[str, Any] = {"method": call.method_name}
            if self._log_request_on_error:
                extra["request"] = str(call.request)
            logger.exception("gRPC request failed (unexpected exception)", extra=extra)
            raise

        logger.info("gRPC request completed", extra={"method": call.method_name})

    def _log_failure(self, call: RpcCall, status: grpc.StatusCode, *, exc_info: bool) -> None:
        """Log a failed RPC at a severity matching the status code."""
        extra: dict[str, Any] = {"method": call.method_name, "status": status.name}
        if is_server_error(status):
            if self._log_request_on_error:
                extra["request"] = str(call.request)
            logger.error("gRPC request failed (server error)", extra=extra, exc_info=exc_info)
        else:
            logger.info("gRPC request failed (client error)", extra=extra)
