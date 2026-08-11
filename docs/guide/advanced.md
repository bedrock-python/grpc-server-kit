# Advanced

## The pieces under GrpcApp

`GrpcApp` is sugar over small, composable pieces you can use directly:

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
bind_server_port(server, settings)            # honors settings.ssl_enabled
await run_async_grpc_server(server, address=f"{settings.host}:{settings.port}")
```

## Writing custom interceptors

Subclass `AsyncServerInterceptor` and implement one async-generator hook. It
wraps **all four** RPC kinds; for response-streaming calls the `yield` covers
the full stream:

```python
import time
from grpc_server_kit.aio.interceptors import AsyncServerInterceptor, RpcCall

class TimingInterceptor(AsyncServerInterceptor):
    async def around_call(self, call: RpcCall):
        start = time.perf_counter()
        try:
            yield                      # the RPC (or the full response stream)
        finally:
            log.info("%s took %.3fs", call.method_name, time.perf_counter() - start)
```

`skip_methods` (constructor kwarg) excludes methods with zero overhead — the
observability interceptors skip the gRPC health methods by default.

Note: `context.abort()` raises `grpc.aio.AbortError` (not `grpc.RpcError`);
interceptors that must not treat a deliberate abort as a failure should
re-raise it.

## Dishka DI providers

`grpc_server_providers(...)` bundles settings, metrics/Sentry/tracer seams, the
canonical interceptor chain, and the server provider in one call:

```python
from dishka import make_async_container
from grpc_server_kit.dishka import GrpcioProvider
from grpc_server_kit.aio.dishka import grpc_server_providers
from grpc_server_kit.aio.server import AsyncServer

container = make_async_container(
    *grpc_server_providers(
        settings.grpc,
        service_name="my.pkg.MyService",
        error_status_map=MY_ERROR_STATUS_MAP,
    ),
    GrpcioProvider(),
    MyDomainProvider(),
)

server = await container.get(AsyncServer)
add_MyServiceServicer_to_server(MyServicer(), server)
```

All three observability seams degrade to `None` (and the interceptors no-op)
when disabled or when their extra is absent. Sentry captures only server-class
errors (per the same status map the exception handler uses) inside a per-RPC
isolated scope. SDK initialization (`sentry_sdk.init`, OpenTelemetry provider
setup, the `/metrics` HTTP server) is a process-wide concern owned by your
application runtime, not by these providers.

## Signals and lifecycle

`ServerLifecycleManager` owns start → serve → drain → stop:

- signal handlers are installed **before** the server starts and restored
  (to the exact previous handlers) **after** the drain completes — a repeated
  Ctrl+C during the grace period is absorbed instead of killing the process;
- `request_shutdown(reason)` triggers a graceful stop programmatically. It is
  idempotent and safe at any point of the lifecycle: requested before serving
  starts it is remembered and honored as soon as the server is up (a signal
  racing startup is never lost), requested after the stop it is a no-op. So
  callers never need an "is it still running?" check — which would be racy
  anyway, since the server can terminate on its own at any moment. Repeated
  requests keep the first reason, which is what actually triggered the stop;
- a manager instance is reusable across sequential runs.
