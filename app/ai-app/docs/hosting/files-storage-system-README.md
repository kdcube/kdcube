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
