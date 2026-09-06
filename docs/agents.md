# grpc-server-kit for AI agents

> One page holding everything a coding assistant needs to wire and run a
> `grpc.aio` server with grpc-server-kit correctly, plus a map of where the rest of
> the documentation keeps the details it leaves out. Give an agent this page rather
> than the whole site.

| | |
|---|---|
| Package | `grpc-server-kit` on PyPI, import root `grpc_server_kit` |
| Requires | Python 3.12+, `grpcio>=1.78,<2` — the only hard dependency |
| Install | `pip install grpc-server-kit` · extras: `reflection`, `channelz`, `settings`, `health`, `postgres`, `redis`, `metrics`, `tracing`, `sentry`, `dishka`, `all` |
| Async | `grpc_server_kit.aio`, over `grpc.aio` — this is the whole server API |
| Sync | There is no sync server. `bind_server_port` accepts a sync `grpc.Server`, and `config` / `options` / `credentials` / `signals` are transport-agnostic; everything else is `grpc.aio` |
| Source | <https://github.com/bedrock-python/grpc-server-kit> |

## How to read this page

Every page of this site is also served as raw Markdown at its own URL with `.md` in
place of the trailing slash — this page is `/agents.md`, the health guide is
`/guide/health.md` — so anything the map below points at can be fetched as plain text
rather than scraped out of HTML. The **Copy page** control at the top of a page does
the same thing for a human with a chat window open. The one exception is the API
reference: its Markdown is a single instruction to a docstring renderer rather than the
API, so it carries neither the control nor a `.md` twin — read it as HTML, or read the
docstrings in the source.

