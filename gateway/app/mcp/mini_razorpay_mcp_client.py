"""
Gateway-side MCP client — spawns mini-razorpay-mcp/server.py as a persistent
stdio subprocess and reuses the session across calls, mirroring Sugam AI OS's
playground_mcp_client.py: one asyncio.Lock serializing calls (stdio
pipelining across concurrent calls isn't something this SDK guarantees),
a hard wall-clock timeout, and clean shutdown on app exit.
"""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import settings

logger = logging.getLogger(__name__)


class MiniRazorpayMCPError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class _PersistentMCPSession:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def _start(self) -> ClientSession:
        params = StdioServerParameters(
            command=settings.mcp_server_python_path,
            args=[settings.mcp_server_path],
            env=os.environ.copy(),
        )
        stack = AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._stack = stack
        self._session = session
        return session

    async def _reset(self) -> None:
        if self._stack:
            try:
                await self._stack.aclose()
            except Exception:
                pass
        self._stack = None
        self._session = None

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        async with self._lock:
            if self._session is None:
                await self._start()

            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(tool_name, arguments),
                    timeout=settings.mcp_call_timeout_seconds,
                )
            except Exception:
                logger.exception("MCP call to %s failed — resetting session and retrying once", tool_name)
                await self._reset()
                await self._start()
                result = await asyncio.wait_for(
                    self._session.call_tool(tool_name, arguments),
                    timeout=settings.mcp_call_timeout_seconds,
                )

            if not result.content:
                raise MiniRazorpayMCPError(f"Tool {tool_name} returned an empty result")

            text = result.content[0].text
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, AttributeError) as e:
                raise MiniRazorpayMCPError(f"Tool {tool_name} returned non-JSON content: {text[:200]}") from e

            return parsed

    async def close(self) -> None:
        async with self._lock:
            await self._reset()


_persistent_session = _PersistentMCPSession()


async def call_tool(tool_name: str, arguments: dict, access_token: str | None) -> dict:
    full_arguments = {**arguments, "access_token": access_token}
    return await _persistent_session.call_tool(tool_name, full_arguments)


async def list_tools() -> list:
    async with _persistent_session._lock:
        if _persistent_session._session is None:
            await _persistent_session._start()
        result = await _persistent_session._session.list_tools()
        return result.tools


async def close_persistent_mcp_session() -> None:
    await _persistent_session.close()
