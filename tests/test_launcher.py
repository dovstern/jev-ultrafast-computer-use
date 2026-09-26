"""The shared plugin launcher reads a literal Jev key without running shell startup."""

from scripts import run_mcp


def test_launcher_reads_literal_zshrc_key_without_running_shell(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text('echo should-not-run\nexport TYPESAFE_API_KEY="test-key"\n')

    run_mcp.load_environment(zshrc)

    assert run_mcp.os.environ["TYPESAFE_API_KEY"] == "test-key"
    assert "TEXT_MODEL_API_KEY" not in run_mcp.os.environ
    assert capsys.readouterr().out == ""


def test_launcher_rejects_dynamic_shell_expressions(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text('export TYPESAFE_API_KEY="$(cat /tmp/secret)"\n')

    try:
        run_mcp.load_environment(zshrc)
    except RuntimeError as error:
        assert "TYPESAFE_API_KEY" in str(error)
    else:
        raise AssertionError("Dynamic shell expression was accepted")
