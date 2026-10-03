"""The plugin launcher requires an explicitly supplied Jev key."""

import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from scripts import run_mcp


def test_launcher_accepts_process_environment_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    run_mcp.require_typesafe_key()


def test_launcher_does_not_read_zshrc_when_key_is_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".zshrc").write_text('export TYPESAFE_API_KEY="test-key"\n')

    with pytest.raises(ToolError, match="MCP process environment"):
        run_mcp.require_typesafe_key()


def _plugin_with_stub_venv(tmp_path, venv_imports_ok):
    """Copy the launcher beside a stub `.venv` python and a stub `uv` that logs its use."""
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True)
    (root / ".venv/bin").mkdir(parents=True)
    (tmp_path / "bin").mkdir()
    launcher = root / "scripts/launch_plugin_mcp.sh"
    launcher.write_text((Path(__file__).parents[1] / "scripts/launch_plugin_mcp.sh").read_text())
    launcher.chmod(0o755)
    venv_python = root / ".venv/bin/python"
    venv_python.write_text(
        f'#!/bin/sh\n[ "$1" = -c ] && {{ echo banner; exit {0 if venv_imports_ok else 1}; }}\n'
        "echo venv-python ${TYPESAFE_API_KEY:-} ${TEXT_MODEL:-}\n"
    )
    venv_python.chmod(0o755)
    uv = tmp_path / "bin/uv"
    uv.write_text('#!/bin/sh\nprintf "uv-run %s\\n" "$UV_CACHE_DIR"\n')
    uv.chmod(0o755)
    return launcher


def _launch(launcher, tmp_path, **env):
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), **env}
    return subprocess.run([str(launcher)], env=env, capture_output=True, text=True, check=True).stdout.strip()


def test_launcher_uses_venv_when_its_packages_import(tmp_path):
    assert _launch(_plugin_with_stub_venv(tmp_path, venv_imports_ok=True), tmp_path) == "venv-python"


def test_launcher_repairs_venv_left_empty_by_an_interrupted_first_sync(tmp_path):
    launcher = _plugin_with_stub_venv(tmp_path, venv_imports_ok=False)

    assert _launch(launcher, tmp_path) == f"uv-run {tmp_path / 'home/.cache/jev-ultrafast-computer-use/uv'}"


def test_launcher_uses_plugin_data_for_its_runtime_cache(tmp_path):
    launcher = _plugin_with_stub_venv(tmp_path, venv_imports_ok=False)
    plugin_data = tmp_path / "plugin-data"

    assert _launch(launcher, tmp_path, PLUGIN_DATA=str(plugin_data)) == f"uv-run {plugin_data / 'uv'}"


def _write_key_file(tmp_path, text):
    key_file = tmp_path / "home/.config/jev/env"
    key_file.parent.mkdir(parents=True)
    key_file.write_text(text)


def test_launcher_reads_the_key_file_when_the_host_strips_the_environment(tmp_path):
    _write_key_file(tmp_path, "TYPESAFE_API_KEY=from-file\n")
    launcher = _plugin_with_stub_venv(tmp_path, venv_imports_ok=True)
    assert _launch(launcher, tmp_path) == "venv-python from-file"


def test_exported_key_wins_over_the_key_file(tmp_path):
    _write_key_file(tmp_path, "TYPESAFE_API_KEY=from-file\n")
    launcher = _plugin_with_stub_venv(tmp_path, venv_imports_ok=True)
    assert _launch(launcher, tmp_path, TYPESAFE_API_KEY="exported") == "venv-python exported"


SETUP = Path(__file__).parents[1] / "scripts/setup_key.sh"


def _setup(tmp_path, stdin="", **env):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), **env}
    return subprocess.run(["sh", str(SETUP)], env=env, input=stdin, capture_output=True, text=True)


@pytest.mark.parametrize("how", [{"TYPESAFE_API_KEY": "key+/=1"}, {"stdin": "key+/=1\n"}])
def test_setup_saves_the_key_privately_in_a_file_the_launcher_can_source(tmp_path, how):
    stdin = how.pop("stdin", "")
    result = _setup(tmp_path, stdin, **how)

    key_file = tmp_path / ".config/jev/env"
    assert result.returncode == 0
    assert key_file.stat().st_mode & 0o777 == 0o600
    assert key_file.parent.stat().st_mode & 0o777 == 0o700
    sourced = subprocess.run(
        ["sh", "-c", f'. "{key_file}"; printf %s "$TYPESAFE_API_KEY"'], capture_output=True, text=True
    )
    assert sourced.stdout == "key+/=1"


@pytest.mark.parametrize("bad_key", ["", "has space", "it's"])
def test_setup_refuses_a_key_the_launcher_could_not_source_safely(tmp_path, bad_key):
    result = _setup(tmp_path, stdin=f"{bad_key}\n")

    assert result.returncode != 0
    assert not (tmp_path / ".config/jev/env").exists()


def test_start_without_a_key_names_the_setup_script(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    with pytest.raises(ToolError, match=r"setup_key\.sh"):
        run_mcp.start_agent("https://example.com", "read the page")


def test_exported_key_wins_but_the_file_still_supplies_other_settings(tmp_path):
    _write_key_file(tmp_path, "TYPESAFE_API_KEY=from-file\nTEXT_MODEL=mercury\n")
    launcher = _plugin_with_stub_venv(tmp_path, venv_imports_ok=True)
    assert _launch(launcher, tmp_path, TYPESAFE_API_KEY="exported") == "venv-python exported mercury"


def test_setup_tightens_the_mode_of_an_existing_key_file(tmp_path):
    key_file = tmp_path / ".config/jev/env"
    key_file.parent.mkdir(parents=True)
    key_file.write_text("TYPESAFE_API_KEY=old\n")
    key_file.chmod(0o644)

    assert _setup(tmp_path, TYPESAFE_API_KEY="new").returncode == 0
    assert key_file.stat().st_mode & 0o777 == 0o600


def test_setup_keeps_other_settings_when_rotating_the_key(tmp_path):
    key_file = tmp_path / ".config/jev/env"
    key_file.parent.mkdir(parents=True)
    key_file.write_text("TEXT_MODEL_API_KEY=abc\nTYPESAFE_API_KEY=old\n")

    assert _setup(tmp_path, TYPESAFE_API_KEY="new").returncode == 0
    assert key_file.read_text() == "TEXT_MODEL_API_KEY=abc\nTYPESAFE_API_KEY='new'\n"
