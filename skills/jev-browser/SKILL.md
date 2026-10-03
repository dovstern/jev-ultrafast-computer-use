---
name: jev-browser
description: Delegate an authorized read-only Chrome browsing task on public or signed-in pages to Jev when fast page interaction is useful, while retaining supervisor control and verifying the result.
---

# Jev browser delegation

Use the `jev-browser` MCP tools for authorized read-only HTTPS browsing on public pages and signed-in private account pages. Jev uses the connected Chrome profile, including its existing sessions. Do not hand off merely because a page is signed in. Jev sends visible page text, including account content, to TypeSafe. The optional text helper receives field context if configured. Use this workflow only when the user authorizes access and that data sharing. For purchases, account changes, uploads, or native apps, use the host agent's own tools.

1. Give `start_browse` an HTTPS starting URL and a concrete goal. Jev opens a Chrome tab.
2. Call `advance_browse` in bounded batches. Use `browse_status` to inspect the URL, visible text, and recent actions between batches. Use `max_steps=1` when each decision needs review. Set `min_confidence` when the task warrants early handoff.
3. If status is `needs_text`, use `submit_browse_text` only with a value supported by the user's request. Then continue the run.
4. If status is `needs_gpt` or `needs_verification`, Jev has released the tab. Claim that same tab with the host's browser controls. Match its URL and title to the MCP result before continuing. Use `handoff_browse` to take over earlier.
5. Check the final page independently. A Jev `DONE` decision does not prove the requested outcome. Report what you verified and any unresolved limit.

Use `close_browse` only for a tab Jev still owns. A released tab belongs to the host agent or user.
