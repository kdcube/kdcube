# Code Exec Widget Events

This document describes the event schema emitted by the code execution widget for UI consumption.

## Grouping

- Events are grouped by `execution_id`.
- `artifact_name` is unique per `sub_type` within the same `execution_id`.

## Event Types

### 1) Program name (optional)

- `sub_type`: `code_exec.program.name`
- `format`: `text`
- `title`: `Program Name`
- `payload`: plain text
- Emitted once when `prog_name` is present in tool params.

### 2) Objective

- `sub_type`: `code_exec.objective`
- `format`: `text`
- `title`: `Objective`
- `payload`: plain text
- Emitted once; includes a completion event.

### 3) Code stream

- `sub_type`: `code_exec.code`
- `format`: `text`
- `language`: `python`
- `payload`: raw code text streamed in chunks
- Completion event marks the end of the code stream.
- When streaming starts, status is set to `gen`.

### 4) Contract

- `sub_type`: `code_exec.contract`
- `format`: `json`
- `payload`:

```json
{
  "execution_id": "...",
  "contract": [
    {
      "artifact_name": "...",
      "description": "...",
      "mime": "...",
      "filename": "..."
    }
  ]
}
```

- After contract emission, status switches to `exec`.

### 5) Status

- `sub_type`: `code_exec.status`
- `format`: `json`
- Emitted at index `0` (no completion event).
- `payload`:

```json
{
  "status": "gen | exec | done | error",
  "timings": {
    "codegen": 123,
    "exec": 456
  },
  "error": {"...": "..."}
}
```

- `error` is present only when status is `error`.

## Status Flow

- `gen`: during code streaming
- `exec`: after contract emission
- `done` or `error`: after execution completes
