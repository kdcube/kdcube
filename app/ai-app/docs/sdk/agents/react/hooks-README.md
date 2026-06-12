---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/hooks-README.md
title: "Hooks"
summary: "Runtime hooks available in the v2 flow."
tags: ["sdk", "agents", "react", "hooks"]
keywords: ["before/after hooks", "ContextBrowser hooks", "compaction hooks"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-discovery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-storage-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/compaction-README.md
---
# Hooks (v2)

This doc lists runtime hooks available in the v2 flow.

---

## ContextBrowser Hooks

Configured via `ContextBrowser.set_runtime_context(...)`.

### on_before_compaction
Called before context compaction runs.

Payload:
- `before_tokens`: estimated token count of current blocks

### on_after_compaction
Called after compaction completes.

Payload:
- `before_tokens`
- `after_tokens`
- `compacted_tokens` (difference)

Typical use:
- emit user-visible status updates ("compacting" / "back to work")

---

## Example

```python
async def _before(payload):
    await emit_status(["compacting", "organizing the thread"])

async def _after(payload):
    await emit_status(["back to work", "continuing"])

ctx_browser.set_runtime_context(
    ...,
    on_before_compaction=_before,
    on_after_compaction=_after,
)
```
