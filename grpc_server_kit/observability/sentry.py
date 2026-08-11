"""Sentry adapter implementing the kit's ``ErrorReporterProtocol`` over ``sentry-sdk``.

Requires the ``[sentry]`` extra. Pass a ``SentrySdkAdapter`` instance to
``AsyncSentryInterceptor`` to report unexpected gRPC errors to Sentry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

try:
    import sentry_sdk

    HAS_SENTRY = True
except ImportError:  # pragma: no cover - exercised only when the [sentry] extra is absent
    HAS_SENTRY = False

__all__ = ["SentrySdkAdapter"]

_MISSING_MESSAGE = "Install grpc-server-kit[sentry] (sentry-sdk) to use the Sentry adapter"


class SentrySdkAdapter:
    """Adapts the global ``sentry-sdk`` API to ``ErrorReporterProtocol``."""

    def __init__(self) -> None:
        """Initialize the adapter.

        Raises:
            ImportError: If sentry-sdk is not installed.
        """
        if not HAS_SENTRY:
            raise ImportError(_MISSING_MESSAGE)

    def isolate(self) -> AbstractContextManager[object]:
        """Fork an isolated Sentry scope for one unit of work.

        Without this, concurrent asyncio tasks share one isolation scope and
        contaminate each other's tags/breadcrumbs.
        """
        return sentry_sdk.isolation_scope()

    def add_breadcrumb(
        self,
        message: str,
        category: str = "default",
        level: str = "info",
        data: dict[str, object] | None = None,
    ) -> None:
        """Add a breadcrumb to the current Sentry scope."""
        sentry_sdk.add_breadcrumb(message=message, category=category, level=level, data=data or {})

    def capture_exception(self, error: Exception) -> None:
        """Capture an exception in Sentry."""
        sentry_sdk.capture_exception(error)

    def set_tags(self, **tags: str) -> None:
        """Set tags on the current Sentry scope."""
        for key, value in tags.items():
            sentry_sdk.set_tag(key, value)
