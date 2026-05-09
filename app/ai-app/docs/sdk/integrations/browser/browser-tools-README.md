# Browser Tools

`browser_tools` is the SDK Playwright-backed tool namespace for browser verification from ReAct-style agents.
It is intentionally small: open a page, inspect state, click, fill, optionally take screenshots, and close tabs or
the whole turn-scoped browser session.

## Purpose

Use `browser_tools` when an agent needs feedback from a real browser, especially for generated HTML applications,
dashboards, forms, and interactive artifacts.

Typical failures this catches:

- JavaScript syntax/runtime errors that make buttons do nothing.
- Missing or wrong selectors.
- Broken navigation or local asset paths.
- Layout state that is only visible after interaction.
- Network/request failures.
- Controls that exist in source but are not actually visible/clickable.

## Registered Tool IDs

The tool module is registered as:

```python
{
    "module": "kdcube_ai_app.apps.chat.sdk.tools.browser_tools",
    "alias": "browser_tools",
    "use_sk": True,
}
```

For ReAct bundles, these tools should run in-process so browser session state survives across calls:

```python
TOOL_RUNTIME = {
    "browser_tools.open_page": "none",
    "browser_tools.click": "none",
    "browser_tools.fill": "none",
    "browser_tools.status": "none",
    "browser_tools.close": "none",
}
```

Do not route `browser_tools.*` through isolated exec. Isolated subprocess execution gets separate process memory,
so it cannot share the supervisor-managed BrowserContext and named tabs across ReAct tool calls.

This does not mean Playwright is unavailable in isolated runtime. One-shot rendering tools can use Chromium there,
and generated code may use its own local browser flow when the runtime image supports it. That is separate from the
ReAct `browser_tools` session used for cross-round verification.

## Tool Set

### `browser_tools.open_page`

Open a URL or local artifact in a named tab.

Important parameters:

- `url_or_path`: `https://...`, `http://...`, `data:...`, `file://...`, `OUTPUT_DIR`-relative path, or `fi:<turn>.outputs/...`.
- `tab_id`: named tab within the current turn-scoped browser session. Defaults to `main`.
- `wait_until`: `commit`, `domcontentloaded`, `load`, or `networkidle`. Defaults to `domcontentloaded`.
- `timeout_ms`: navigation timeout. Defaults to `10000`.
- `settle_ms`: extra delay after navigation before inspection. Defaults to `150`.
- `width`, `height`: viewport size.
- `text_limit`: visible body text preview size.
- `screenshot`: when true, writes a PNG screenshot artifact under `OUTPUT_DIR` and exposes it as an internal `fi:` file. Use sparingly because screenshots add multimodal tokens.
- `screenshot_full_page`: capture full page when true, viewport only when false.
- `screenshot_path`: optional `OUTPUT_DIR`-relative screenshot path.
- `session_id`: optional explicit session id. Omit for normal turn-scoped behavior.

### `browser_tools.click`

Click a CSS selector in an already-open tab and return updated diagnostics.

Important parameters:

- `selector`: CSS selector, for example `#run`, `.tab[data-id="q"]`, or `button:nth-of-type(2)`.
- `tab_id`: tab to use. Defaults to `main`.
- `timeout_ms`, `settle_ms`, `text_limit`.
- `screenshot`, `screenshot_full_page`, `screenshot_path`; keep `screenshot=false` unless visual state/layout matters.

### `browser_tools.fill`

Fill an input-like element and return updated diagnostics.

Important parameters:

- `selector`: CSS selector for `input`, `textarea`, or compatible editable field.
- `text`: text to fill.
- `tab_id`, `timeout_ms`, `settle_ms`, `text_limit`.
- `screenshot`, `screenshot_full_page`, `screenshot_path`; keep `screenshot=false` unless visual state/layout matters.

### `browser_tools.status`

Inspect an already-open tab without changing it.

Use this after delayed page updates, after multiple actions, or when a screenshot is needed without another click. Screenshots are internal image artifacts and should be requested only when DOM diagnostics are insufficient.

### `browser_tools.close`

Close a tab or the whole current browser session.

- If `tab_id` is provided, only that tab is closed.
- If `tab_id` is omitted, the full current session is closed.

Call this when a browser workflow is complete if the agent has a chance to do so. If a turn is canceled before
explicit close, the backend idle janitor closes stale contexts later.

## Result Shape

The raw `browser_tools` SK callables return the SDK envelope:

```json
{
  "ok": true,
  "error": null,
  "ret": { "...": "..." }
}
```

In ReAct execution, the runtime recognizes `{ok,error,ret}` as an optional tool envelope and unwraps successful
results to the inner `ret` payload. Do not rely on every rendered tool result having top-level `ok`, `error`, and
`ret`.

Errors are still explicit. If the callable returns `ok=false` or the runtime catches a call error, the rendered
`tc:` result metadata includes:

```json
{
  "status": "error",
  "error": {
    "code": "FileNotFoundError",
    "message": "missing.html",
    "where": "browser_tools.open_page"
  }
}
```

Browser-page diagnostics inside a successful payload, such as `page_errors` or `console_errors`, are page
feedback. They do not necessarily mean the tool call itself failed.

The page-action payload includes:

