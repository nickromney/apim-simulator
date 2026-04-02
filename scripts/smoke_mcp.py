#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import traceback
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def run(url: str, subscription_key: str) -> None:
    async with streamablehttp_client(
        url,
        headers={"Ocp-Apim-Subscription-Key": subscription_key},
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            if "add_numbers" not in names:
                raise RuntimeError(f"add_numbers tool missing from {names}")

            result = await session.call_tool("add_numbers", {"a": 2, "b": 3})
            text_values: list[str] = []
            for item in result.content:
                text = getattr(item, "text", None)
                if text is not None:
                    text_values.append(text)

            if not text_values or "5" not in " ".join(text_values):
                raise RuntimeError(f"unexpected add_numbers result: {result}")

            print("MCP smoke passed")
            print(f"- server: {init.serverInfo.name}")
            print(f"- tools: {', '.join(names)}")
            print(f"- add_numbers: {' | '.join(text_values)}")


def main() -> int:
    url = "http://localhost:8000/mcp"
    subscription_key = "mcp-demo-key"
    try:
        asyncio.run(run(url=url, subscription_key=subscription_key))
        return 0
    except Exception as exc:
        sys.stderr.write(f"MCP smoke failed: {exc}\n")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
