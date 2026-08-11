"""Shared, transport-agnostic interceptor helpers.

The async server interceptors live under :mod:`grpc_server_kit.aio.interceptors`; this
package holds only the shared constants and utilities they build on.
"""

from .constants import SKIPPED_HEALTH_METHODS, X_REQUEST_ID
from .utils import is_server_error, resolve_status_code

__all__ = [
    "SKIPPED_HEALTH_METHODS",
    "X_REQUEST_ID",
    "is_server_error",
    "resolve_status_code",
]
