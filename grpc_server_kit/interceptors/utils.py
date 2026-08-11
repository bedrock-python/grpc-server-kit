"""Utility functions for gRPC interceptors."""

from __future__ import annotations

from typing import Any

import grpc


def is_server_error(code: grpc.StatusCode) -> bool:
    """Check if the gRPC status code is a server-side error (5xx equivalent).

    Server-side errors include internal failures, timeouts, and unavailability.
    If the code is not one of these, it is either OK or a client-side error (4xx equivalent).
    """
    return code in (
        grpc.StatusCode.INTERNAL,
        grpc.StatusCode.UNKNOWN,
        grpc.StatusCode.DATA_LOSS,
        grpc.StatusCode.UNIMPLEMENTED,
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
    )


def resolve_status_code(
    exc: BaseException | None,
    context: Any,
    default: grpc.StatusCode = grpc.StatusCode.UNKNOWN,
) -> grpc.StatusCode:
    """Best-effort gRPC status code for a finished or failed RPC.

    Prefers the exception's own ``code()`` (client-style ``RpcError``); falls
    back to the servicer context's code (set by ``abort()`` / ``set_code()``)
    when the exception carries no code or only a generic ``UNKNOWN``; returns
    ``default`` when neither yields a :class:`grpc.StatusCode`.
    """
    exc_code = _status_code_of_exception(exc)
    if exc_code is not None and exc_code is not grpc.StatusCode.UNKNOWN:
        return exc_code

    ctx_code = _status_code_of_context(context)
    if ctx_code is not None and ctx_code is not grpc.StatusCode.OK:
        return ctx_code
    if exc_code is not None:
        # UNKNOWN from the exception and nothing better on the context.
        return exc_code
    if ctx_code is not None:
        return ctx_code

    return default


def _status_code_of_exception(exc: BaseException | None) -> grpc.StatusCode | None:
    if exc is None:
        return None
    code_func = getattr(exc, "code", None)
    if callable(code_func):
        code = code_func()
        if isinstance(code, grpc.StatusCode):
            return code
    return None


def _status_code_of_context(context: Any) -> grpc.StatusCode | None:
    code_func = getattr(context, "code", None)
    if not callable(code_func):
        return None
    try:
        code = code_func()
    except Exception:
        # Non-conforming context objects fall through to the default.
        return None
    return code if isinstance(code, grpc.StatusCode) else None
