# Jev Ultrafast Computer Use

Read README.md before editing. Upstream Jev owns the browser loop. This package owns only the supervisor bridge, MCP server, and plugin files.

- The input is one natural-language goal. Do not add site-specific plans or hardcoded field values.
- TypeSafe chooses an operation and operation-specific target heads in one request. Consume only the selected operation's target.
- Targets must map to observed elements and supported operations. Never let the model emit selectors or executable code.
- Without TEXT_MODEL_API_KEY, pause on TYPE_TEXT and ask the supervisor for field text. Upstream Jev handles the pending value and stale retry.
- Never retry a browser mutation. Log execution before observing its result.
- Screenshots are optional; the model does not consume them. Keep demonstration footage at its original speed.
- Keep credentials server-side and .env ignored. Tests must not call paid APIs.
- Verify actual final outcomes independently. A DONE choice is not proof of success.
- Keep the upstream dependency in uv.lock pinned to a Git commit. Test changes before accepting its update PR.
- Do not commit or push unless the user requests it.

Checks: uv run ruff check ., uv run pytest, uv build.
