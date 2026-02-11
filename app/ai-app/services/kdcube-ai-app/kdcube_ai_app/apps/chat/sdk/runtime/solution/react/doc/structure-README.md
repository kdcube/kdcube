# React v2 Structure

All v2 runtime code lives under `react/v2/`.

## Core Modules
- `runtime.py` — state machine and decision loop. Orchestrates rounds and updates context blocks.
- `context.py` — persistent ReactContext. Owns `history_blocks`, `current_turn_blocks`, `event_blocks`, `sources_pool`, and tool call registry.
- `round.py` — ReactRound (single tool call orchestration; emits blocks; updates sources pool).
- `tools.py` — React-only tool implementations + catalog:
  - `react.read`
  - `react.stream`
  - `react.file`
  - `react.patch`
  - `react.memory_read`
- `artifacts.py` — ArtifactView helpers (paths, visibility, kind, rendering).
- `layout.py` — block builders for history/current turn/stage blocks + sources catalog.


## Block Flow (Decision Input)
1) `history_blocks` — prior turns (user, react log, assistant), built once by ContextBrowser.
2) `current_turn_blocks` — user prompt + attachments + coordinator plan, built once per turn.
3) `event_blocks` — appended per React round (tool call/result; react.completion).
4) Sources pool + active state blocks — built per decision call, always uncached.

Caching rule: only the **last React-produced block** is cached.

## Context Compaction
ContextBrowser can compact older turns into summary blocks:
- `conv.range.summary` blocks contain a summary + `meta.covered_turn_ids`.
- Summaries are stored in the conversation index (tag: `conv.range.summary`).
- On turn start, ContextBrowser loads recent summaries + turns after the latest summary.

## Context Safety (SDK Example)
SDK flows can use ContextBrowser’s timeline view and retry-on-limit logic.
Example: the built-in coordinator/decision calls `timeline(...)`, and retries once with
`force_sanitize=True` on a context-limit error.

Notes:
- Downstream agents may append stage blocks, so the shared prefix can grow.
- Mid-turn compaction can occur during a later decision; this rewrites the cached
  block stream and inserts a new summary block (prefix changes).

## Storage Rules
Tool outputs are persisted in `event_blocks` (react log). Files are written to disk only when:
- exec tools produce files
- `react.file` / rendering_tools.write_* produce files

Everything else stays in the log.
