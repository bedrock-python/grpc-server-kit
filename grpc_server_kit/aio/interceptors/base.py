"""Streaming-aware base class for async gRPC server interceptors.

The kit's interceptors wrap *whole* RPCs — including response-streaming ones —
so timings, error mapping, and cleanup cover the full stream lifetime, not just
handler creation. Subclasses implement a single async-generator hook,
:meth:`AsyncServerInterceptor.around_call`, which the base class turns into a
context manager around each of the four gRPC call kinds (unary-unary,
unary-stream, stream-unary, stream-stream).
"""

from __future__ import annotations

import abc
import contextlib
import dataclasses
import functools
from typing import TYPE_CHECKING, Any

import grpc

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Collection

__all__ = ["AsyncServerInterceptor", "RpcCall", "split_method_name"]


@dataclasses.dataclass(frozen=True, slots=True)
class RpcCall:
    """One RPC as seen by an interceptor.

    Attributes:
        method_name: Full RPC name, e.g. ``"/pkg.Service/Method"``.
        request: The request message (unary request) or an async iterator of
            request messages (streaming request).
        context: The gRPC servicer context of this RPC.
        request_streaming: True for stream-unary / stream-stream calls.
        response_streaming: True for unary-stream / stream-stream calls.
    """

    method_name: str
    request: Any
    context: grpc.aio.ServicerContext[Any, Any]
    request_streaming: bool
    response_streaming: bool


def split_method_name(method_name: str) -> tuple[str, str]:
    """Split ``"/pkg.Service/Method"`` into ``("pkg.Service", "Method")``.

    Returns empty strings for parts that cannot be parsed.
    """
    service, _, method = method_name.removeprefix("/").partition("/")
    return service, method


@contextlib.asynccontextmanager
async def _null_around() -> AsyncIterator[None]:
    yield


def _get_factory_and_behavior(
    handler: grpc.RpcMethodHandler,
) -> tuple[Callable[..., grpc.RpcMethodHandler], Callable[..., Any]]:
    """Return the matching ``*_rpc_method_handler`` factory and the handler behavior."""
    if handler.unary_unary:
        return grpc.unary_unary_rpc_method_handler, handler.unary_unary
    if handler.unary_stream:
        return grpc.unary_stream_rpc_method_handler, handler.unary_stream
    if handler.stream_unary:
        return grpc.stream_unary_rpc_method_handler, handler.stream_unary
    if handler.stream_stream:
        return grpc.stream_stream_rpc_method_handler, handler.stream_stream
    raise NotImplementedError("RPC handler implements no known behavior")


