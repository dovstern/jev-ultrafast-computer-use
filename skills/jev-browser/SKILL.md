---
name: jev-browser
description: Delegate an authorized read-only Chrome browsing task on public or signed-in pages to Jev when fast page interaction is useful, while retaining supervisor control and verifying the result.
argument-hint: "[--sensitive]"
---

# Jev browser delegation

Use the `jev-browser` MCP tools for authorized read-only HTTPS browsing on public pages and signed-in private account pages. Jev uses the connected Chrome profile, including its existing sessions. Do not hand off merely because a page is signed in. Jev sends visible page text, including account content, to TypeSafe. The optional text helper receives field context if configured. Use this workflow only when the user authorizes access and that data sharing. For purchases, account changes, uploads, or native apps, use the host agent's own tools.

1. Give `start_browse` an HTTPS starting URL and a concrete goal. Jev opens a Chrome tab. If the human requests sensitive mode (including `--sensitive`), or the browsing task may reach a sensitive decision, pass `sensitive=True`. It stays active for the run, adds a 0.2 confidence floor, and instructs Jev to report `BLOCKED` before personal/payment disclosure, purchases, financial commitments, account/permission changes, publishing/uploads, deletion, or consequential terms. This does not authorize those actions or guarantee their prevention.
2. Call `advance_browse` in bounded batches. Use `browse_status` to inspect the URL, visible text, and recent actions between batches. Omit `min_confidence` during ordinary runs. Set it only when debugging or when the human explicitly requests a threshold. For that diagnostic flow, `allow_guidance=True` pauses before an uncertain action. Use `max_steps=1` only when individual decisions need review. Status includes the observed control indices, values, and supported operations.
3. If status is `needs_text`, use `submit_browse_text` only with a value supported by the user's request. Then continue the run.
4. If status is `needs_guidance`, inspect the current controls and choose one supported action with `guide_browse(operation, target)`. Use the returned control index as the target; for a select option, use its option index as well, such as `3:2`. This executes through Jev and keeps the same run. If the page changed, inspect the refreshed state before choosing again. Do not invent controls or reuse an old index. Use `handoff_browse` when the host browser must take over.
5. If status is `needs_reasoning_llm` or `needs_verification`, Jev has released the tab. Claim that same tab with the host's browser controls. Match its URL and title to the MCP result before continuing. For a sensitive boundary, the slower reasoning supervisor must decide what is authorized and surface any required human approval before acting. Confidence never substitutes for authorization. Use `handoff_browse` to take over earlier.
6. Check the final page independently. A Jev `DONE` decision does not prove the requested outcome. Report what you verified and any unresolved limit.

Use `close_browse` only for a tab Jev still owns. A released tab belongs to the host agent or user.
