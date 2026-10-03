"""Start the MCP server with a key from the host environment or ~/.config/jev/env (see setup_key.sh)."""

import os
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError


def require_typesafe_key() -> None:
    """A ToolError reaches the agent; any other exception shows it only a generic failure."""
    if not os.environ.get("TYPESAFE_API_KEY"):
        setup = Path(__file__).with_name("setup_key.sh")
        raise ToolError(
            f"TYPESAFE_API_KEY is not set in the MCP process environment. Ask the user to run `{setup}` "
            "in a terminal, then reconnect the jev-browser server."
        )


def start_agent(url: str, goal: str):
    require_typesafe_key()
    from jev_ultrafast import Agent

    return Agent(url, goal)


def main() -> None:
    from jev_ultrafast_computer_use.handoff import BrowseSessions
    from jev_ultrafast_computer_use.mcp_server import create_server

    create_server(BrowseSessions(agent_factory=start_agent)).run(transport="stdio")


if __name__ == "__main__":
    main()
