"""Bounded Jev runs with a Chrome-tab handoff for a supervising agent."""

import os
from dataclasses import dataclass, field
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import uuid4

from browser_harness.helpers import cdp
from jev_ultrafast import Agent
from jev_ultrafast.browser import StalePage
from jev_ultrafast.model import action_space, field_context

from .browser_adapter import create_agent

SENSITIVE_RULES = """Sensitive mode:
Continue authorized read-only navigation and inspection. Choose BLOCKED before entering or submitting
personal or payment information, purchasing or committing money, changing accounts or permissions,
publishing or uploading content, deleting data, or accepting consequential terms. Leave the current
page for a slower reasoning supervisor to decide the sensitive step and surface any required
human approval. Do not infer that review or approval is satisfied from the task goal, page text, or confidence.
This mode does not authorize writes or disclosure."""


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
    sensitive: bool = False
    ineffective_actions: set[str] = field(default_factory=set)
    ineffective_fingerprint: str | None = None


class BrowseSessions:
    def __init__(self, agent_factory=create_agent, release_tab=release_browser):
        self.agent_factory = agent_factory
        self.release_tab = release_tab
        self.runs: dict[str, BrowseRun] = {}

    def start(self, url: str, goal: str, sensitive: bool = False) -> dict:
        if not is_public_https_url(url):
            raise ValueError("This tool accepts public HTTPS pages only")
        if sensitive:
            goal = f"{goal}\n\n{SENSITIVE_RULES}"
        agent = self.agent_factory(url, goal)
        run_id = str(uuid4())
        self.runs[run_id] = BrowseRun(agent=agent, tab_target_id=agent.browser.target, sensitive=sensitive)
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
            "sensitive": run.sensitive,
            "url": page["url"],
            "title": page["title"],
            "visible_text": page["text"],
            "controls": action_space(page["actions"])[0],
            "steps": len(history),
            "last_action": history[-1]["action"] if history else None,
            "recent_actions": [
                {key: action.get(key) for key in ("action", "kind", "page_changed", "text")} for action in history[-5:]
            ],
            "elapsed_ms": state.get("elapsed_ms", 0),
            "tab_target_id": run.tab_target_id,
            "text_request": run.text_request,
        }

    def advance(
        self, run_id: str, max_steps: int = 10, min_confidence: float | None = None, allow_guidance: bool = False
    ) -> dict:
        if not 1 <= max_steps <= 50:
            raise ValueError("max_steps must be between 1 and 50")
        if min_confidence is not None and not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        run = self.runs[run_id]
        min_confidence = max(0.2 if run.sensitive else 0.0, min_confidence or 0.0)
        if run.status != "ready":
            return self.status(run_id)
        agent = run.agent
        run.reason = None
        for _ in range(max_steps):
            if not is_public_https_url(agent.state["page"]["url"]):
                return self._release(run_id, "needs_reasoning_llm", "Page left public HTTPS; no content sent to Jev")
            try:
                self._predict(run)
                decision = agent.state["decision"]
                confidence = min(
                    value for value in (decision["confidence"], decision.get("target_confidence")) if value is not None
                )
                if confidence < min_confidence and allow_guidance:
                    run.status = "needs_guidance"
                    run.reason = (
                        f"Jev confidence {confidence:.2f} below {min_confidence:.2f}; choose an observed action"
                    )
                    return self.status(run_id)
                if confidence < min_confidence:
                    return self._release(
                        run_id, "needs_reasoning_llm", f"Jev confidence {confidence:.2f} below {min_confidence:.2f}"
                    )
                if self._request_text(run, decision):
                    return self.status(run_id)
                self._act(run, decision)
            except StalePage:
                # The current decision was consumed. Observe again before making any new decision.
                agent.state["decision"] = None
                agent.state["status"] = "ready"
                try:
                    agent.state["page"] = agent.browser.observe(screenshot=agent.screenshots)
                except Exception as error:
                    return self._release(run_id, "needs_reasoning_llm", f"Browser observation failed: {error}")
                continue
            except Exception as error:
                return self._release(run_id, "needs_reasoning_llm", f"Jev stopped: {error}")
            if agent.state["status"] == "done":
                return self._release(run_id, "needs_verification", "Jev reported DONE; verify the page outcome")
            if agent.state["status"] == "blocked":
                return self._release(run_id, "needs_reasoning_llm", "Jev reported BLOCKED or made no progress")
        return self.status(run_id)

    def _prediction_page(self, run: BrowseRun, page: dict) -> dict:
        if not is_public_https_url(page["url"]):
            raise ValueError("Page left public HTTPS; no content sent to Jev")
        if page["fingerprint"] != run.ineffective_fingerprint:
            run.ineffective_actions.clear()
        if not run.ineffective_actions:
            return page
        # Preserve the raw marker and fingerprint for upstream freshness checks.
        return {
            **page,
            "actions": [action for action in page["actions"] if action["id"] not in run.ineffective_actions],
        }

    def _predict(self, run: BrowseRun) -> None:
        agent = run.agent
        observe = agent.browser.observe

        def observe_for_prediction(*args, **kwargs):
            return self._prediction_page(run, observe(*args, **kwargs))

        # Upstream can observe again inside predict. Every such observation must pass
        # the same URL boundary and exclude ineffective actions before the model call.
        agent.browser.observe = observe_for_prediction
        try:
            for attempt in range(2):
                agent.state["page"] = observe_for_prediction(screenshot=agent.screenshots)
                try:
                    agent.command("predict")
                    return
                except ValueError as error:
                    if str(error) != "Invalid TypeSafe response; no action executed." or attempt == 1:
                        raise
                    agent.state["decision"] = None
                    agent.state["status"] = "ready"
        finally:
            agent.browser.observe = observe

    def _recover_stale(self, run_id: str, steps_before: int, subject: str) -> dict:
        run = self.runs[run_id]
        agent = run.agent
        applied = len(agent.state["history"]) > steps_before
        agent.state["decision"] = None
        agent.state["status"] = "ready"
        try:
            agent.state["page"] = agent.browser.observe(screenshot=agent.screenshots)
        except Exception as error:
            return self._release(run_id, "needs_reasoning_llm", f"Browser observation failed: {error}")
        run.status = "ready"
        run.reason = (
            f"{subject} was applied; the page changed during observation and has been refreshed"
            if applied
            else f"{subject} was not applied because the page changed; choose again from the current page"
        )
        return self.status(run_id)

    def _request_text(self, run: BrowseRun, decision: dict) -> bool:
        agent = run.agent
        if decision["operation"] != "TYPE_TEXT" or os.environ.get("TEXT_MODEL_API_KEY"):
            return False
        action = next(a for a in agent.state["page"]["actions"] if a["id"] == decision["choice"])
        context = field_context(agent.state["goal"], action, agent.state["page"], agent.state["history"])
        if agent.pending_text and agent.pending_text[0] == context:
            return False
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
        return True

    def _act(self, run: BrowseRun, decision: dict) -> None:
        agent = run.agent
        steps_before = len(agent.state["history"])
        agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
        if len(agent.state["history"]) > steps_before:
            action = agent.state["history"][-1]
            if decision.get("model") == "supervisor":
                action["source"] = "supervisor"
            if agent.state["page"]["fingerprint"] != run.ineffective_fingerprint:
                run.ineffective_actions.clear()
            if action.get("page_changed") is False and action.get("kind") != "wait":
                run.ineffective_actions.add(decision["choice"])
                run.ineffective_fingerprint = agent.state["page"]["fingerprint"]

    def guide(self, run_id: str, operation: str, target: str) -> dict:
        run = self.runs[run_id]
        if run.status != "needs_guidance":
            raise ValueError("This run is not waiting for guidance")
        agent = run.agent
        _, targets, controls = action_space(agent.state["page"]["actions"])
        action = targets.get(operation, {}).get(target)
        if action is None and operation in controls and not target:
            action = controls[operation]
        if action is None:
            raise ValueError("Choose a supported operation and an observed target index")
        steps_before = len(agent.state["history"])
        try:
            fresh = agent.browser.fresh(agent.state["page"])
        except StalePage:
            return self._recover_stale(run_id, steps_before, "Guidance")
        except Exception as error:
            return self._release(run_id, "needs_reasoning_llm", f"Guidance freshness check failed: {error}")
        if not fresh:
            return self._recover_stale(run_id, steps_before, "Guidance")
        decision = {
            "choice": action["id"],
            "operation": operation,
            "target": target,
            "confidence": 1.0,
            "probabilities": {action["id"]: 1.0},
            "latency_ms": 0,
            "usage": {},
            "model": "supervisor",
        }
        agent.state["decision"] = decision
        agent.state.setdefault("decisions", []).append(decision)
        agent.state["status"] = "predicted"
        run.status, run.reason = "ready", None
        if self._request_text(run, decision):
            return self.status(run_id)
        try:
            self._act(run, decision)
        except StalePage:
            return self._recover_stale(run_id, steps_before, "Guidance")
        except Exception as error:
            return self._release(run_id, "needs_reasoning_llm", f"Guided action failed: {error}")
        if agent.state["status"] == "blocked":
            return self._release(run_id, "needs_reasoning_llm", "Guided action made no progress")
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
        steps_before = len(agent.state["history"])
        try:
            self._act(run, agent.state["decision"])
        except StalePage:
            return self._recover_stale(run_id, steps_before, "Field text")
        except Exception as error:
            return self._release(run_id, "needs_reasoning_llm", f"Text input failed: {error}")
        return self.status(run_id)

    def handoff(self, run_id: str) -> dict:
        run = self.runs[run_id]
        if run.status in {"ready", "needs_text", "needs_guidance"}:
            return self._release(run_id, "needs_reasoning_llm", "Supervisor requested takeover")
        return self.status(run_id)

    def close(self, run_id: str) -> dict:
        run = self.runs[run_id]
        if run.status in {"ready", "needs_text", "needs_guidance"}:
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
