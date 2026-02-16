# React Round (Tool Call) Model

This document describes the ReactRound concept and how tool calls are represented.

## Overview
- A **React round** corresponds to exactly one tool call.
- Each round emits:
  - One tool-call block (`react.tool.call`)
  - One or more tool-result blocks (`react.tool.result`)
- The round also updates the sources pool and artifacts registry.

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
- `react.stream`: Stream text content to a display block (`kind=display`).
- `react.file`: Persist text content to disk (`kind=file`) + emit result block.
- `react.patch`: Update an existing file. If `patch` starts with `---/+++/@@`, treat as unified diff; otherwise replace full file content.
- `react.memsearch`: Query conversation index for prior turns; returns snippets.

## Storage
- Tool results are **not** persisted as files by default.
- Only files produced by:
  - exec tools
  - `react.file`
  - `rendering_tools.write_*`
  are written to disk.

## Reading Results
`react.read` can load:
- Artifacts via `ar:<turn_id>.artifacts.<path>`
- Files via `fi:<turn_id>.files/<filepath>`
- Sources via `so:sources_pool[...]`
- Tool call payloads via `tc:<turn_id>.<id>.call` or `.result`
