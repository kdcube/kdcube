# React Tools (react.*)

This document describes the react-only tools injected into the decision tool catalog. These tools
are not available in external execution environments. All effects are reflected in the timeline
via react.tool.call / react.tool.result blocks.

Common behaviors
- All react tools are invoked as a call_tool action.
- All tool calls append a react.tool.call block (reasoning + params) and one or more react.tool.result blocks.
- Artifacts created by tools are referenced by stable paths (use turn_id, never current_turn):
  - fi:<turn_id>.files/<relative_path>
  - ar:<turn_id>.artifacts.<artifact_path>
  - tc:<turn_id>.tool_calls.<id>.in.json / .out.json

react.read
- Purpose: load an existing artifact into the timeline for inspection.
- Effect: emits a tool.result block with the artifact content (text or multimodal) and metadata.
- Visibility: does not change artifact visibility; it only makes content visible in the timeline.
Params:
- paths: list[str] (FIRST FIELD). One or more artifact/context paths to load.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "ar:turn_123.artifacts.notes", "mime": "text/markdown", "text": "..." }
```

react.stream
- Purpose: generate or stream content for a new artifact.
- Effect: emits tool.result blocks with streamed content and metadata.
- If kind='display', content is shown to the user (external). If kind='file', it is recorded as a file.
Params:
- path: str (FIRST FIELD). Artifact path under <turn_id>/files/.
- channel: str (SECOND FIELD). 'canvas' or 'timeline_text'.
- content: str (THIRD FIELD). Streamed content.
- kind: str (FOURTH FIELD). 'display' or 'file'.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "fi:turn_123.files/report.md", "mime": "text/markdown", "text": "..." }
```

react.file
- Purpose: write content to a file path (relative under <turn_id>/files/).
- Effect: records a file artifact and emits tool.result metadata; content is stored on disk.
Params:
- path: str (FIRST FIELD). File path under <turn_id>/files/.
- content: str (SECOND FIELD). File content.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "fi:turn_123.files/data.json", "mime": "application/json", "text": "..." }
```

react.patch
- Purpose: patch an existing file and stream the patch.
- Patch format:
  - If patch starts with ---/+++/@@, treat as unified diff.
  - Otherwise replace the entire file with the patch text.
- Effect: emits tool.result with patch text; optionally marks the file external if kind='file'.
Params:
- path: str (FIRST FIELD). File path under <turn_id>/files/.
- channel: str (SECOND FIELD). 'canvas' or 'timeline_text'.
- patch: str (THIRD FIELD). Unified diff or full replacement text.
- kind: str (FOURTH FIELD). 'display' or 'file'.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "fi:turn_123.files/report.md", "mime": "text/markdown", "text": "<patch>..." }
```

react.memory_read
- Purpose: search past turns and surface relevant snippets into context.
- Effect: emits a tool.result block with compact snippets and metadata (turn_id, timestamps, scores).
- Use when you suspect missing details exist in prior turns but are not visible.
Params:
- query: str (FIRST FIELD). Search query.
- targets: list[str] (SECOND FIELD). assistant|user|attachment.
- top_k: int (optional). Default 5.
- days: int (optional). Default 365.

Example result block (simplified):
```json
{
  "type": "react.tool.result",
  "call_id": "<tool_call_id>",
  "mime": "application/json",
  "path": "tc:<turn_id>.tool_calls.<id>.out.json",
  "text": "[{'turn_id': 'turn_123', 'text': '...', 'score': 0.84, 'ts': '2026-02-01T...Z'}]"
}
```

react.memory_hide
- Purpose: replace a large snippet in the visible timeline with a short placeholder.
- Use only when the snippet is near the tail and clearly no longer needed.
- The original content remains retrievable via react.read(path).
Params:
- path: str (FIRST FIELD). Block path to hide.
- replacement: str (SECOND FIELD). Replacement text.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "tc:turn_123.tool_calls.abc.out.json", "mime": "application/json", "text": "{...}" }
```

react.search_files
- Purpose: safely search files under the current workdir without shell execution.
- Returns: list of matching file paths.
Params:
- name_regex: str (FIRST FIELD). Regex for filenames (optional).
- content_regex: str (SECOND FIELD). Regex for file content (optional).
- max_hits: int (optional). Default 200.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "tc:turn_123.tool_calls.abc.out.json", "mime": "application/json", "text": "{...}" }
```

Notes
- react.* tools are only valid inside the react loop.
- The decision agent must order params exactly as specified in each tool signature.
