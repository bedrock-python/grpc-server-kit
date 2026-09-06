# Quick start

## Minimal server

```python
import asyncio

from grpc_server_kit import GrpcApp

app = GrpcApp(port=50051)
app.add_servicer(MyServicer(), add_MyServiceServicer_to_server)

asyncio.run(app.run())
```

`app.run()` builds the `grpc.aio` server, binds the port (TLS-aware when
configured), installs SIGINT/SIGTERM handlers, and serves until termination —
then drains in-flight RPCs for the configured grace period.

A `GrpcApp` instance is **single-use**: once its server has run and stopped,
create a new instance to serve again (gRPC servers cannot restart).

## Adding the observability chain

```python
from grpc_server_kit import GrpcApp, GrpcServerConfig
from grpc_server_kit.aio.interceptors import (
    AsyncContextInterceptor, AsyncExceptionHandlerInterceptor,
    AsyncMetricsInterceptor, AsyncRequestLoggerInterceptor, HeaderConfig,
)
from grpc_server_kit.observability.metrics import get_grpc_server_metrics

config = GrpcServerConfig(host="[::]", port=50051, grace_period=10.0)
app = GrpcApp(config, interceptors=[
    AsyncMetricsInterceptor(metrics=get_grpc_server_metrics(), service_name="my.pkg.MyService"),
    AsyncContextInterceptor([HeaderConfig("x-request-id", "request_id")]),
    AsyncRequestLoggerInterceptor(),
    AsyncExceptionHandlerInterceptor(MY_ERROR_STATUS_MAP),
])
app.add_servicer(MyServicer(), add_MyServiceServicer_to_server)
```

Canonical chain order (outermost first): `metrics → context → logger → tracing
→ exception handler → sentry`. Sentry sits *inside* the exception handler so it
observes raw handler exceptions before they become aborts.

## Health checks

```python
from grpc_server_kit.aio.health import DatabaseHealthChecker, RedisHealthChecker

app.enable_health(checkers=[
    DatabaseHealthChecker(session_maker),   # [postgres] extra
    RedisHealthChecker(redis_client),       # [redis] extra
])
```

Requires the `[health]` extra. `Check` and `Watch` share one real dependency
check per cache TTL (single-flight), so kubelet probes and multiple watchers
never stampede a degraded dependency.

## Reflection and channelz

```python
app.enable_reflection(["my.pkg.MyService"])   # [reflection] extra
app.enable_channelz()                          # [channelz] extra
```

`enable_reflection` needs the names: `grpc.health.v1.Health` is appended to
the list you pass whenever health is also enabled, so it is reflected without
you spelling it out — but health never turns reflection on by itself, and
`settings.enable_reflection = True` with no `enable_reflection([...])` call
fails the build with `ValueError`.

## Embedding and tests

```python
async with GrpcApp(host="127.0.0.1", port=0) as app:   # ephemeral port
    print(app.bound_port)
    ...
```

The context manager starts the server without signal handling and stops it with
the configured grace period on exit.
