---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-round-README.md
title: "React Round"
summary: "How React rounds represent decision attempts, executed tool calls, and results in v2 and v3."
tags: ["sdk", "agents", "react", "tool-calls"]
keywords: ["ReactRound", "tool call blocks", "results mapping", "react.round.start", "react.notice", "v3 multi-action"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/tool-call-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/timeline-README.md
---
# React Round Model

This document describes the ReactRound concept and how decision attempts and tool calls are represented.

## Overview
- In production `v2`, one response corresponds to one round, and a successful round executes exactly one action.
- In experimental `v3`, one response can request multiple actions, but accepted actions are still executed sequentially.
- Each executed action still gets its own `tool_call_id`, `react.tool.call`, and `react.tool.result` blocks.
- A round can also be a failed decision attempt with no executed action. In that case the timeline still shows:
  - `react.round.start`
  - `react.decision.raw`
  - `react.notice`
- A single turn may later contain multiple visible `assistant.completion` blocks if reactive
  same-turn events arrive after an earlier completion attempt.

So a React round is the unit of visible reasoning/execution progress, not only the unit of successful tool execution.

## Tool Call Blocks
Tool call blocks are JSON text blocks with metadata:
- `type`: `react.tool.call`
- `call_id`: tool call id
- `mime`: `application/json`
- `path`: `tc:<turn_id>.<call_id>.call`
- `text`: JSON payload `{tool_id, tool_call_id, reasoning, params}`

## Tool Result Blocks
Tool result blocks are emitted per artifact or per result type:
- Text results produce a `text` block with an appropriate mime:
  - `text/markdown`, `text/html`, or `application/json`
- Binary results produce a `base64` block with a binary mime:
  - `application/pdf`, `image/png`, etc.
- For search/fetch tools, the result is a JSON block containing only SIDs.

## React Tools
- `react.read`: Load artifacts/files/sources/tool-call payloads by path.
- `react.pull` / `react.checkout`: Materialize historical refs or define the current-turn workspace.
- `react.write`: Create a text artifact for display, file delivery, or internal note keeping.
- `react.patch`: Update an existing file. If `patch` starts with `---/+++/@@`, treat as unified diff; generated hunk counts are normalized before apply. Otherwise replace full file content.
- `react.memsearch`: Query conversation index for prior turns; returns snippets.
- `react.hide`, `react.rg`, and `react.plan` provide cache-tail cleanup, safe file/region search, and in-loop planning.

Text artifact previews in the rendered timeline are line-numbered for navigation.
Those numbers are viewing prefixes only; do not include them in patch or full-file
replacement content.

## Storage
- Tool results are **not** persisted as files by default.
- Only files produced by:
  - exec tools
  - `react.write`
  - `rendering_tools.write_*`
  are written to disk.

## Reading Results
`react.read` can load:
- Turn-level artifacts via:
  - `ar:<turn_id>.user.prompt`
  - `ar:<turn_id>.assistant.completion`
  - `ar:<turn_id>.assistant.completion.<n>`
- Files via `fi:<turn_id>.files/<filepath>`
- Sources via `so:sources_pool[...]`
- Tool call payloads via `tc:<turn_id>.<id>.call` or `.result`
