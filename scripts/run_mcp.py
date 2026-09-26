"""Start the MCP server with the Jev key from the environment or zsh config."""

import os
import re
import shlex
from pathlib import Path

KEY_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?TYPESAFE_API_KEY=(.*)$")


def load_environment(zshrc: Path | None = None) -> None:
    if os.environ.get("TYPESAFE_API_KEY"):
        return

    path = zshrc or Path.home() / ".zshrc"
    key = None
    if path.is_file():
        for line in path.read_text().splitlines():
            match = KEY_ASSIGNMENT.match(line)
            if not match:
                continue
            value = match.group(1).strip()
            if "$" in value or "`" in value:
                key = None
                continue
            try:
                tokens = shlex.split(value, comments=True)
            except ValueError:
                key = None
                continue
            key = tokens[0] if len(tokens) == 1 else None

    if not key:
        raise RuntimeError("Set TYPESAFE_API_KEY in the process environment or as a literal export in ~/.zshrc")
    os.environ["TYPESAFE_API_KEY"] = key


def main() -> None:
    load_environment()
    from jev_ultrafast_computer_use.mcp_server import main as serve

    serve()


if __name__ == "__main__":
    main()
