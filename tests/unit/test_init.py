"""Tests for the grpc_server_kit package's public API surface."""

from __future__ import annotations

import pytest

import grpc_server_kit
from grpc_server_kit import (
    aio,
    bind_server_port,
    build_grpc_options,
    load_server_credentials,
    setup_signal_handlers,
)

pytestmark = pytest.mark.unit


def test__grpc_server_kit__version__is_non_empty_string() -> None:
    # Act
    version = grpc_server_kit.__version__

    # Assert
    assert isinstance(version, str)
    assert version


def test__grpc_server_kit__public_api__is_importable() -> None:
    # Act
    missing = [name for name in grpc_server_kit.__all__ if not hasattr(grpc_server_kit, name)]

    # Assert
    assert not missing, f"missing exports: {missing}"


def test__grpc_server_kit_aio__public_api__is_importable() -> None:
    # Act
    missing = [name for name in aio.__all__ if not hasattr(aio, name)]

    # Assert
    assert not missing, f"missing exports: {missing}"


def test__grpc_server_kit__key_entrypoints__are_callable() -> None:
    # Act
    entrypoints = [bind_server_port, build_grpc_options, load_server_credentials, setup_signal_handlers]

    # Assert
    assert all(callable(entrypoint) for entrypoint in entrypoints)
    # The async builder is an aio-level export, not a top-level one.
    assert callable(aio.AsyncGrpcServerBuilder)
