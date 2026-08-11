"""Tests for the Sentry SDK adapter (``ErrorReporterProtocol`` implementation)."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from grpc_server_kit.observability.sentry import SentrySdkAdapter

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_sentry_sdk() -> Iterator[MagicMock]:
    """Patch the ``sentry_sdk`` module referenced by the adapter."""
    with patch("grpc_server_kit.observability.sentry.sentry_sdk") as mock_sdk:
        yield mock_sdk


@pytest.fixture
def adapter(mock_sentry_sdk: MagicMock) -> SentrySdkAdapter:
    """Adapter constructed while ``sentry_sdk`` is patched."""
    return SentrySdkAdapter()


def test__sentry_sdk_adapter__add_breadcrumb__delegates_to_sentry_sdk(
    adapter: SentrySdkAdapter,
    mock_sentry_sdk: MagicMock,
) -> None:
    # Act
    adapter.add_breadcrumb("msg", category="grpc", level="info", data={"k": "v"})

    # Assert
    mock_sentry_sdk.add_breadcrumb.assert_called_once_with(
        message="msg", category="grpc", level="info", data={"k": "v"}
    )


def test__sentry_sdk_adapter__add_breadcrumb_without_data__defaults_to_empty_dict(
    adapter: SentrySdkAdapter,
    mock_sentry_sdk: MagicMock,
) -> None:
    # Act
    adapter.add_breadcrumb("msg")

    # Assert
    mock_sentry_sdk.add_breadcrumb.assert_called_once_with(message="msg", category="default", level="info", data={})


def test__sentry_sdk_adapter__capture_exception__delegates_to_sentry_sdk(
    adapter: SentrySdkAdapter,
    mock_sentry_sdk: MagicMock,
) -> None:
    # Arrange
    exc = RuntimeError("boom")

    # Act
    adapter.capture_exception(exc)

    # Assert
    mock_sentry_sdk.capture_exception.assert_called_once_with(exc)


def test__sentry_sdk_adapter__set_tags__delegates_each_tag_to_sentry_sdk(
    adapter: SentrySdkAdapter,
    mock_sentry_sdk: MagicMock,
) -> None:
    # Act
    adapter.set_tags(a="1", b="2")

    # Assert
    assert mock_sentry_sdk.set_tag.call_count == 2


def test__sentry_sdk_adapter__isolate__forks_isolation_scope(
    adapter: SentrySdkAdapter,
    mock_sentry_sdk: MagicMock,
) -> None:
    # Act
    scope = adapter.isolate()

    # Assert
    mock_sentry_sdk.isolation_scope.assert_called_once_with()
    assert scope is mock_sentry_sdk.isolation_scope.return_value


def test__sentry_sdk_adapter__isolate__works_against_real_sdk() -> None:
    # sentry_sdk.isolation_scope() must exist in the pinned sentry-sdk 2.x —
    # guard against the API disappearing in an upgrade.
    # Act
    with SentrySdkAdapter().isolate():
        pass


def test__sentry_sdk_adapter__sentry_sdk_missing__raises_import_error() -> None:
    # Act & Assert
    with (
        patch("grpc_server_kit.observability.sentry.HAS_SENTRY", False),
        pytest.raises(ImportError, match=r"grpc-server-kit\[sentry\]"),
    ):
        SentrySdkAdapter()