Top to bottom before writing code. [Rules that hold or break the code](#rules-that-hold-or-break-the-code)
is the section correctness lives in — those are the things the library will not save you
from. Every name used below is in the public API; if you need something not listed here,
fetch the page the [documentation map](#documentation-map) points at rather than guessing
a method that sounds plausible.

## Scope

**It does** everything around a `grpc.aio` server that is not your service logic:
validated channel options from a settings object, TLS/mTLS credential loading with
permission and PEM checks, port binding, SIGINT/SIGTERM handling and a graceful drain,
a streaming-aware interceptor base class plus six shipped interceptors, the gRPC Health
Checking Protocol v1 with a single-flight TTL cache and Postgres/Redis checkers,
observability seams declared as structural protocols, and ready-made Dishka providers.

**It does not** compile protobufs — `protoc`/`buf` and the generated
`add_*Servicer_to_server` functions are yours; it has no client side at all (no channel,
stub or retry helpers); it has no sync server facade; it runs no scheduler and no HTTP
server; it never calls `sentry_sdk.init`, sets up an OpenTelemetry `TracerProvider` or
starts a Prometheus `/metrics` endpoint — those are process-wide and stay with your
application; and it does not restart a stopped server, because gRPC cannot.

## Mental model

Four nouns, in the order a request meets them.

* **Settings** — one object satisfying `GrpcServerSettingsProtocol`. Two shipped shapes
  are interchangeable everywhere: `GrpcServerConfig` (stdlib dataclass, core) and
  `BaseGrpcServerSettings` (pydantic model, `[settings]` extra). Both validate at
  construction; a misconfigured server must not start. `build_grpc_options(settings)`
  turns them into the `grpc.` channel options.
* **`GrpcApp`** — the facade. You configure it with plain method calls, then `build()`
  creates the server, registers everything and binds the port, and `run()` installs
  signal handlers and serves until termination. It is a one-way sequence: configure,
  build, run, done.
* **The pieces underneath**, for DI containers and custom lifecycles:
  `AsyncGrpcServerBuilder(settings).with_interceptors(...).with_servicers(...).build()`
  returns an `AsyncServer` (a typed wrapper over `grpc.aio.Server` — generated
  `add_*Servicer_to_server` functions accept it directly, and `.raw_server` is the
  underlying object). Then `bind_server_port(server, settings)` and
  `run_async_grpc_server(server, address=...)`.
* **`ServerLifecycleManager`** — start, serve, drain, stop, restore signal handlers.
  `request_shutdown(reason)` stops it programmatically; it is idempotent, keeps the
  first reason, and a request made before the run starts is remembered rather than lost.

Interceptors are a list handed to `grpc.aio.server()` at construction, **outermost
first**. They cannot be added afterwards, which is why every `GrpcApp` configuration
method refuses to run once the server is built.

Everything past the core is an opt-in extra, and each optional subpackage imports its
dependency at module level: `grpc_server_kit.aio.health` needs `[health]`,
`grpc_server_kit.settings` needs `[settings]`, `grpc_server_kit.dishka` and
`grpc_server_kit.aio.dishka` need `[dishka]`. Importing one without its extra raises
`ImportError` at import time. The observability modules are the exception — they import
safely and raise only when you construct the adapter.

## Wiring

```python
import asyncio

from grpc_server_kit import GrpcApp, GrpcServerConfig
from grpc_server_kit.aio.interceptors import (
    AsyncExceptionHandlerInterceptor,
    AsyncRequestLoggerInterceptor,
)

from my_pkg_pb2_grpc import add_MyServiceServicer_to_server

config = GrpcServerConfig(host="[::]", port=50051, grace_period=10.0)

app = GrpcApp(
    config,
    interceptors=[                                   # outermost first
        AsyncRequestLoggerInterceptor(),
        AsyncExceptionHandlerInterceptor(),          # innermost of the two
    ],
)
app.add_servicer(MyServicer(), add_MyServiceServicer_to_server)
app.enable_reflection(["my.pkg.MyService"])          # [reflection] extra
app.enable_health(checkers=[MyChecker()])            # [health] extra

asyncio.run(app.run())        # build + bind + SIGINT/SIGTERM + graceful drain
```

Without a settings object, `GrpcApp(host=..., port=...)` builds a default
`GrpcServerConfig` for you — but passing `settings` **and** a shortcut raises
`ValueError`.

For tests and embedding, the async context manager starts the server without touching
signal handlers and stops it with the configured grace period:

```python
async with GrpcApp(host="127.0.0.1", port=0) as app:   # 0 = ephemeral port
    print(app.bound_port)
```

The same server without the facade:

```python
from grpc_server_kit import bind_server_port
from grpc_server_kit.aio import AsyncGrpcServerBuilder, run_async_grpc_server

server = (
    AsyncGrpcServerBuilder(config)
    .with_interceptors(interceptors)
    .with_servicers(lambda s: add_MyServiceServicer_to_server(MyServicer(), s))
    .with_reflection(["my.pkg.MyService"])
    .build()
)
port = bind_server_port(server, config)               # honors config.ssl_enabled
await run_async_grpc_server(server, address=f"{config.host}:{port}")
```

## The API

### `grpc_server_kit` (package root)

| Name | What it is |
|---|---|
| `GrpcApp` | The facade — re-exported from `grpc_server_kit.aio.app` |
| `GrpcServerConfig` | Settings dataclass, core |
| `build_grpc_options(settings)` | `list[tuple[str, int]]` of gRPC channel options |
| `COMPRESSION_ALGORITHMS` | `{"none": 0, "deflate": 1, "gzip": 2}` |
| `load_server_credentials(settings, strict=True)` | `grpc.ServerCredentials` from the TLS files |
| `bind_server_port(server, settings)` | Binds, TLS-aware; returns the **actual** port |
| `setup_signal_handlers(callback)` | Installs SIGINT/SIGTERM on a process-global manager |
| `GrpcSettingsProtocol`, `GrpcSslSettingsProtocol`, `GrpcServerSettingsProtocol`, `GrpcServerProtocol`, `GrpcAsyncServerProtocol` | The structural seams |
| `__version__` | The version string |

`grpc_server_kit.protocols` also defines `GrpcServiceName`, a `NewType("GrpcServiceName", str)`
used as the DI key for the fully-qualified service name. It is not re-exported at the
package root — import it from `grpc_server_kit.protocols`.

### `grpc_server_kit.aio`

Everything above plus `AsyncGrpcServerBuilder`, `AsyncServer`,
`create_async_grpc_server`, `create_base_async_grpc_server`, `ServerLifecycleManager`,
`run_async_grpc_server` and `reset_signal_handlers`. The subpackages
(`aio.interceptors`, `aio.health`, `aio.observability`, `aio.dishka`) are **not**
re-exported here — import them by their own module path.

### `GrpcApp`

`GrpcApp(settings=None, *, host=None, port=None, interceptors=None)`.

| Method | When | What it does |
|---|---|---|
| `add_servicer(servicer, add_to_server)` | before build | Registers via the generated `add_*Servicer_to_server` |
| `register(callback)` | before build | Escape hatch: the callback gets the raw `grpc.aio.Server` |
| `add_interceptors(interceptors)` | before build | Appends to the chain (outermost first) |
| `enable_reflection(service_names)` | before build | `[reflection]` extra; names are required |
| `enable_channelz()` | before build | `[channelz]` extra |
| `enable_health(checkers=None, *, cache_ttl=…, check_timeout=…, service_names=None)` | before build | `[health]` extra; see below |
| `build()` | — | Builds, registers, binds. Idempotent; caches nothing on failure |
| `await run(*, setup_signals=True)` | — | Builds if needed, then serves until termination |
| `run_sync(*, setup_signals=True)` | — | `asyncio.run(self.run(...))` |
| `request_shutdown(reason="requested")` | any time | Idempotent graceful stop; honored even if requested before `run()` |
| `async with app:` | — | Starts without signal handling, stops with the grace period |
| `.settings` / `.server` / `.bound_port` | after build for the last two | The settings, the `AsyncServer`, the actually bound port |

`enable_health` defaults `cache_ttl` and `check_timeout` from `settings.health` when the
settings object carries that block (`BaseGrpcServerSettings` does), otherwise from
`DEFAULT_HEALTH_CACHE_TTL` (5.0) and `DEFAULT_HEALTH_CHECK_TIMEOUT` (10.0). A `cache_ttl`
of `0` or `None` disables caching.

### `AsyncGrpcServerBuilder`

`with_interceptors(list)` **extends** the chain, `with_servicers(callback)` **sets** it
(a second call replaces the first), `with_reflection(names)`, `with_channelz()`, and
`build() -> AsyncServer`. All the `with_*` methods return `self`.

### Lifecycle

| Name | Signature |
|---|---|
| `ServerLifecycleManager` | `(server, address, grace_period=5.0, signal_manager=None)` |
| `await manager.run(setup_signals=True)` | Serves until termination, request, or signal |
| `await manager.stop()` | Drains, then restores the previous signal handlers |
| `manager.request_shutdown(reason="requested")` | From the event loop; from a thread use `loop.call_soon_threadsafe` |
| `await run_async_grpc_server(server, *, address, grace_period=5.0, setup_signals=True, signal_manager=None)` | The one-call form |
| `SignalManager` (`grpc_server_kit.signals`) | `setup(callback)`, `reset()`, `await reset_async()` |

### Interceptors

Subclass `AsyncServerInterceptor` and implement `around_call` as an async generator that
yields exactly once. `RpcCall` is the frozen view of one RPC: `method_name`, `request`,
`context`, `request_streaming`, `response_streaming`.

```python
from collections.abc import AsyncIterator

from grpc_server_kit.aio.interceptors import AsyncServerInterceptor, RpcCall


class TimingInterceptor(AsyncServerInterceptor):
    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        start = time.perf_counter()
        try:
            yield                       # the RPC, or the FULL response stream
        finally:
            log.info("%s took %.3fs", call.method_name, time.perf_counter() - start)
```

The six shipped interceptors, in canonical order (outermost first):

| # | Constructor | Notes |
|---|---|---|
| 1 | `AsyncMetricsInterceptor(metrics=None, service_name="unknown", *, skip_methods=SKIPPED_HEALTH_METHODS)` | `metrics=None` no-ops; empty `service_name` raises `ValueError` |
| 2 | `AsyncContextInterceptor(header_configs, bind_method_name=True, bind_structlog=True, method_key="grpc_method")` | The only one with **no** `skip_methods` argument |
| 3 | `AsyncRequestLoggerInterceptor(*, log_peer=False, log_request_on_error=False, skip_methods=SKIPPED_HEALTH_METHODS)` | Peer is logged as a protocol name only, never an IP |
| 4 | `AsyncTracingInterceptor(service_name, tracer=None, *, skip_methods=SKIPPED_HEALTH_METHODS)` | `service_name` is required; `tracer=None` no-ops |
| 5 | `AsyncExceptionHandlerInterceptor(error_status_map=None, *, detail_factory=None, merge_defaults=True)` | Your map wins over the defaults it is merged into |
| 6 | `AsyncSentryInterceptor(sentry=None, *, capture_filter=None, skip_methods=SKIPPED_HEALTH_METHODS)` | Must sit **after** the exception handler in the list |

`AsyncServerInterceptor(*, skip_methods=())` is the base; `skip_methods` holds full RPC
names such as `"/grpc.health.v1.Health/Check"`, and a skipped method gets gRPC's original
unwrapped handler back — zero overhead, not a fast path.

`HeaderConfig(header_name, context_var_name, context_setter=None, default_factory=None,
validator=None, required=False, allow_empty=False)` configures one header for
`AsyncContextInterceptor`. A missing, empty or invalid value aborts the RPC with
`INVALID_ARGUMENT` only when `required=True`; otherwise it is skipped.

Also exported from `grpc_server_kit.aio.interceptors`:
`split_method_name("/pkg.Service/Method") -> ("pkg.Service", "Method")`,
`get_metadata_dict(context)` (per-RPC cached), `default_error_detail(exc, status)`,
`find_mapped_status(exc_type, error_status_map)`, `GRPC_DEFAULT_ERROR_STATUS_MAP`,
`GRPC_SAFE_ERROR_MESSAGES`, the `ErrorDetailFactory` type alias, and the seam protocols
`GrpcServerMetricsProtocol`, `TracerProtocol`, `SpanProtocol`, `SpanAttributeValue`,
`ErrorReporterProtocol`.

`grpc_server_kit.interceptors` (transport-agnostic) holds `SKIPPED_HEALTH_METHODS`,
`X_REQUEST_ID`, `is_server_error(code)` and
`resolve_status_code(exc, context, default=grpc.StatusCode.UNKNOWN)`.

`GRPC_DEFAULT_ERROR_STATUS_MAP` maps `ValueError` → `INVALID_ARGUMENT`, `PermissionError`
→ `PERMISSION_DENIED`, `NotImplementedError` → `UNIMPLEMENTED`, `TimeoutError` →
`DEADLINE_EXCEEDED`, `FileNotFoundError` → `NOT_FOUND`. Anything unmapped resolves to
`INTERNAL`. `TypeError` is deliberately absent — it is a server bug, not a client one.

### Health (`grpc_server_kit.aio.health`, `[health]` extra)

| Name | Signature |
|---|---|
| `AsyncDynamicHealthServicer` | `(checkers=None, cache=None, service_names=None, check_interval=5.0, heartbeat_interval=60.0, check_timeout=10.0)` |
| `HealthCache` | `(ttl=5.0)` — TTL freshness plus single-flight; `ttl <= 0` raises `ValueError` |
| `check_async_overall_health` | `(checkers=None, cache=None, timeout=10.0) -> ServingStatus` |
| `AsyncHealthChecker` | Protocol: `async check(self) -> bool` |
| `AsyncHealthCacheProtocol` | Protocol: `async get_or_set(check_func, now=None)` |
| `DatabaseHealthChecker` | `(session_maker, timeout=5.0)` — `SELECT 1`, `[postgres]` extra |
| `RedisHealthChecker` | `(redis_client, timeout=5.0)` — `PING`, `[redis]` extra |
| `FunctionalHealthChecker` | `(check_func, resource=None, timeout=5.0)` — the adapter both are built on |
| `handle_check_exceptions(check_name)` | Decorator normalising a check's failures to `False` |
| `check_async_database_health`, `check_async_redis_health` | The bare functions behind the two checkers |
| `SessionMakerProtocol`, `RedisClientProtocol` | The duck-typed client shapes |

### Observability

| Name | Module | Extra |
|---|---|---|
| `GrpcServerMetrics(prefix=None, buckets=DEFAULT_GRPC_BUCKETS, registry=None)` | `observability.metrics` | `metrics` |
| `get_grpc_server_metrics(prefix=None, buckets=None)` | `observability.metrics` | `metrics` |
| `DEFAULT_GRPC_BUCKETS`, `make_metric_name(name, prefix=None)` | `observability.metrics` / `.naming` | core |
| `SentrySdkAdapter()` | `observability.sentry` | `sentry` |
| `instrument_aio_server(**kwargs)` / `uninstrument_aio_server()` | `aio.observability` | `tracing` |
| `SpanProtocol`, `TracerProtocol`, `ErrorReporterProtocol`, `SpanAttributeValue` | `observability.protocols` | core |

Two metrics, frozen: `grpc_requests_total` (Counter; labels `service`, `method`,
`status`, `grpc_code`) and `grpc_request_duration_seconds` (Histogram; labels `service`,
`method`).

### Dishka (`[dishka]` extra)

`grpc_server_kit.dishka` re-exports `inject`, `FromDishka`, `GrpcioProvider` and
`from_context`, and adds `GrpcServerSettingsProvider(settings, *, service_name)`.
`grpc_server_kit.aio.dishka` adds `DishkaAioInterceptor`, `AsyncGrpcServerProvider`,
`GrpcServerInterceptorsProvider`, `PrometheusGrpcServerMetricsProvider`,
`SentryAdapterProvider`, `TracerAdapterProvider`, and the bundle:

```python
grpc_server_providers(
    settings, *, service_name,
    error_status_map=None, detail_factory=None, header_configs=None,
    metrics_enabled=None, metrics_prefix=None,
    sentry_enabled=True, tracing_enabled=True,
    reflection_service_names=None,
) -> tuple[Provider, ...]
```

`GrpcServerInterceptorsProvider` takes `include_metrics` / `include_context` /
`include_request_logger` / `include_tracing` / `include_sentry` /
`include_exception_handler` toggles and `extra_outer` / `extra_inner` slots, and appends
`DishkaAioInterceptor` innermost so the request scope opens per RPC.

### Settings fields

`GrpcServerConfig` and `BaseGrpcServerSettings` carry the same fields with the same
defaults: `host="[::]"`, `port=50051`, `ssl_enabled=False`, `ssl_cert_file`,
`ssl_key_file`, `ssl_ca_file`, `ssl_client_auth=False`, `ssl_max_cert_size=None` (1 MB),
`keepalive_time_ms=7_200_000`, `keepalive_timeout_ms=20_000`,
`keepalive_permit_without_calls=False`,
`http2_min_recv_ping_interval_without_data_ms=300_000`, `http2_max_pings_without_data=2`,
`max_concurrent_rpcs=None`, `max_connection_idle_ms=None`, `max_connection_age_ms=None`,
`max_connection_age_grace_ms=None`, `max_send_message_length=4 MiB`,
`max_receive_message_length=4 MiB`, `max_metadata_size=8 KiB`,
`compression_algorithm=None`, `initial_stream_window_size=65535`,
`initial_connection_window_size=65535`, `enable_reflection=False`,
`enable_channelz=False`, `grace_period=5.0`, `metrics_enabled=False`.
`BaseGrpcServerSettings` additionally nests `health: BaseHealthSettings`
(`cache_ttl=5.0`, `check_timeout=10.0`); `GrpcServerConfig` has no `health` block.
The kit's defaults live in `grpc_server_kit.constants` as `DEFAULT_*` names, alongside
`HEALTH_SERVICE_NAME = "grpc.health.v1.Health"`.

## Rules that hold or break the code

1. **Configure before `build()`.** `add_servicer`, `register`, `add_interceptors`,
   `enable_reflection`, `enable_channelz` and `enable_health` all raise `RuntimeError`
   once the server exists. Interceptors especially are handed to `grpc.aio.server()` at
   construction — there is no adding one to a built server, at any layer.
2. **A `GrpcApp` is single-use.** After `run()` returns, or after the async context
   manager exits, the app is finished and every entry point raises `RuntimeError`. gRPC
   servers cannot restart; make a new `GrpcApp`. A `ServerLifecycleManager`, by contrast,
   *is* reusable across sequential runs.
3. **The interceptor list is outermost first**, and position is semantics, not style.
   `AsyncSentryInterceptor` must come **after** `AsyncExceptionHandlerInterceptor` in the
   list — closer to the handler — because the handler converts raw exceptions into
   `grpc.aio.AbortError`, and Sentry never captures an `AbortError`. Put it first and it
   reports nothing, ever, with no error to tell you.
4. **`context.abort()` raises `grpc.aio.AbortError`, which is not a `grpc.RpcError`.**
   `except grpc.RpcError:` does not catch a deliberate abort; `except Exception:` does.
   An interceptor that logs and re-raises everything will fire on every intentional
   abort unless it filters `AbortError` out first.
5. **`around_call` is an async generator, not a coroutine.** It must contain exactly one
   `yield`; the RPC — including the complete consumption of a response stream — happens
   there. A body with no `yield` anywhere is a plain coroutine, and every RPC through
   that interceptor dies with `TypeError: 'coroutine' object is not an async iterator`;
   a body that returns before reaching its `yield` raises `RuntimeError: generator
   didn't yield`. Neither is caught for you.
6. **Reflection needs names.** `settings.enable_reflection = True` with no
   `enable_reflection([...])` call fails the build with `ValueError`. `enable_health()`
   adds `grpc.health.v1.Health` to the reflection list only when you already gave it
   names; it never turns reflection on by itself.
7. **`grace_period=None` means abort immediately, not wait forever.** That is gRPC's own
   semantics for `stop(None)`. The settings objects type `grace_period` as a
   non-negative float and default it to 5.0; only the lower-level
   `ServerLifecycleManager` / `run_async_grpc_server` / `AsyncServer.stop` accept `None`.
8. **The private key must be owner-only on Unix.** `load_server_credentials` raises
   `PermissionError` for a key with any group or world bits set (`chmod 600`), and
   `bind_server_port` always calls it in strict mode — there is no way to relax that
   through the settings. Pass `strict=False` yourself only if you are calling
   `load_server_credentials` directly and accept the warning instead.
9. **`port=0` binds an ephemeral port, and only `bind_server_port` knows which.** Read it
   back from `app.bound_port` (or the return value of `bind_server_port`); before the
   build it raises `RuntimeError`, and `settings.port` still says `0`.
10. **Optional subpackages import their extra at module level.** `import
    grpc_server_kit.aio.health` raises `ImportError` without `[health]`, and the same
    holds for `grpc_server_kit.settings` (`[settings]`) and both dishka packages
    (`[dishka]`). Guard the import, do not guard the call.
11. **A health checker returns `bool` and does not raise for "unhealthy".** A raised
    exception is logged and folded into `NOT_SERVING` all the same, but it costs you the
    per-checker signal. `heartbeat_interval` must be `>= check_interval`, `check_timeout`
    and `check_interval` must be positive — all validated eagerly at construction.
12. **`HealthCache(ttl=0)` raises; `enable_health(cache_ttl=0)` does not.** The app
    normalises a falsy TTL to "no cache" before constructing anything, which is why a
    settings-valid `cache_ttl=0` is safe there and fatal here.
13. **`with_servicers` replaces, `with_interceptors` extends.** Calling
    `with_servicers` twice on the same builder silently drops the first callback.
    Register several servicers from one callback, or register them on the built
    `AsyncServer` afterwards.
14. **The Dishka bundle hands you a server with no servicers on it.** `AsyncGrpcServerProvider`
    deliberately stops at interceptors, options and reflection/channelz: resolve
    `AsyncServer` from the container, register your servicers on it, then
    `bind_server_port` and `run_async_grpc_server`.
15. **Signal handlers need the main thread.** Off it, `SignalManager.setup` logs a warning
    and gives up rather than crashing — the server then runs with no signal-driven
    shutdown at all. `setup_signal_handlers()` uses one process-global manager; `GrpcApp`
    and `ServerLifecycleManager` each use their own, and restore the exact handlers that
    were installed before them.
16. **The metrics `method` label is the full RPC name** (`/pkg.Service/Method`), and the
    `service` label is the constructor's `service_name`, not the service parsed out of
    the call. `get_grpc_server_metrics` caches by prefix and raises `ValueError` if you
    ask for a cached prefix again with different `buckets`.
17. **`AsyncTracingInterceptor` does not read `traceparent`.** It opens a new span with
    OTel semconv attributes; incoming trace context comes from `instrument_aio_server()`
    (`[tracing]`). The two compose — use both for real distributed tracing.
18. **The settings shapes are structural, not nominal.** Anything with the right
    read-only properties satisfies `GrpcServerSettingsProtocol`; there is no base class
    to inherit and no registration step. `GrpcServerConfig` is `kw_only=True` and
    `slots=True`, so every field is a keyword and unknown ones are a `TypeError`.
19. **`build_grpc_options` rejects bad values loudly.** A negative option or an
    unsupported `compression_algorithm` raises `ValueError`; `None` means "use the kit
    default". Note the asymmetry: `COMPRESSION_ALGORITHMS` accepts `"none"`, but
    `BaseGrpcServerSettings` types the field as `Literal["deflate", "gzip"] | None`.

## Common mistakes

```python
# WRONG — configuring an app that has already been built
app = GrpcApp(port=50051)
await app.run()
app.add_servicer(Other(), add_OtherServicer_to_server)   # RuntimeError
await app.run()                                          # RuntimeError: single-use

# RIGHT
app = GrpcApp(port=50051)
app.add_servicer(MyServicer(), add_MyServiceServicer_to_server)
app.add_servicer(Other(), add_OtherServicer_to_server)
await app.run()
```

```python
# WRONG — Sentry outside the exception handler: it will capture nothing, silently
interceptors = [
    AsyncSentryInterceptor(sentry=SentrySdkAdapter()),
    AsyncExceptionHandlerInterceptor(),
]

# RIGHT — outermost first, so Sentry sits closer to the handler
interceptors = [
    AsyncExceptionHandlerInterceptor(),
    AsyncSentryInterceptor(sentry=SentrySdkAdapter()),
]
```

```python
# WRONG — around_call written as a coroutine
class Mine(AsyncServerInterceptor):
    async def around_call(self, call: RpcCall) -> None:
        log.info("start")
        return                      # no yield: every RPC raises TypeError instead

# RIGHT — an async generator with exactly one yield
class Mine(AsyncServerInterceptor):
    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        log.info("start")
        try:
            yield
        finally:
            log.info("done")
```

```python
# WRONG — expecting RpcError to cover a deliberate abort
try:
    yield
except grpc.RpcError:               # AbortError is NOT an RpcError
    metrics.failure()
    raise

# RIGHT — narrowest first
try:
    yield
except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
    raise
except grpc.aio.AbortError:
    raise                           # deliberate; the context carries the real status
except grpc.RpcError:
    raise
except Exception:
    metrics.failure()
    raise
```

```python
# WRONG — reading the port off the settings after asking for an ephemeral one
app = GrpcApp(host="127.0.0.1", port=0)
await app.run()
client = f"127.0.0.1:{app.settings.port}"     # 0

# RIGHT
async with GrpcApp(host="127.0.0.1", port=0) as app:
    client = f"127.0.0.1:{app.bound_port}"
```

```python
# WRONG — guarding an optional extra at the call site; the import already failed
from grpc_server_kit.aio.health import DatabaseHealthChecker   # ImportError, right here

def make_checker(session_maker):
    try:
        return DatabaseHealthChecker(session_maker)
    except ImportError:             # never reached — the module never loaded
        return None

# RIGHT — install the extra, or guard the import itself
try:
    from grpc_server_kit.aio.health import DatabaseHealthChecker
except ImportError:
    DatabaseHealthChecker = None
```

```python
# WRONG — two servicer callbacks on one builder; the first is dropped
builder.with_servicers(lambda s: add_AServicer_to_server(A(), s))
builder.with_servicers(lambda s: add_BServicer_to_server(B(), s))

# RIGHT — one callback registers everything
def register(server):
    add_AServicer_to_server(A(), server)
    add_BServicer_to_server(B(), server)

builder.with_servicers(register)
```

## Errors

**The kit defines no exception classes of its own.** Do not import or catch a
`GrpcServerKitError` — there isn't one. Everything it raises is a builtin or a gRPC type:

| Raised | When |
|---|---|
| `ValueError` | Invalid settings (host, port, `grace_period`, `max_concurrent_rpcs`, TLS combinations), a negative channel option, an unsupported compression algorithm, an invalid metric prefix, an invalid reflection service name, reflection with no names, `settings` together with a `host`/`port` shortcut, an out-of-range health interval or timeout, a metrics prefix re-requested with different buckets |
| `RuntimeError` | Configuring or running a `GrpcApp` out of order: already built, already running, already finished, or reading `.server` / `.bound_port` before the build |
| `ImportError` | An optional subpackage or adapter used without its extra — `[health]`, `[settings]`, `[dishka]`, `[reflection]`, `[channelz]`, `[metrics]`, `[sentry]`, `[tracing]` |
| `FileNotFoundError` | A TLS certificate, key or CA file that is not there |
| `PermissionError` | A private key readable beyond its owner, or a TLS file that cannot be read |
| `OSError` | Any other I/O failure reading a TLS file |
| `grpc.aio.AbortError` | Raised by `context.abort()` — by your handler, by `AsyncExceptionHandlerInterceptor`, by `AsyncContextInterceptor` on a missing required header, or by the health servicer aborting a broken `Watch` |
| `grpc.RpcError` | gRPC's own transport-level errors, passed through untouched |

Pydantic's `ValidationError` replaces `ValueError` when the settings object is a
`BaseGrpcServerSettings`.

## Documentation map

Fetch a page when the task is the one named beside it.

| Page | Read it when |
|---|---|
| [Home](index.md) | installing, choosing extras, the one-paragraph pitch |
| [Quick start](guide/quickstart.md) | the first server end to end: servicers, chain, health, reflection, embedding |
| [Configuration](guide/configuration.md) | picking a settings shape, TLS/mTLS, channel tuning, shutdown semantics |
| [Interceptors](guide/interceptors.md) | `around_call`, streaming coverage, `skip_methods`, the handler cache, `AbortError`, writing your own |
| [Health](guide/health.md) | the Health v1 servicer, writing checkers, the TTL cache, `Check` vs `Watch`, Kubernetes probes |
| [Observability](guide/observability.md) | the four seam protocols, Prometheus metrics, tracing attributes and propagation, Sentry scopes |
| [Advanced](guide/advanced.md) | the pieces under `GrpcApp`, Dishka providers, signals and lifecycle |
| [API reference](reference/index.md) | an exact signature, field or docstring — HTML only, see above |
| [Changelog](changelog.md) | what changed between versions |
