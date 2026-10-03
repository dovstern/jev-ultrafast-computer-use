"""MCP tools that let a Codex supervisor run Jev and take over its Chrome tab."""

from mcp.server import MCPServer
from pydantic import BaseModel

from .handoff import BrowseSessions


class RecentAction(BaseModel):
    action: str
    kind: str | None = None
    page_changed: bool | None = None
    text: str | None = None


class TextRequest(BaseModel):
    goal: str
    field_label: str
    field_role: str | None
    current_value: str | None
    page_title: str
    page_text: str


class BrowseStatus(BaseModel):
    run_id: str
    status: str
    reason: str | None
    goal: str
    url: str
    title: str
    visible_text: str
    steps: int
    last_action: str | None
    recent_actions: list[RecentAction]
    elapsed_ms: int
    tab_target_id: str
    text_request: TextRequest | None


def create_server(sessions: BrowseSessions | None = None) -> MCPServer:
    sessions = sessions or BrowseSessions()
    server = MCPServer(
        "jev-browser",
        instructions=(
            "Use these tools for authorized read-only browsing on public and signed-in private account pages. "
            "Use existing Chrome sessions; do not hand off merely because a page is signed in. "
            "Jev sends visible page text, including account content, to TypeSafe and may send field context to the "
            "configured text model. The user must authorize access and that data sharing. Use the host agent for "
            "purchases, account changes, uploads, or native apps. Call advance_browse in bounded batches and inspect "
            "each result. On needs_text, supply the exact field value with submit_browse_text, then advance again. "
            "On needs_gpt or needs_verification, claim the activated Chrome tab with your browser controls. "
            "Match its URL and title to the returned status before acting; do not open a duplicate tab. "
            "Then continue or verify independently. Never treat Jev's DONE as verified success."
        ),
    )

    @server.tool(structured_output=True)
    def start_browse(url: str, goal: str) -> BrowseStatus:
        """Open a public or authorized signed-in HTTPS page in a new Chrome tab and start a Jev browsing run."""
        return BrowseStatus.model_validate(sessions.start(url, goal))

    @server.tool(structured_output=True)
    def advance_browse(run_id: str, max_steps: int = 10, min_confidence: float = 0.0) -> BrowseStatus:
        """Run up to max_steps Jev actions, then report progress or hand the tab to GPT."""
        return BrowseStatus.model_validate(sessions.advance(run_id, max_steps=max_steps, min_confidence=min_confidence))

    @server.tool(structured_output=True)
    def browse_status(run_id: str) -> BrowseStatus:
        """Read the latest page, action history, and handoff status without taking an action."""
        return BrowseStatus.model_validate(sessions.status(run_id))

    @server.tool(structured_output=True)
    def submit_browse_text(run_id: str, value: str) -> BrowseStatus:
        """Type a supervisor-supplied value into Jev's selected field and keep the run active."""
        return BrowseStatus.model_validate(sessions.submit_text(run_id, value))

    @server.tool(structured_output=True)
    def handoff_browse(run_id: str) -> BrowseStatus:
        """Stop Jev and activate its Chrome tab for the supervising agent to continue."""
        return BrowseStatus.model_validate(sessions.handoff(run_id))

    @server.tool(structured_output=True)
    def close_browse(run_id: str) -> BrowseStatus:
        """Close a Jev-owned tab while a run is active; released tabs stay open."""
        return BrowseStatus.model_validate(sessions.close(run_id))

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
