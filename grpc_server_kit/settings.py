"""Standardized gRPC server and health-check configuration models (pydantic).

These are plain :class:`pydantic.BaseModel` building blocks meant to be nested
inside a service's own ``BaseSettings`` (so environment loading and prefixes stay
under the service's control). They structurally satisfy
:class:`grpc_server_kit.protocols.GrpcServerSettingsProtocol`.

This module is gated behind the ``[settings]`` optional dependency
(``pip install grpc-server-kit[settings]``) which pulls in ``pydantic``.
For a zero-dependency alternative see :class:`grpc_server_kit.config.GrpcServerConfig`.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from grpc_server_kit.constants import (
    DEFAULT_GRACE_PERIOD,
    DEFAULT_HEALTH_CACHE_TTL,
    DEFAULT_HEALTH_CHECK_TIMEOUT,
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

GrpcCompressionAlgorithm = Literal["deflate", "gzip"]


class BaseHealthSettings(BaseModel):
    """Base configuration for gRPC health checks.

    Consumed by :meth:`grpc_server_kit.aio.GrpcApp.enable_health` as the source
    of defaults when the app's settings object carries a ``health`` block.
    """

    cache_ttl: float = Field(
        default=DEFAULT_HEALTH_CACHE_TTL,
        ge=0,
        description="Health check result cache TTL in seconds (0 disables caching)",
    )
    check_timeout: float = Field(
        default=DEFAULT_HEALTH_CHECK_TIMEOUT, gt=0, description="Timeout for each health check run in seconds"
    )


class BaseGrpcServerSettings(BaseModel):
    """Base gRPC server configuration.

    Standardized settings for gRPC servers, including TLS, keepalive, and limits.
    """

    # Basic
    host: str = Field(default=DEFAULT_HOST, min_length=1, description="gRPC server host")
    port: int = Field(default=DEFAULT_PORT, ge=0, le=65535, description="gRPC server port (0 = ephemeral)")

    # TLS/SSL settings
    ssl_enabled: bool = Field(default=False, description="Enable TLS/SSL")
    ssl_cert_file: str | None = Field(default=None, description="Path to server certificate")
    ssl_key_file: str | None = Field(default=None, description="Path to server private key")
    ssl_ca_file: str | None = Field(default=None, description="Path to CA certificate (for mTLS)")
    ssl_client_auth: bool = Field(default=False, description="Require client certificate (mTLS)")
    ssl_max_cert_size: int | None = Field(default=None, ge=1, description="Maximum certificate file size (bytes)")

    # Keepalive settings
    keepalive_time_ms: int = Field(
        default=DEFAULT_KEEPALIVE_TIME_MS, ge=0, description="Time between keepalive pings (ms)"
    )
    keepalive_timeout_ms: int = Field(
        default=DEFAULT_KEEPALIVE_TIMEOUT_MS, ge=0, description="Timeout for keepalive ping response (ms)"
    )
    keepalive_permit_without_calls: bool = Field(
        default=False,
        description="Allow keepalive pings without active RPCs",
    )
    http2_min_recv_ping_interval_without_data_ms: int = Field(
        default=DEFAULT_HTTP2_MIN_RECV_PING_INTERVAL_WITHOUT_DATA_MS,
        ge=0,
        description="Minimum time between pings from client without data",
    )
    http2_max_pings_without_data: int = Field(
        default=DEFAULT_HTTP2_MAX_PINGS_WITHOUT_DATA, ge=0, description="Max pings allowed without data before GOAWAY"
    )

    # Connection limits
    max_concurrent_rpcs: int | None = Field(default=None, ge=1, description="Maximum concurrent RPCs")
    max_connection_idle_ms: int | None = Field(default=None, ge=1, description="Max idle time (ms)")
    max_connection_age_ms: int | None = Field(default=None, ge=1, description="Max connection age (ms)")
    max_connection_age_grace_ms: int | None = Field(default=None, ge=1, description="Grace period (ms)")

    # Message size limits
    max_send_message_length: int = Field(
        default=DEFAULT_MAX_SEND_MESSAGE_LENGTH, ge=1, description="Max send length (bytes)"
    )
    max_receive_message_length: int = Field(
        default=DEFAULT_MAX_RECEIVE_MESSAGE_LENGTH, ge=1, description="Max receive length (bytes)"
    )
    max_metadata_size: int = Field(default=DEFAULT_MAX_METADATA_SIZE, ge=1, description="Max metadata size (bytes)")

    # Compression
    compression_algorithm: GrpcCompressionAlgorithm | None = Field(
        default=None,
        description="Default compression: deflate or gzip (None = no compression)",
    )

    # Flow control settings
    initial_stream_window_size: int = Field(
        default=DEFAULT_INITIAL_WINDOW_SIZE, ge=1, description="Initial stream window (bytes)"
    )
    initial_connection_window_size: int = Field(
        default=DEFAULT_INITIAL_WINDOW_SIZE, ge=1, description="Initial connection window (bytes)"
    )

    # Features
    enable_reflection: bool = Field(default=False, description="Enable gRPC reflection")
    enable_channelz: bool = Field(default=False, description="Enable channelz for debugging")

    # Graceful shutdown
    grace_period: float = Field(
        default=DEFAULT_GRACE_PERIOD, ge=0, description="Graceful shutdown grace period in seconds"
    )

    # Metrics (consumed by the dishka bundle / your own wiring)
    metrics_enabled: bool = Field(default=False, description="Enable gRPC server metrics")

    # Health (consumed by GrpcApp.enable_health as its defaults)
    health: BaseHealthSettings = Field(default_factory=BaseHealthSettings, description="Health check configuration")

    @model_validator(mode="after")
    def _validate_ssl(self) -> "BaseGrpcServerSettings":
        if self.ssl_enabled:
            if not self.ssl_cert_file:
                raise ValueError("gRPC ssl_cert_file is required when ssl_enabled=True")
            if not self.ssl_key_file:
                raise ValueError("gRPC ssl_key_file is required when ssl_enabled=True")
            if self.ssl_client_auth and not self.ssl_ca_file:
                raise ValueError("gRPC ssl_ca_file is required when ssl_client_auth=True")
        return self


__all__ = [
    "BaseGrpcServerSettings",
    "BaseHealthSettings",
    "GrpcCompressionAlgorithm",
]
