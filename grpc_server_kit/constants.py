"""gRPC server constants and default values."""

# Bind address
DEFAULT_HOST = "[::]"
DEFAULT_PORT = 50051

# Message size limits
DEFAULT_MAX_SEND_MESSAGE_LENGTH = 4 * 1024 * 1024  # 4 MB
DEFAULT_MAX_RECEIVE_MESSAGE_LENGTH = 4 * 1024 * 1024  # 4 MB
DEFAULT_MAX_METADATA_SIZE = 8 * 1024  # 8 KB (grpc-core default soft limit)

# Keepalive settings
DEFAULT_KEEPALIVE_TIME_MS = 7200000  # 2 hours
DEFAULT_KEEPALIVE_TIMEOUT_MS = 20000  # 20 seconds
DEFAULT_HTTP2_MIN_RECV_PING_INTERVAL_WITHOUT_DATA_MS = 300000  # 5 minutes
DEFAULT_HTTP2_MAX_PINGS_WITHOUT_DATA = 2

# Flow control (HTTP/2 default initial window)
DEFAULT_INITIAL_WINDOW_SIZE = 65535

# Graceful shutdown
DEFAULT_GRACE_PERIOD = 5.0  # seconds

# gRPC Health Checking Protocol v1 (single source of truth for the name)
HEALTH_SERVICE_NAME = "grpc.health.v1.Health"

# Health checking defaults. The cache TTL matches the Watch check interval so
# that concurrent watchers and Check probes amortize onto one real dependency
# check per interval instead of each running their own.
DEFAULT_HEALTH_CACHE_TTL = 5.0  # seconds; 0 disables caching
DEFAULT_HEALTH_CHECK_TIMEOUT = 10.0  # seconds; one full check round
DEFAULT_HEALTH_CHECK_INTERVAL = 5.0  # seconds; Watch polling interval
DEFAULT_HEALTH_HEARTBEAT_INTERVAL = 60.0  # seconds; Watch resend-if-unchanged
DEFAULT_CHECKER_TIMEOUT = 5.0  # seconds; a single dependency checker (pg/redis)
