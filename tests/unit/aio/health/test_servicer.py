"""Tests for the dynamic health check servicer."""

from __future__ import annotations

from collections.abc import Iterable
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest
from grpc_health.v1 import health_pb2
from pytest_lazy_fixtures import lf

from grpc_server_kit.aio.health.servicer import AsyncDynamicHealthServicer

from .conftest import FakeHealthChecker

pytestmark = pytest.mark.unit

SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING
SERVICE_UNKNOWN = health_pb2.HealthCheckResponse.SERVICE_UNKNOWN


class _Ctx:
    """Minimal servicer context (no wait_for_termination attribute)."""

    def __init__(self, done_seq: Iterable[bool]) -> None:
        self._done = iter(done_seq)

    def done(self) -> bool:
        return next(self._done)


async def _collect(
    servicer: AsyncDynamicHealthServicer,
    request: health_pb2.HealthCheckRequest,
    context: object,
) -> list[health_pb2.HealthCheckResponse]:
    return [r async for r in servicer.Watch(request, context)]


@pytest.fixture
def two_iteration_context() -> _Ctx:
    """Context whose done() sequence allows exactly two check iterations before terminating.

    done() is consulted twice per iteration (loop condition + before sleep): two full
    iterations, then termination.
    """
    return _Ctx([False, False, False, True])


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"check_interval": 0}, "check_interval must be positive"),
        ({"heartbeat_interval": 1, "check_interval": 5}, "must be >="),
        ({"check_timeout": 0}, "check_timeout must be positive"),
    ],
    ids=["check_interval_non_positive", "heartbeat_below_check_interval", "check_timeout_non_positive"],
)
def test__health_servicer__init_invalid_arguments__raises(kwargs: dict[str, float], match: str) -> None:
    # Act & Assert
    with pytest.raises(ValueError, match=match):
        AsyncDynamicHealthServicer(**kwargs)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("", True),
        ("grpc.health.v1.Health", True),
        ("a" * 254, False),
        ("bad name!", False),
    ],
    ids=["empty_string", "valid_name", "too_long", "invalid_characters"],
)
def test__health_servicer__service_name_format__reports_validity(name: str, expected: bool) -> None:
    # Arrange
    servicer = AsyncDynamicHealthServicer()

    # Act
    result = servicer._validate_service_name(name)

    # Assert
    assert result is expected


@pytest.fixture
def servicer_with_known_service() -> AsyncDynamicHealthServicer:
    return AsyncDynamicHealthServicer(service_names=["my.Service"])


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("", True),
        ("my.Service", True),
        ("other", False),
    ],
    ids=["overall_health_wildcard", "registered_service", "unregistered_service"],
)
def test__health_servicer__registered_service_names__reports_membership(
    servicer_with_known_service: AsyncDynamicHealthServicer,
    service: str,
    expected: bool,
) -> None:
    # Act
    result = servicer_with_known_service._is_service_known(service)

    # Assert
    assert result is expected


@pytest.fixture
def servicer_for_update_checks() -> AsyncDynamicHealthServicer:
    return AsyncDynamicHealthServicer(check_interval=1.0, heartbeat_interval=10.0)


@pytest.mark.parametrize(
    ("status", "last_status", "last_heartbeat", "now", "expected"),
    [
        (SERVING, None, 0.0, 1.0, True),
        (SERVING, SERVING, 0.0, 20.0, True),
        (SERVING, SERVING, 0.0, 1.0, False),
    ],
    ids=["status_changed", "heartbeat_interval_reached", "unchanged_and_no_heartbeat"],
)
def test__health_servicer__update_conditions__reports_whether_to_send(
    servicer_for_update_checks: AsyncDynamicHealthServicer,
    status: health_pb2.HealthCheckResponse.ServingStatus,
    last_status: health_pb2.HealthCheckResponse.ServingStatus | None,
    last_heartbeat: float,
    now: float,
    expected: bool,
) -> None:
    # Act
    result = servicer_for_update_checks._should_send_update(status, last_status, last_heartbeat=last_heartbeat, now=now)

    # Assert
    assert result is expected


async def test__health_servicer__invalid_service_name__returns_service_unknown() -> None:
    # Arrange
    servicer = AsyncDynamicHealthServicer()
    request = health_pb2.HealthCheckRequest(service="bad name!")

    # Act
    resp = await servicer.Check(request, MagicMock())

    # Assert
    assert resp.status == SERVICE_UNKNOWN


