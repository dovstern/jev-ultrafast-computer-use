"""Codex can call the browse lifecycle over MCP."""

import asyncio

from mcp import Client

from jev_ultrafast.handoff import BrowseSessions
from jev_ultrafast.mcp_server import create_server
from tests.test_handoff import agent_with_decisions, decision


def test_mcp_exposes_bounded_browsing_and_takeover():
    agent = agent_with_decisions(decision("Open"), decision("BLOCKED"))
    server = create_server(BrowseSessions(agent_factory=lambda *_: agent))

    async def exercise():
        async with Client(server) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "start_browse",
                "advance_browse",
                "browse_status",
                "handoff_browse",
                "close_browse",
                "submit_browse_text",
            }
            started = await client.call_tool(
                "start_browse", {"url": "https://example.org/", "goal": "Find the article"}
            )
            run_id = started.structured_content["run_id"]
            progress = await client.call_tool("advance_browse", {"run_id": run_id, "max_steps": 1})
            assert progress.structured_content["status"] == "ready"
            assert progress.structured_content["steps"] == 1
            takeover = await client.call_tool("advance_browse", {"run_id": run_id})
            assert takeover.structured_content["status"] == "needs_gpt"
            assert takeover.structured_content["url"] == "https://example.org/"

    asyncio.run(exercise())
