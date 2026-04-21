---
description: >
  Test the KDCube UI in a real browser using Playwright. TRIGGER when: user wants to test the
  UI, check that a bundle responds in the chat, verify the interface opens, or do end-to-end
  testing of a bundle.
  SKIP: unit tests or bundle suite tests (use local-runtime for those).
allowed-tools: Bash, Read, mcp__playwright__browser_navigate, mcp__playwright__browser_screenshot, mcp__playwright__browser_click, mcp__playwright__browser_type, mcp__playwright__browser_wait_for_selector, mcp__playwright__browser_evaluate
---

# KDCube UI Test

Test the KDCube chat UI in a real browser using Playwright MCP.

## Resolve the UI URL first

```bash
grep "KDCUBE_UI_PORT\|routesPrefix" \
  ~/.kdcube/kdcube-runtime/config/.env \
  ~/.kdcube/kdcube-runtime/config/frontend.config.*.json 2>/dev/null | head -10
```

Default URL: `http://localhost:5174/platform/chat`

## Standard test flow

1. **Open the UI**
   - Navigate to the chat URL
   - Take a screenshot to confirm it loaded

2. **Send a test message**
   - Click the chat input
   - Type a simple message (e.g. "hello")
   - Submit and wait for a response

3. **Verify the response**
   - Wait for the response to appear
   - Take a screenshot
   - Check for error messages in the UI

4. **Check for errors**
   - Look for error banners or "Internal Server Error" text in the page
   - If errors found, check `docker logs all_in_one_kdcube-chat-proc-1 --tail 30`

## Hardened test (after bundle changes)

1. Reload the bundle first:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" reload <bundle-id>
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kdcube_local.py" verify-reload <bundle-id>
   ```
2. Run the standard test flow above.
3. Report: loaded OK / response received / errors found.

## General rules

- Always take a screenshot before and after sending a message.
- If the page shows a login screen, use the hardcoded token from `frontend.config.hardcoded.json`.
- If the page does not load at all, run `status` first to check containers.
- Do not click randomly — follow the test flow above.