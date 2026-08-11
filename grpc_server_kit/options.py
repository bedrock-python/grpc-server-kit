"""gRPC server options and compression configuration."""

from __future__ import annotations

from .constants import (
    DEFAULT_HTTP2_MAX_PINGS_WITHOUT_DATA,
    DEFAULT_HTTP2_MIN_RECV_PING_INTERVAL_WITHOUT_DATA_MS,
    DEFAULT_INITIAL_WINDOW_SIZE,
    DEFAULT_KEEPALIVE_TIME_MS,
    DEFAULT_KEEPALIVE_TIMEOUT_MS,
    DEFAULT_MAX_METADATA_SIZE,
    DEFAULT_MAX_RECEIVE_MESSAGE_LENGTH,
    DEFAULT_MAX_SEND_MESSAGE_LENGTH,
)
from .protocols import GrpcSettingsProtocol

type GrpcOption = tuple[str, int]
type GrpcOptions = list[GrpcOption]

COMPRESSION_ALGORITHMS = {
    "none": 0,
    "deflate": 1,
    "gzip": 2,
}


def build_grpc_options(settings: GrpcSettingsProtocol) -> GrpcOptions:
    """Build gRPC server options from settings.

    Settings fields set to ``None`` fall back to the kit's defaults; invalid
    values fail loudly — a misconfigured server must not start.

    Args:
        settings: gRPC configuration settings following GrpcSettingsProtocol

    Returns:
        List of tuples containing gRPC channel options

    Raises:
        ValueError: If any option value is negative or the compression
            algorithm is not supported.
    """
    options: GrpcOptions = [
        # Message size
        (
            "grpc.max_send_message_length",
            _require_option(
                settings.max_send_message_length, "max_send_message_length", DEFAULT_MAX_SEND_MESSAGE_LENGTH
            ),
        ),
        (
            "grpc.max_receive_message_length",
            _require_option(
                settings.max_receive_message_length, "max_receive_message_length", DEFAULT_MAX_RECEIVE_MESSAGE_LENGTH
            ),
        ),
        (
            "grpc.max_metadata_size",
            _require_option(settings.max_metadata_size, "max_metadata_size", DEFAULT_MAX_METADATA_SIZE),
        ),
        # Keepalive
        (
            "grpc.keepalive_time_ms",
            _require_option(settings.keepalive_time_ms, "keepalive_time_ms", DEFAULT_KEEPALIVE_TIME_MS),
        ),
        (
            "grpc.keepalive_timeout_ms",
            _require_option(settings.keepalive_timeout_ms, "keepalive_timeout_ms", DEFAULT_KEEPALIVE_TIMEOUT_MS),
        ),
        ("grpc.keepalive_permit_without_calls", int(settings.keepalive_permit_without_calls)),
        (
            "grpc.http2.min_recv_ping_interval_without_data_ms",
            _require_option(
                settings.http2_min_recv_ping_interval_without_data_ms,
                "http2_min_recv_ping_interval_without_data_ms",
                DEFAULT_HTTP2_MIN_RECV_PING_INTERVAL_WITHOUT_DATA_MS,
            ),
        ),
        (
            "grpc.http2.max_pings_without_data",
            _require_option(
                settings.http2_max_pings_without_data,
                "http2_max_pings_without_data",
                DEFAULT_HTTP2_MAX_PINGS_WITHOUT_DATA,
            ),
        ),
        # Flow control
        (
            "grpc.http2.initial_stream_window_size",
            _require_option(
                settings.initial_stream_window_size, "initial_stream_window_size", DEFAULT_INITIAL_WINDOW_SIZE
            ),
        ),
        (
            "grpc.http2.initial_connection_window_size",
            _require_option(
                settings.initial_connection_window_size, "initial_connection_window_size", DEFAULT_INITIAL_WINDOW_SIZE
            ),
        ),
    ]

    # Optional: connection limits (omitted entirely when not configured)
    for option_name, value, setting_name in (
        ("grpc.max_concurrent_streams", settings.max_concurrent_rpcs, "max_concurrent_rpcs"),
        ("grpc.max_connection_idle_ms", settings.max_connection_idle_ms, "max_connection_idle_ms"),
        ("grpc.max_connection_age_ms", settings.max_connection_age_ms, "max_connection_age_ms"),
        ("grpc.max_connection_age_grace_ms", settings.max_connection_age_grace_ms, "max_connection_age_grace_ms"),
    ):
        if value is not None:
            options.append((option_name, _require_non_negative(value, setting_name)))

    # Compression
    if settings.compression_algorithm:
        algo = settings.compression_algorithm.lower()
        if algo not in COMPRESSION_ALGORITHMS:
            supported = ", ".join(sorted(COMPRESSION_ALGORITHMS))
            raise ValueError(
                f"Unsupported compression_algorithm: {settings.compression_algorithm!r}. Supported: {supported}"
            )
        options.append(("grpc.default_compression_algorithm", COMPRESSION_ALGORITHMS[algo]))

    return options


def _require_option(value: int | None, name: str, default: int) -> int:
    """Return ``default`` when unset; reject negative values loudly."""
    if value is None:
        return default
    return _require_non_negative(value, name)


def _require_non_negative(value: int, name: str) -> int:
    """Validate that a configured option value is non-negative."""
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")
    return value
