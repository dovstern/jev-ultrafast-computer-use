"""The plugin launcher requires an explicitly supplied Jev key."""

import pytest

from scripts import run_mcp


def test_launcher_accepts_process_environment_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    run_mcp.require_typesafe_key()


def test_launcher_does_not_read_zshrc_when_key_is_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".zshrc").write_text('export TYPESAFE_API_KEY="test-key"\n')

    with pytest.raises(RuntimeError, match="MCP process environment"):
        run_mcp.require_typesafe_key()
