"""Optional observability wiring for the gRPC server.

Submodules are import-gated by their own extras so importing this package never
pulls heavy dependencies:

- ``grpc_server_kit.observability.protocols`` — SDK-free seam protocols (core)
- ``grpc_server_kit.observability.metrics`` — Prometheus metrics (``[metrics]`` extra)
- ``grpc_server_kit.observability.sentry``  — Sentry adapter (``[sentry]`` extra)

OpenTelemetry instrumentation for the async server lives at
``grpc_server_kit.aio.observability.tracing`` (``[tracing]`` extra).
"""
