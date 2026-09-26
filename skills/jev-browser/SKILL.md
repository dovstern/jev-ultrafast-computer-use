---
name: jev-browser
description: Delegate a public, read-only Chrome browsing task to Jev when fast page interaction is useful, while retaining supervisor control and verifying the result.
---

# Jev browser delegation

Use the `jev-browser` MCP tools for public HTTPS browsing when the user wants the browser to read or navigate pages. The current server does not control native apps or support private sites, purchases, account changes, or uploads. Use the host agent's own tools for those tasks.

1. Give `start_browse` a public starting URL and a concrete goal. Jev opens a Chrome tab.
2. Call `advance_browse` in bounded batches. Use `browse_status` to inspect the URL, visible text, and recent actions between batches. Use `max_steps=1` when each decision needs review. Set `min_confidence` when the task warrants early handoff.
3. If status is `needs_text`, use `submit_browse_text` only with a value supported by the user's request. Then continue the run.
4. If status is `needs_gpt` or `needs_verification`, Jev has released the tab. Claim that same tab with the host's browser controls. Match its URL and title to the MCP result before continuing. Use `handoff_browse` to take over earlier.
5. Check the final page independently. A Jev `DONE` decision does not prove the requested outcome. Report what you verified and any unresolved limit.

Use `close_browse` only for a tab Jev still owns. A released tab belongs to the host agent or user.
