#!/bin/sh
set -eu
plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# Codex starts MCP servers with a minimal environment, so a key exported in a shell never
# arrives. This user-owned file reaches every client; an exported key still wins.
key_file="${XDG_CONFIG_HOME:-$HOME/.config}/jev/env"
if [ -r "$key_file" ]; then
  exported_key="${TYPESAFE_API_KEY:-}"
  set -a
  . "$key_file"
  set +a
  [ -z "$exported_key" ] || export TYPESAFE_API_KEY="$exported_key"
fi
python="$plugin_root/.venv/bin/python"
# A first sync killed by the host's startup timeout leaves a .venv with no packages, so probe
# the imports instead of trusting the file. `uv run` finishes the interrupted install.
if [ -x "$python" ] && "$python" -c "import jev_ultrafast_computer_use.mcp_server" >/dev/null 2>&1; then
  exec "$python" "$plugin_root/scripts/run_mcp.py"
fi
runtime_root="${PLUGIN_DATA:-${XDG_CACHE_HOME:-$HOME/.cache}/jev-ultrafast-computer-use}"
export UV_CACHE_DIR="$runtime_root/uv"
exec uv run --project "$plugin_root" --locked python "$plugin_root/scripts/run_mcp.py"
