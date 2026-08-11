"""Writing a custom interceptor.

This example shows how to:

1. Subclass `AsyncServerInterceptor` and implement the single `around_call`
   async-generator hook (code before `yield` runs first, the RPC executes at
   the `yield`, code after -- or in a `finally` -- runs last).
2. Confirm the hook wraps the WHOLE RPC for both call kinds: for the unary
   call the timing covers the single response; for the server-streaming call
   it covers production of the ENTIRE stream, not just the first item.
3. Use `skip_methods` to exclude a method from interception entirely -- the
   original handler is registered completely unwrapped, at zero overhead.

Run with: `python examples/custom_interceptor.py`. It starts a server with
one custom timing interceptor, calls a unary method, a server-streaming
method, and a skipped method, prints the measured timings, and exits 0.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import grpc

from grpc_server_kit import GrpcApp, GrpcServerConfig
from grpc_server_kit.aio.interceptors import AsyncServerInterceptor, RpcCall

SERVICE_NAME = "example.Timed"
SKIPPED_METHOD = f"/{SERVICE_NAME}/Ping"


class TimingInterceptor(AsyncServerInterceptor):
    """Measures wall-clock time spent inside each RPC, including full streams."""

    async def around_call(self, call: RpcCall) -> AsyncIterator[None]:
        start = time.perf_counter()
        try:
            yield  # the RPC -- or, for a streaming response, the WHOLE stream -- runs here
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            kind = "stream" if call.response_streaming else "unary"
            print(f"[timing] {call.method_name} ({kind}) took {elapsed_ms:.2f}ms")


async def slow_echo(request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    """A unary handler with an artificial delay."""
    await asyncio.sleep(0.05)
    return b"echo:" + request


async def slow_count_up(request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> AsyncIterator[bytes]:
    """A server-streaming handler: each item is delayed, so the whole stream takes a while."""
    for i in range(1, int(request) + 1):
        await asyncio.sleep(0.03)
        yield str(i).encode()


async def ping(_request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    """Registered with skip_methods -- this one is never timed."""
    return b"pong"


def build_generic_handler() -> grpc.GenericRpcHandler:
    return grpc.method_handlers_generic_handler(
        SERVICE_NAME,
        {
            "SlowEcho": grpc.unary_unary_rpc_method_handler(slow_echo),
            "SlowCountUp": grpc.unary_stream_rpc_method_handler(slow_count_up),
            "Ping": grpc.unary_unary_rpc_method_handler(ping),
        },
    )


async def main() -> None:
    interceptor = TimingInterceptor(skip_methods={SKIPPED_METHOD})
    config = GrpcServerConfig(host="127.0.0.1", port=0, grace_period=1.0)
    app = GrpcApp(config, interceptors=[interceptor])
    app.register(lambda raw: raw.add_generic_rpc_handlers((build_generic_handler(),)))

    async with app:
        target = f"127.0.0.1:{app.bound_port}"
        print(f"[server] listening on {target}")

        async with grpc.aio.insecure_channel(target) as channel:
            print("\n--- unary call ---")
            echo = channel.unary_unary(f"/{SERVICE_NAME}/SlowEcho")
            result = await echo(b"hi")
            print(f"[client] SlowEcho result={result!r}")

            print("\n--- server-streaming call (timing spans the whole stream) ---")
            count_up = channel.unary_stream(f"/{SERVICE_NAME}/SlowCountUp")
            items = [item async for item in count_up(b"4")]
            print(f"[client] SlowCountUp items={items!r}")

            print("\n--- skipped method (no [timing] line printed for this call) ---")
            ping_call = channel.unary_unary(SKIPPED_METHOD)
            pong = await ping_call(b"")
            print(f"[client] Ping result={pong!r}")


if __name__ == "__main__":
    asyncio.run(main())
