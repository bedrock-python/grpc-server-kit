"""Shared fixtures for tests/unit/aio test modules."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_settings() -> MagicMock:
    """Bare gRPC server settings double; each test sets only the attributes it needs."""
    return MagicMock()
