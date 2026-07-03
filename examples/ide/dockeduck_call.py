#!/usr/bin/env python3
"""Pure IDE call — run a DockeDuck spec through the MCP server with NO cloud LLM.

Point it at a task spec (YAML/JSON with `conditions:` or `tests:`) and it calls the local
`write_and_fix` tool over the running server's SSE endpoint, printing the generated code.
Perfect as a PyCharm / VS Code *Run Configuration* or a terminal command.

Setup once:   pip install mcp
Run the server first:   make start   (vLLM)   |   make up   (Ollama)

Usage:
    python dockeduck_call.py path/to/spec.yaml
    python dockeduck_call.py path/to/spec.yaml --tool recommend_model
    DOCKEDUCK_URL=http://localhost:8000/sse python dockeduck_call.py spec.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
except ImportError:
    sys.exit("This client needs the MCP SDK.  Install it with:  pip install mcp")

URL = os.getenv("DOCKEDUCK_URL", "http://localhost:8000/sse")


async def _run(tool: str, args: dict) -> None:
    async with sse_client(URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            if tool not in tools:
                sys.exit(f"Server has no tool '{tool}'. Available: {', '.join(sorted(tools))}")
            result = await session.call_tool(tool, args)
            for chunk in result.content:
                print(getattr(chunk, "text", chunk))


def main() -> None:
    p = argparse.ArgumentParser(description="Call a DockeDuck MCP tool with no cloud LLM.")
    p.add_argument("spec", nargs="?", help="path to a task spec (.yaml/.json) for write_and_fix")
    p.add_argument("--tool", default="write_and_fix",
                   help="write_and_fix (default) | validate_output_file | recommend_model | "
                        "recommend_context_window")
    p.add_argument("--code", help="path to a code file (for validate_output_file)")
    p.add_argument("--prefer", default="quality", help="recommend_model: quality|context|speed")
    args = p.parse_args()

    if args.tool == "recommend_model":
        call_args = {"prefer": args.prefer}
    elif args.tool == "recommend_context_window":
        call_args = {}
    else:
        if not args.spec:
            p.error(f"{args.tool} needs a spec file")
        call_args = {"spec": open(args.spec).read()}
        if args.tool == "validate_output_file":
            if not args.code:
                p.error("validate_output_file needs --code <file>")
            call_args["code"] = open(args.code).read()

    asyncio.run(_run(args.tool, call_args))


if __name__ == "__main__":
    main()
