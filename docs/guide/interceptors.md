# Interceptors

An ordinary gRPC interceptor wraps only the moment a handler is invoked —
for a response-streaming RPC that tells you nothing about how long the
stream actually ran, and any cleanup scheduled after the wrapped call fires
before the client has read a single message. `AsyncServerInterceptor`
(`grpc_server_kit.aio.interceptors`) closes that gap: a subclass implements
one async-generator method, `around_call`, and the base class turns it into
a context manager spanning the **entire** RPC — including full consumption
of a streamed response — for all four gRPC call kinds.

## The `around_call` hook

```python
from collections.abc import AsyncIterator

from grpc_server_kit.aio.interceptors import AsyncServerInterceptor, RpcCall


class MyInterceptor(AsyncServerInterceptor):
    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        ...                 # setup — runs before the handler
        yield
        ...                 # teardown — runs after the handler (or the full stream) completes
```

`RpcCall` is the frozen view of one RPC: `method_name` (e.g.
`"/pkg.Service/Method"`), `request` (the request message, or an async
iterator of them for streaming requests), `context`
(`grpc.aio.ServicerContext`), and the `request_streaming` /
`response_streaming` flags. One implementation covers unary-unary,
unary-stream, stream-unary, and stream-stream alike — `intercept_service`
inspects the resolved `grpc.RpcMethodHandler` and dispatches internally to
unary or streaming wiring, but both drive the exact same `around_call`
generator, so a subclass never branches on call kind unless its own logic
needs to.

## Streaming coverage

For a response-streaming call, the code inside the context manager built
from `around_call` iterates the handler's async generator to completion
(`async for response in result: yield response`) before control passes back
out through your `yield`. On early termination — the client disconnects, the
consumer breaks, or an exception propagates — a `finally` block closes the
inner generator immediately, so the handler's own `finally` blocks and any
DI-scoped teardown run **inside** the RPC's async context, not later under
the event loop's async-generator finalizer, which offers no timing guarantee
at all. Practically: durations `AsyncMetricsInterceptor` records and spans
`AsyncTracingInterceptor` opens cover the full stream lifetime, not just
time-to-first-message, and an exception raised at message 500 of a stream is
caught by the same `try`/`except` around your `yield` as one raised before
message 1.

## `skip_methods`

A constructor kwarg — a `Collection[str]` of full method names such as
`"/grpc.health.v1.Health/Check"`. A method in this set is not merely
fast-pathed inside the wrapper: `intercept_service` returns gRPC's original,
unwrapped handler for it, so interception has zero overhead for skipped
methods.

