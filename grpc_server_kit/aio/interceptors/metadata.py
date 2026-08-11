"""Per-request invocation-metadata parsing with a context-local cache."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import grpc

__all__ = ["get_metadata_dict"]

# Max bytes to represent as hex before truncating (avoids memory spike on large binary metadata).
_METADATA_HEX_TRUNCATE_BYTES = 512

# Context-local cache: each RPC runs in its own asyncio task (and therefore its
# own execution context), so a ContextVar isolates entries per request without a
# global registry or explicit cleanup. The cached context object is kept to
# guard against reuse across nested calls within one task.
_metadata_cache: contextvars.ContextVar[tuple[Any, dict[str, str]] | None] = contextvars.ContextVar(
    "grpc_server_kit_metadata_cache",
    default=None,
)


def get_metadata_dict(context: grpc.aio.ServicerContext[Any, Any]) -> dict[str, str]:
    """Return invocation metadata as a dictionary, cached for the current RPC.

    Avoids repeated ``dict()`` conversions of the metadata across interceptors.
    All values are converted to strings (bytes are decoded as UTF-8; undecodable
    bytes are hex-encoded and truncated).
    """
    cached = _metadata_cache.get()
    if cached is not None and cached[0] is context:
        return cached[1]

    raw_metadata = context.invocation_metadata()
    metadata: dict[str, str] = {}
    if raw_metadata is not None:
        for key, value in raw_metadata:
            if isinstance(value, bytes):
                try:
                    metadata[key] = value.decode("utf-8")
                except UnicodeDecodeError:
                    metadata[key] = _bytes_to_safe_str(value)
            else:
                metadata[key] = value

    _metadata_cache.set((context, metadata))
    return metadata


def _bytes_to_safe_str(value: bytes) -> str:
    """Convert bytes to string for metadata; avoid unbounded .hex() on large payloads."""
    if len(value) <= _METADATA_HEX_TRUNCATE_BYTES:
        return value.hex()
    return value[:_METADATA_HEX_TRUNCATE_BYTES].hex() + "...(truncated)"
