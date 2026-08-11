"""Tests for the health package's public API."""

from __future__ import annotations

import pytest

from grpc_server_kit.aio import health

pytestmark = pytest.mark.unit


def test__health_public_api__each_dunder_all_export__is_importable() -> None:
    # Act
    missing = [name for name in health.__all__ if not hasattr(health, name)]

    # Assert
    assert missing == []


def test__health_public_api__key_entrypoints__are_callable() -> None:
    # Act & Assert
    assert callable(health.AsyncDynamicHealthServicer)
    assert callable(health.HealthCache)
    assert callable(health.check_async_overall_health)
    assert callable(health.DatabaseHealthChecker)
    assert callable(health.RedisHealthChecker)
