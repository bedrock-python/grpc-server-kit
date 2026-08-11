# grpc-server-kit

Batteries-optional async gRPC server toolkit: a one-object `GrpcApp` facade,
validated channel options, TLS/mTLS credential loading, graceful signal-driven
shutdown, streaming-aware interceptors, and gRPC health checking — with
**optional** extras for reflection, channelz, Prometheus metrics, OpenTelemetry
tracing, Sentry, Dishka DI, and typed pydantic settings.

The core depends only on `grpcio`. Every integration is an opt-in extra, so you
install exactly what you use.

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

That's the whole server. Continue with the [guide](guide/quickstart.md) for
interceptors, health checks, TLS, and DI wiring.

## Why grpc-server-kit

- **One-object happy path** — `GrpcApp` collapses builder, port binding (TLS-aware),
  signal handling, and graceful shutdown into a single facade; the underlying
  pieces stay available for advanced wiring.
- **Streaming-aware interceptors** — every interceptor wraps the *whole* RPC for
  all four call kinds; durations cover full streams, mid-stream errors map to
  proper gRPC statuses, cleanup always runs inside the RPC.
- **Safe error mapping** — exception types map to gRPC statuses over the MRO
  with safe, non-leaking details; server bugs stay INTERNAL with stack traces.
- **Production health checking** — Health v1 `Check`/`Watch` with single-flight
  TTL caching and Postgres/Redis dependency checkers.
- **Zero-dependency core** — `grpcio` only; observability integrates through
  SDK-free structural protocols.
