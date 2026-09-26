"""Start the MCP server with a key supplied by the host process."""

import os


def require_typesafe_key() -> None:
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise RuntimeError("Set TYPESAFE_API_KEY in the MCP process environment")


def main() -> None:
    require_typesafe_key()
    from jev_ultrafast_computer_use.mcp_server import main as serve

    serve()


if __name__ == "__main__":
    main()