async def test__health_servicer__unknown_service__returns_service_unknown() -> None:
    # Arrange
    servicer = AsyncDynamicHealthServicer()
    request = health_pb2.HealthCheckRequest(service="not.Registered")

    # Act
    resp = await servicer.Check(request, MagicMock())

    # Assert
    assert resp.status == SERVICE_UNKNOWN


@pytest.mark.parametrize(
    ("checkers", "expected"),
    [
        (None, SERVING),
        ([lf("failing_checker")], NOT_SERVING),
    ],
    ids=["no_checkers", "failing_checker"],
)
async def test__health_servicer__check_with_checkers__returns_matching_status(
    checkers: list[FakeHealthChecker] | None,
    expected: health_pb2.HealthCheckResponse.ServingStatus,
) -> None:
    # Arrange
    servicer = AsyncDynamicHealthServicer(checkers=checkers)
    request = health_pb2.HealthCheckRequest(service="")

    # Act
    resp = await servicer.Check(request, MagicMock())

    # Assert
    assert resp.status == expected


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("recoverable"), KeyError("unexpected")],
    ids=["recoverable", "unexpected"],
)
async def test__health_servicer__perform_health_check_raises__returns_not_serving(exc: Exception) -> None:
    # Arrange
    servicer = AsyncDynamicHealthServicer()

    # Act
    with patch("grpc_server_kit.aio.health.servicer.check_async_overall_health", side_effect=exc):
        status = await servicer._perform_health_check(service="x")

    # Assert
    assert status == NOT_SERVING


async def test__health_servicer__watch_invalid_service_name__yields_service_unknown() -> None:
    # Arrange
    servicer = AsyncDynamicHealthServicer()
    request = health_pb2.HealthCheckRequest(service="bad name!")

    # Act
    responses = await _collect(servicer, request, MagicMock())

    # Assert
    assert [r.status for r in responses] == [SERVICE_UNKNOWN]


async def test__health_servicer__watch_unknown_service__yields_service_unknown() -> None:
    # Arrange
    servicer = AsyncDynamicHealthServicer()
    request = health_pb2.HealthCheckRequest(service="not.Registered")

    # Act
    responses = await _collect(servicer, request, MagicMock())

    # Assert
    assert [r.status for r in responses] == [SERVICE_UNKNOWN]


async def test__health_servicer__watch_done_after_first_check__yields_one_update() -> None:
    # Arrange
    servicer = AsyncDynamicHealthServicer()
    request = health_pb2.HealthCheckRequest(service="")
    context = MagicMock()
    context.done.side_effect = [False, True]  # yield once, then terminate

    # Act
    responses = await _collect(servicer, request, context)

    # Assert
    assert [r.status for r in responses] == [SERVING]


async def test__health_servicer__watch_short_heartbeat_interval__resends_every_iteration(
    two_iteration_context: _Ctx,
) -> None:
    # Arrange
    servicer = AsyncDynamicHealthServicer(check_interval=0.01, heartbeat_interval=0.01)
    request = health_pb2.HealthCheckRequest(service="")

    # Act
    responses = await _collect(servicer, request, two_iteration_context)

    # Assert
    assert [r.status for r in responses] == [SERVING, SERVING]


async def test__health_servicer__watch_long_heartbeat_interval__suppresses_repeat_status(
    two_iteration_context: _Ctx,
) -> None:
    # Arrange
    servicer = AsyncDynamicHealthServicer(check_interval=0.01, heartbeat_interval=60.0)
    request = health_pb2.HealthCheckRequest(service="")

    # Act
    responses = await _collect(servicer, request, two_iteration_context)

    # Assert
    # Unchanged status within the heartbeat interval is sent only once.
    assert [r.status for r in responses] == [SERVING]


async def test__health_servicer__watch_internal_failure__aborts_instead_of_clean_eof() -> None:
    # Arrange
    # Per Health v1 a Watch stream never completes normally: infrastructure
    # failures must surface as a stream error, not an OK end-of-stream that
    # clients would read as a deliberate completion.
    servicer = AsyncDynamicHealthServicer(check_interval=0.01, heartbeat_interval=0.01)
    request = health_pb2.HealthCheckRequest(service="")

    context = MagicMock()
    context.done.side_effect = [False, RuntimeError("connection torn")]

    async def _abort(_code: object, _details: str = "") -> None:
        raise grpc.aio.AbortError

    context.abort = AsyncMock(side_effect=_abort)
    responses = []

    # Act & Assert
    with pytest.raises(grpc.aio.AbortError):
        async for response in servicer.Watch(request, context):
            responses.append(response)

    assert [r.status for r in responses] == [SERVING]
    assert context.abort.call_args.args[0] == grpc.StatusCode.INTERNAL
