# Runtime modes for built-in tools

This document explains where built-in tools run (in-process vs isolated subprocess vs Docker), how to change the default, and how to wire new tools.

## Runtime modes

- `none` (in-process): tool code runs in the main server process.
- `local` (isolated subprocess): tool code runs in a separate Python process on the same host (no supervisor).
- `local_network` (reserved): same as `local` but with a dedicated network namespace (not used yet).
- `docker`: tool code runs inside a Docker container with a **supervisor** that executes tools. The exec sandbox itself is locked down (no network, no secrets, limited FS).

The runtime selector lives in `kdcube_ai_app/apps/chat/sdk/tools/tools_insights.py` (`tool_isolation`).

## Current defaults for built-in tools

These are the current defaults as of Jan 26, 2026:

- Web tools (network + native deps) run in **isolated subprocess** (`local`):
  - `generic_tools.web_search`
  - `generic_tools.fetch_url_contents`
- Write tools (file outputs) run in **isolated subprocess** (`local`):
  - `generic_tools.write_pdf`, `write_pptx`, `write_docx`, `write_html`, `write_png`, `write_xlsx`, `write_file`
- Everything else defaults to **in-process** (`none`) unless `should_isolate_in_docker` is enabled.

## Why web tools are isolated

Native libraries (HTML parsers, PDFs, browser bindings) can crash the process. Isolation keeps the main server alive even if a tool segfaults or calls `free()` incorrectly.

## How isolation works (high level)

Execution uses the ISO runtime (`kdcube_ai_app/apps/chat/sdk/runtime/iso_runtime.py`). For `local`, it spawns a standalone subprocess via `py_code_exec_entry.py`. For `docker`, the supervisor runs inside the container and brokers tool execution while the exec sandbox stays restricted (no network, no secrets).

Sources are merged back into the main sources pool; artifacts and logs are recorded in the same way as in-process tools.

## Changing runtime for a tool

1) Update `should_isolate_tool_execution` and/or `should_isolate_in_docker` in:
   - `kdcube_ai_app/apps/chat/sdk/tools/tools_insights.py`

2) Optionally add tool-level policy in your bundle (if you expose a custom tool registry later).

## Example: isolating a custom tool

```python
# in tools_insights.py
CUSTOM_ISOLATED = {"my_tools.my_heavy_tool"}

def should_isolate_tool_execution(tool_id: str) -> bool:
    return (
        should_isolate_in_docker(tool_id)
        or is_write_tool(tool_id)
        or is_search_tool(tool_id)
        or is_fetch_uri_content_tool(tool_id)
        or tool_id in CUSTOM_ISOLATED
    )
```