class AsyncServerInterceptor(grpc.aio.ServerInterceptor, abc.ABC):
    """Base class for async server interceptors covering all four RPC kinds.

    Subclasses implement :meth:`around_call` — an async generator that yields
    exactly once:

    - code before ``yield`` runs before the RPC handler is invoked;
    - the RPC (including full consumption of a streaming response) happens at
      the ``yield`` point;
    - code after ``yield`` (or in a ``finally`` block) runs after the RPC has
      fully completed.

    An exception raised by the handler — at any point of a streaming response —
    propagates through the ``yield``, so ordinary ``try/except`` around it
    covers the whole call. Note that :meth:`grpc.aio.ServicerContext.abort`
    raises :class:`grpc.aio.AbortError` (not ``grpc.RpcError``); interceptors
    that must not treat an intentional abort as a failure should re-raise it.

    Args:
        skip_methods: Full RPC method names (e.g. ``"/grpc.health.v1.Health/Check"``)
            this interceptor must not wrap at all — handlers for these methods
            pass through with zero interception overhead.

    Example::

        class TimingInterceptor(AsyncServerInterceptor):
            async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
                start = time.perf_counter()
                try:
                    yield
                finally:
                    print(call.method_name, time.perf_counter() - start)
    """

    def __init__(self, *, skip_methods: Collection[str] = ()) -> None:
        super().__init__()
        self._skip_methods = frozenset(skip_methods)

    @abc.abstractmethod
    def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        """Wrap one RPC (async generator: setup before ``yield``, teardown after)."""
        ...

    @functools.cached_property
    def _around_factory(self) -> Callable[[RpcCall], contextlib.AbstractAsyncContextManager[None]]:
        # Built once per interceptor instance; asynccontextmanager itself
        # produces a fresh context manager per call.
        return contextlib.asynccontextmanager(self.around_call)

    @functools.cached_property
    def _wrapped_handlers(self) -> dict[str, tuple[grpc.RpcMethodHandler, grpc.RpcMethodHandler]]:
        # Per-method cache of (source handler, wrapped handler). Registered
        # handlers are stable objects, so the identity check on the source
        # handler keeps the cache correct even for dynamic generic handlers.
        return {}

    def around(self, call: RpcCall) -> contextlib.AbstractAsyncContextManager[None]:
        """Return the per-RPC context manager built from :meth:`around_call`.

        Skipped methods get a no-op context manager. Useful for composing
        interceptors manually and for unit tests.
        """
        if call.method_name in self._skip_methods:
            return _null_around()
        return self._around_factory(call)

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler | None]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        """Wrap the resolved RPC handler with :meth:`around_call` (grpc plumbing)."""
        handler = await continuation(handler_call_details)
        if handler is None:
            return None

        raw_method = handler_call_details.method
        method_name = raw_method.decode("utf-8", errors="replace") if isinstance(raw_method, bytes) else raw_method

        if method_name in self._skip_methods:
            return handler

        cached = self._wrapped_handlers.get(method_name)
        if cached is not None and cached[0] is handler:
            return cached[1]

        factory, behavior = _get_factory_and_behavior(handler)

        wrapped_behavior: Callable[..., Any]
        if handler.response_streaming:
            wrapped_behavior = self._wrap_streaming_response(behavior, method_name, bool(handler.request_streaming))
        else:
            wrapped_behavior = self._wrap_unary_response(behavior, method_name, bool(handler.request_streaming))

        wrapped = factory(
            wrapped_behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
        self._wrapped_handlers[method_name] = (handler, wrapped)
        return wrapped

    def _wrap_unary_response(
        self,
        behavior: Callable[..., Any],
        method_name: str,
        request_streaming: bool,
    ) -> Callable[..., Any]:
        async def invoke(request_or_iterator: Any, context: grpc.aio.ServicerContext[Any, Any]) -> Any:
            call = RpcCall(
                method_name=method_name,
                request=request_or_iterator,
                context=context,
                request_streaming=request_streaming,
                response_streaming=False,
            )
            async with self.around(call):
                response = await behavior(request_or_iterator, context)
            return response

        return invoke

    def _wrap_streaming_response(
        self,
        behavior: Callable[..., Any],
        method_name: str,
        request_streaming: bool,
    ) -> Callable[..., Any]:
        async def invoke(
            request_or_iterator: Any,
            context: grpc.aio.ServicerContext[Any, Any],
        ) -> AsyncIterator[Any]:
            call = RpcCall(
                method_name=method_name,
                request=request_or_iterator,
                context=context,
                request_streaming=request_streaming,
                response_streaming=True,
            )
            async with self.around(call):
                result = behavior(request_or_iterator, context)
                if not hasattr(result, "__aiter__"):
                    # Handlers written against the read/write API are plain
                    # coroutines (returning None); interceptor-wrapped handlers
                    # may also be coroutines resolving to an async generator.
                    result = await result
                if result is not None:
                    try:
                        async for response in result:
                            yield response
                    finally:
                        # Close the inner generator NOW (client gone / early
                        # break included) so servicer finally-blocks and DI
                        # teardown run inside the RPC, not later under the
                        # event loop's async-generator finalizer.
                        aclose = getattr(result, "aclose", None)
                        if aclose is not None:
                            await aclose()

        return invoke