```json
{
  "session_key": "browser:<hash>",
  "session_label": "tenant=...|project=...|user_id=...|conversation_id=...|turn_id=...",
  "tab_id": "main",
  "open_tabs": ["main", "reference"],
  "url": "file:///...",
  "title": "Page title",
  "ready_state": "complete",
  "resolved": {
    "kind": "file",
    "path": "/absolute/runtime/path/app.html",
    "size_bytes": 52039
  },
  "text_preview": "Visible body text...",
  "text_symbols": 1234,
  "text_truncated": false,
  "screenshot": {
    "path": "fi:turn_x.outputs/browser_screenshots/123_main.png",
    "logical_path": "fi:turn_x.outputs/browser_screenshots/123_main.png",
    "artifact_path": "fi:turn_x.outputs/browser_screenshots/123_main.png",
    "physical_path": "turn_x/outputs/browser_screenshots/123_main.png",
    "filename": "123_main.png",
    "mime": "image/png",
    "kind": "file",
    "visibility": "internal",
    "size_bytes": 91234,
    "full_page": true,
    "description": "Browser screenshot captured for visual verification."
  },
  "artifact_type": "files",
  "files": [
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
  ],
  "controls": [
    {
      "index": 0,
      "tag": "button",
      "id": "run",
      "classes": "primary",
      "text": "Run",
      "type": "",
      "href": "",
      "visible": true,
      "selector_hint": "#run"
    }
  ],
  "console_errors": [],
  "page_errors": [],
  "request_failures": []
}
```

`screenshot` is `null` unless the action requested `screenshot=true`.

## Feedback Model

A browser action is useful even without a screenshot. The agent receives:

- `page_errors`: uncaught JavaScript errors, including many syntax/runtime errors.
- `console_errors`: browser console warnings/errors.
- `request_failures`: failed resource/API requests.
- `controls`: clickable/fillable elements discovered from DOM.
- `text_preview`: visible page text after the action.
- `ready_state`, `url`, and `title`: basic page state.

Screenshots add visual feedback, but they add multimodal tokens. Prefer DOM diagnostics first and request screenshots only for:

- Layout overlap, clipping, and viewport issues.
- State that is visible but not obvious in text.
- Canvas/SVG rendering.
- Responsive checks with different viewport dimensions.

## Recommended ReAct Flow

For generated interactive HTML:

1. Generate or update the HTML artifact.
2. Open it:

   ```json
   {
     "tool_id": "browser_tools.open_page",
     "params": {
       "url_or_path": "fi:turn_x.outputs/app.html",
       "tab_id": "main",
       "screenshot": false,
       "width": 1280,
       "height": 900
     }
   }
   ```

3. Inspect `page_errors`, `console_errors`, `request_failures`, and `controls`.
4. If there are errors, fix the artifact before claiming success. Request a screenshot only when the DOM diagnostics do not answer a visual/layout question.
5. Use returned `selector_hint` values when available:

   ```json
   {
     "tool_id": "browser_tools.click",
     "params": {
       "selector": "#run",
       "tab_id": "main",
       "screenshot": false
     }
   }
   ```

6. Repeat for important controls and states.
7. Use `browser_tools.status` when waiting/inspecting without another action; set `screenshot:true` only for visual state that needs multimodal inspection.
8. Close the session when done:

   ```json
   {
     "tool_id": "browser_tools.close",
     "params": {}
   }
   ```

## Multiple Tabs

Use distinct `tab_id` values inside the same turn:

```json
{"tab_id": "app"}
{"tab_id": "reference"}
```

Tabs share cookies/localStorage because they are in the same turn-scoped BrowserContext. Other users/turns get
different BrowserContexts.

## Local File Access

The browser tool can open:

- HTTP/HTTPS/data URLs.
- `fi:<turn>.outputs/...`, `fi:<turn>.files/...`, and `fi:<turn>.attachments/...`.
- Paths relative to the current runtime `OUTPUT_DIR` or `WORKDIR`.
- `file://` URLs only when they resolve under allowed runtime roots.

Absolute local paths outside runtime roots are refused. This prevents a generated agent action from browsing
arbitrary host files.

## Screenshots

Screenshots are stored as internal artifacts under `OUTPUT_DIR`, not inlined as base64 in the JSON tool result. The tool hosts the screenshot with the normal `host_files(..., emit=false)` helper, then ReAct emits it as an internal `fi:` file block so the model can inspect it as multimodal content when the renderer keeps it visible. Internal screenshots are not sent to the user as files.

Default path:

```text
<turn_id>/outputs/browser_screenshots/<timestamp>_<tab_id>.png
```

Returned logical path:

```text
fi:<turn_id>.outputs/browser_screenshots/<timestamp>_<tab_id>.png
```

If `screenshot_path` is supplied, it must remain `OUTPUT_DIR`-relative.

## Cleanup

There are three cleanup paths:

- Explicit: `browser_tools.close`.
- Turn lifecycle: the chatbot workflow and processor finalizer close the current turn browser session on normal
  completion, managed errors, watchdog timeouts, and processor cancellation.
- Automatic: a backend janitor task closes idle sessions after the configured backend TTL.

The lifecycle/finalizer path is the normal cleanup path. The janitor remains a fallback for process interruption,
hard kill, or paths that end before cleanup can run.
