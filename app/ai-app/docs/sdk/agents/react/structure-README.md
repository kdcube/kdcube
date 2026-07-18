---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/structure-README.md
title: "ReAct Adapter Structure"
summary: "Module layout of the ReAct adapter, its shared harness dependencies, and the v2/v3 decision runtimes."
tags: ["sdk", "agents", "react", "structure"]
keywords: ["react/v2", "react/v3", "modules", "runtime layout", "core files", "workspace docs", "rationale docs"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/timeline-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/runtime-configuration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/flow-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/workspace/git-backed-workspace-engineering-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/why/why-not-simply-tool-calling-README.md
---
# ReAct Adapter Structure

ReAct is a framework adapter over the shared
[Agent Harness Runtime](../../../runtime/harness/README.md), plus two
versioned ReAct implementations:

- `solutions/react/proto.py`
  - ReAct runtime contracts such as `RuntimeCtx`
- `solutions/react/v2/`
  - production runtime
  - one response = one round
  - one round = one action or one final answer
- `solutions/react/v3/`
  - the current default runtime for the built-in apps
  - same timeline/workspace model
  - real-time action governance (the action overseer gates each streamed action lane; see `round-generation-feedback-README.md`)
  - optional multi-action mode where repeated action blocks can be accepted in one response and executed sequentially

## ReAct Modules

The ReAct adapter keeps shared modules at `solutions/react/`; version folders
contain the decision runtime and version-specific tests:

- `runtime.py` — state machine, decision loop, and per-round generation feedback (see `round-generation-feedback-README.md`)
- `timeline.py` — timeline storage, rendering, pruning, and compaction
- `round.py` — round lifecycle, decision attempts, and tool-call/result grouping
- `browser.py` — context loading, timeline contribution, external-event folding, and live listener ownership
- `layout.py` — block builders and rendered views
- `tools/` — React-only tools such as `react.read`, `react.write`, `react.patch`, `react.pull`, `react.checkout`, `react.memsearch`, and `react.hide`
- `artifacts.py` — artifact helpers and logical-path views

Framework-neutral harness modules:

- `runtime/harness/events/`
  - canonical event/object ref resolution
- `runtime/harness/timeline/`
  - block identity, persisted payloads, turn log/view, provider projection
- `runtime/harness/workspace/`
  - canonical refs, artifacts, paths, change detection, and materialization

Adjacent shared modules:

- `solutions/widgets/`
  - canvas, timeline, and exec streamers
- `sdk/streaming/`
  - channel stream parsing and the buffered stream gates
  - `action_overseer.py` (in `react/v3/`) drives real-time action gating
  - `workspace_streamer.py` for the current shared path
  - `workspace_streamer_v3.py` for v3 multi-instance channel handling

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

The documentation is organized into a shared harness plus ReAct adapter layers:

- `docs/runtime/harness/`
  - framework-neutral events, timeline, and workspace contracts
- root `react/`
  - ReAct runtime, timeline adapter, context, tools, flow, and round model
- `react/workspace/`
  - ReAct-specific git-backed workspace engineering and adapter behavior
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
