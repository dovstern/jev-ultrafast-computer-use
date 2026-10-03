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
        "actions": [{"id": "Search", "node": 1, "kind": "fill", "label": "Search", "role": "textbox", "value": ""}],
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
    assert result["status"] == "needs_reasoning_llm"
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
    assert result["status"] == "needs_reasoning_llm"
    assert result["url"] == "https://example.org/"
    release.assert_called_once_with(agent.browser)
    agent.close.assert_not_called()


def test_supervisor_can_intervene_without_waiting_for_jev_to_stop():
    agent = agent_with_decisions(decision("Open"))
    release = Mock()
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=release)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]

    result = sessions.handoff(run_id)
    assert result["status"] == "needs_reasoning_llm"
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


def test_no_op_click_is_not_offered_again_until_the_page_changes(monkeypatch):
    import jev_ultrafast.agent as upstream

    page = {
        "url": "https://example.org/",
        "title": "Calendar",
        "text": "Choose a date",
        "fingerprint": "f1",
        "actions": [
            {"id": "open", "node": 1, "kind": "click", "label": "Open Return", "role": "textbox", "value": ""},
            {"id": "day", "node": 2, "kind": "click", "label": "April 30", "role": "button", "value": ""},
        ],
    }
    browser = Mock(target="chrome-target-1")
    browser.observe.side_effect = lambda **_: dict(page, actions=[dict(a) for a in page["actions"]])
    browser.fresh.return_value = True
    monkeypatch.setattr(upstream, "Browser", lambda _: browser)
    offered = []

    def choose(observation, *_):
        offered.append([a["id"] for a in observation["actions"]])
        selected = observation["actions"][0]["id"]
        return dict(
            choice=selected,
            operation="CLICK",
            target=selected,
            confidence=0.95,
            probabilities={selected: 1.0},
            latency_ms=0,
            usage={},
        )

    monkeypatch.setattr(upstream, "choose", choose)
    sessions = BrowseSessions(agent_factory=upstream.Agent, release_tab=Mock())
    run_id = sessions.start("https://example.org/", "Choose the return date")["run_id"]
    assert sessions.advance(run_id, max_steps=2)["status"] == "ready"
    assert [call.args[0]["id"] for call in browser.act.call_args_list] == ["open", "day"]
    assert offered == [["open", "day"], ["day"]]

    page["fingerprint"] = "f2"
    page["text"] = "A different calendar"
    browser.act.side_effect = lambda *_args, **_kwargs: page.update(fingerprint="f3", text="Calendar opened")
    assert sessions.advance(run_id, max_steps=1)["status"] == "ready"
    assert offered[-1] == ["open", "day"]


def test_status_reports_control_values_missing_from_visible_text():
    agent = agent_with_decisions(decision("DONE"))
    agent.state["page"]["actions"][0].update(node=1, value="April 30", selected="true")
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=Mock())
    result = sessions.start("https://example.org/", "Read the selected date")
    assert result["controls"][0]["label"] == "Search"
    assert result["controls"][0]["value"] == "April 30"
    assert result["controls"][0]["selected"] == "true"


def test_stale_text_submission_reports_that_no_input_was_applied(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    agent = agent_with_decisions(decision("Search", operation="TYPE_TEXT"))
    original = agent.command.side_effect

    def command(name, body=None):
        if name == "act":
            raise StalePage("Page changed before input")
        return original(name, body)

    agent.command.side_effect = command
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=Mock())
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]
    assert sessions.advance(run_id)["status"] == "needs_text"
    result = sessions.submit_text(run_id, "article name")
    assert result["status"] == "ready"
    assert result["steps"] == 0
    assert "not applied" in result["reason"]


def test_uncertain_decision_can_pause_for_guidance_without_releasing_the_tab():
    agent = agent_with_decisions(decision("Search", confidence=0.4))
    release = Mock()
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=release)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]
    result = sessions.advance(run_id, min_confidence=0.6, allow_guidance=True)
    assert result["status"] == "needs_guidance"
    assert result["steps"] == 0
    assert result["controls"][0]["operations"] == ["TYPE_TEXT"]
    release.assert_not_called()


