"""gRPC health checking with a custom dependency checker.

This example shows how to:

1. Implement a custom health checker: any object with `async check() -> bool`
   satisfies `AsyncHealthChecker` structurally -- no base class to inherit.
2. Wire it up with `app.enable_health(checkers=[...])` (the `[health]`
   extra -- `grpcio-health-checking`).
3. Drive it through the REAL `grpc_health.v1` stubs, exactly like a
   Kubernetes liveness/readiness probe or `grpcurl grpc.health.v1.Health/Check`
   would.
4. Watch the reported status flip from SERVING to NOT_SERVING when the
   dependency degrades.

Health status is cached for `cache_ttl` seconds (5s by default -- see
`grpc_server_kit.constants.DEFAULT_HEALTH_CACHE_TTL`), with single-flight
semantics: concurrent probes share one real dependency check per TTL window
instead of stampeding an already-struggling dependency. This example uses a
short `cache_ttl` and sleeps past it before the second call, so the flip
below is visible on the very next `Check` instead of a stale cached result.

Run with: `python examples/health_checks.py`. It exits 0 after showing both
a SERVING and a NOT_SERVING response.
"""

from __future__ import annotations

import asyncio

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from grpc_server_kit import GrpcApp, GrpcServerConfig

CACHE_TTL_SECONDS = 0.2


class DependencyHealthChecker:
    """Satisfies `AsyncHealthChecker` structurally: any `async check() -> bool` works."""

    def __init__(self) -> None:
        self.healthy = True

    async def check(self) -> bool:
        return self.healthy


async def main() -> None:
    checker = DependencyHealthChecker()
    config = GrpcServerConfig(host="127.0.0.1", port=0, grace_period=1.0)
    app = GrpcApp(config)
    app.enable_health(checkers=[checker], cache_ttl=CACHE_TTL_SECONDS)

    async with app:
        target = f"127.0.0.1:{app.bound_port}"
        print(f"[server] listening on {target}")

        async with grpc.aio.insecure_channel(target) as channel:
            stub = health_pb2_grpc.HealthStub(channel)

            print("\n--- dependency healthy ---")
            response = await stub.Check(health_pb2.HealthCheckRequest(service=""))
            status_name = health_pb2.HealthCheckResponse.ServingStatus.Name(response.status)
            print(f"[client] Check -> {status_name}")

            checker.healthy = False
            print(f"\n[checker] dependency flipped unhealthy; sleeping {CACHE_TTL_SECONDS * 2:.1f}s past cache_ttl")
            await asyncio.sleep(CACHE_TTL_SECONDS * 2)

            print("\n--- dependency unhealthy ---")
            response = await stub.Check(health_pb2.HealthCheckRequest(service=""))
            status_name = health_pb2.HealthCheckResponse.ServingStatus.Name(response.status)
            print(f"[client] Check -> {status_name}")


if __name__ == "__main__":
    asyncio.run(main())
