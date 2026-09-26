"""Start the MCP server with the Jev key from the user's interactive zsh setup."""

import os
import subprocess


def load_environment() -> None:
    if os.environ.get("TYPESAFE_API_KEY"):
        return
    command = "printf '\n__JEV_KEY_BEGIN__\n%s\n__JEV_KEY_END__\n' \"$TYPESAFE_API_KEY\""
    result = subprocess.run(["/bin/zsh", "-ic", command], capture_output=True, text=True, timeout=15, check=True)
    start = result.stdout.rfind("__JEV_KEY_BEGIN__\n")
    end = result.stdout.find("\n__JEV_KEY_END__", start)
    if start < 0 or end < 0:
        raise RuntimeError("Could not read TYPESAFE_API_KEY from interactive zsh")
    key = result.stdout[start + len("__JEV_KEY_BEGIN__\n") : end]
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY is not set in interactive zsh")
    os.environ["TYPESAFE_API_KEY"] = key


def main() -> None:
    load_environment()
    from jev_ultrafast.mcp_server import main as serve

    serve()


if __name__ == "__main__":
    main()
