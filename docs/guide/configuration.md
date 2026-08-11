# Configuration

Two interchangeable settings shapes satisfy `GrpcServerSettingsProtocol`:

- **`grpc_server_kit.config.GrpcServerConfig`** — a stdlib dataclass with
  production-grade defaults; zero dependencies.
- **`grpc_server_kit.settings.BaseGrpcServerSettings`** — a pydantic model
  (the `[settings]` extra) meant to be nested inside your service's own
  `BaseSettings` for env-driven loading.

```python
from grpc_server_kit import GrpcServerConfig

config = GrpcServerConfig(
    host="[::]",
    port=50051,          # 0 binds an ephemeral port (see GrpcApp.bound_port)
    grace_period=10.0,   # graceful shutdown drain, seconds
)
```

Invalid configuration fails loudly at construction (dataclass) or validation
(pydantic) — a misconfigured server must not start.

## TLS / mTLS

```python
config = GrpcServerConfig(
    port=50051,
    ssl_enabled=True,
    ssl_cert_file="/etc/tls/server.crt",
    ssl_key_file="/etc/tls/server.key",
    ssl_ca_file="/etc/tls/ca.crt",   # for mTLS
    ssl_client_auth=True,             # require client certificates
)
```

Private keys with group/world-readable permissions are rejected on Unix
(chmod 600). PEM files are sanity-checked only — real validation belongs to the
TLS stack, so encrypted keys, preambles, and CA bundles all work.

## Channel tuning

Keepalive, HTTP/2 ping policy, message/metadata size limits, flow-control
windows, connection age/idle limits, and compression are all first-class
fields on both settings shapes; `build_grpc_options()` translates them into
gRPC channel options and rejects invalid values.

## Health block

`BaseGrpcServerSettings.health` (`cache_ttl`, `check_timeout`) supplies the
defaults for `GrpcApp.enable_health()` automatically; `cache_ttl=0` disables
caching end-to-end.

## Graceful shutdown semantics

`grace_period` is the number of seconds in-flight RPCs get to finish after a
stop is requested. Passing `grace=None` to the lower-level `stop()` APIs maps
to gRPC's own semantics: **all in-flight RPCs are aborted immediately.**
