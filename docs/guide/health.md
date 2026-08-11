# Health checks

The gRPC Health Checking Protocol v1 (`grpc.health.v1.Health`) defines two
RPCs — `Check` (unary) and `Watch` (server-streaming) — that report
`SERVING` / `NOT_SERVING` / `SERVICE_UNKNOWN` for the overall server or a
named sub-service. `grpc_server_kit.aio.health` (the `[health]` extra) is a
servicer that runs your own dependency checkers on demand, rather than a
static flag you flip by hand.

## Enabling it

See [Quick start](quickstart.md#health-checks) for the one-call form. The
full signature:

```python
from grpc_server_kit.aio.health import DatabaseHealthChecker, RedisHealthChecker

app.enable_health(
    checkers=[DatabaseHealthChecker(session_maker), RedisHealthChecker(redis_client)],
    cache_ttl=5.0,        # None or 0 disables caching
    check_timeout=10.0,   # budget for one full round across all checkers
    service_names=["my.pkg.MyService"],   # optional: report health per-service too
)
```

When `cache_ttl` / `check_timeout` are omitted, they're read from
`settings.health` if your settings object carries one, else from the kit's
own defaults (5s cache, 10s timeout) — see
[Configuration](configuration.md#health-block).

## Wiring the servicer manually

For embedding, tests, or a builder-based setup that doesn't go through
`GrpcApp`, register `AsyncDynamicHealthServicer` directly:

```python
import grpc
from grpc_health.v1 import health_pb2_grpc
from grpc_server_kit.aio.health import AsyncDynamicHealthServicer, HealthCache

servicer = AsyncDynamicHealthServicer(
    checkers=[DatabaseHealthChecker(session_maker)],
    cache=HealthCache(ttl=5.0),
    check_interval=5.0,        # Watch: seconds between dependency re-checks
    heartbeat_interval=60.0,   # Watch: resend even when status is unchanged
    check_timeout=10.0,
)


def register(server: grpc.aio.Server) -> None:
    health_pb2_grpc.add_HealthServicer_to_server(servicer, server)


builder.with_servicers(register)   # AsyncGrpcServerBuilder — see advanced.md
```

This is what `enable_health()` does under the hood. `heartbeat_interval` must
be `>= check_interval`; both, along with `check_timeout`, are validated
eagerly at construction.

## Writing a checker

Any object with `async def check(self) -> bool` satisfies `AsyncHealthChecker`
— a `Protocol`, so plain duck typing is enough:

```python
class QueueDepthChecker:
    def __init__(self, queue: Queue, max_depth: int) -> None:
        self._queue = queue
        self._max_depth = max_depth

    async def check(self) -> bool:
        return await self._queue.size() < self._max_depth
```

Return `True` for healthy, `False` for unhealthy — don't raise for an
expected "unhealthy" outcome. If a checker does raise, the orchestrator
(`check_async_overall_health`) treats it the same as `False` (logged, then
`NOT_SERVING`) instead of failing the health RPC itself.

## Shipped checkers

| Checker | Extra | What it does |
| :--- | :--- | :--- |
| `DatabaseHealthChecker` | `[postgres]` | Runs `SELECT 1` through a SQLAlchemy async `session_maker`; session cleanup runs under its own short timeout so it can't turn a successful probe into one that times out |
| `RedisHealthChecker` | `[redis]` | Calls `PING` on any client exposing an async `ping()`; for clustered clients every node must respond for the check to pass |

Both wrap `FunctionalHealthChecker`, a thin adapter that just calls
`check_func(resource, timeout=...)`. The `handle_check_exceptions` decorator
normalizes timeouts, connection errors, and unexpected exceptions down to a
`False` result plus a log line, so one flaky dependency never raises out of
your health servicer.

## The TTL cache and single-flight checks

Without a cache, every `Check` call and every tick of every `Watch` stream
would run your checkers directly — cheap for one client, expensive with a
few dozen concurrent watchers all polling the same degraded database.
`HealthCache` (the `cache=` argument to the servicer) fixes this with two
properties:

- **TTL freshness** — a cached status is reused for `ttl` seconds after it
  was produced.
- **Single-flight** — on a cache miss, callers take a lock and only the first
  one actually runs the checkers; the rest wait and receive that same result,
  so *N* concurrent callers hitting a miss trigger exactly one real check,
  not *N*.

The default TTL (5s) intentionally equals the default `Watch` check interval
(also 5s), so every `Watch` stream's own polling and every `Check` probe
hitting the server amortize onto roughly one real dependency check per
interval, instead of each running its own. If a check happens to take longer
than the TTL, the entry's freshness clock starts at the check's
**completion**, not its start — a slow dependency never produces an entry
that is already stale the instant it's written.

## `Check` vs `Watch`

`Check` is unary: one request, one response of `SERVING`, `NOT_SERVING`, or
`SERVICE_UNKNOWN` (invalid or unregistered service name). It suits one-shot
probes.

`Watch` is server-streaming: it sends a response whenever the status changes,
and otherwise resends the current status every `heartbeat_interval` seconds
so a connected client always knows the connection itself is alive. Per the
Health v1 spec, a `Watch` stream must never end with a clean OK:
`grpc_server_kit`'s implementation loops until the client disconnects
(`context.done()`), and if the check loop itself fails unexpectedly, it
aborts the stream with `INTERNAL` rather than returning — so a client can
always distinguish "the server told me it's unhealthy" (an ordinary
`NOT_SERVING` message) from "the watch itself broke" (a stream error).

## Timeouts

Two independent layers:

- `check_timeout` on the servicer (default 10s) bounds one full round across
  **all** checkers together, run concurrently via `asyncio.gather`. If the
  round doesn't finish in time, that check reports `NOT_SERVING`.
- Each shipped checker also carries its own per-dependency timeout (5s by
  default), bounding just that one dependency's query or ping. This is what
  stops a single dead connection from silently eating the whole
  `check_timeout` budget meant for every checker.

## Wiring it to Kubernetes probes

Both options below probe the `""` (overall) service name unless you passed
`service_names=[...]` and want a specific one.

**`grpc_health_probe`** — a small static binary added to your image — works
on any Kubernetes version:

```yaml
readinessProbe:
  exec:
    command: ["/bin/grpc_health_probe", "-addr=:50051"]
livenessProbe:
  exec:
    command: ["/bin/grpc_health_probe", "-addr=:50051"]
```

**Native gRPC probes** (Kubernetes 1.24+, no extra binary) call `Check`
directly:

```yaml
readinessProbe:
  grpc:
    port: 50051
livenessProbe:
  grpc:
    port: 50051
```

Point probes at `Check`, not `Watch` — both options above do. Probes are
one-shot by design, and a long-lived `Watch` stream held open by kubelet
would just be one more concurrent client for the TTL cache to amortize, for
no benefit over a plain `Check`.
