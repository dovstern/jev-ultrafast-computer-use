"""The adapter preserves one browser owner and cleans up failed initialization."""

from unittest.mock import Mock

import pytest

from jev_ultrafast_computer_use import browser_adapter
from jev_ultrafast_computer_use.browser_adapter import BrowserAdapter, create_agent


def test_proxy_forwards_ownership_changes_to_the_original_browser():
    original = Mock(target="tab", session="session")
    adapter = BrowserAdapter(original)
    adapter.target = None
    adapter.session = None
    assert original.target is None
    assert original.session is None
    adapter.close()
    original.close.assert_called_once_with()


def test_factory_adapts_the_existing_browser_without_opening_another(monkeypatch):
    original = Mock(target="tab", session="session")
    agent = Mock(browser=original, screenshots=False, state={"browser": original})
    factory = Mock(return_value=agent)
    monkeypatch.setattr(browser_adapter, "Agent", factory)
    page = {"title": "Page"}
    observe = Mock(return_value=page)
    monkeypatch.setattr(BrowserAdapter, "observe", observe)
    assert create_agent("https://example.org/", "Read the page") is agent
    factory.assert_called_once_with("https://example.org/", "Read the page")
    assert agent.browser is agent.state["browser"]
    assert agent.browser._browser is original
    assert agent.state["page"] is page
    original.close.assert_not_called()


def test_factory_closes_owned_tab_when_adapter_observation_fails(monkeypatch):
    original = Mock(target="tab")
    agent = Mock(browser=original, screenshots=False, state={"browser": original})
    monkeypatch.setattr(browser_adapter, "Agent", Mock(return_value=agent))
    monkeypatch.setattr(BrowserAdapter, "observe", Mock(side_effect=RuntimeError("Navigation interrupted")))
    with pytest.raises(RuntimeError, match="Navigation interrupted"):
        create_agent("https://example.org/", "Read the page")
    original.close.assert_called_once_with()


def observation_browser():
    page = {
        "url": "https://example.org/",
        "text": "Page",
        "actions": [],
        "guards": {},
        "page_key": [],
        "marker": [],
        "scroll": {"y": 0, "height": 100},
    }
    original = Mock()
    original.observe.side_effect = lambda **_: dict(page, actions=[], guards={})
    original.evaluate.return_value = {"actions": [], "guards": {}, "available_nodes": [], "page_key": []}
    original.fresh.return_value = True
    return original


def test_observation_retries_transient_navigation_without_repeating_input():
    from jev_ultrafast.browser import StalePage

    original = observation_browser()
    observe = original.observe.side_effect
    original.observe.side_effect = [StalePage("Document navigating"), observe()]
    result = BrowserAdapter(original).observe(screenshot=False)
    assert result["text"] == "Page"
    assert original.observe.call_count == 2
    original.act.assert_not_called()


def test_observation_retries_when_label_read_changes_the_snapshot():
    original = observation_browser()
    original.fresh.side_effect = [False, True]
    result = BrowserAdapter(original).observe(screenshot=False)
    assert result["text"] == "Page"
    assert original.observe.call_count == 2
    original.act.assert_not_called()


def test_persistently_unstable_observation_stops_after_bounded_retries():
    from jev_ultrafast.browser import StalePage

    original = observation_browser()
    original.fresh.return_value = False
    with pytest.raises(StalePage, match="Page changed while observing native labels"):
        BrowserAdapter(original).observe(screenshot=False)
    assert original.observe.call_count == 20
    original.act.assert_not_called()
