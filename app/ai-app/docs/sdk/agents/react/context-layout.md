---
id: ks:docs/sdk/agents/react/context-layout.md
title: "Context Layout"
summary: "Rendered block stream format that agents receive each turn."
tags: ["sdk", "agents", "react", "context", "layout"]
keywords: ["block stream", "rendered context", "agent view", "blocks order"]
see_also:
  - ks:docs/sdk/agents/react/context-browser-README.md
  - ks:docs/sdk/agents/react/context-progression.md
  - ks:docs/sdk/agents/react/react-context-README.md
---
# Context Layout (Blocks)

This document describes the **rendered block stream** that agents receive and how it is
assembled each turn.

## Block Stream (per agent call)
1) **History blocks**
   - Built from timeline (including any `conv.range.summary` blocks).
   - May include preserved Internal Memory Beacons (`react.note.preserved`) directly after a summary.
   - Includes prior turns (user → contributions → assistant → tool calls/results).
   - May include `turn.feedback` blocks injected as the **last block of a turn** (cache cold only).

2) **Current turn user blocks**
   - Current user prompt + attachments.

3) **Turn progress blocks**
   - Added by downstream agents (e.g., `react.notes`, `react.note`, tool call/result blocks).
   - Appended via `ContextBrowser.contribute(...)` and persisted into the turn log.

4) **Sources pool block** (optional)
   - Only included if `timeline.render(include_sources=True)`.
   - Always appended at the tail; never cached.

5) **Announce block** (optional)
   - Only included if `timeline.render(include_announce=True)`.
   - Always appended at the tail; never cached.
   - Contains `[NEW USER FEEDBACKS]` / `[NEW FEEDBACKS]` when feedback is fetched on turn start
     and cache is hot (not injected).

---

## Caching (exact behavior)
Cache points are placed **inside the stable stream** (history + current + contributions).
Tail blocks (sources/announce) are never cached.

**Cache points (in order):**
1) **Previous‑turn cache point** — last block before the current turn header (if present).
2) **Pre‑tail cache point** — last block of round `N‑4` (`cache_point_offset_rounds=4`).
3) **Tail cache point** — last block in the visible stream.

This ensures prompt caching is stable even when turns are edited or hidden.

---

## Schematic Layout
```
┌──────────────────────────┐
│ RANGE SUMMARIES          │
│  - conv.range.summary    │
│  - react.note.preserved  │
└──────────────────────────┘
┌──────────────────────────┐
│ HISTORY BLOCKS           │
│  - prior user prompt     │
│  - prior contributions   │
│  - prior assistant       │
│  - tool call/result      │
└──────────────────────────┘
┌──────────────────────────┐
│ CURRENT TURN USER BLOCKS │
│  - user prompt           │
│  - user attachments      │
└──────────────────────────┘
┌──────────────────────────┐
│ TURN PROGRESS LOG        │
│  - gate/react notes      │
│  - internal beacons      │
│  - tool call/result      │
└──────────────────────────┘
┌──────────────────────────┐
│ SOURCES POOL (optional)  │  ← uncached tail
└──────────────────────────┘
┌──────────────────────────┐
│ ANNOUNCE (optional)      │  ← uncached tail
└──────────────────────────┘
```

---

## Compaction Notes (high‑level)
When compaction runs, a `conv.range.summary` block is inserted at a cut point.
`timeline.render(...)` then slices the visible stream **from the latest summary onward**.
Internal Memory Beacons are copied after the summary as `react.note.preserved`, so those anchors remain visible.
Other older blocks remain in storage but are hidden from the rendered context.

See `compaction-README.md` for exact cut‑point rules.

---

## Stable Paths (concrete, no “current_turn”)
All paths use a concrete `turn_id` and standard `tool_call_id` (always `tc_<id>`):

- `ar:<turn_id>.user.prompt`
- `ar:<turn_id>.assistant.completion`
- `ar:<turn_id>.react.notes.<tool_call_id>`
- `ar:<turn_id>.react.note.preserved.<idx>`
- `fi:<turn_id>.user.attachments/<name>`
- `fi:<turn_id>.files/<relative_path>`
- `fi:<turn_id>.code.<tool_call_id>`
- `tc:<turn_id>.<tool_call_id>.call`
- `tc:<turn_id>.<tool_call_id>.result`
- `tc:<turn_id>.tool_calls.<tool_call_id>.notice.json`
- `so:sources_pool[...]`

---

## Visible Timeline (rendered order)
Blocks are rendered **oldest → newest** (newest at bottom). Each turn begins with:

```
[TURN <turn_id>] ts=<iso>
```

### Example (rendered)
```
[TURN turn_1770603271112_2yz1lp] ts=2026-02-09T02:14:32.676425Z

[USER MESSAGE]
[path: ar:turn_1770603271112_2yz1lp.user.prompt]
Could you find top 3 places to eat here in Wuppertal?

[USER ATTACHMENT] menu.pdf | application/pdf
summary: 2‑page menu, prices & address (Wuppertal)
[path: fi:turn_1770603271112_2yz1lp.user.attachments/menu.pdf]
[physical_path: turn_1770603271112_2yz1lp/attachments/menu.pdf]

<document media_type=application/pdf b64_len=183942>

[AI Agent say]: Searching for top‑rated restaurants in Wuppertal

[TOOL CALL tc_18f62649fb3b].call web_tools.web_search
tc:turn_1770603271112_2yz1lp.tc_18f62649fb3b.call
Params:
{ ... }

[TOOL RESULT tc_18f62649fb3b].result web_tools.web_search
logical_path: so:sources_pool[1-5]
...
```

Notes:
- Artifact‑producing tools render **summary + artifact** blocks.
- Non‑artifact tools render a single `.result` block with `logical_path`.
- For file artifacts, the content block uses `fi:<turn_id>.files/...`; hosted metadata stays in `meta`.
- Feedback updates are fetched **only at turn start** (timeline load). If cache is cold, they are injected
  into the target turn and still announced once with “(incorporated into turn timeline)”.
- Internal Memory Beacons render as `[INTERNAL NOTE]` blocks and are part of the model-visible timeline.

---

## Agent Contribution Example
```python
block = {
    "type": "react.notes",
    "author": "react",
    "turn_id": scratchpad.turn_id,
    "ts": scratchpad.started_at,
    "mime": "text/markdown",
    "text": "short progress note",
    "meta": {"channel": "timeline_text"},
}
ctx_browser.contribute(blocks=[block])
```

---

## Contribution Filters
ContextBrowser can filter contribution blocks centrally:
- `exclude_contributions=[...]` hides matching `type` values.
- `include_only_contributions=[...]` allows only matching `type` values.

Filtering applies to both:
- historical reconstruction
- current‑turn contributions

---

## Retry with Compaction (example)
```python
blocks = ctx_browser.timeline.render(
    include_sources=True,
    include_announce=True,
    cache_last=True,
)
try:
    await agent_call(blocks=blocks)
except ServiceException as exc:
    if is_context_limit_error(exc.err):
        blocks = ctx_browser.timeline.render(
            include_sources=True,
            include_announce=True,
            cache_last=True,
            force_sanitize=True,
        )
        await agent_call(blocks=blocks)
    else:
        raise
```
