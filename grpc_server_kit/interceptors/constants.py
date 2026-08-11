"""Constants shared across gRPC interceptors."""

from grpc_server_kit.constants import HEALTH_SERVICE_NAME

# gRPC Health methods to skip in metrics, logging, tracing, and error tracking
# (derived from the single canonical health service name).
SKIPPED_HEALTH_METHODS = frozenset(
    {
        f"/{HEALTH_SERVICE_NAME}/Check",
        f"/{HEALTH_SERVICE_NAME}/Watch",
    }
)

# Common header names
X_REQUEST_ID = "x-request-id"

# Context variable names
REQUEST_ID_KEY = "request_id"
METHOD_KEY = "method"

# OpenTelemetry semantic-convention attributes
OTEL_RPC_SYSTEM = "rpc.system"
OTEL_RPC_METHOD = "rpc.method"
OTEL_RPC_SERVICE = "rpc.service"
OTEL_RPC_GRPC_STATUS_CODE = "rpc.grpc.status_code"
OTEL_REQUEST_ID = "request.id"

# Sentry tag names
SENTRY_GRPC_METHOD = "grpc_method"
SENTRY_REQUEST_ID = "request_id"

__all__ = [
    "METHOD_KEY",
    "OTEL_REQUEST_ID",
    "OTEL_RPC_GRPC_STATUS_CODE",
    "OTEL_RPC_METHOD",
    "OTEL_RPC_SERVICE",
    "OTEL_RPC_SYSTEM",
    "REQUEST_ID_KEY",
    "SENTRY_GRPC_METHOD",
    "SENTRY_REQUEST_ID",
    "SKIPPED_HEALTH_METHODS",
    "X_REQUEST_ID",
]
