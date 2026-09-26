"""The supervisor can inspect Jev progress and take over its Chrome tab."""

from unittest.mock import Mock

import pytest
from jev_ultrafast.browser import StalePage

from jev_ultrafast_computer_use.handoff import BrowseSessions, release_browser


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
    release = Mock()
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=release)
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
    release.assert_called_once_with(agent.browser)


def test_low_confidence_escalates_before_any_action():
    agent = agent_with_decisions(decision("Open", confidence=0.4))
    release = Mock()
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=release)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]

    result = sessions.advance(run_id, max_steps=3, min_confidence=0.6)
    assert result["status"] == "needs_gpt"
    assert "confidence" in result["reason"]
    assert result["steps"] == 0
    release.assert_called_once_with(agent.browser)
    assert agent.command.call_count == 1


def test_blocked_escalates_and_keeps_tab_open():
    agent = agent_with_decisions(decision("BLOCKED"))
    release = Mock()
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=release)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]

    result = sessions.advance(run_id)
    assert result["status"] == "needs_gpt"
    assert result["url"] == "https://example.org/"
    release.assert_called_once_with(agent.browser)
    agent.close.assert_not_called()


def test_supervisor_can_intervene_without_waiting_for_jev_to_stop():
    agent = agent_with_decisions(decision("Open"))
    release = Mock()
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=release)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]

    result = sessions.handoff(run_id)
    assert result["status"] == "needs_gpt"
    assert result["reason"] == "Supervisor requested takeover"
    release.assert_called_once_with(agent.browser)


def test_codex_can_supply_field_text_and_jev_continues(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    agent = agent_with_decisions(decision("Search", operation="TYPE_TEXT"), decision("DONE"))
    agent.state["decision"] = None
    release = Mock()
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=release)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]

    request = sessions.advance(run_id)
    assert request["status"] == "needs_text", request["reason"]
    assert request["text_request"]["field_label"] == "Search"
    assert request["steps"] == 0
    release.assert_not_called()

    result = sessions.submit_text(run_id, "article name")
    assert result["status"] == "ready"
    assert result["steps"] == 1
    assert agent.pending_text is None or agent.pending_text[1] == "article name"
    assert sessions.advance(run_id)["status"] == "needs_verification"


def test_browser_handoff_detaches_without_closing_tab(monkeypatch):
    from jev_ultrafast import browser
    from jev_ultrafast_computer_use import handoff

    cdp = Mock()
    monkeypatch.setattr(handoff, "cdp", cdp)
    tab = browser.Browser.__new__(browser.Browser)
    tab.target = "chrome-target-1"
    tab.session = "session-1"

    release_browser(tab)

    assert cdp.call_args_list[0].args == ("Target.activateTarget",)
    assert cdp.call_args_list[1].args == ("Target.detachFromTarget",)
    assert tab.target is None
    assert not any(call.args[0] == "Target.closeTarget" for call in cdp.call_args_list)


def test_upstream_agent_uses_supervisor_text_without_text_model_key(monkeypatch):
    import jev_ultrafast.agent as upstream

    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    page = {
        "url": "https://example.org/",
        "title": "Example",
        "text": "Search for an article",
        "fingerprint": "f1",
        "actions": [{"id": "Search", "node": 1, "kind": "fill", "label": "Search", "role": "textbox", "value": ""}],
    }
    browser = Mock(target="chrome-target-1", session="session-1")
    browser.observe.return_value = page
    browser.fresh.return_value = True
    monkeypatch.setattr(upstream, "Browser", lambda _url: browser)
    monkeypatch.setattr(
        upstream,
        "choose",
        lambda *_: {
            "choice": "Search",
            "operation": "TYPE_TEXT",
            "confidence": 0.95,
            "target_confidence": 0.95,
            "probabilities": {"Search": 1.0},
            "latency_ms": 0,
            "target": "Search",
            "usage": {},
        },
    )
    text_model = Mock(side_effect=AssertionError("A text model call was made"))
    monkeypatch.setattr(upstream, "field_text", text_model)

    sessions = BrowseSessions(agent_factory=upstream.Agent, release_tab=Mock())
    run_id = sessions.start("https://example.org/", "Search for a paper")["run_id"]
    request = sessions.advance(run_id, max_steps=1)
    assert request["status"] == "needs_text", request["reason"]
    assert request["text_request"]["field_label"] == "Search"

    result = sessions.submit_text(run_id, "paper title")
    assert result["status"] == "ready"
    browser.act.assert_called_once_with(page["actions"][0], page, text="paper title")
    text_model.assert_not_called()


def test_supervisor_text_survives_a_stale_page_retry(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    agent = agent_with_decisions(decision("Search", operation="TYPE_TEXT"))
    command = agent.command.side_effect
    stale_once = True

    def command_with_stale_retry(name, body=None):
        nonlocal stale_once
        if name == "act" and stale_once:
            stale_once = False
            raise StalePage("Page changed before input")
        return command(name, body)

    agent.command.side_effect = command_with_stale_retry
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=Mock())
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]
    assert sessions.advance(run_id, max_steps=1)["status"] == "needs_text"
    first = sessions.submit_text(run_id, "article name")
    assert first["status"] == "ready"
    assert first["steps"] == 0

    retried = sessions.advance(run_id, max_steps=1)
    assert retried["status"] == "ready"
    assert retried["steps"] == 1


@pytest.mark.parametrize(
    "url", ["file:///etc/hosts", "http://localhost:8080", "https://10.0.0.1", "https://intranet.local"]
)
def test_start_rejects_non_public_urls_before_opening_browser(url):
    factory = Mock()
    sessions = BrowseSessions(agent_factory=factory)

    with pytest.raises(ValueError, match="public HTTPS"):
        sessions.start(url, "Read this page")

    factory.assert_not_called()
