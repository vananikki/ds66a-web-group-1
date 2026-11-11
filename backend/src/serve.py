import asyncio
import socket

import uvicorn

from src.app import app


async def serve() -> None:
    listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    listener.bind(("::", 8000))
    listener.listen(socket.SOMAXCONN)
    listener.setblocking(False)

    try:
        server = uvicorn.Server(
            uvicorn.Config(app, host="::", port=8000, access_log=False)
        )
        await server.serve(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    asyncio.run(serve())
