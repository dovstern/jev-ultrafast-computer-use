"""Both agent hosts discover one shared browser workflow and local MCP server."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def test_claude_and_codex_plugin_configs_share_one_skill_and_server():
    portable = read_json("plugin.json")
    codex = read_json(".codex-plugin/plugin.json")
    claude = read_json(".claude-plugin/plugin.json")
    codex_mcp = read_json("mcp.json")["mcpServers"]
    claude_mcp = read_json(".mcp.json")["mcpServers"]

    assert portable["name"] == codex["name"] == claude["name"] == "jev-ultrafast-computer-use"
    assert [path.name for path in (ROOT / "skills").iterdir()] == ["jev-browser"]
    assert (ROOT / "skills/jev-browser/SKILL.md").is_file()
    assert codex_mcp.keys() == claude_mcp.keys() == {"jev-browser"}
    assert codex_mcp["jev-browser"]["command"] == "./scripts/launch_plugin_mcp.sh"
    assert claude_mcp["jev-browser"]["command"] == "${CLAUDE_PLUGIN_ROOT}/scripts/launch_plugin_mcp.sh"


def test_distribution_uses_project_name_and_shared_mcp_entrypoint():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert project["name"] == "jev-ultrafast-computer-use"
    assert project["scripts"]["jev-computer-use-mcp"] == "jev_ultrafast.mcp_server:main"
