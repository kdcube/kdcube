# /kdcube-ui-test

Test the KDCube chat UI in a real browser using a Playwright MCP server.

Requires a Playwright MCP server configured in `~/.codex/config.toml`:

```toml
[mcp_servers.playwright]
command = "npx"
args = ["@playwright/mcp@latest"]
```

## Resolve the UI URL first

```bash
grep "KDCUBE_UI_PORT\|routesPrefix" \
  ~/.kdcube/kdcube-runtime/config/.env \
  ~/.kdcube/kdcube-runtime/config/frontend.config.*.json 2>/dev/null | head -10
```

Default URL: `http://localhost:5174/platform/chat`

## Standard test flow

1. **Open the UI** — navigate to the chat URL; take a screenshot to confirm it loaded.
2. **Send a test message** — click the chat input, type "hello", submit, and wait for a
   response.
3. **Verify the response** — wait for the response to appear, take a screenshot, check for
   error messages.
4. **Check for errors** — look for error banners or "Internal Server Error" in the page;
   if found, run:
   ```bash
   docker logs all_in_one_kdcube-chat-proc-1 --tail 30
   ```

## Hardened test (after bundle changes)

1. Reload and verify the bundle first:
   ```bash
   python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" reload <bundle-id>
   python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" verify-reload <bundle-id>
   ```
2. Run the standard test flow above.
3. Report: loaded OK / response received / errors found.

## Rules

- Always take a screenshot before and after sending a message.
- If the page shows a login screen, use the hardcoded token from
  `~/.kdcube/kdcube-runtime/config/frontend.config.hardcoded.json`.
- If the page does not load at all, run `status` first to check containers:
  ```bash
  python3 "${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}/kdcube_local.py" status
  ```
- Do not click randomly — follow the test flow above.