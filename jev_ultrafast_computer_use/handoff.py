"""Bounded Jev runs with a Chrome-tab handoff for a supervising agent."""

import os
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import uuid4

from browser_harness.helpers import cdp
from jev_ultrafast.browser import StalePage
from jev_ultrafast.model import field_context

from jev_ultrafast import Agent


def release_browser(browser) -> None:
    """Activate and detach an owned Jev tab without closing it."""
    if not browser.target:
        return
    cdp("Target.activateTarget", targetId=browser.target)
    cdp("Target.detachFromTarget", sessionId=browser.session)
    browser.target = None
    browser.session = None


def is_public_https_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return False
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        return False
    return (
        parts.scheme == "https"
        and bool(host)
        and "." in host
        and not host.endswith((".local", ".internal", ".test"))
        and parts.username is None
        and parts.password is None
    )


@dataclass
class BrowseRun:
    agent: Agent
    tab_target_id: str
    status: str = "ready"
    reason: str | None = None
    pending_context: dict | None = None
    text_request: dict | None = None


class BrowseSessions:
    def __init__(self, agent_factory=Agent, release_tab=release_browser):
        self.agent_factory = agent_factory
        self.release_tab = release_tab
        self.runs: dict[str, BrowseRun] = {}

    def start(self, url: str, goal: str) -> dict:
        if not is_public_https_url(url):
            raise ValueError("This tool accepts public HTTPS pages only")
        agent = self.agent_factory(url, goal)
        run_id = str(uuid4())
        self.runs[run_id] = BrowseRun(agent=agent, tab_target_id=agent.browser.target)
        return self.status(run_id)

    def status(self, run_id: str) -> dict:
        run = self.runs[run_id]
        state = run.agent.state
        page = state["page"]
        history = state["history"]
        return {
            "run_id": run_id,
            "status": run.status,
            "reason": run.reason,
            "goal": state["goal"],
            "url": page["url"],
            "title": page["title"],
            "visible_text": page["text"][:2000],
            "steps": len(history),
            "last_action": history[-1]["action"] if history else None,
            "recent_actions": [
                {key: action.get(key) for key in ("action", "kind", "page_changed", "text")} for action in history[-5:]
            ],
            "elapsed_ms": state.get("elapsed_ms", 0),
            "tab_target_id": run.tab_target_id,
            "text_request": run.text_request,
        }

    def advance(self, run_id: str, max_steps: int = 10, min_confidence: float = 0.0) -> dict:
        if not 1 <= max_steps <= 50:
            raise ValueError("max_steps must be between 1 and 50")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        run = self.runs[run_id]
        if run.status != "ready":
            return self.status(run_id)
        agent = run.agent
        for _ in range(max_steps):
            if not is_public_https_url(agent.state["page"]["url"]):
                return self._release(run_id, "needs_gpt", "Page left public HTTPS; no content sent to Jev")
            try:
                agent.command("predict")
                decision = agent.state["decision"]
                confidence = min(
                    value for value in (decision["confidence"], decision.get("target_confidence")) if value is not None
                )
                if confidence < min_confidence:
                    return self._release(
                        run_id, "needs_gpt", f"Jev confidence {confidence:.2f} below {min_confidence:.2f}"
                    )
                if decision["operation"] == "TYPE_TEXT" and not os.environ.get("TEXT_MODEL_API_KEY"):
                    action = next(a for a in agent.state["page"]["actions"] if a["id"] == decision["choice"])
                    context = field_context(agent.state["goal"], action, agent.state["page"], agent.state["history"])
                    if not agent.pending_text or agent.pending_text[0] != context:
                        run.pending_context = context
                        run.text_request = {
                            "goal": context["goal"],
                            "field_label": context["field"]["label"],
                            "field_role": context["field"]["role"],
                            "current_value": context["field"]["value"],
                            "page_title": context["page"]["title"],
                            "page_text": context["page"]["text"],
                        }
                        run.status = "needs_text"
                        run.reason = "The supervisor must supply text for the selected field"
                        return self.status(run_id)
                agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
            except StalePage:
                # The current decision was consumed. Observe again before making any new decision.
                agent.state["decision"] = None
                agent.state["status"] = "ready"
                try:
                    agent.state["page"] = agent.browser.observe(screenshot=agent.screenshots)
                except Exception as error:
                    return self._release(run_id, "needs_gpt", f"Browser observation failed: {error}")
                continue
            except Exception as error:
                return self._release(run_id, "needs_gpt", f"Jev stopped: {error}")
            if agent.state["status"] == "done":
                return self._release(run_id, "needs_verification", "Jev reported DONE; verify the page outcome")
            if agent.state["status"] == "blocked":
                return self._release(run_id, "needs_gpt", "Jev reported BLOCKED or made no progress")
        return self.status(run_id)

    def submit_text(self, run_id: str, value: str) -> dict:
        run = self.runs[run_id]
        if run.status != "needs_text" or run.pending_context is None:
            raise ValueError("This run is not waiting for field text")
        if not value.strip() or len(value) > 2000:
            raise ValueError("Field text must contain 1 to 2000 characters")
        agent = run.agent
        agent.pending_text = (run.pending_context, value, {"model": "supervisor", "latency_ms": 0, "usage": {}})
        run.pending_context = None
        run.text_request = None
        run.status = "ready"
        run.reason = None
        try:
            agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
        except StalePage:
            agent.state["decision"] = None
            agent.state["status"] = "ready"
            agent.state["page"] = agent.browser.observe(screenshot=agent.screenshots)
        except Exception as error:
            return self._release(run_id, "needs_gpt", f"Text input failed: {error}")
        return self.status(run_id)

    def handoff(self, run_id: str) -> dict:
        run = self.runs[run_id]
        if run.status in {"ready", "needs_text"}:
            return self._release(run_id, "needs_gpt", "Supervisor requested takeover")
        return self.status(run_id)

    def close(self, run_id: str) -> dict:
        run = self.runs[run_id]
        if run.status in {"ready", "needs_text"}:
            run.agent.close()
            run.status = "closed"
            run.reason = "Supervisor closed the browser tab"
        return self.status(run_id)

    def _release(self, run_id: str, status: str, reason: str) -> dict:
        run = self.runs[run_id]
        self.release_tab(run.agent.browser)
        run.status = status
        run.reason = reason
        return self.status(run_id)
