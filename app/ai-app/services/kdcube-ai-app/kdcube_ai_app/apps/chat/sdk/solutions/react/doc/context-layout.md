# Context Layout (Blocks)

This describes the block stream that agents receive.

## Block Stream (per agent call)
1) **History blocks**  
   Built once per turn by `ContextBrowser.load_timeline()`.  
   Includes prior turns (user → contributions → assistant) plus any stored `conv.range.summary` blocks.

2) **Current turn user blocks**  
   Built once per turn.  
   Includes current user prompt + attachments.

3) **Turn progress blocks**  
   Any downstream agent can append progress contributions (e.g., gate blocks, `react.notes`).  
   These are appended via `ContextBrowser.contribute(...)` and appear in the same stream.  
   By default they are persisted into the turn log for reconstruction next turn.

4) **Sources pool block** (uncached; optional via `timeline.render(include_sources=True)`).

5) **Announce block** (uncached; optional via `timeline.render(include_announce=True)`; active until explicitly cleared).

Caching: two cache checkpoints are applied inside the stable stream (history + current + contributions).  
Tail blocks (sources/announce) are never cached.

## Schematic Layout

```
┌──────────────────────────┐
│ RANGE SUMMARIES          │
│  - conv.range.summary    │
└──────────────────────────┘
┌──────────────────────────┐
│ HISTORY BLOCKS           │
│  - prior user prompt     │
│  - prior contributions   │
│  - prior assistant       │
└──────────────────────────┘
┌──────────────────────────┐
│ CURRENT TURN USER BLOCKS │
│  - user prompt           │
│  - user attachments      │
└──────────────────────────┘
┌──────────────────────────┐
│ TURN PROGRESS LOG        │
│  - gate/react            │
│  - react.notes           │
└──────────────────────────┘
┌──────────────────────────┐
│ SOURCES POOL (optional)  │  ← uncached tail
└──────────────────────────┘
┌──────────────────────────┐
│ ANNOUNCE (optional)      │  ← uncached tail
└──────────────────────────┘
```

### Announce Example (rendered)
```
╔══════════════════════════════════════╗
║  ANNOUNCE — Iteration 3/15          ║
╚══════════════════════════════════════╝

[BUDGET]
  iterations  ███░░░░░░░  12 remaining
  time_elapsed_in_turn   2m15s

[AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)]
  user_timezone: Europe/Berlin
  current_utc_timestamp: 2026-02-10T13:55:01Z
  current_utc_date: 2026-02-10
  All relative dates MUST be interpreted against this context.

[ACTIVE PLAN]
  - plans:
    • plan #1 (current) last=2026-02-07T19:22:10Z
      ✓ [1] gather sources
      □ [2] draft report
  - plan_status: done=1 failed=0 pending=1
  - plan_complete: false

[SYSTEM MESSAGE]
  Context was pruned because the session TTL (300s) was exceeded.
  Use react.read(path) to restore a logical path (fi:/ar:/so:/sk:).
```

Caching: tail blocks are always uncached. Cache checkpoints are placed by the browser (see context-caching.md).

## Compaction Notes
When compaction runs, `conv.range.summary` is inserted at a cut point.
`timeline.render(...)` slices the visible stream from the **latest** summary onward,
so older blocks remain in the timeline (storage) but are hidden from the rendered context.
Summaries are stored in the index (not in the turn log).

## Stable Paths
All paths use concrete `turn_id` (no `current_turn` namespace):
- `ar:<turn_id>.user.prompt` / `ar:<turn_id>.assistant.completion`
- `ar:<turn_id>.react.notes.<tool_call_id>`
- `fi:<turn_id>.user.attachments/<name>`
- `fi:<turn_id>.files/<relative_path>`
- `tc:<turn_id>.tool_calls.<id>.in.json` / `.out.json`
- `so:sources_pool[...]`

## Visible Timeline (rendered order)
Blocks are rendered **oldest → newest** (newest at bottom).  
Each turn begins with:

```
[TURN <turn_id>] ts=<iso>
```

### Example (rendered)
```
[TURN turn_1770603271112_2yz1lp] ts=2026-02-09T02:14:32.676425Z

[USER MESSAGE]
[path: ar:turn_1770603271112_2yz1lp.user.prompt]
could you find top 3 places to eat here in wuppertal

[USER ATTACHMENT] menu.pdf | application/pdf
summary: 2‑page menu, prices & address (Wuppertal)
[path: fi:turn_1770603271112_2yz1lp.user.attachments/menu.pdf]
[physical_path: turn_1770603271112_2yz1lp/attachments/menu.pdf]

<document media_type=application/pdf b64_len=183942>

[STAGE: GATE OUTPUT]
route: answer
conversation_title: Wuppertal dining
needs_clarification: False

[REACT.PLAN]
id=plan_abc
□ 1) Search top restaurants
□ 2) Cross‑check ratings
□ 3) Draft ranked list

[AI Agent say]: searching for top-rated restaurants in Wuppertal

[react.tool.call] (JSON)
{
  "tool_id": "web_tools.web_search",
  "tool_call_id": "18f62649fb3b",
  "params": { ... }
}

[react.tool.result] (JSON meta)
{
  "artifact_path": "tc:turn_1770603271112_2yz1lp.tool_calls.18f62649fb3b.out.json",
  "tool_call_id": "18f62649fb3b",
  "mime": "application/json"
}

[react.tool.call] (JSON)
{
  "tool_id": "react.patch",
  "tool_call_id": "6a3f1e0d9b21",
  "reasoning": "...",
  "params": { "path": "turn_1770603271112_2yz1lp/files/draft.md", "patch": "..." }
}

[react.tool.result] (JSON meta)
{
  "artifact_path": "fi:turn_1770603271112_2yz1lp.files/draft.md",
  "physical_path": "turn_1770603271112_2yz1lp/files/draft.md",
  "tool_call_id": "6a3f1e0d9b21",
  "mime": "text/markdown",
  "edited": true
}
```

## Agent Contribution Example
An agent can append blocks using a formatter:

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
ctx_browser.contribute(
    blocks=[block],
)
```

## Contribution Filters
ContextBrowser can filter contribution blocks centrally:
- `exclude_contributions=[...]` hides matching `type` values.
- `include_only_contributions=[...]` allows only matching `type` values.

Filtering applies to both:
- historical reconstruction
- current-turn contributions

Example: use timeline context with retry-on-limit
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
