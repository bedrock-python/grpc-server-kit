"""The one-object happy path for running an async gRPC server.

:class:`GrpcApp` collapses builder + port binding + lifecycle into a single
facade::

    import asyncio
    from grpc_server_kit.aio import GrpcApp

    app = GrpcApp(port=50051)
    app.add_servicer(MyServicer(), add_MyServiceServicer_to_server)
    asyncio.run(app.run())

For finer control (DI containers, custom wiring) use the underlying pieces:
:class:`~grpc_server_kit.aio.builder.AsyncGrpcServerBuilder`,
:func:`~grpc_server_kit.server.bind_server_port`, and
:class:`~grpc_server_kit.aio.runtime.ServerLifecycleManager`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

from ..config import GrpcServerConfig
from ..constants import DEFAULT_HEALTH_CACHE_TTL, DEFAULT_HEALTH_CHECK_TIMEOUT, HEALTH_SERVICE_NAME
from ..server import bind_server_port
from .runtime import ServerLifecycleManager
from .server import AsyncServer, create_async_grpc_server

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import TracebackType

    import grpc

    from ..protocols import GrpcServerSettingsProtocol
    from .health.protocols import AsyncHealthChecker

__all__ = ["GrpcApp"]

logger = logging.getLogger(__name__)

_UNSET: Any = object()


class _HealthDefaultsProtocol(Protocol):
    """Duck-typed shape of a ``health`` settings block (see BaseHealthSettings)."""

    @property
    def cache_ttl(self) -> float: ...
    @property
    def check_timeout(self) -> float: ...


class GrpcApp:
    """Async gRPC application: configuration, servicers, and lifecycle in one place.

    The app is configured with plain method calls, then :meth:`run` builds the
    server, binds the port (TLS-aware), installs signal handlers, and serves
    until termination or :meth:`request_shutdown`.

    A GrpcApp instance is **single-use**: once its server has run and stopped
    (via :meth:`run` or the async context manager), create a new instance to
    serve again — gRPC servers cannot be restarted.

    Args:
        settings: Full server settings (anything satisfying
            ``GrpcServerSettingsProtocol``). When omitted, a default
            :class:`~grpc_server_kit.config.GrpcServerConfig` is created from
            ``host`` / ``port``.
        host: Bind host shortcut (only when ``settings`` is omitted).
        port: Bind port shortcut (only when ``settings`` is omitted; 0 binds an
            ephemeral port exposed via :attr:`bound_port`).
        interceptors: Initial interceptor chain (outermost first).
    """

    def __init__(
        self,
        settings: GrpcServerSettingsProtocol | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        interceptors: Sequence[grpc.aio.ServerInterceptor] | None = None,
    ) -> None:
        if settings is not None and (host is not None or port is not None):
            raise ValueError("Pass either settings or host/port shortcuts, not both")
        if settings is None:
            shortcut_kwargs: dict[str, Any] = {}
            if host is not None:
                shortcut_kwargs["host"] = host
            if port is not None:
                shortcut_kwargs["port"] = port
            settings = GrpcServerConfig(**shortcut_kwargs)

        self._settings = settings
        self._interceptors: list[grpc.aio.ServerInterceptor] = list(interceptors or [])
        self._registrars: list[Callable[[grpc.aio.Server], None]] = []
        self._reflection_names: list[str] | None = None
        self._channelz_enabled = settings.enable_channelz
        self._health_kwargs: dict[str, Any] | None = None
        self._server: AsyncServer | None = None
        self._manager: ServerLifecycleManager | None = None
        self._bound_port: int | None = None
        self._finished = False
        self._shutdown_requested: str | None = None

    # -- configuration ----------------------------------------------------------------

    def add_servicer(self, servicer: object, add_to_server: Callable[[Any, Any], Any]) -> None:
        """Add a servicer via its generated ``add_*Servicer_to_server`` function.

        Example::

            app.add_servicer(MyServicer(), add_MyServiceServicer_to_server)
        """
        self._ensure_not_built()
        self._registrars.append(lambda server: add_to_server(servicer, server))

    def register(self, callback: Callable[[grpc.aio.Server], None]) -> None:
        """Add a registration callback receiving the raw ``grpc.aio.Server``.

        Escape hatch for registrations that need the server object directly
        (generic handlers, third-party integrations).
        """
        self._ensure_not_built()
        self._registrars.append(callback)

    def add_interceptors(self, interceptors: Sequence[grpc.aio.ServerInterceptor]) -> None:
        """Append interceptors to the chain (outermost first)."""
        self._ensure_not_built()
        self._interceptors.extend(interceptors)

    def enable_reflection(self, service_names: Sequence[str]) -> None:
        """Enable server reflection for the given fully-qualified service names.

        Requires the ``[reflection]`` extra.
        """
        self._ensure_not_built()
        self._reflection_names = list(service_names)

    def enable_channelz(self) -> None:
        """Enable channelz debugging. Requires the ``[channelz]`` extra."""
        self._ensure_not_built()
        self._channelz_enabled = True

    def enable_health(
        self,
        checkers: Sequence[AsyncHealthChecker] | None = None,
        *,
        cache_ttl: float | None = _UNSET,
        check_timeout: float = _UNSET,
        service_names: Sequence[str] | None = None,
    ) -> None:
        """Enable the gRPC Health Checking Protocol v1 servicer.

        Requires the ``[health]`` extra. When ``cache_ttl`` / ``check_timeout``
        are not passed explicitly, they are read from ``settings.health`` if the
        app's settings object carries a health block (see
        :class:`~grpc_server_kit.settings.BaseHealthSettings`), else from the
        kit defaults.

        Args:
            checkers: Dependency checkers (``async check() -> bool``); with none,
                the service always reports SERVING.
            cache_ttl: TTL for cached check results in seconds. ``0`` and
                ``None`` both disable caching.
            check_timeout: Timeout for one health check run in seconds.
            service_names: Additional service names to report health for.

        Raises:
            ValueError: If ``cache_ttl`` is negative or ``check_timeout`` is not positive.
        """
        self._ensure_not_built()

        health_defaults: _HealthDefaultsProtocol | None = getattr(self._settings, "health", None)
        if cache_ttl is _UNSET:
            cache_ttl = health_defaults.cache_ttl if health_defaults is not None else DEFAULT_HEALTH_CACHE_TTL
        if check_timeout is _UNSET:
            check_timeout = (
                health_defaults.check_timeout if health_defaults is not None else DEFAULT_HEALTH_CHECK_TIMEOUT
            )

        if cache_ttl is not None and cache_ttl < 0:
            raise ValueError(f"cache_ttl must be non-negative or None, got {cache_ttl}")
        if check_timeout <= 0:
            raise ValueError(f"check_timeout must be positive, got {check_timeout}")

        self._health_kwargs = {
            "checkers": list(checkers) if checkers else None,
            # 0 naturally means "no caching" — normalize it to None here so a
            # settings-valid 0 can never crash HealthCache at build time.
            "cache_ttl": cache_ttl if cache_ttl else None,
            "check_timeout": check_timeout,
            "service_names": list(service_names) if service_names else None,
        }

    # -- state ------------------------------------------------------------------------

    @property
    def settings(self) -> GrpcServerSettingsProtocol:
        """The settings this app was configured with."""
        return self._settings

    @property
    def server(self) -> AsyncServer:
        """The built server (available after :meth:`build` / :meth:`run`)."""
        if self._server is None:
            raise RuntimeError("Server is not built yet; call build() or run() first")
        return self._server

    @property
    def bound_port(self) -> int:
        """The actually bound port (useful with ``port=0``)."""
        if self._bound_port is None:
            raise RuntimeError("Port is not bound yet; call build() or run() first")
        return self._bound_port

    def request_shutdown(self, reason: str = "requested") -> None:
        """Request a graceful stop of this app.

        Idempotent and safe at any point of the lifecycle — stopping something
        that is not running is not an error, and any "is it still running?"
        check by the caller would be racy anyway (the server can terminate on
        its own between the check and the call):

        - while serving: the :meth:`run` loop drains and returns;
        - before :meth:`run`: the request is remembered and honored as soon as
          the server starts, so a signal racing startup is never lost;
        - after the app has stopped: no-op.
        """
        if self._shutdown_requested is None:
            self._shutdown_requested = reason
        if self._manager is not None:
            self._manager.request_shutdown(reason)
        else:
            logger.debug("Shutdown requested while not serving", extra={"reason": reason})

    # -- lifecycle --------------------------------------------------------------------

    def build(self) -> AsyncServer:
        """Build the server, register servicers/health, and bind the port.

        Idempotent: subsequent calls return the already-built server. Nothing is
        cached on failure, so a failed build (e.g. a busy port or a bad TLS
        file) can simply be retried after fixing the cause.
        """
        self._ensure_not_finished()
        if self._server is not None:
            return self._server

        registrars = list(self._registrars)
        reflection_names = list(self._reflection_names) if self._reflection_names is not None else None

        if self._health_kwargs is not None:
            registrars.append(self._make_health_registrar(self._health_kwargs))
            if reflection_names is not None and HEALTH_SERVICE_NAME not in reflection_names:
                reflection_names.append(HEALTH_SERVICE_NAME)

        enable_reflection = reflection_names is not None or self._settings.enable_reflection
        if enable_reflection and not reflection_names:
            raise ValueError(
                "Reflection is enabled but no service names are configured; "
                "call app.enable_reflection([...]) with your fully-qualified service names"
            )

        def register_all(server: grpc.aio.Server) -> None:
            for registrar in registrars:
                registrar(server)

        # Build into locals; publish to self only after the port is bound so a
        # bind failure leaves the app clean and retryable.
        server = create_async_grpc_server(
            interceptors=self._interceptors,
            settings=self._settings,
            register_servicers=register_all,
            enable_reflection=enable_reflection,
            reflection_service_names=reflection_names,
            enable_channelz=self._channelz_enabled,
        )
        bound_port = bind_server_port(server, self._settings)

        self._server = server
        self._bound_port = bound_port
        return server

    async def run(self, *, setup_signals: bool = True) -> None:
        """Build (if needed) and serve until termination, signal, or shutdown request."""
        self._ensure_not_finished()
        if self._manager is not None:
            raise RuntimeError("Server is already running")

        server = self.build()
        self._manager = ServerLifecycleManager(
            server=server,
            address=f"{self._settings.host}:{self.bound_port}",
            grace_period=self._settings.grace_period,
        )
        if self._shutdown_requested is not None:
            # A shutdown requested before serving started (e.g. SIGTERM racing
            # a slow startup) is honored instead of being dropped.
            self._manager.request_shutdown(self._shutdown_requested)
        try:
            await self._manager.run(setup_signals=setup_signals)
        finally:
            self._manager = None
            self._finished = True

    def run_sync(self, *, setup_signals: bool = True) -> None:
        """Blocking convenience wrapper: ``asyncio.run(app.run())``."""
        asyncio.run(self.run(setup_signals=setup_signals))

    async def __aenter__(self) -> GrpcApp:
        """Start the server without signal handling (embedding/tests)."""
        self._ensure_not_finished()
        server = self.build()
        await server.start()
        logger.info("gRPC server started", extra={"address": f"{self._settings.host}:{self.bound_port}"})
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Stop the server with the configured grace period."""
        self._finished = True
        await self.server.stop(self._settings.grace_period)

    # -- internals --------------------------------------------------------------------

    def _ensure_not_built(self) -> None:
        if self._server is not None:
            raise RuntimeError("Server is already built; configure the app before build()/run()")

    def _ensure_not_finished(self) -> None:
        if self._finished:
            raise RuntimeError(
                "This GrpcApp has already served and stopped; gRPC servers cannot restart — create a new GrpcApp"
            )

    @staticmethod
    def _make_health_registrar(health_kwargs: dict[str, Any]) -> Callable[[grpc.aio.Server], None]:
        try:
            # Deferred import: the [health] extra is optional for the core app.
            from grpc_health.v1 import health_pb2_grpc  # noqa: PLC0415

            from .health import AsyncDynamicHealthServicer, HealthCache  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "grpcio-health-checking is not installed. "
                "Please install it with 'pip install grpcio-health-checking' "
                "or use 'grpc-server-kit[health]' optional dependency."
            ) from exc

        cache_ttl = health_kwargs["cache_ttl"]
        servicer = AsyncDynamicHealthServicer(
            checkers=health_kwargs["checkers"],
            cache=HealthCache(ttl=cache_ttl) if cache_ttl is not None else None,
            service_names=health_kwargs["service_names"],
            check_timeout=health_kwargs["check_timeout"],
        )

        def register(server: grpc.aio.Server) -> None:
            health_pb2_grpc.add_HealthServicer_to_server(servicer, server)

        return register
