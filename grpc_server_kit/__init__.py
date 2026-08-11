"""grpc-server-kit — batteries-optional async gRPC server toolkit."""

from . import aio
from .__version__ import __version__
from .aio.app import GrpcApp
from .config import GrpcServerConfig
from .credentials import load_server_credentials
from .options import COMPRESSION_ALGORITHMS, build_grpc_options
from .protocols import (
    GrpcAsyncServerProtocol,
    GrpcServerProtocol,
    GrpcServerSettingsProtocol,
    GrpcSettingsProtocol,
    GrpcSslSettingsProtocol,
)
from .server import bind_server_port
from .signals import setup_signal_handlers

__all__ = [
    "COMPRESSION_ALGORITHMS",
    "GrpcApp",
    "GrpcAsyncServerProtocol",
    "GrpcServerConfig",
    "GrpcServerProtocol",
    "GrpcServerSettingsProtocol",
    "GrpcSettingsProtocol",
    "GrpcSslSettingsProtocol",
    "__version__",
    "aio",
    "bind_server_port",
    "build_grpc_options",
    "load_server_credentials",
    "setup_signal_handlers",
]
