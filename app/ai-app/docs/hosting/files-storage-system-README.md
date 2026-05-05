---
id: ks:docs/hosting/files-storage-system-README.md
title: "File Storage and Hosting"
summary: "How agent‑produced files are stored and served via resource routes."
tags: ["hosting", "storage", "artifacts", "resources", "react"]
keywords: ["files", "artifacts", "resources", "KDCUBE_STORAGE_PATH", "RN", "conversation store"]
see_also:
  - ks:docs/sdk/storage/sdk-store-README.md
  - ks:docs/sdk/agents/react/conversation-artifacts-README.md
  - ks:docs/sdk/agents/react/artifact-storage-README.md
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
---
# File Storage and Hosting

This document explains **how files are stored and served** when the agent
produces artifacts during a turn.

## Storage root
All artifacts are stored under `KDCUBE_STORAGE_PATH` (local FS or S3).

Layout reference:
https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/storage/sdk-store-README.md

## Lifecycle (high‑level)
1. **Agent produces a file** during a turn (e.g., image, PDF, dataset).
2. The file is **written into conversation storage** under the current turn.
3. The file is **registered in the turn log / timeline** with an RN (resource name).
4. The **resources API** resolves the RN and serves the file.

## Declared File Tool Results

Custom tools can return already-created files to React by using the standard
tool envelope. This is a strict protocol:

- `ret.artifact_type` must equal exactly `files`.
- `ret.files` must be the file row list.
- File rows describe paths or hosted file references.

```json
{
  "ok": true,
  "error": null,
  "ret": {
    "artifact_type": "files",
    "files": [
      {
        "type": "file",
        "source_type": "file",
        "visibility": "external",
        "filename": "invoice.pdf",
        "mime_type": "application/pdf",
        "physical_path": "turn_123/outputs/email-attachments/invoice.pdf",
        "logical_path": "fi:turn_123.outputs/email-attachments/invoice.pdf"
      }
    ]
  }
}
```

`artifact_type` is currently not a broad result taxonomy. The only recognized
family is the declared-file result, and the marker is:

```json
{
  "artifact_type": "files"
}
```

Each row may identify the file with `physical_path`, `path`, `local_path`,
`artifact_path`, `logical_path`, `hosted_uri`, `rn`, or `key`. For locally
created files, prefer `physical_path` plus `filename` and `mime_type`.

The explicit marker makes file hosting an intentional tool result contract.

Tools can also host files directly with
`bundle_tool_context.host_files(...)`. That helper uses the same conversation
hosting service and returns `artifact_type: "files"` rows already populated with
`hosted_uri`, `key`, and `rn`.

Tool-side hosting is available from trusted bundle/catalog tools in the normal
workflow process and in isolated execution on the trusted supervisor/runtime
side. The isolated bootstrap reconstructs the communicator, conversation store,
and hosting-capable tool subsystem from portable runtime state. Generated
executor code reaches hosting by calling such a catalog tool through
`agent_io_tools.tool_call(...)`; the catalog tool then writes or materializes the
files and calls `host_files(...)`.

Tool-side hosting requires prepared runtime context. At minimum the tool runtime
must have tenant, project, user id, conversation id, turn id, user type,
conversation storage, and an active output directory. In normal React workflows
this is prepared by `BaseWorkflow.build_react(...)` and refreshed by
`BaseWorkflow.rebind_request_context(...)`. In isolated execution it is prepared
by `bootstrap_bind_all(...)` in `kdcube_ai_app.apps.chat.sdk.runtime.bootstrap`.

If a trusted tool calls `host_files(...)` before that preparation, the helper
raises a runtime error instead of silently creating an unscoped artifact. Common
messages are `tools are not bound to the current tool subsystem`,
`tool hosting service is unavailable`, `tool communicator is unavailable`, or
`bundle storage root is unavailable`.

The two file-hosting paths use the same strict result protocol:
- declarative path: the tool returns `ret.artifact_type: "files"` and local file
  rows; React hosts them after the tool returns.
- tool-side path: the tool calls `host_files(...)` and returns the hosted rows;
  React records them and avoids hosting the same files again.

Visibility controls hosting intent:

```text
external  host and emit as a conversation artifact
internal  keep as internal timeline/tool metadata
```

If `visibility` is omitted for declared files, React defaults to `external`.

## Where it is implemented
**Storage / workspace**
- `kdcube_ai_app/apps/chat/sdk/solutions/react/v2/solution_workspace.py`
- `kdcube_ai_app/apps/chat/sdk/solutions/react/v2/artifacts.py`
- `kdcube_ai_app/apps/chat/sdk/solutions/react/v2/tools/external.py`

**Resource retrieval (ingress)**
- `kdcube_ai_app/apps/chat/ingress/resources/resources.py`

## Notes
- Artifacts are **hosted as soon as they are produced** (during the turn).
- The **conversation store** combines Postgres (metadata/indexing) and
  object storage (artifacts/attachments).
- Full turn workspace snapshots are **optional** and enabled via
  `REACT_PERSIST_WORKSPACE=1` (for debugging only).

## Related docs
- [Conversation artifacts](https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/agents/react/conversation-artifacts-README.md)
- [Artifact storage](https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/agents/react/artifact-storage-README.md)
- [React turn workspace](https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/agents/react/react-turn-workspace-README.md)
