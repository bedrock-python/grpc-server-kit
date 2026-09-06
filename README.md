# grpc-server-kit

Batteries-optional async gRPC server toolkit (app facade, builder, TLS, lifecycle, health, interceptors, observability)

[![PyPI](https://img.shields.io/pypi/v/grpc-server-kit?color=blue)](https://pypi.org/project/grpc-server-kit/)
[![Python](https://img.shields.io/pypi/pyversions/grpc-server-kit)](https://pypi.org/project/grpc-server-kit/)
[![License](https://img.shields.io/github/license/bedrock-python/grpc-server-kit)](LICENSE)
[![CI](https://github.com/bedrock-python/grpc-server-kit/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/bedrock-python/grpc-server-kit/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/bedrock-python/grpc-server-kit/graph/badge.svg)](https://codecov.io/gh/bedrock-python/grpc-server-kit)
[![Docs](https://img.shields.io/badge/docs-online-blue)](https://bedrock-python.github.io/grpc-server-kit/)

`grpc-server-kit` gives you a clean, production-grade foundation for building
`grpc.aio` servers: a one-object `GrpcApp` facade, validated channel options,
TLS/mTLS credential loading, graceful signal-driven shutdown, streaming-aware
interceptors, and gRPC health checking — with **optional** extras for
reflection, channelz, Prometheus metrics, OpenTelemetry tracing, Sentry,
Dishka DI, and typed pydantic settings.

The core depends only on `grpcio`. Every integration is an opt-in extra, so you
install exactly what you use.

> [!TIP]
> **Building this with an AI assistant?** Hand it
> **[one page](https://bedrock-python.github.io/grpc-server-kit/agents/)** instead of
> the whole site: the complete API surface, the ordering rules that break a server when
> they are broken (build-then-configure, outermost-first interceptors, Sentry inside the
> exception handler), the mistakes models actually make with `grpc.aio`, and a map of
> which page to fetch for the rest. Every docs page is also served as raw Markdown at its
> own URL, and a **Copy page** button at the top of each one hands it straight to a chat
> window.

## Installation

```bash
pip install grpc-server-kit                      # core (grpcio only)
pip install "grpc-server-kit[settings]"          # + pydantic settings models
pip install "grpc-server-kit[health]"            # + gRPC health checking
pip install "grpc-server-kit[metrics,tracing]"   # + Prometheus + OpenTelemetry
pip install "grpc-server-kit[all]"               # everything
```

**Requirements:** Python 3.12+

## Quick start

```python
import asyncio

from grpc_server_kit import GrpcApp

app = GrpcApp(port=50051)
app.add_servicer(MyServicer(), add_MyServiceServicer_to_server)

asyncio.run(app.run())   # build + bind + SIGINT/SIGTERM + graceful shutdown
```

That's the whole server. `GrpcApp` builds the `grpc.aio` server, binds the port
(TLS-aware), installs signal handlers, and serves until termination. More knobs
when you need them:

```python
from grpc_server_kit import GrpcApp, GrpcServerConfig
from grpc_server_kit.aio.interceptors import (
    AsyncContextInterceptor, AsyncExceptionHandlerInterceptor,
    AsyncMetricsInterceptor, AsyncRequestLoggerInterceptor, HeaderConfig,
)
from grpc_server_kit.observability.metrics import get_grpc_server_metrics

config = GrpcServerConfig(host="[::]", port=50051, grace_period=10.0)   # stdlib dataclass
app = GrpcApp(config, interceptors=[
    AsyncMetricsInterceptor(metrics=get_grpc_server_metrics(), service_name="my.pkg.MyService"),
    AsyncContextInterceptor([HeaderConfig("x-request-id", "request_id")]),
    AsyncRequestLoggerInterceptor(),
    AsyncExceptionHandlerInterceptor(MY_ERROR_STATUS_MAP),   # domain error → gRPC status
])
app.add_servicer(MyServicer(), add_MyServiceServicer_to_server)
app.enable_health(checkers=[DatabaseHealthChecker(session_maker)])   # [health] extra
app.enable_reflection(["my.pkg.MyService"])                          # [reflection] extra

asyncio.run(app.run())
```

Prefer pydantic-validated, env-friendly settings? Use
`grpc_server_kit.settings.BaseGrpcServerSettings` (the `[settings]` extra) —
`GrpcServerConfig` and `BaseGrpcServerSettings` are interchangeable everywhere
the kit accepts settings.

## Streaming-aware interceptors

Every interceptor wraps the **whole** RPC for all four call kinds — unary-unary,
unary-stream, stream-unary, stream-stream. Durations cover the full stream,
errors raised mid-stream are mapped to proper gRPC statuses, and cleanup always
runs. Write your own by implementing a single async-generator hook:

```python
from grpc_server_kit.aio.interceptors import AsyncServerInterceptor, RpcCall

class TimingInterceptor(AsyncServerInterceptor):
    async def around_call(self, call: RpcCall):
        start = time.perf_counter()
        try:
            yield                      # the RPC (or the full response stream) runs here
        finally:
            log.info("%s took %.3fs", call.method_name, time.perf_counter() - start)
```

Canonical chain order (outermost first): `metrics → context → logger → tracing
→ exception handler → sentry`. The exception handler maps exception types to
gRPC statuses over the MRO and sends safe, non-leaking details; deliberate
`context.abort()` calls pass through untouched. Sentry sits **inside** the
exception handler so it observes raw handler exceptions before they become
aborts (outside it would never capture anything), with a capture filter that
reports only server-class errors.

## Advanced API

`GrpcApp` is sugar over small, composable pieces you can use directly — for DI
containers, custom lifecycles, or embedding:

```python
from grpc_server_kit import bind_server_port
from grpc_server_kit.aio import AsyncGrpcServerBuilder, run_async_grpc_server

server = (
    AsyncGrpcServerBuilder(settings)
    .with_interceptors(interceptors)
    .with_servicers(lambda s: add_MyServiceServicer_to_server(MyServicer(), s))
    .with_reflection(["my.pkg.MyService"])
    .build()
)
bind_server_port(server, settings)            # honors settings.ssl_enabled (TLS/mTLS)
await run_async_grpc_server(server, address=f"{settings.host}:{settings.port}")
```

## Dishka DI providers (cut the per-service boilerplate)

If your service uses [Dishka](https://github.com/reagento/dishka), the kit ships ready-made
providers so you register a handful of providers instead of hand-writing the interceptor
list, settings aliasing, and a metrics provider. `grpc_server_providers(...)` bundles the
standard set in one call:

```python
from dishka import make_async_container
from grpc_server_kit.dishka import GrpcioProvider, FromDishka, inject
from grpc_server_kit.aio.dishka import grpc_server_providers
from grpc_server_kit.aio.server import AsyncServer
from grpc_server_kit.aio import run_async_grpc_server
from grpc_server_kit import bind_server_port

container = make_async_container(
    *grpc_server_providers(                       # settings + metrics + sentry/tracer seams
        settings.grpc,                            # + the canonical interceptor chain + AsyncServer
        service_name="my.pkg.MyService",
        error_status_map=MY_ERROR_STATUS_MAP,     # service-specific domain map (optional)
        metrics_enabled=settings.grpc.metrics_enabled,
    ),
    GrpcioProvider(),                             # request-scoped ServicerContext for FromDishka
    MyDomainProvider(),                           # your use-cases / repos
)

server = await container.get(AsyncServer)         # interceptors + options already wired
add_MyServiceServicer_to_server(MyServicer(), server)   # AsyncServer works directly
bind_server_port(server, settings.grpc)
await run_async_grpc_server(server, address=f"{settings.grpc.host}:{settings.grpc.port}")
```

The interceptor chain is built in the canonical order
(`metrics → context → logger → tracing → exception → sentry → DishkaAioInterceptor`), with
`include_*` toggles and `extra_outer` / `extra_inner` slots on
`GrpcServerInterceptorsProvider`. Providers degrade gracefully: the metrics / Sentry / tracer
seams resolve to `None` when disabled or their extra is absent, and the interceptors no-op.
SDK initialization (`sentry_sdk.init`, OpenTelemetry setup, the `/metrics` HTTP server) is a
process-wide concern and is **not** done by these providers. Requires `[dishka]`
(+ `[metrics]`/`[sentry]`/`[tracing]` for those seams).

Granular providers (`GrpcServerSettingsProvider`, `PrometheusGrpcServerMetricsProvider`,
`SentryAdapterProvider`, `TracerAdapterProvider`, `GrpcServerInterceptorsProvider`,
`AsyncGrpcServerProvider`) are available for finer control.

## What's inside

> **Layout convention:** all async code lives under a single `grpc_server_kit.aio`
> package (mirroring `grpc.aio`), organized by domain (`aio.health`, `aio.interceptors`,
> `aio.observability`, `aio.dishka`). Modules that work for both sync and async stay at
> the top level — either directly (`config`, `options`, `credentials`, `server`,
> `protocols`, `signals`, `settings`) or as shared domain packages (`interceptors` →
> constants/utils, `observability` → protocols/metrics/sentry, `dishka` → providers).

| Module | Responsibility | Extra |
| :--- | :--- | :--- |
| `aio.GrpcApp` | One-object facade: config → servicers → run | core |
| `config.GrpcServerConfig` | Zero-dependency settings dataclass | core |
| `aio.builder.AsyncGrpcServerBuilder` | Fluent builder for an `AsyncServer` | core |
| `aio.create_async_grpc_server` / `aio.AsyncServer` | Server factory + typed wrapper | core |
| `aio.ServerLifecycleManager` / `aio.run_async_grpc_server` | Start/serve/stop with graceful shutdown | core |
| `aio.interceptors.*` | streaming-aware base + context, logger, metrics, tracing, sentry, exception-handler | core |
| `options` / `credentials` / `server` / `signals` / `protocols` | Channel options, TLS/mTLS, port binding, SIGINT/SIGTERM, seams | core |
| `settings.BaseGrpcServerSettings` | Pydantic config model | `settings` |
| `aio.health.*` | gRPC Health v1 servicer, orchestrator, TTL cache, pg/redis checkers | `health` |
| `observability.metrics.GrpcServerMetrics` | Prometheus metric definitions | `metrics` |
| `aio.observability.tracing.instrument_aio_server` | OpenTelemetry instrumentation | `tracing` |
| `observability.sentry.SentrySdkAdapter` | Sentry adapter for the sentry interceptor | `sentry` |
| `aio.dishka.*` / `dishka.*` | DI providers + `DishkaAioInterceptor` re-export | `dishka` |

## Optional dependencies

| Extra | Pulls in | Enables |
| :--- | :--- | :--- |
| `reflection` | `grpcio-reflection` | server reflection |
| `channelz` | `grpcio-channelz` | channelz debugging |
| `settings` | `pydantic` | `BaseGrpcServerSettings` / `BaseHealthSettings` |
| `health` | `grpcio-health-checking` | health servicer / orchestrator / cache |
| `postgres` | `sqlalchemy[asyncio]` | `DatabaseHealthChecker` |
| `redis` | `redis` | `RedisHealthChecker` |
| `metrics` | `prometheus-client` | `GrpcServerMetrics` |
| `tracing` | `opentelemetry-instrumentation-grpc` | `instrument_aio_server` |
| `sentry` | `sentry-sdk` | `SentrySdkAdapter` |
| `dishka` | `dishka`, `protobuf` | DI providers + `DishkaAioInterceptor` |
| `all` | all of the above | everything |

## Examples

Runnable, self-contained scripts in [`examples/`](examples/) — each prints what it's doing and exits on its own:

- [`minimal_server.py`](examples/minimal_server.py) — the smallest real server: `GrpcApp`, one servicer, `asyncio.run(app.run())`.
- [`observability_chain.py`](examples/observability_chain.py) — the canonical interceptor chain (metrics → context → logger → tracing → exception handler → sentry) wired by hand.
- [`custom_interceptor.py`](examples/custom_interceptor.py) — writing your own streaming-aware interceptor with `AsyncServerInterceptor.around_call`.
- [`health_checks.py`](examples/health_checks.py) — `app.enable_health()` with a custom dependency checker, driven through the real `grpc_health.v1` stubs.

## Documentation

Full documentation at [bedrock-python.github.io/grpc-server-kit](https://bedrock-python.github.io/grpc-server-kit/).

- [For AI agents](https://bedrock-python.github.io/grpc-server-kit/agents/) — the whole
  API surface, the rules that break a server when broken and a map of the rest, on one
  page to hand to a coding assistant

## License

Apache 2.0 — see [LICENSE](LICENSE).
