"""Tests for per-request invocation-metadata parsing and caching."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from grpc_server_kit.aio.interceptors.metadata import _bytes_to_safe_str, get_metadata_dict

pytestmark = pytest.mark.unit


def _ctx(metadata: list[tuple[str, str | bytes]]) -> MagicMock:
    ctx = MagicMock()
    ctx.invocation_metadata.return_value = metadata
    return ctx


async def test__get_metadata_dict__mixed_metadata__decodes_bytes_and_str_values() -> None:
    # Arrange
    ctx = _ctx([("x-request-id", "abc"), ("x-bin", b"hello")])

    # Act
    result = get_metadata_dict(ctx)

    # Assert
    assert result == {"x-request-id": "abc", "x-bin": "hello"}


async def test__get_metadata_dict__called_twice_same_context__reads_invocation_metadata_once() -> None:
    # Arrange
    ctx = _ctx([("x-request-id", "abc")])
    get_metadata_dict(ctx)

    # Act
    # Cached within the same task/context: invocation_metadata is only read once.
    get_metadata_dict(ctx)

    # Assert
    assert ctx.invocation_metadata.call_count == 1


async def test__get_metadata_dict__different_context__recomputes() -> None:
    # Arrange
    first = _ctx([("k", "v1")])
    second = _ctx([("k", "v2")])

    # Act
    first_result = get_metadata_dict(first)
    # A different context object (a new RPC) must not see the cached dict.
    second_result = get_metadata_dict(second)

    # Assert
    assert first_result == {"k": "v1"}
    assert second_result == {"k": "v2"}


async def test__get_metadata_dict__concurrent_tasks__cache_isolated_per_task() -> None:
    # Arrange
    # Each RPC runs in its own task; a task must never see another task's cache.
    async def rpc(value: str) -> dict[str, str]:
        return get_metadata_dict(_ctx([("k", value)]))

    # Act
    results = await asyncio.gather(rpc("a"), rpc("b"))

    # Assert
    assert results == [{"k": "a"}, {"k": "b"}]


async def test__get_metadata_dict__none_metadata__returns_empty_dict() -> None:
    # Arrange
    ctx = MagicMock()
    ctx.invocation_metadata.return_value = None

    # Act
    result = get_metadata_dict(ctx)

    # Assert
    assert result == {}


async def test__get_metadata_dict__invalid_utf8_bytes__falls_back_to_hex() -> None:
    # Arrange
    ctx = _ctx([("x-bin", b"\xff\xfe")])

    # Act
    result = get_metadata_dict(ctx)

    # Assert
    assert result["x-bin"] == "fffe"


@pytest.mark.parametrize(
    ("payload", "expect_truncated"),
    [
        pytest.param(b"\xff" * 10, False, id="within_limit"),
        pytest.param(b"\xff" * 1000, True, id="over_limit"),
    ],
)
def test__bytes_to_safe_str__payload_size__hex_encodes_and_truncates_when_large(
    payload: bytes,
    expect_truncated: bool,
) -> None:
    # Act
    result = _bytes_to_safe_str(payload)

    # Assert
    if expect_truncated:
        assert result.endswith("...(truncated)")
        assert len(result) < len(payload.hex())
    else:
        assert result == payload.hex()


class _UnhashableContext:
    """Context objects need no hashing for the context-local cache."""

    __hash__ = None  # type: ignore[assignment]

    def invocation_metadata(self) -> list[tuple[str, str | bytes]]:
        return [("k", "v")]


async def test__get_metadata_dict__unhashable_context__is_supported() -> None:
    # Arrange
    ctx = _UnhashableContext()

    # Act
    result = get_metadata_dict(ctx)

    # Assert
    assert result == {"k": "v"}
