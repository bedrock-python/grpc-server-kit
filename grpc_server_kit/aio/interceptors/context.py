"""Context interceptor: extract gRPC metadata into context variables / structlog."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn

import grpc

from .base import AsyncServerInterceptor, RpcCall
from .metadata import get_metadata_dict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    import structlog
else:
    try:
        import structlog
    except ImportError:
        structlog = None

__all__ = ["AsyncContextInterceptor", "HeaderConfig"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeaderConfig:
    """Configuration for a single header-to-context-variable mapping."""

    header_name: str
    context_var_name: str
    context_setter: Callable[[str], Callable[[], None] | Any] | None = None
    default_factory: Callable[[], str] | None = None
    validator: Callable[[str], bool] | None = None
    required: bool = False
    allow_empty: bool = False

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not self.header_name:
            raise ValueError("header_name cannot be empty")
        if not self.context_var_name:
            raise ValueError("context_var_name cannot be empty")


class AsyncContextInterceptor(AsyncServerInterceptor):
    """Interceptor that extracts gRPC metadata and binds it to context variables.

    This interceptor should be early in the chain so subsequent interceptors and
    the handler see the bound context variables. It supports extracting standard
    headers like ``x-request-id`` as well as custom header mappings. Bindings
    stay in place for the whole RPC — including the full response stream of
    streaming calls — and are removed afterwards.
    """

    def __init__(
        self,
        header_configs: list[HeaderConfig],
        bind_method_name: bool = True,
        bind_structlog: bool = True,
        method_key: str = "grpc_method",
    ) -> None:
        """Initialize interceptor with header configurations.

        Args:
            header_configs: List of header configurations to extract and bind.
            bind_method_name: Whether to bind the gRPC method name to context.
            bind_structlog: Whether to bind extracted variables to structlog context
                (no-op if structlog is not installed).
            method_key: Key for the gRPC method name in context.
        """
        super().__init__()
        self._header_configs = header_configs
        self._bind_method_name = bind_method_name
        self._bind_structlog = bind_structlog
        self._method_key = method_key

    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        """Bind context variables from metadata for the duration of the RPC."""
        metadata = get_metadata_dict(call.context)
        bound_vars, removers = await self._process_headers(metadata, call.context, call.method_name)

        if self._bind_method_name:
            bound_vars[self._method_key] = call.method_name

        self._bind_context(bound_vars)
        try:
            yield
        finally:
            self._unbind_context(bound_vars, removers)

    async def _process_headers(
        self,
        metadata: dict[str, str],
        context: grpc.aio.ServicerContext[Any, Any],
        method_name: str,
    ) -> tuple[dict[str, str], list[Callable[[], None]]]:
        """Process configured headers and extract values."""
        bound_vars: dict[str, str] = {}
        removers: list[Callable[[], None]] = []

        for config in self._header_configs:
            value = self._get_header_value(config, metadata)

            if value is None:
                if config.required:
                    await self._abort_request(
                        context, method_name, config.header_name, f"Required header '{config.header_name}' not found"
                    )
                continue

            if not value and not config.allow_empty:
                if config.required:
                    await self._abort_request(
                        context, method_name, config.header_name, f"Header '{config.header_name}' cannot be empty"
                    )
                continue

            if config.validator and not config.validator(value):
                if config.required:
                    await self._abort_request(
                        context, method_name, config.header_name, f"Invalid value for header '{config.header_name}'"
                    )
                continue

            if config.context_setter:
                # The setter may return a cleanup callable (remover)
                remover = config.context_setter(value)
                if callable(remover):
                    removers.append(remover)

            bound_vars[config.context_var_name] = value

        logger.debug("Extracted context variables", extra=bound_vars)
        return bound_vars, removers

    async def _abort_request(
        self,
        context: grpc.aio.ServicerContext[Any, Any],
        method_name: str,
        header_name: str,
        details: str,
    ) -> NoReturn:
        """Log and abort a gRPC request (abort raises ``grpc.aio.AbortError``)."""
        logger.warning(
            "gRPC request context extraction failed",
            extra={"method": method_name, "header": header_name, "details": details},
        )
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, details)
        # grpc.aio contexts never return from abort(); guard against non-conforming ones.
        raise RuntimeError("ServicerContext.abort() returned instead of raising")

    def _get_header_value(self, config: HeaderConfig, metadata: dict[str, str]) -> str | None:
        """Get header value from metadata or default factory."""
        value = metadata.get(config.header_name)
        if value is None and config.default_factory:
            return config.default_factory()
        return value

    def _bind_context(self, bound_vars: dict[str, str]) -> None:
        """Bind variables to structlog context."""
        if self._bind_structlog and structlog is not None:
            structlog.contextvars.bind_contextvars(**bound_vars)

    def _unbind_context(self, bound_vars: dict[str, str], removers: list[Callable[[], None]]) -> None:
        """Unbind variables from structlog context and call cleanup removers."""
        if self._bind_structlog and structlog is not None and bound_vars:
            structlog.contextvars.unbind_contextvars(*bound_vars.keys())

        # Call cleanup removers in reverse order
        while removers:
            remover = removers.pop()
            try:
                remover()
            except Exception:
                logger.exception("Failed to call context cleanup remover")
