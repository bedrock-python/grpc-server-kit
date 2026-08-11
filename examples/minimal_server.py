"""The smallest real grpc-server-kit server.

This example shows how to:

1. Build a `GrpcApp` bound to an ephemeral port (`port=0`) and read back the
   actual port via `app.bound_port`.
2. Register a service. A real service compiles a `.proto` file into a
   servicer base class and an `add_MyServiceServicer_to_server()` function,
   then wires them up with:

       app.add_servicer(MyServicer(), add_MyServiceServicer_to_server)

   This example ships no compiled protos, so it registers a plain
   `grpc.GenericRpcHandler` instead via `app.register(...)` -- the same
   technique `tests/integration/conftest.py` uses to exercise the kit
   without protoc.
3. Run the server with `asyncio.run(app.run())`, the one-liner from the
   README quickstart.
4. Terminate on its own instead of hanging forever: a background task calls
   `app.request_shutdown()` after a few seconds. Ctrl+C works too --
   `app.run()` installs SIGINT/SIGTERM handlers that call that very same
   `request_shutdown()`.

Run with: `python examples/minimal_server.py`. It prints the bound port and
a client hint, serves for a few seconds (or until Ctrl+C), then shuts down
gracefully and exits 0.
"""

from __future__ import annotations

import asyncio
import contextlib

import grpc

from grpc_server_kit import GrpcApp, GrpcServerConfig

SERVICE_NAME = "example.Greeter"
SHUTDOWN_AFTER_SECONDS = 3.0


async def say_hello(request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    """The RPC handler. In a real service this would be a method on your servicer class."""
    return b"Hello, " + request + b"!"


def build_generic_handler() -> grpc.GenericRpcHandler:
    """Expose one unary RPC with no protoc-generated code.

    A real service instead runs protoc/buf to generate `GreeterServicer` and
    `add_GreeterServicer_to_server`, then registers it with:

        app.add_servicer(MyServicer(), add_GreeterServicer_to_server)
    """
    return grpc.method_handlers_generic_handler(
        SERVICE_NAME,
        {"SayHello": grpc.unary_unary_rpc_method_handler(say_hello)},
    )


async def request_shutdown_after_delay(app: GrpcApp, delay: float) -> None:
    """Stop the server after `delay` seconds so this example is self-terminating."""
    await asyncio.sleep(delay)
    print(f"[server] {delay:.0f}s demo timer elapsed, requesting graceful shutdown")
    app.request_shutdown("example timer elapsed")


async def main() -> None:
    config = GrpcServerConfig(host="127.0.0.1", port=0, grace_period=1.0)
    app = GrpcApp(config)
    app.register(lambda raw: raw.add_generic_rpc_handlers((build_generic_handler(),)))

    # build() binds the port right away so we can print it before serving.
    # run() calls build() again internally -- it is idempotent and just
    # returns the already-built server.
    app.build()
    port = app.bound_port
    print(f"[server] listening on 127.0.0.1:{port}")
    print(f"[client hint] grpcurl -plaintext -d '\"world\"' 127.0.0.1:{port} {SERVICE_NAME}/SayHello")
    print(f"[client hint] python: channel = grpc.aio.insecure_channel('127.0.0.1:{port}')")
    print(f'[client hint]         await channel.unary_unary("/{SERVICE_NAME}/SayHello")(b"world")')
    print(f"[server] will shut down in {SHUTDOWN_AFTER_SECONDS:.0f}s -- press Ctrl+C to stop sooner")

    # Fire-and-forget, but keep the reference so we can cancel it cleanly if
    # the server stops for some other reason first (e.g. Ctrl+C).
    shutdown_task = asyncio.create_task(request_shutdown_after_delay(app, SHUTDOWN_AFTER_SECONDS))
    try:
        await app.run()
    finally:
        shutdown_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await shutdown_task

    print("[server] stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