def test_supervisor_guidance_uses_an_observed_action_and_returns_to_jev(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    agent = agent_with_decisions(decision("Search", confidence=0.4), decision("DONE"))
    release = Mock()
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=release)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]
    assert sessions.advance(run_id, min_confidence=0.6, allow_guidance=True)["status"] == "needs_guidance"
    result = sessions.guide(run_id, operation="TYPE_TEXT", target="1")
    assert result["status"] == "needs_text"
    assert sessions.submit_text(run_id, "article name")["steps"] == 1
    assert sessions.advance(run_id)["status"] == "needs_verification"
    release.assert_called_once_with(agent.browser)


def test_guidance_rejects_an_unobserved_target_without_executing():
    agent = agent_with_decisions(decision("Search", confidence=0.4))
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=Mock())
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]
    sessions.advance(run_id, min_confidence=0.6, allow_guidance=True)
    with pytest.raises(ValueError, match="observed"):
        sessions.guide(run_id, operation="CLICK", target="999")
    assert agent.state["history"] == []


def test_guidance_rejects_a_changed_page_without_any_input():
    agent = agent_with_decisions(decision("Search", confidence=0.4))
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=Mock())
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]
    sessions.advance(run_id, min_confidence=0.6, allow_guidance=True)
    agent.browser.fresh.return_value = False
    result = sessions.guide(run_id, operation="TYPE_TEXT", target="1")
    assert result["status"] == "ready"
    assert "not applied" in result["reason"]
    assert agent.state["history"] == []


def test_guidance_recovers_navigation_during_freshness_check():
    agent = agent_with_decisions(decision("Search", confidence=0.4))
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=Mock())
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]
    sessions.advance(run_id, min_confidence=0.6, allow_guidance=True)
    agent.browser.fresh.side_effect = StalePage("Document navigating")
    result = sessions.guide(run_id, operation="TYPE_TEXT", target="1")
    assert result["status"] == "ready"
    assert "not applied" in result["reason"]
    assert agent.state["decision"] is None
    assert result["steps"] == 0


def test_guidance_releases_tab_when_refresh_fails():
    agent = agent_with_decisions(decision("Search", confidence=0.4))
    release = Mock()
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=release)
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]
    sessions.advance(run_id, min_confidence=0.6, allow_guidance=True)
    agent.browser.fresh.return_value = False
    agent.browser.observe.side_effect = RuntimeError("Tab unavailable")
    result = sessions.guide(run_id, operation="TYPE_TEXT", target="1")
    assert result["status"] == "needs_reasoning_llm"
    assert "observation failed" in result["reason"]
    release.assert_called_once_with(agent.browser)


@pytest.mark.parametrize("guided", [False, True])
def test_applied_input_is_preserved_when_post_action_observation_is_stale(monkeypatch, guided):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    agent = agent_with_decisions(decision("Search", confidence=0.4, operation="TYPE_TEXT"))
    original = agent.command.side_effect

    def command(name, body=None):
        result = original(name, body)
        if name == "act":
            raise StalePage("Document changed after input")
        return result

    agent.command.side_effect = command
    if guided:
        agent.state["page"]["actions"][0]["kind"] = "click"
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=Mock())
    run_id = sessions.start("https://example.org/", "Find the article")["run_id"]
    if guided:
        sessions.advance(run_id, min_confidence=0.6, allow_guidance=True)
        result = sessions.guide(run_id, operation="CLICK", target="1")
    else:
        sessions.advance(run_id)
        result = sessions.submit_text(run_id, "article name")
    assert result["status"] == "ready"
    assert result["steps"] == 1
    assert "was applied" in result["reason"]
    assert "not applied" not in result["reason"]


