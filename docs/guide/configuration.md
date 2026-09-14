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

## Ephemeral ports

`port=0` asks the OS for any free port, which is what a test wants; read the
result from `GrpcApp.bound_port`. `GrpcServerConfig` accepts it as is. The
pydantic model is the shape that receives `GRPC__PORT` from the environment,
and there `0` is one keystroke from a real port and, in a Deployment, a silent
outage: the process starts, passes its own health check, and is not on the port
the `Service` sends traffic to. So `BaseGrpcServerSettings` rejects `port=0`
unless the decision is written down:

```python
from grpc_server_kit.settings import BaseGrpcServerSettings

BaseGrpcServerSettings(port=0)                             # ValidationError, names the flag
BaseGrpcServerSettings(port=0, allow_ephemeral_port=True)  # an ephemeral port on purpose
```

When the port comes from the environment the decision travels with it:
`GRPC__ALLOW_EPHEMERAL_PORT=true` next to `GRPC__PORT=0`, or a subclass whose
class default is `allow_ephemeral_port: bool = True`. pydantic-settings builds a
nested section afresh from its variables, so a default *instance* on the parent
class (`grpc: BaseGrpcServerSettings = BaseGrpcServerSettings(allow_ephemeral_port=True)`)
is replaced, not merged, as soon as one `GRPC__*` variable is set.

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
