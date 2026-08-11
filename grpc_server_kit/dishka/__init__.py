"""Dishka dependency-injection integration for the gRPC server (``[dishka]`` extra).

A thin, curated re-export of Dishka's official ``grpcio`` integration. The shared,
transport-agnostic helpers (``inject``, ``FromDishka``, ``GrpcioProvider``,
``from_context``) live here; the async server interceptor is
:class:`grpc_server_kit.aio.dishka.DishkaAioInterceptor`.

Example:
    from dishka import make_async_container
    from grpc_server_kit.dishka import FromDishka, inject
    from grpc_server_kit.aio.dishka import DishkaAioInterceptor

    container = make_async_container(MyProvider())
    server = (
        AsyncGrpcServerBuilder(settings)
        .with_interceptors([DishkaAioInterceptor(container)])
        .with_servicers(register)
        .build()
    )

    class MyServicer(...):
        @inject
        async def MyMethod(self, request, context, service: FromDishka[MyService]):
            ...
"""

from dishka import from_context
from dishka.integrations.grpcio import FromDishka, GrpcioProvider, inject

from grpc_server_kit.dishka.settings import GrpcServerSettingsProvider

__all__ = [
    "FromDishka",
    "GrpcServerSettingsProvider",
    "GrpcioProvider",
    "from_context",
    "inject",
]
