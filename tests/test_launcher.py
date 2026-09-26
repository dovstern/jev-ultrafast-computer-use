"""The shared plugin launcher loads the Jev key without writing it to MCP stdout."""

from subprocess import CompletedProcess

from scripts import run_mcp


def test_launcher_reads_interactive_zsh_key_without_forwarding_startup_output(monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    monkeypatch.setattr(
        run_mcp.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess(
            args=[],
            returncode=0,
            stdout="pyenv warning\n__JEV_KEY_BEGIN__\ntest-key\n__JEV_KEY_END__\n",
            stderr="",
        ),
    )

    run_mcp.load_environment()

    assert run_mcp.os.environ["TYPESAFE_API_KEY"] == "test-key"
    assert "TEXT_MODEL_API_KEY" not in run_mcp.os.environ
    assert capsys.readouterr().out == ""
