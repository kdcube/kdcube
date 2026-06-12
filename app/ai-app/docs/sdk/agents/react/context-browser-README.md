---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-browser-README.md
title: "Context Browser"
summary: "ContextBrowser builds the per‑turn timeline view and collects new blocks."
tags: ["sdk", "agents", "react", "context", "timeline"]
keywords: ["ContextBrowser", "timeline", "blocks", "turn_id", "agent contributions"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-layout.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-progression.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
---
# Context Browser (v2)

`ContextBrowser` owns the **timeline** used by all agents in a turn.
It loads historical turns, builds the current‑turn user blocks, and lets agents contribute
progress blocks that become part of the in‑turn log.

Relevant code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/browser.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/context/retrieval/ctx_rag.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/layout.py` (block formatting)

---

## 1) Create a ContextBrowser

```python
from kdcube_ai_app.apps.chat.sdk.solutions.browser import ContextBrowser

browser = ContextBrowser(
    ctx_client=self.ctx_client,
    logger=self.logger,
    turn_view_class=TurnView,
    model_service=self.model_service,
)
```

## 2) Set runtime context (once per turn)

```python
browser.set_runtime_context(
    tenant=tenant,
    project=project,
    user_id=user_id,
    conversation_id=conversation_id,
    user_type=user_type,
    turn_id=turn_id,
    bundle_id=bundle_id,
    max_tokens=max_tokens,
)
```

## 3) Load context (history + current user blocks)

```python
bundle = await browser.load_context(
    scratchpad=scratchpad,
    limit=8,
    days=365,
)
```

This builds and caches:
- `history_blocks` (prior turns + summaries)
- `current_turn_blocks` (user prompt + attachments)

Later same-turn user additions (`user.followup`, `user.steer`) and any additional
`assistant.completion` blocks are contributed into the same timeline/turn log; they are not a
separate fetch-only layer.

## 4) Get the timeline for an agent call

```python
blocks = await browser.timeline(
    conversation_id=conversation_id,
    turn_id=turn_id,
    include_sources=True,
)
```

If the agent hits a context limit, retry with compaction:

```python
blocks = await browser.timeline(
    conversation_id=conversation_id,
    turn_id=turn_id,
    include_sources=True,
    force_sanitize=True,
)
```

## 5) Contribute in‑turn progress blocks

Any agent can append progress blocks. These show up in the timeline and can be
persisted into the turn log for next‑turn reconstruction.

That same persistence path is also what later produces:
- multiple prompt-like `chat:user` entries from one turn
- multiple visible `chat:assistant` completions from one turn

```python
block = {
  "type": "stage.gate",
  "author": "gate",
  "turn_id": turn_id,
  "ts": scratchpad.started_at,
  "mime": "text/markdown",
  "text": "[STAGE: GATE OUTPUT]\nroute: tools_general",
  "path": f"ar:{turn_id}.stage.gate",
}

browser.contribute(
    scratchpad=scratchpad,
    blocks=[block],
    persist=True,
)
```

Special helpers:
- `contribute_feedback(...)`
- `contribute_clarification(...)`
- `contribute_clarification_resolution(...)`

## 6) Announce blocks (ephemeral)

```python
browser.announce(
    scratchpad=scratchpad,
    blocks=[{"type": "announce", "author": "system", "text": "[ACTIVE STATE] ..."}],
)
```

Pass `include_announce=True` to `timeline(...)` to include them.

## 7) Sources pool (ephemeral)

Use `browser.set_sources_pool(...)` to update the current sources pool.
Pass `include_sources=True` to `timeline(...)` to include it.

## 8) External Owner Refs

`ContextBrowser` supplies the current runtime scope to ReAct tools. For native
timeline/artifact refs, `react.read` reopens blocks directly. For external
owner refs such as `mem:` or `cnv:`, exact content is imported with
`react.pull` through the loaded `EventSourceSubsystem` namespace rehoster:

```text
react.pull(paths=["mem:mem_123"])
  -> artifact_namespace_rehoster(namespace="mem")
  -> owner uses current tenant/project/user scope
  -> owner mirrors content into the ReAct artifact workspace
  -> pull returns fi:/physical paths for later read/search/exec
```

This keeps ContextBrowser generic. It does not know how to render memory,
canvas, task, or knowledge objects. The module that owns the namespace must
register the rehoster and any policies that render visible previews elsewhere.

---

## Cache points (round‑based)
When `timeline.render(...)` is called, it inserts **two cache points** in the stable
timeline window (see `context-caching-README.md`). These are computed by **rounds** (tool_call_id
plus the final completion round) using:
- `RuntimeCtx.cache.cache_point_min_rounds`
- `RuntimeCtx.cache.cache_point_offset_rounds`

This cache window also bounds `react.hide`: paths **before** the pre‑tail cache point cannot be hidden.

## Notes on compaction
- `timeline(force_sanitize=True)` triggers compaction and inserts `conv.range.summary`.
- Internal Memory Beacons from the compacted region are preserved after that summary as `react.note.preserved`.
- Summaries are stored in the index and **not** in the turn log.
- Beacons themselves remain ordinary timeline / turn-log blocks rather than standalone conversation artifacts.

See also:
- `context-layout.md`
- `context-progression.md`
- `conversation-artifacts-README.md`