The four observability interceptors default `skip_methods` to the gRPC
Health `Check`/`Watch` methods, so routine liveness/readiness polling
doesn't inflate request logs, latency histograms, or trace volume — see
[Observability](observability.md#health-checks-are-excluded-by-default).

`AsyncContextInterceptor` takes the same kwarg but defaults it to empty: it
binds context variables the handler itself may read, so nothing is skipped
unless you ask. Pass `skip_methods=SKIPPED_HEALTH_METHODS` to keep health
probes out of it — worth doing when a `HeaderConfig` is `required=True`, since
a kubelet probe carries none of your headers and would be aborted with
`INVALID_ARGUMENT`.

## The per-method handler cache

`intercept_service` runs on every incoming RPC, not once at startup, so
re-deriving a wrapped handler on each call would be wasted work. Each
interceptor instance caches `(source_handler, wrapped_handler)` per method
name; a cache hit requires the handler `continuation()` just resolved to be
the **same object** (`is`, not `==`) as the one that produced the cached
entry. Registered servicers hand back stable handler objects, so the cache
is effectively permanent for them; for dynamic/generic handlers whose
resolution can swap the underlying implementation between calls, the
identity check detects the change and rewraps instead of serving a stale
wrapper.

## `context.abort()` and `AbortError`

`grpc.aio.ServicerContext.abort()` raises `grpc.aio.AbortError` — a plain
`Exception` subclass that is **not** a `grpc.RpcError` subclass. Two
consequences for a custom interceptor written around a `yield`:

- `except grpc.RpcError:` does **not** catch a deliberate abort. To treat "the
  handler (or an inner interceptor) already decided the outcome" differently
  from "something broke," add an explicit `except grpc.aio.AbortError:`.
- `except Exception:` **does** catch it. A catch-all that logs and re-raises
  every exception will also fire for every deliberate abort unless it filters
  `AbortError` out first.

Every shipped interceptor that wraps exceptions follows the same shape,
narrowest first:

```python
try:
    yield
except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
    raise
except grpc.aio.AbortError:
    raise               # deliberate — the context already carries the real status
except grpc.RpcError:
    raise               # already a proper gRPC error
except Exception as exc:
    ...                 # genuinely unexpected — map it, log it, capture it
```

## The canonical chain

The six shipped interceptors, outermost first:

| # | Interceptor | What it does |
| :-: | :--- | :--- |
| 1 | `AsyncMetricsInterceptor` | Records request count and duration through any `GrpcServerMetricsProtocol`; outermost so its timer covers every interceptor below it too |
| 2 | `AsyncContextInterceptor` | Binds configured headers (e.g. `x-request-id`) to context vars / structlog before anything else logs or traces |
| 3 | `AsyncRequestLoggerInterceptor` | Logs start, completion, and failure at a severity matched to the resulting status |
| 4 | `AsyncTracingInterceptor` | Opens one OTel-semconv span per RPC through any `TracerProtocol` |
| 5 | `AsyncExceptionHandlerInterceptor` | Maps exception types to `grpc.StatusCode` over the MRO; aborts with a safe, non-leaking detail |
| 6 | `AsyncSentryInterceptor` | Reports unexpected exceptions through any `ErrorReporterProtocol`, inside an isolated per-RPC scope |

Sentry is placed **inside** (closer to the handler than) the exception
handler on purpose. An exception raised by the handler propagates from the
innermost interceptor outward, so Sentry — innermost — sees the raw
exception first and can capture it, then re-raises it unchanged. The
exception handler, one level out, catches that same raw exception and
converts it into a `grpc.aio.AbortError`. If Sentry sat *outside* the
exception handler instead, every exception reaching it would already be an
`AbortError` — which Sentry explicitly never captures — so it would report
nothing, ever. See [Observability](observability.md#error-tracking) for the
capture-filter mechanics.

## Writing a custom interceptor

A concurrency limiter: reject a method with `RESOURCE_EXHAUSTED` once too many
calls to it are already in flight.

```python
from collections.abc import AsyncIterator

import grpc

from grpc_server_kit.aio.interceptors import AsyncServerInterceptor, RpcCall


class ConcurrencyLimitInterceptor(AsyncServerInterceptor):
    """Reject a method with RESOURCE_EXHAUSTED past a per-method in-flight limit."""

    def __init__(self, max_in_flight: int, **kwargs: object) -> None:
        super().__init__(**kwargs)          # forwards skip_methods
        self._max_in_flight = max_in_flight
        self._in_flight: dict[str, int] = {}

    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        count = self._in_flight.get(call.method_name, 0)
        if count >= self._max_in_flight:
            await call.context.abort(
                grpc.StatusCode.RESOURCE_EXHAUSTED,
                f"Too many concurrent calls to {call.method_name}",
            )
        self._in_flight[call.method_name] = count + 1
        try:
            yield                            # the RPC — or the full response stream — runs here
        finally:
            self._in_flight[call.method_name] -= 1
```

No special handling of `AbortError` is needed here since this interceptor
doesn't catch exceptions around its `yield` — the abort just propagates
outward like any other outcome. And because the same hook wraps
stream-stream calls too, a streamed RPC stays counted as "in flight" for its
whole duration, not just until its first response, which is exactly what a
concurrency limit should do:

```python
app.add_interceptors([
    AsyncMetricsInterceptor(metrics=get_grpc_server_metrics(), service_name="my.pkg.MyService"),
    ConcurrencyLimitInterceptor(max_in_flight=50),
    ...
])
```

See [Advanced](advanced.md#dishka-di-providers) for wiring interceptors
through Dishka instead of `app.add_interceptors`.
