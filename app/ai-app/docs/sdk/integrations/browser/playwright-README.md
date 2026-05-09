# Playwright Integration

This document describes how KDCube uses Playwright for browser-backed tools and rendering.

The current browser integration has two layers:

- A process-wide shared Chromium service.
- Per-turn BrowserContexts managed by `browser_tools`.

## Shared Browser Service

The shared browser service lives in:

```text
kdcube_ai_app/infra/rendering/shared_browser.py
```

Callers use:

```python
from kdcube_ai_app.infra.rendering.shared_browser import get_shared_browser

shared_browser = await get_shared_browser()
browser = await shared_browser.get_browser()
```

`get_shared_browser()` lazily starts one Playwright driver and one Chromium browser process for the current
Python process. This is already used by rendering utilities such as PDF/PNG generation.

The shared browser service is responsible for:

- Starting Playwright.
- Launching Chromium.
- Reusing the browser process across renderers/tools.
- Closing browser/Playwright on process shutdown when requested.

It is not responsible for per-user or per-turn isolation. That isolation happens at the BrowserContext layer.

## BrowserContext Scope

Playwright `Browser` is process-wide. Playwright `BrowserContext` is the isolation boundary.

`browser_tools` creates one BrowserContext per derived browser session:

```text
tenant + project + user_id + conversation_id + turn_id + request_id + bundle_id
```

The exact key is hashed before storage:

```text
browser:<sha256-prefix>
```

The human-readable `session_label` is returned for diagnostics, but the map key is a hash.

Why per-turn context:

- Same turn can reuse page state across `open_page`, `click`, `fill`, and `status`.
- Same turn can have multiple tabs.
- Different users/turns do not share cookies, localStorage, sessionStorage, or open pages.
- A canceled turn can be garbage-collected without affecting other active turns.

## Multiple Tabs

Each session stores a `pages` map:

```text
tab_id -> Playwright Page
```

Examples:

- `main`
- `app`
- `reference`
- `mobile`

Tabs in the same session share the same BrowserContext and therefore the same browser storage. This is intentional
for one turn. Use separate sessions only when state must be isolated.

## Tool Runtime Placement

Browser tools must run in-process:

```python
"browser_tools.open_page": "none"
```

Do not run browser tools in isolated exec or local subprocess mode. A subprocess would get its own module memory,
so the BrowserContext and tabs would not persist between tool calls.

Generated Python code inside `exec_tools.execute_code_python` should not directly control this browser session.
The browser tools are supervisor-side tools for the agent decision loop.

## Browser Feedback

The backend listens to Playwright page events:

- `console`: saved as `console_errors` when type is `error` or `warning`.
- `pageerror`: saved as `page_errors`.
- `requestfailed`: saved as `request_failures`.

Every browser action returns current page state:

- URL
- title
- document ready state
- visible body text preview
- DOM controls summary
- recent diagnostics
- optional screenshot artifact

This means an action has observable effects even without screenshot capture. Use screenshots only when visual layout,
canvas/SVG state, or responsive rendering must be inspected; screenshots add multimodal tokens.

If the browser tool itself fails, for example because a local `fi:` file cannot be resolved, ReAct renders the
`tc:` result with `status: error` and an `error` object containing `code`, `message`, and `where`.

## Screenshot Storage

Screenshots are written under `OUTPUT_DIR`, hosted with the normal tool artifact helper using `emit=false`, and returned as internal file descriptors:

```json
{
  "path": "fi:turn_x.outputs/browser_screenshots/123_main.png",
  "logical_path": "fi:turn_x.outputs/browser_screenshots/123_main.png",
  "artifact_path": "fi:turn_x.outputs/browser_screenshots/123_main.png",
  "physical_path": "turn_x/outputs/browser_screenshots/123_main.png",
  "filename": "123_main.png",
  "mime": "image/png",
  "kind": "file",
  "visibility": "internal",
  "size_bytes": 91234,
  "full_page": true
}
```

They are not returned inline as base64 in the JSON payload. ReAct renders the hosted internal `fi:` screenshot as a file block for multimodal model inspection and does not emit it to the user.

## Local File Resolution

Browser tools can open runtime files by:

- `fi:<turn>.outputs/...`
- `fi:<turn>.files/...`
- `fi:<turn>.attachments/...`
- `OUTPUT_DIR`-relative paths
- `WORKDIR`-relative paths

The backend resolves these to local file URLs only when the resolved file is under allowed runtime roots.

This is intentionally narrower than arbitrary host file access.

## Cleanup and Canceled Turns

There are three cleanup mechanisms:

1. Explicit close:

   ```json
   {"tool_id": "browser_tools.close", "params": {}}
   ```

2. Opportunistic cleanup:

   Each new browser session access closes stale sessions first.

3. Timer cleanup:

   When at least one browser session exists, the backend starts a lightweight janitor task. It wakes periodically
   and closes idle BrowserContexts. If all sessions are closed, the janitor exits.

The timer path is important for canceled or timed-out turns. If a turn is killed before the agent can call
`browser_tools.close`, its BrowserContext is still closed after it becomes idle.

Current backend defaults:

```text
session_idle_ttl: 30 minutes
janitor_interval: 60 seconds
max_sessions: 64
```

These are backend constants today. If we need runtime configurability, move them under the ReAct `ai.react`
configuration block and pass them through the same runtime config path used for context caps.

## Playwright Installation

`shared_browser.py` can auto-install Chromium on first use when configured with `auto_install_browser=True`.

The auto-install path now has a bounded timeout. If Chromium cannot be installed quickly, startup fails with a
managed error instead of hanging indefinitely.

Recommended production behavior:

- Install browser binaries in the image or runtime environment ahead of time.
- Use `PLAYWRIGHT_BROWSERS_PATH` or the image default path consistently.
- Treat auto-install as a developer convenience, not the normal production path.

## Concurrency Model

The shared Chromium process can serve multiple users. Isolation relies on BrowserContexts:

```text
Browser process
  BrowserContext: turn A / user A
    Page: main
    Page: reference
  BrowserContext: turn B / user B
    Page: main
```

The session map is guarded by an asyncio lock. Page event callbacks append bounded diagnostics to the owning
session. Event logs are capped to avoid unbounded memory growth.

## Future Extensions

Likely next tools:

- Screenshot pixel checks or visual diff helpers.
- Selector assertions.
- Accessibility tree snapshot.
- Network request inspection by URL pattern.
- Console log retrieval with filters.
- Mobile viewport presets.
- Dedicated search/browser tabs once web browsing moves through Playwright.
