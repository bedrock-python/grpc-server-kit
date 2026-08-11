"""Async Dishka integration for the gRPC server (``[dishka]`` extra).

- ``DishkaAioInterceptor`` — request-scoped container per RPC.
- ``AsyncGrpcServerProvider`` — builds the configured ``AsyncServer``.
- ``grpc_server_providers`` + the granular providers — the standard interceptor
  chain, metrics, and observability seams in one registration.

Combine with the shared helpers in :mod:`grpc_server_kit.dishka`
(``GrpcServerSettingsProvider``, ``GrpcioProvider``, ``inject``, ``FromDishka``).
"""

from dishka.integrations.grpcio import DishkaAioInterceptor

from .bundle import grpc_server_providers
from .interceptors import GrpcServerInterceptorsProvider
from .metrics import PrometheusGrpcServerMetricsProvider
from .observability import SentryAdapterProvider, TracerAdapterProvider
from .server import AsyncGrpcServerProvider

__all__ = [
    "AsyncGrpcServerProvider",
    "DishkaAioInterceptor",
    "GrpcServerInterceptorsProvider",
    "PrometheusGrpcServerMetricsProvider",
    "SentryAdapterProvider",
    "TracerAdapterProvider",
    "grpc_server_providers",
]
