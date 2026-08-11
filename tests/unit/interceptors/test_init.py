"""Tests for the shared interceptors package's public API surface."""

from __future__ import annotations

import pytest

from grpc_server_kit import interceptors

pytestmark = pytest.mark.unit


def test__interceptors_module__public_api__exports_every_declared_name() -> None:
    # Act
    missing = [name for name in interceptors.__all__ if not hasattr(interceptors, name)]

    # Assert
    assert missing == []


def test__interceptors_module__helper_exports__are_callable() -> None:
    # Assert
    assert callable(interceptors.is_server_error)
    assert callable(interceptors.resolve_status_code)


def test__interceptors_module__skipped_health_methods__is_frozenset_containing_health_check() -> None:
    # Assert
    assert isinstance(interceptors.SKIPPED_HEALTH_METHODS, frozenset)
    assert "/grpc.health.v1.Health/Check" in interceptors.SKIPPED_HEALTH_METHODS
