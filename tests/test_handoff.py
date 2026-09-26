"""The supervisor can inspect Jev progress and take over its Chrome tab."""

from unittest.mock import Mock

import pytest

from jev_ultrafast.handoff import BrowseSessions


def agent_with_decisions(*decisions):
    browser = Mock(target="chrome-target-1")
    browser.fresh.return_value = True
    page = {
        "url": "https://example.org/",
        "title": "Example",
        "text": "Example page",
        "fingerprint": "f1",
        "actions": [{"id": "Search", "kind": "fill", "label": "Search", "role": "textbox", "value": ""}],
    }
    browser.observe.return_value = page
    agent = Mock(browser=browser)
    agent.pending_text = None
    agent.screenshots = False
    agent.state = {
        "page": page,
        "goal": "Find the article",
        "history": [],
        "status": "ready",
        "decision": None,
        "elapsed_ms": 0,
    }

    def command(name, body=None):
        if name == "predict":
            agent.state["decision"] = decisions[len(agent.state["history"])]
            agent.state["status"] = "predicted"
        elif name == "act":
            selected = agent.state["decision"]["choice"]
            agent.state["decision"] = None
            if selected in {"DONE", "BLOCKED"}:
                agent.state["status"] = selected.lower()
            else:
                agent.state["history"].append({"action": selected, "page_changed": True})
                agent.state["status"] = "ready"
        return agent.state

    agent.command.side_effect = command
    return agent


def decision(choice, confidence=0.95, target_confidence=None, operation=None):
    return {
        "choice": choice,
        "operation": operation or ("CLICK" if choice not in {"DONE", "BLOCKED"} else choice),
        "confidence": confidence,
        "target_confidence": target_confidence,
    }


def test_advance_runs_a_bounded_batch_and_reports_progress():
    agent = agent_with_decisions(decision("Open"), decision("Next"), decision("DONE"))
    sessions = BrowseSessions(agent_factory=lambda *_: agent)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]

    first = sessions.advance(run_id, max_steps=2)
    assert first["status"] == "ready"
    assert first["steps"] == 2
    assert first["last_action"] == "Next"
    assert first["tab_target_id"] == "chrome-target-1"
    assert sessions.status(run_id) == first

    final = sessions.advance(run_id, max_steps=2)
    assert final["status"] == "needs_verification"
    assert final["steps"] == 2
    agent.browser.handoff.assert_called_once_with(activate=True)


def test_low_confidence_escalates_before_any_action():
    agent = agent_with_decisions(decision("Open", confidence=0.4))
    sessions = BrowseSessions(agent_factory=lambda *_: agent)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]

    result = sessions.advance(run_id, max_steps=3, min_confidence=0.6)
    assert result["status"] == "needs_gpt"
    assert "confidence" in result["reason"]
    assert result["steps"] == 0
    agent.browser.handoff.assert_called_once_with(activate=True)
    assert agent.command.call_count == 1


def test_blocked_escalates_and_keeps_tab_open():
    agent = agent_with_decisions(decision("BLOCKED"))
    sessions = BrowseSessions(agent_factory=lambda *_: agent)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]

    result = sessions.advance(run_id)
    assert result["status"] == "needs_gpt"
    assert result["url"] == "https://example.org/"
    agent.browser.handoff.assert_called_once_with(activate=True)
    agent.close.assert_not_called()


def test_supervisor_can_intervene_without_waiting_for_jev_to_stop():
    agent = agent_with_decisions(decision("Open"))
    sessions = BrowseSessions(agent_factory=lambda *_: agent)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]

    result = sessions.handoff(run_id)
    assert result["status"] == "needs_gpt"
    assert result["reason"] == "Supervisor requested takeover"
    agent.browser.handoff.assert_called_once_with(activate=True)


def test_codex_can_supply_field_text_and_jev_continues(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    agent = agent_with_decisions(decision("Search", operation="TYPE_TEXT"), decision("DONE"))
    agent.state["decision"] = None
    sessions = BrowseSessions(agent_factory=lambda *_: agent)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]

    request = sessions.advance(run_id)
    assert request["status"] == "needs_text"
    assert request["text_request"]["field_label"] == "Search"
    assert request["steps"] == 0
    agent.browser.handoff.assert_not_called()

    result = sessions.submit_text(run_id, "article name")
    assert result["status"] == "ready"
    assert result["steps"] == 1
    assert agent.pending_text is None or agent.pending_text[1] == "article name"
    assert sessions.advance(run_id)["status"] == "needs_verification"


def test_browser_handoff_detaches_without_closing_tab(monkeypatch):
    from jev_ultrafast import browser

    cdp = Mock()
    monkeypatch.setattr(browser, "cdp", cdp)
    tab = browser.Browser.__new__(browser.Browser)
    tab.target = "chrome-target-1"
    tab.session = "session-1"

    tab.handoff(activate=True)

    assert cdp.call_args_list[0].args == ("Target.activateTarget",)
    assert cdp.call_args_list[1].args == ("Target.detachFromTarget",)
    assert tab.target is None
    assert not any(call.args[0] == "Target.closeTarget" for call in cdp.call_args_list)


@pytest.mark.parametrize(
    "url", ["file:///etc/hosts", "http://localhost:8080", "https://10.0.0.1", "https://intranet.local"]
)
def test_start_rejects_non_public_urls_before_opening_browser(url):
    factory = Mock()
    sessions = BrowseSessions(agent_factory=factory)

    with pytest.raises(ValueError, match="public HTTPS"):
        sessions.start(url, "Read this page")

    factory.assert_not_called()
