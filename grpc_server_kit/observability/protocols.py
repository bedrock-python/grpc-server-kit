"""Observability seam protocols (duck-typing, SDK-free).

Pure structural protocols that the kit's interceptors call without knowing the
concrete backend. The names are vendor-neutral: an OpenTelemetry tracer, the
Sentry SDK adapter, or any test double with the same shape fits. Downstream
frameworks (e.g. servicewright) adapt to these shapes — protocols match
structurally, so no import dependency is required in either direction.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import TracebackType
from typing import Protocol, runtime_checkable

type SpanAttributeValue = str | int | float | bool | list[str] | list[int] | list[float] | list[bool]


@runtime_checkable
class SpanProtocol(Protocol):
    """A tracing span context manager."""

    def set_attribute(self, key: str, value: SpanAttributeValue) -> None:
        """Attach a typed attribute to the span."""
        ...

    def record_exception(self, exception: Exception) -> None:
        """Record an exception against the span."""
        ...

    def __enter__(self) -> SpanProtocol: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


@runtime_checkable
class TracerProtocol(Protocol):
    """Mints spans for the active trace."""

    def start_as_current_span(self, name: str) -> SpanProtocol:
        """Start a span and make it current for its ``with`` block."""
        ...


class ErrorReporterProtocol(Protocol):
    """Error-reporting seam (vendor-neutral; any tracker with this shape fits)."""

    def isolate(self) -> AbstractContextManager[object]:
        """Return a context manager forking an isolated per-unit scope.

        Tags and breadcrumbs set inside the block must not leak into other
        concurrent units of work. Backends without scope isolation may return
        ``contextlib.nullcontext()``.
        """
        ...

    def capture_exception(self, error: Exception) -> None:
        """Report an exception to the error tracker."""
        ...

    def add_breadcrumb(
        self,
        message: str,
        category: str = "default",
        level: str = "info",
        data: dict[str, object] | None = None,
    ) -> None:
        """Attach a breadcrumb to the current scope."""
        ...

    def set_tags(self, **tags: str) -> None:
        """Set tags on the current scope."""
        ...


__all__ = [
    "ErrorReporterProtocol",
    "SpanAttributeValue",
    "SpanProtocol",
    "TracerProtocol",
]
