"""Codex can call the browse lifecycle over MCP."""

import asyncio
from unittest.mock import Mock

import pytest
from mcp import Client

from jev_ultrafast_computer_use.handoff import BrowseSessions
from jev_ultrafast_computer_use.mcp_server import create_server
from tests.test_handoff import agent_with_decisions, decision


def test_mcp_exposes_bounded_browsing_and_takeover():
    agent = agent_with_decisions(decision("Open"), decision("BLOCKED"))
    server = create_server(BrowseSessions(agent_factory=lambda *_: agent, release_tab=Mock()))

    async def exercise():
        async with Client(server) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "start_browse",
                "advance_browse",
                "browse_status",
                "handoff_browse",
                "close_browse",
                "submit_browse_text",
                "guide_browse",
            }
            started = await client.call_tool(
                "start_browse", {"url": "https://example.org/", "goal": "Find the article"}
            )
            run_id = started.structured_content["run_id"]
            progress = await client.call_tool("advance_browse", {"run_id": run_id, "max_steps": 1})
            assert progress.structured_content["status"] == "ready"
            assert progress.structured_content["steps"] == 1
            takeover = await client.call_tool("advance_browse", {"run_id": run_id})
            assert takeover.structured_content["status"] == "needs_gpt"
            assert takeover.structured_content["url"] == "https://example.org/"

    asyncio.run(exercise())


def test_mcp_allows_authorized_signed_in_browsing():
    account_url = "https://example.org/account/appointments"
    agent = agent_with_decisions(decision("Next"))
    agent.state["page"].update(url=account_url, title="My appointments", text="Signed in. Available appointments.")
    release = Mock()
    server = create_server(BrowseSessions(agent_factory=lambda *_: agent, release_tab=release))

    async def exercise():
        async with Client(server) as client:
            assert "signed-in" in client.instructions
            assert "authorized" in client.instructions
            tools = await client.list_tools()
            start_tool = next(tool for tool in tools.tools if tool.name == "start_browse")
            assert "signed-in" in start_tool.description
            started = await client.call_tool(
                "start_browse", {"url": account_url, "goal": "Read my available appointments without booking"}
            )
            assert started.structured_content["visible_text"] == "Signed in. Available appointments."
            progress = await client.call_tool(
                "advance_browse", {"run_id": started.structured_content["run_id"], "max_steps": 1}
            )
            assert progress.structured_content["status"] == "ready"
            assert progress.structured_content["steps"] == 1
            assert progress.structured_content["url"] == account_url
            release.assert_not_called()

    asyncio.run(exercise())


def test_mcp_guidance_keeps_the_run_and_serializes_controls(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    agent = agent_with_decisions(decision("Search", confidence=0.2), decision("DONE"))
    release = Mock()
    server = create_server(BrowseSessions(agent_factory=lambda *_: agent, release_tab=release))

    async def exercise():
        async with Client(server) as client:
            started = await client.call_tool(
                "start_browse", {"url": "https://example.org/", "goal": "Find the article"}
            )
            run_id = started.structured_content["run_id"]
            paused = await client.call_tool(
                "advance_browse", {"run_id": run_id, "min_confidence": 0.6, "allow_guidance": True}
            )
            assert paused.structured_content["status"] == "needs_guidance"
            control = paused.structured_content["controls"][0]
            assert control["index"] == "1"
            assert "TYPE_TEXT" in control["operations"]
            release.assert_not_called()
            guided = await client.call_tool("guide_browse", {"run_id": run_id, "operation": "TYPE_TEXT", "target": "1"})
            assert guided.structured_content["status"] == "needs_text"
            typed = await client.call_tool("submit_browse_text", {"run_id": run_id, "value": "article name"})
            assert typed.structured_content["steps"] == 1
            assert typed.structured_content["run_id"] == run_id
            done = await client.call_tool("advance_browse", {"run_id": run_id})
            assert done.structured_content["status"] == "needs_verification"
            release.assert_called_once_with(agent.browser)

    asyncio.run(exercise())


@pytest.mark.parametrize("sensitive", [False, True])
def test_mcp_mode_reaches_upstream_requests_and_blocked_hands_off(monkeypatch, sensitive):
    import jev_ultrafast.agent as upstream
    import jev_ultrafast.model as model

    page = {
        "url": "https://example.org/",
        "title": "Test order",
        "text": "Review order details",
        "fingerprint": "f1",
        "actions": [{"id": "review", "node": 1, "kind": "click", "label": "Review order", "role": "button"}],
    }
    browser = Mock(target="fixture-target", session="fixture-session")
    browser.fresh.return_value = True
    browser.observe.side_effect = lambda **_: page.copy()

    def act(*_, **__):
        page.update(text="Submit personal details", fingerprint="f2")
        page["actions"] = [
            {"id": "submit", "node": 2, "kind": "click", "label": "Submit personal details", "role": "button"}
        ]

    browser.act.side_effect = act
    monkeypatch.setattr(upstream, "Browser", lambda _: browser)
    requests = []

    def respond(_url, _key, body):
        requests.append(body)
        questions = body["questions"]
        operation = "CLICK" if len(requests) == 1 else ("BLOCKED" if sensitive else "DONE")
        answers = {}
        for name, question in questions.items():
            ids = list(question["criteria"])
            choice = operation if name == "operation" else ids[0]
            answers[name] = {
                "choice": choice,
                "confidence": 0.3 if sensitive else 0.1,
                "probabilities": {key: float(key == choice) for key in ids},
            }
        return {"answers": answers, "model": "test-jev", "usage": {}}

    monkeypatch.setattr(model, "post_json", respond)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    release = Mock()
    sessions = BrowseSessions(agent_factory=upstream.Agent, release_tab=release)
    server = create_server(sessions)

    async def exercise():
        async with Client(server) as client:
            tools = await client.list_tools()
            advance = next(t for t in tools.tools if t.name == "advance_browse")
            assert advance.input_schema["properties"]["min_confidence"].get("default") is None
            started = await client.call_tool(
                "start_browse",
                {
                    "url": "https://example.org/",
                    "goal": "Open the order details",
                    "sensitive": sensitive,
                },
            )
            assert started.structured_content["sensitive"] is sensitive
            result = await client.call_tool("advance_browse", {"run_id": started.structured_content["run_id"]})
            assert result.structured_content["status"] == ("needs_gpt" if sensitive else "needs_verification")
            assert result.structured_content["steps"] == 1
            release.assert_called_once_with(browser)

    asyncio.run(exercise())
    assert len(requests) == 2
    browser.act.assert_called_once()
    for request in requests:
        goal = request["questions"]["operation"]["instructions"]["goal"]
        assert goal.startswith("Open the order details")
        assert ("Sensitive mode" in goal) is sensitive
        assert "min_confidence" not in request
        for question in request["questions"].values():
            assert question["instructions"]["goal"] == goal
    assert requests[1]["state"]["recent_actions"][0]["action"] == "Review order"
