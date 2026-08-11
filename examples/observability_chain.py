"""The canonical interceptor chain, wired by hand.

This example shows how to:

1. Build the canonical interceptor chain in its documented order (outermost
   first): metrics -> context -> request logger -> tracing -> exception
   handler -> sentry.
2. Satisfy `GrpcServerMetricsProtocol` with a few lines of plain Python --
   no `[metrics]` extra (no `prometheus-client`) required.
3. Map a domain exception to a gRPC status via `error_status_map`, instead
   of relying only on the kit's built-in defaults.
4. See WHY Sentry sits INSIDE the exception handler (later in the
   interceptor list, so it runs closer to the handler). `AsyncSentryInterceptor`
   deliberately re-raises `grpc.aio.AbortError` without capturing it -- it
   only captures raw, unmapped exceptions. If Sentry sat OUTSIDE (earlier
   than) the exception handler, every exception it would ever see would
   already have been turned into an `AbortError` by the handler below it, so
   it would capture NOTHING, ever. Sitting inside means it observes the raw
   `DuplicateEntryError` before that mapping happens -- proven below by the
   printed breadcrumb and capture.

Run with: `python examples/observability_chain.py`. It starts a server with
the full chain, makes one failing call and one successful call against it,
prints what each interceptor observed, and exits 0.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field

import grpc

from grpc_server_kit import GrpcApp, GrpcServerConfig
from grpc_server_kit.aio.interceptors import (
    AsyncContextInterceptor,
    AsyncExceptionHandlerInterceptor,
    AsyncMetricsInterceptor,
    AsyncRequestLoggerInterceptor,
    AsyncSentryInterceptor,
    AsyncTracingInterceptor,
    HeaderConfig,
)

SERVICE_NAME = "example.Ledger"


class DuplicateEntryError(Exception):
    """Domain error -- deliberately NOT one of the kit's built-in default mappings."""


@dataclass
class PrintingMetrics:
    """Satisfies `GrpcServerMetricsProtocol` structurally -- no `[metrics]` extra needed."""

    records: list[dict[str, str | float]] = field(default_factory=list)

    def record_request(self, service: str, method: str, status: str, grpc_code: str, duration: float) -> None:
        self.records.append(
            {"service": service, "method": method, "status": status, "grpc_code": grpc_code, "duration": duration}
        )
        print(f"[metrics] {method} status={status} grpc_code={grpc_code} duration={duration * 1000:.2f}ms")


@dataclass
class PrintingErrorReporter:
    """Satisfies `ErrorReporterProtocol` structurally -- no `[sentry]` extra needed."""

    captured: list[Exception] = field(default_factory=list)

    @contextlib.contextmanager
    def isolate(self) -> Iterator[PrintingErrorReporter]:
        yield self

    def capture_exception(self, error: Exception) -> None:
        self.captured.append(error)
        print(f"[sentry] capture_exception: {type(error).__name__}: {error}")

    def add_breadcrumb(
        self,
        message: str,
        category: str = "default",
        level: str = "info",
        data: dict[str, object] | None = None,
    ) -> None:
        print(f"[sentry] breadcrumb: {message} (category={category})")

    def set_tags(self, **tags: str) -> None:
        print(f"[sentry] set_tags: {tags}")


async def create_entry(request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    """The RPC handler: raises a domain error for one specific input."""
    if request == b"duplicate":
        raise DuplicateEntryError(f"entry {request!r} already exists")
    return b"created:" + request


def build_generic_handler() -> grpc.GenericRpcHandler:
    return grpc.method_handlers_generic_handler(
        SERVICE_NAME,
        {"CreateEntry": grpc.unary_unary_rpc_method_handler(create_entry)},
    )


async def main() -> None:
    metrics = PrintingMetrics()
    reporter = PrintingErrorReporter()

    # Canonical order (outermost first): metrics -> context -> logger ->
    # tracing -> exception handler -> sentry.
    interceptors: list[grpc.aio.ServerInterceptor] = [
        AsyncMetricsInterceptor(metrics=metrics, service_name=SERVICE_NAME),
        AsyncContextInterceptor([HeaderConfig("x-request-id", "request_id")], bind_structlog=False),
        AsyncRequestLoggerInterceptor(),
        AsyncTracingInterceptor(service_name=SERVICE_NAME, tracer=None),  # no-op without the [tracing] extra
        AsyncExceptionHandlerInterceptor({DuplicateEntryError: grpc.StatusCode.ALREADY_EXISTS}),
        AsyncSentryInterceptor(sentry=reporter),  # INSIDE the exception handler -- see module docstring
    ]

    config = GrpcServerConfig(host="127.0.0.1", port=0, grace_period=1.0)
    app = GrpcApp(config, interceptors=interceptors)
    app.register(lambda raw: raw.add_generic_rpc_handlers((build_generic_handler(),)))

    async with app:
        target = f"127.0.0.1:{app.bound_port}"
        print(f"[server] listening on {target}")

        async with grpc.aio.insecure_channel(target) as channel:
            create_entry_call = channel.unary_unary(f"/{SERVICE_NAME}/CreateEntry")

            print("\n--- failing call: duplicate entry (maps to ALREADY_EXISTS) ---")
            try:
                await create_entry_call(b"duplicate", metadata=(("x-request-id", "req-1"),))
            except grpc.aio.AioRpcError as exc:
                print(f"[client] observed status={exc.code().name} details={exc.details()!r}")

            print("\n--- successful call ---")
            response = await create_entry_call(b"widget-1", metadata=(("x-request-id", "req-2"),))
            print(f"[client] observed response={response!r}")

    captured_names = [type(exc).__name__ for exc in reporter.captured]
    print(f"\n[summary] sentry captured {len(reporter.captured)} exception(s): {captured_names}")
    print(f"[summary] metrics recorded {len(metrics.records)} request(s)")


if __name__ == "__main__":
    asyncio.run(main())