def prediction_session(monkeypatch):
    import jev_ultrafast.agent as upstream

    page = {
        "url": "https://example.org/",
        "title": "Calendar",
        "text": "Choose a date",
        "fingerprint": "f1",
        "actions": [
            {"id": "Search", "node": 1, "kind": "click", "label": "Open Return", "role": "button", "value": ""},
            {"id": "Next", "node": 2, "kind": "click", "label": "April 30", "role": "button", "value": ""},
        ],
    }
    browser = Mock(target="chrome-target-1")
    browser.observe.side_effect = lambda **_: dict(page, actions=[dict(a) for a in page["actions"]])
    browser.fresh.return_value = False
    monkeypatch.setattr(upstream, "Browser", lambda _: browser)

    def choose(observation, *_):
        selected = observation["actions"][0]["id"]
        return dict(
            choice=selected,
            operation="CLICK",
            target=selected,
            confidence=0.95,
            probabilities={selected: 1.0},
            latency_ms=0,
            usage={},
        )

    model = Mock(side_effect=choose)
    monkeypatch.setattr(upstream, "choose", model)
    release = Mock()
    sessions = BrowseSessions(agent_factory=upstream.Agent, release_tab=release)
    run_id = sessions.start("https://example.org/", "Choose a date")["run_id"]
    return sessions, run_id, browser, page, model, release


def test_prediction_refresh_preserves_no_op_exclusion(monkeypatch):
    sessions, run_id, browser, _, model, _ = prediction_session(monkeypatch)
    run = sessions.runs[run_id]
    run.ineffective_actions = {"Search"}
    run.ineffective_fingerprint = "f1"
    original_observe = browser.observe
    result = sessions.advance(run_id, max_steps=1)
    assert result["status"] == "ready"
    assert [action["id"] for action in model.call_args.args[0]["actions"]] == ["Next"]
    assert browser.act.call_args.args[0]["id"] == "Next"
    assert browser.observe is original_observe


def test_prediction_refresh_rejects_private_url_before_model_call(monkeypatch):
    sessions, run_id, browser, page, model, release = prediction_session(monkeypatch)
    page["url"] = "https://intranet.local/"
    original_observe = browser.observe
    result = sessions.advance(run_id, max_steps=1)
    model.assert_not_called()
    browser.act.assert_not_called()
    assert result["status"] == "needs_reasoning_llm"
    assert "public HTTPS" in result["reason"]
    release.assert_called_once_with(browser)
    assert browser.observe is original_observe


def test_transient_invalid_prediction_reobserves_and_retries_before_input(monkeypatch):
    sessions, run_id, browser, page, model, release = prediction_session(monkeypatch)
    valid = model.side_effect
    seen = []

    def predict(observation, *args):
        seen.append(observation["text"])
        if len(seen) == 1:
            page["text"] = "Updated results"
            raise ValueError("Invalid TypeSafe response; no action executed.")
        return valid(observation, *args)

    model.side_effect = predict
    result = sessions.advance(run_id, max_steps=1)
    assert result["status"] == "ready"
    assert seen == ["Choose a date", "Updated results"]
    assert model.call_count == 2
    browser.act.assert_called_once()
    release.assert_not_called()


def test_persistent_invalid_predictions_release_after_two_attempts(monkeypatch):
    sessions, run_id, browser, _, model, release = prediction_session(monkeypatch)
    model.side_effect = ValueError("Invalid TypeSafe response; no action executed.")
    result = sessions.advance(run_id, max_steps=1)
    assert result["status"] == "needs_reasoning_llm"
    assert "Invalid TypeSafe response" in result["reason"]
    assert model.call_count == 2
    browser.act.assert_not_called()
    release.assert_called_once_with(browser)


