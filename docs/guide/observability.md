# Observability

Metrics, tracing, and error tracking each reach the kit through a small
structural protocol instead of a concrete SDK import.
`AsyncMetricsInterceptor`, `AsyncTracingInterceptor`, and
`AsyncSentryInterceptor` (see [Interceptors](interceptors.md)) call these
protocols and nothing else, so the core package never depends on
`prometheus-client`, `opentelemetry`, or `sentry-sdk` — you pay only for what
you install, and you can swap in your own adapter, or a test double, without
subclassing anything.

## The protocols

| Protocol | Shape | Ships as |
| :--- | :--- | :--- |
| `GrpcServerMetricsProtocol` | `record_request(service, method, status, grpc_code, duration) -> None` | `GrpcServerMetrics` (`[metrics]`) |
| `TracerProtocol` | `start_as_current_span(name) -> SpanProtocol` | An adapted OTel tracer |
| `SpanProtocol` | `set_attribute(key, value)`, `record_exception(exc)`, context manager | An OTel span |
| `ErrorReporterProtocol` | `isolate()`, `capture_exception(exc)`, `add_breadcrumb(...)`, `set_tags(**tags)` | `SentrySdkAdapter` (`[sentry]`) |

All four import straight from the interceptors package:

```python
from grpc_server_kit.aio.interceptors import (
    ErrorReporterProtocol, GrpcServerMetricsProtocol, SpanProtocol, TracerProtocol,
)
```

`SpanProtocol` and `TracerProtocol` are `@runtime_checkable`, so a custom
adapter can be sanity-checked with `isinstance(adapter, TracerProtocol)`.
Pass `None` for any of them — the default — and the corresponding
interceptor no-ops. That's exactly how each interceptor behaves when its
extra isn't installed or observability is disabled, so "no backend
configured" is a first-class, always-safe state rather than an error path.

## Prometheus metrics

`GrpcServerMetrics` (`grpc_server_kit.observability.metrics`, `[metrics]`
extra) defines exactly two, frozen metrics:

| Metric | Type | Labels |
| :--- | :--- | :--- |
| `grpc_requests_total` | Counter | `service`, `method`, `status` (`success`/`error`), `grpc_code` |
| `grpc_request_duration_seconds` | Histogram | `service`, `method` |

```python
from grpc_server_kit.observability.metrics import get_grpc_server_metrics

metrics = get_grpc_server_metrics(prefix="my_service")   # my_service_grpc_requests_total, ...
```

`get_grpc_server_metrics()` caches by `prefix` — registering the same
Prometheus metric name twice raises inside `prometheus_client`, so repeated
calls for the same prefix hand back the existing instance — and raises
`ValueError` if you ask for that prefix again with different histogram
`buckets`, since silently keeping the old buckets would record real
latencies into the wrong histogram with no warning at all.
`AsyncMetricsInterceptor` catches and logs any exception `record_request`
itself raises, so a metrics backend hiccup never fails the RPC being
measured.

## Tracing attributes and propagation

`AsyncTracingInterceptor` opens one span per RPC, named after the method
(e.g. `pkg.Service/Method`), and sets the OpenTelemetry semantic-convention
attributes:

- `rpc.system` = `"grpc"`
- `rpc.service`, `rpc.method` — parsed from the full method name
- `rpc.grpc.status_code` — the numeric status code, set once the RPC (or the
  full response stream) finishes
- `request.id` — the `x-request-id` metadata value, when present

It records exceptions selectively. For an RPC that ends with a server-class
status, the interceptor looks for an original exception chained onto the
`AbortError` (via `__cause__` or `__context__`) and records *that* on the
span — recovering the real bug even though, by the time the outcome reaches
this interceptor, an inner `AsyncExceptionHandlerInterceptor` has already
turned it into a clean abort. A deliberate abort with a client-class status,
or one raised with nothing being handled, has no exception to recover and is
not recorded.

This interceptor is a lightweight seam: it does **not** extract an incoming
`traceparent` header, so a span it opens starts a new trace rather than
continuing the caller's. For full distributed-tracing propagation, call
`instrument_aio_server()` once at startup — a thin wrapper over
OpenTelemetry's own `GrpcAioInstrumentorServer` (`[tracing]` extra):

```python
from grpc_server_kit.aio.observability import instrument_aio_server

instrument_aio_server()   # extracts incoming trace context; call once, before serving
```

The two compose: `instrument_aio_server()` handles context propagation at the
transport level, and `AsyncTracingInterceptor` adds the kit's semconv
attributes and selective exception recording on top.

## Error tracking

`AsyncSentryInterceptor` runs every RPC inside `reporter.isolate()` — a
forked, isolated scope — before setting tags (`grpc_method`, `request_id`)
and a breadcrumb. Isolation matters because asyncio tasks otherwise share
process-global SDK state: without a fresh scope per RPC, tags and breadcrumbs
from concurrent calls would bleed into each other's error reports.

It only captures raw `Exception`s that reach it — `grpc.aio.AbortError` and
`grpc.RpcError` are re-raised untouched, never captured. Combined with the
[canonical chain order](interceptors.md#the-canonical-chain), this is why
Sentry's position matters: placed inside (closer to the handler than)
`AsyncExceptionHandlerInterceptor`, it sees the exact exception the handler
raised; placed outside, it would only ever see the `AbortError` the
exception handler already converted that exception into, and would capture
nothing, ever. An optional `capture_filter` narrows *which* exceptions get
reported — the Dishka bundle wires one that reuses the exception handler's
own status map, so only exceptions mapping to a server-class status
(`INTERNAL`, `UNAVAILABLE`, ...) are captured, keeping expected client errors
like a mapped `ValueError` out of the error tracker.

`SentrySdkAdapter` (`grpc_server_kit.observability.sentry`, `[sentry]`
extra) adapts the global `sentry_sdk` module API to `ErrorReporterProtocol`;
it assumes `sentry_sdk.init(...)` has already run.

## SDK initialization is the application's job

None of the three seams initializes its own backend, and that's deliberate:
`sentry_sdk.init(...)`, OpenTelemetry `TracerProvider` setup, and starting a
Prometheus `/metrics` HTTP server are process-wide concerns with their own
lifecycle, unrelated to any single gRPC server instance. Your application
does this once at startup, then hands the resulting adapters to the
interceptors — directly, or via the
[Dishka providers](advanced.md#dishka-di-providers):

```python
import sentry_sdk
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import start_http_server

sentry_sdk.init(dsn="...")                  # before building any interceptor
trace.set_tracer_provider(TracerProvider())
start_http_server(9090)                     # scrape target for grpc_requests_total etc.
```

## Health checks are excluded by default

All four observability interceptors — metrics, request logger, tracing,
sentry — default their `skip_methods` to the gRPC Health `Check`/`Watch`
methods, so routine liveness/readiness polling doesn't inflate request logs,
latency histograms, or trace and error volume. Pass your own `skip_methods=`
if you'd rather observe health traffic too — see
[Interceptors](interceptors.md#skip_methods) for exactly what skipping does
under the hood.
