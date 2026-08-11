"""Exception handler interceptor: map exceptions to gRPC errors (framework-agnostic).

This interceptor is intentionally decoupled from any error/domain model. It maps an
exception type to a ``grpc.StatusCode`` (walking the MRO) and aborts the request with
a details string produced by a pluggable ``detail_factory``. The default factory emits
a safe, non-leaking message per status code.

To expose a structured error model (e.g. a domain ``DomainError.to_json()``), inject a
custom ``error_status_map`` and ``detail_factory`` — no subclassing required.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, NoReturn

import grpc

from grpc_server_kit.interceptors.utils import is_server_error

from .base import AsyncServerInterceptor, RpcCall

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

__all__ = [
    "GRPC_DEFAULT_ERROR_STATUS_MAP",
    "GRPC_SAFE_ERROR_MESSAGES",
    "AsyncExceptionHandlerInterceptor",
    "ErrorDetailFactory",
    "default_error_detail",
    "find_mapped_status",
]

logger = logging.getLogger(__name__)

# Pluggable factory: given the raised exception and the resolved status code, return the
# gRPC ``details`` string to send to the client (e.g. a plain message or serialized JSON).
type ErrorDetailFactory = Callable[[Exception, grpc.StatusCode], str]

# Default mapping for common built-in exceptions (no domain/framework types).
# Deliberately excludes TypeError: it almost always signals a server-side
# programming bug, which must surface as INTERNAL (logged with a stack trace),
# not be blamed on the client as INVALID_ARGUMENT.
GRPC_DEFAULT_ERROR_STATUS_MAP: dict[type[Exception], grpc.StatusCode] = {
    ValueError: grpc.StatusCode.INVALID_ARGUMENT,
    PermissionError: grpc.StatusCode.PERMISSION_DENIED,
    NotImplementedError: grpc.StatusCode.UNIMPLEMENTED,
    TimeoutError: grpc.StatusCode.DEADLINE_EXCEEDED,
    FileNotFoundError: grpc.StatusCode.NOT_FOUND,
}

# Safe, non-leaking messages per status code (used by the default detail factory).
GRPC_SAFE_ERROR_MESSAGES: dict[grpc.StatusCode, str] = {
    grpc.StatusCode.INVALID_ARGUMENT: "Invalid request data",
    grpc.StatusCode.PERMISSION_DENIED: "Permission denied",
    grpc.StatusCode.UNIMPLEMENTED: "Method is not implemented",
    grpc.StatusCode.NOT_FOUND: "Resource not found",
    grpc.StatusCode.ALREADY_EXISTS: "Resource already exists",
    grpc.StatusCode.FAILED_PRECONDITION: "Failed precondition",
    grpc.StatusCode.UNAUTHENTICATED: "Unauthenticated",
    grpc.StatusCode.RESOURCE_EXHAUSTED: "Resource exhausted",
    grpc.StatusCode.DEADLINE_EXCEEDED: "Deadline exceeded",
    grpc.StatusCode.UNAVAILABLE: "Service unavailable",
    grpc.StatusCode.ABORTED: "Operation aborted",
    grpc.StatusCode.INTERNAL: "Internal server error",
}

_DEFAULT_FALLBACK_MESSAGE = "Request processing failed"


def default_error_detail(exc: Exception, status: grpc.StatusCode) -> str:
    """Default detail factory: a safe, non-leaking message for the resolved status code.

    Never exposes the exception's own message/internals to the client.
    """
    return GRPC_SAFE_ERROR_MESSAGES.get(status, _DEFAULT_FALLBACK_MESSAGE)


def find_mapped_status(
    exc_type: type[BaseException],
    error_status_map: dict[type[Exception], grpc.StatusCode],
) -> grpc.StatusCode:
    """Resolve the gRPC status an exception type maps to (walking the MRO).

    Unmapped types resolve to ``INTERNAL``. Shared by the exception handler and
    by capture filters that must classify errors with the SAME mapping.
    """
    for base in exc_type.__mro__:
        if base in error_status_map:
            return error_status_map[base]
    return grpc.StatusCode.INTERNAL


class AsyncExceptionHandlerInterceptor(AsyncServerInterceptor):
    """Interceptor that catches exceptions and converts them to gRPC errors.

    Rules:
    - ``grpc.aio.AbortError`` (the handler already aborted with its own status) and
      ``grpc.RpcError`` are re-raised as-is.
    - ``CancelledError`` / ``KeyboardInterrupt`` / ``SystemExit`` are re-raised.
    - Any other exception is mapped to a ``grpc.StatusCode`` (via the MRO) and the request
      is aborted with details from the ``detail_factory``. Unmapped exceptions default to
      ``INTERNAL`` with a safe message (no internal details leaked).

    Covers streaming RPCs: exceptions raised while producing a response stream are
    mapped and aborted the same way as unary failures.
    """

    def __init__(
        self,
        error_status_map: dict[type[Exception], grpc.StatusCode] | None = None,
        *,
        detail_factory: ErrorDetailFactory | None = None,
        merge_defaults: bool = True,
    ) -> None:
        """Initialize interceptor.

        Args:
            error_status_map: Mapping of exception types to gRPC status codes. User entries
                take precedence over the built-in defaults.
            detail_factory: Callable producing the gRPC ``details`` string from
                ``(exception, status_code)``. Defaults to :func:`default_error_detail`.
            merge_defaults: When True (default), merge ``error_status_map`` over the built-in
                defaults; when False, use only the provided map.
        """
        super().__init__()
        base = dict(GRPC_DEFAULT_ERROR_STATUS_MAP) if merge_defaults else {}
        if error_status_map:
            base.update(error_status_map)
        self._error_status_map = base
        self._detail_factory = detail_factory or default_error_detail

    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        """Run the RPC; map any unexpected exception to a gRPC abort."""
        try:
            yield
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except grpc.aio.AbortError:
            # The handler (or an inner interceptor) already aborted this RPC with
            # its own status — pass it through untouched.
            raise
        except grpc.RpcError:
            raise
        except Exception as exc:
            await self._abort_with_mapped_status(exc, call)

    async def _abort_with_mapped_status(self, exc: Exception, call: RpcCall) -> NoReturn:
        """Map the exception to a gRPC status and abort the request."""
        status_code = find_mapped_status(type(exc), self._error_status_map)
        details = self._detail_factory(exc, status_code)
        log_extra = {
            "error_type": type(exc).__name__,
            "method": call.method_name,
            "status_code": status_code.name,
        }

        if is_server_error(status_code):
            logger.exception("gRPC request failed (server error)", extra=log_extra)
        else:
            logger.info("gRPC request failed (client error)", extra=log_extra)

        await call.context.abort(status_code, details)
        # grpc.aio contexts never return from abort(); guard against non-conforming ones.
        raise RuntimeError("ServicerContext.abort() returned instead of raising")