def test_unrelated_prediction_errors_are_not_retried(monkeypatch):
    sessions, run_id, browser, _, model, release = prediction_session(monkeypatch)
    model.side_effect = ValueError("Unexpected prediction failure")
    result = sessions.advance(run_id, max_steps=1)
    assert result["status"] == "needs_reasoning_llm"
    assert model.call_count == 1
    browser.act.assert_not_called()
    release.assert_called_once_with(browser)


def test_invalid_prediction_retry_checks_new_url_before_sending_content(monkeypatch):
    sessions, run_id, browser, page, model, release = prediction_session(monkeypatch)

    def predict(*_):
        page["url"] = "https://intranet.local/"
        raise ValueError("Invalid TypeSafe response; no action executed.")

    model.side_effect = predict
    result = sessions.advance(run_id, max_steps=1)
    assert result["status"] == "needs_reasoning_llm"
    assert "public HTTPS" in result["reason"]
    assert model.call_count == 1
    browser.act.assert_not_called()
    release.assert_called_once_with(browser)


def test_act_errors_are_never_retried_even_if_the_message_matches(monkeypatch):
    sessions, run_id, browser, _, model, release = prediction_session(monkeypatch)
    browser.act.side_effect = ValueError("Invalid TypeSafe response; no action executed.")
    result = sessions.advance(run_id, max_steps=1)
    assert result["status"] == "needs_reasoning_llm"
    assert model.call_count == 1
    browser.act.assert_called_once()
    release.assert_called_once_with(browser)


@pytest.mark.parametrize(
    "sensitive,confidence,target_confidence,override,expected",
    [
        (False, 0.1, None, None, "ready"),
        (True, 0.19, None, None, "needs_reasoning_llm"),
        (True, 0.2, 0.2, None, "ready"),
        (True, 0.9, 0.19, None, "needs_reasoning_llm"),
        (True, 0.19, None, 0.0, "needs_reasoning_llm"),
        (True, 0.3, None, 0.4, "needs_reasoning_llm"),
    ],
)
def test_mode_sets_confidence_without_an_ordinary_override(
    sensitive, confidence, target_confidence, override, expected
):
    agent = agent_with_decisions(decision("Open", confidence, target_confidence))
    release = Mock()
    sessions = BrowseSessions(agent_factory=lambda *_: agent, release_tab=release)
    started = sessions.start("https://example.org/", "Inspect the order", sensitive=sensitive)
    assert started["sensitive"] is sensitive
    kwargs = {} if override is None else {"min_confidence": override}
    result = sessions.advance(started["run_id"], max_steps=1, **kwargs)
    assert result["status"] == expected
    assert result["steps"] == (1 if expected == "ready" else 0)
    assert release.call_count == (0 if expected == "ready" else 1)


def test_sensitive_policy_is_added_once_and_mode_persists():
    agent = agent_with_decisions(decision("Open", 0.3), decision("Next", 0.1))
    factory = Mock(return_value=agent)
    release = Mock()
    sessions = BrowseSessions(agent_factory=factory, release_tab=release)
    started = sessions.start("https://example.org/", "Inspect the order", sensitive=True)
    effective_goal = factory.call_args.args[1]
    assert effective_goal.startswith("Inspect the order")
    assert "BLOCKED" in effective_goal
    assert "personal" in effective_goal
    assert "human approval" in effective_goal
    assert sessions.advance(started["run_id"], max_steps=1)["status"] == "ready"
    assert sessions.advance(started["run_id"], max_steps=1)["status"] == "needs_reasoning_llm"
    release.assert_called_once_with(agent.browser)
    factory.assert_called_once()


def test_normal_goal_is_unchanged_and_explicit_null_uses_the_default():
    agent = agent_with_decisions(decision("Open", 0.1))
    factory = Mock(return_value=agent)
    sessions = BrowseSessions(agent_factory=factory, release_tab=Mock())
    started = sessions.start("https://example.org/", "Inspect the order")
    factory.assert_called_once_with("https://example.org/", "Inspect the order")
    assert sessions.advance(started["run_id"], max_steps=1, min_confidence=None)["status"] == "ready"
