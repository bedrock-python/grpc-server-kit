"""Tests for the shared Dishka integration's public API surface."""

from __future__ import annotations

import pytest

from grpc_server_kit import dishka

pytestmark = pytest.mark.unit


def test__dishka_module__public_api__exports_every_declared_name() -> None:
    # Act
    missing = [name for name in dishka.__all__ if not hasattr(dishka, name)]

    # Assert
    assert missing == []


def test__dishka_module__helper_exports__are_callable() -> None:
    # Assert
    assert callable(dishka.inject)
    assert callable(dishka.from_context)
