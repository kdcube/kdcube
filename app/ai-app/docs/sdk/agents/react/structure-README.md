---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/structure-README.md
title: "Structure"
summary: "Module layout of the shared React runtime surface, production v2 implementation, and experimental v3 implementation."
tags: ["sdk", "agents", "react", "structure"]
keywords: ["react/v2", "react/v3", "modules", "runtime layout", "core files", "workspace docs", "rationale docs"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/timeline-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/runtime-configuration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/flow-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/workspace/git-based-isolated-workspace-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/why/why-not-simply-tool-calling-README.md
---
# React Structure

React now has a shared runtime surface plus two versioned implementations:

- `solutions/react/proto.py`
  - shared contracts such as `RuntimeCtx`
- `solutions/react/v2/`
  - production runtime
  - one response = one round
  - one round = one action or one final answer
- `solutions/react/v3/`
  - experimental runtime
  - same timeline/workspace model
  - optional multi-action mode where repeated `ReactDecisionOutV2` action blocks can be accepted in one response and executed sequentially

## Runtime Modules

Each runtime version keeps the same core module layout:

- `runtime.py` — state machine and decision loop
- `timeline.py` — timeline storage, rendering, pruning, and compaction
- `round.py` — round lifecycle, decision attempts, and tool-call/result grouping
- `browser.py` — context loading, timeline contribution, external-event folding, and live listener ownership
- `layout.py` — block builders and rendered views
- `tools/` — React-only tools such as `react.read`, `react.write`, `react.patch`, `react.pull`, `react.checkout`, `react.memsearch`, and `react.hide`
- `artifacts.py` — artifact helpers and logical-path views

Adjacent shared modules:

- `solutions/widgets/`
  - canvas, timeline, and exec streamers
- `sdk/streaming/`
  - channel stream parsing
  - `versatile_streamer.py` for the current shared path
  - `versatile_streamer_v3.py` for v3 multi-instance channel handling

## Shared Runtime Shape

Both versions are still timeline-first:

1. render current context from timeline + sources pool + announce
2. run one decision model call
3. execute accepted actions
4. append blocks back into the same timeline
5. persist timeline, sources pool, and turn log on turn finish

The important difference is only the decision contract:

- `v2`: exactly one action channel instance per response
- `v3`: production-compatible single-action mode by default, plus experimental multi-action mode

## Docs Map

The React docs are now organized into four layers:

- root `react/`
  - runtime, timeline, context, tools, flow, round model
- `react/workspace/`
  - core workspace capability docs such as git-backed isolated workspaces and checkout semantics
- `react/why/`
  - rationale/origin docs such as memory architecture and why React is not built as plain provider-native tool calling
- `react/design/` and `react/draft/`
  - remaining proposals and unfinished notes

## Block Flow

The decision prompt is built from:

1. visible timeline slice
2. current-turn blocks, attachments, and preserved summaries
3. sources pool and announce tail
4. per-round blocks such as `react.round.start`, `react.tool.call`, `react.tool.result`, `react.notice`, and `assistant.completion`

Rendered tool output still uses the compact view documented in `tool-call-blocks-README.md`.
