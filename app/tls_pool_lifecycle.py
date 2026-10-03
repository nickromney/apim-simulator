"""Retire credential-specific connection pools after their active streams finish."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx


class _LeasedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, transport: LeasedTLSTransport):
        self.stream = stream
        self.transport = transport
        self.closed = False

    async def __aiter__(self):
        async for chunk in self.stream:
            yield chunk

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        try:
            await self.stream.aclose()
        finally:
            self.transport.release()


class LeasedTLSTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncBaseTransport):
        self.transport = transport
        self.active = 0
        self.retirement: Callable[[], Any] | None = None
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.active += 1
        try:
            response = await self.transport.handle_async_request(request)
        except BaseException:
            self.release()
            raise
        response.stream = _LeasedStream(response.stream, self)
        return response

    def release(self):
        self.active -= 1
        self._retire_if_idle()

    def retire(self, callback: Callable[[], Any]):
        self.retirement = callback
        self._retire_if_idle()

    def _retire_if_idle(self):
        if self.retirement is not None and self.active == 0:
            callback, self.retirement = self.retirement, None
            callback()

    async def aclose(self):
        if not self.closed:
            self.closed = True
            await self.transport.aclose()
