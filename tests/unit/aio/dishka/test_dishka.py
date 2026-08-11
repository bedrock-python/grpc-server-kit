"""Tests for the async Dishka integration's public API surface."""

from __future__ import annotations

import pytest

from grpc_server_kit.aio import dishka

pytestmark = pytest.mark.unit


def test__aio_dishka_module__public_api__exports_every_declared_name() -> None:
    # Act
    missing = [name for name in dishka.__all__ if not hasattr(dishka, name)]

    # Assert
    assert missing == []


def test__aio_dishka_module__dishka_aio_interceptor__is_callable() -> None:
    # Assert
    assert callable(dishka.DishkaAioInterceptor)
