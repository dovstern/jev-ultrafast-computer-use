#!/bin/sh
set -eu
plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec uv run --project "$plugin_root" --locked python "$plugin_root/scripts/run_mcp.py"
