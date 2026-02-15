# React v2 Structure

All v2 runtime code lives under `react/v2/`.

## Core Modules
- `runtime.py` — state machine and decision loop. Orchestrates rounds and updates context blocks.
- `timeline.py` — timeline storage, rendering, and cache/TLL pruning.
- `round.py` — ReactRound (single tool call orchestration; emits blocks; updates sources pool).
- `tools/` — React-only tool implementations + catalog:
  - `react.read`
  - `react.write`
  - `react.patch`
  - `react.memsearch`
  - `react.hide`
- `artifacts.py` — ArtifactView helpers (paths, visibility, kind, rendering).
- `layout.py` — block builders for stage blocks + tool catalog.


## Block Flow (Decision Input)
The decision prompt is built from the timeline:
1) history blocks (prior turns + summaries)
2) current turn blocks (user prompt + attachments + stage blocks)
3) per-round tool blocks (react.notes + react.tool.call/result)
4) sources pool + announce (tail, uncached)

Caching rule: two cache points are inserted in the stable stream (see `context-caching-README.md`).

## Context Compaction
The timeline can compact older turns into summary blocks:
- `conv.range.summary` blocks contain a summary + `meta.covered_turn_ids`.
- Summaries are stored in the conversation index (tag: `conv.range.summary`).
- On turn start, ContextBrowser loads recent summaries + turns after the latest summary.

## Context Safety (SDK Example)
SDK flows can use `timeline.render(...)` and retry-on-limit logic with `force_sanitize=True`.

## Storage Rules
Tool outputs are persisted in timeline blocks.
Files are written to disk only when:
- exec tools produce files
- `react.write(kind=file)` or `rendering_tools.write_*`

For exact block shapes, see:
`tool-call-blocks-README.md`
