# Changelog

## [0.1.1](https://github.com/bedrock-python/grpc-server-kit/compare/grpc-server-kit-v0.1.0...grpc-server-kit-v0.1.1) (2026-09-06)


### Bug Fixes

* missing exports, context skip_methods, and compression "none" ([#17](https://github.com/bedrock-python/grpc-server-kit/issues/17)) ([9c70e9d](https://github.com/bedrock-python/grpc-server-kit/commit/9c70e9db467f8fc3c25c2426c98869b22a3ce0aa))

## 0.1.0 (2026-08-11)

Initial release of grpc-server-kit.

### Features

* `GrpcApp` facade — server construction, TLS-aware port binding, signal handling and graceful shutdown behind a single object
* Streaming-aware interceptors — one `around_call` hook covers all four RPC kinds, spanning the full response stream
* Canonical interceptor chain: metrics, request context, request logging, tracing, exception mapping, error reporting
* Exception-to-gRPC-status mapping over the MRO with safe, non-leaking details
* gRPC Health Checking Protocol v1 — `Check`/`Watch`, Postgres and Redis dependency checkers, single-flight TTL cache
* SDK-free observability seams for Prometheus metrics, OpenTelemetry tracing and Sentry error reporting
* Dishka DI providers wiring the canonical interceptor chain and the configured server
* Typed configuration: zero-dependency `GrpcServerConfig` dataclass or pydantic `BaseGrpcServerSettings`
* TLS/mTLS credential loading with private-key permission checks
* Zero-dependency core (`grpcio` only) — every integration is an opt-in extra
