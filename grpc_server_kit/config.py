"""Zero-dependency gRPC server configuration (pure stdlib dataclass).

:class:`GrpcServerConfig` structurally satisfies
:class:`grpc_server_kit.protocols.GrpcServerSettingsProtocol` without pulling in
pydantic. Services that want environment loading and validation via pydantic
should use :class:`grpc_server_kit.settings.BaseGrpcServerSettings` (the
``[settings]`` extra) — both shapes are interchangeable everywhere the kit
accepts settings.
"""

from __future__ import annotations

import dataclasses

from .constants import (
    DEFAULT_GRACE_PERIOD,
    DEFAULT_HOST,
    DEFAULT_HTTP2_MAX_PINGS_WITHOUT_DATA,
    DEFAULT_HTTP2_MIN_RECV_PING_INTERVAL_WITHOUT_DATA_MS,
    DEFAULT_INITIAL_WINDOW_SIZE,
    DEFAULT_KEEPALIVE_TIME_MS,
    DEFAULT_KEEPALIVE_TIMEOUT_MS,
    DEFAULT_MAX_METADATA_SIZE,
    DEFAULT_MAX_RECEIVE_MESSAGE_LENGTH,
    DEFAULT_MAX_SEND_MESSAGE_LENGTH,
    DEFAULT_PORT,
)

__all__ = ["GrpcServerConfig"]


@dataclasses.dataclass(kw_only=True, slots=True)
class GrpcServerConfig:
    """gRPC server configuration with production-grade defaults (stdlib only)."""

    # Basic
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    # TLS/SSL
    ssl_enabled: bool = False
    ssl_cert_file: str | None = None
    ssl_key_file: str | None = None
    ssl_ca_file: str | None = None
    ssl_client_auth: bool = False
    ssl_max_cert_size: int | None = None

    # Keepalive
    keepalive_time_ms: int = DEFAULT_KEEPALIVE_TIME_MS
    keepalive_timeout_ms: int = DEFAULT_KEEPALIVE_TIMEOUT_MS
    keepalive_permit_without_calls: bool = False
    http2_min_recv_ping_interval_without_data_ms: int = DEFAULT_HTTP2_MIN_RECV_PING_INTERVAL_WITHOUT_DATA_MS
    http2_max_pings_without_data: int = DEFAULT_HTTP2_MAX_PINGS_WITHOUT_DATA

    # Connection limits
    max_concurrent_rpcs: int | None = None
    max_connection_idle_ms: int | None = None
    max_connection_age_ms: int | None = None
    max_connection_age_grace_ms: int | None = None

    # Message size limits
    max_send_message_length: int = DEFAULT_MAX_SEND_MESSAGE_LENGTH
    max_receive_message_length: int = DEFAULT_MAX_RECEIVE_MESSAGE_LENGTH
    max_metadata_size: int = DEFAULT_MAX_METADATA_SIZE

    # Compression
    compression_algorithm: str | None = None

    # Flow control
    initial_stream_window_size: int = DEFAULT_INITIAL_WINDOW_SIZE
    initial_connection_window_size: int = DEFAULT_INITIAL_WINDOW_SIZE

    # Features
    enable_reflection: bool = False
    enable_channelz: bool = False

    # Graceful shutdown
    grace_period: float = DEFAULT_GRACE_PERIOD

    # Metrics
    metrics_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("host cannot be empty")
        if not (0 <= self.port <= 65535):
            raise ValueError(f"port must be within [0, 65535], got {self.port}")
        if self.grace_period < 0:
            raise ValueError(f"grace_period must be non-negative, got {self.grace_period}")
        if self.max_concurrent_rpcs is not None and self.max_concurrent_rpcs < 1:
            raise ValueError(
                f"max_concurrent_rpcs must be >= 1 (or None for unlimited), got {self.max_concurrent_rpcs}"
            )
        if self.ssl_enabled:
            if not self.ssl_cert_file:
                raise ValueError("ssl_cert_file is required when ssl_enabled=True")
            if not self.ssl_key_file:
                raise ValueError("ssl_key_file is required when ssl_enabled=True")
            if self.ssl_client_auth and not self.ssl_ca_file:
                raise ValueError("ssl_ca_file is required when ssl_client_auth=True")
