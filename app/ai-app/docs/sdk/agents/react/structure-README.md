---
id: ks:docs/sdk/agents/react/structure-README.md
title: "Structure"
summary: "Module layout of React v2 runtime."
tags: ["sdk", "agents", "react", "structure"]
keywords: ["react/v2", "modules", "runtime layout", "core files"]
see_also:
  - ks:docs/sdk/agents/react/timeline-README.md
  - ks:docs/sdk/agents/react/context-browser-README.md
  - ks:docs/sdk/agents/react/context-progression.md
---
# React v2 Structure

All v2 runtime code lives under `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/`.

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

Rendered tool output uses a compact view:
- `[TOOL CALL <id>].call <tool_id>` + `tc:<turn_id>.<tool_call_id>.call`
- `[TOOL RESULT <id>].summary <tool_id>` for artifact-producing tools
- `[TOOL RESULT <id>].result <tool_id>` for non‑artifact tools
- `[TOOL RESULT <id>].artifact <tool_id>` per artifact (logical_path + content)

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
