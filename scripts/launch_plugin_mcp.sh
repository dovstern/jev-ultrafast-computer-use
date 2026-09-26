#!/bin/sh
set -eu
plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -x "$plugin_root/.venv/bin/python" ]; then
  exec "$plugin_root/.venv/bin/python" "$plugin_root/scripts/run_mcp.py"
fi
export UV_CACHE_DIR="$plugin_root/.uv-cache"
exec uv run --project "$plugin_root" --locked python "$plugin_root/scripts/run_mcp.py"
