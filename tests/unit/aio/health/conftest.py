"""Shared fixtures for grpc_server_kit.aio.health tests."""

from __future__ import annotations

import pytest


class FakeHealthChecker:
    """AsyncHealthChecker double that returns a fixed result or raises a fixed exception.

    Shared by test_orchestrator.py and test_servicer.py, which both need a minimal
    ``AsyncHealthChecker`` (``async check() -> bool``) test double.
    """

    def __init__(self, result: bool = True, exc: BaseException | None = None) -> None:
        self._result = result
        self._exc = exc

    async def check(self) -> bool:
        if self._exc is not None:
            raise self._exc
        return self._result


@pytest.fixture
def healthy_checker() -> FakeHealthChecker:
    return FakeHealthChecker(result=True)


@pytest.fixture
def failing_checker() -> FakeHealthChecker:
    return FakeHealthChecker(result=False)
