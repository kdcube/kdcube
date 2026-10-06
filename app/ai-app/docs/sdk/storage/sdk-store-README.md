---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/storage/sdk-store-README.md
title: "SDK Store"
summary: "Storage layout used by the Chat SDK (local FS or S3) for artifacts and accounting."
tags: ["sdk", "storage", "layout", "artifacts", "accounting"]
keywords: ["storage paths", "artifacts", "attachments", "s3", "local fs", "conversation data", "file URI", "percent decoding"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/storage/cache-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/storage/git-store-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/aggregations/README-AGGREGATIONS.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
---
# SDK Storage Layout

This document summarizes the **storage paths** used by the Chat SDK. It reflects the current production layout (local FS or S3).

`<kdcube storage path>` example (configured via `KDCUBE_STORAGE_PATH`):
- `s3://<bucket>/<path>/kdcube/ai-app/<deployment>`

## Local file URI paths

Local storage roots and file-backed secret descriptors can be addressed with
`file:///...` URIs. Build a URI from a physical path with `Path.as_uri()`:

```python
from pathlib import Path
from kdcube_ai_app.storage.storage import create_storage_backend

root = Path("/srv/author@home/with space/literal%40")
backend = create_storage_backend(root.as_uri())
```

The local backend and secret-descriptor read/write helpers share
`kdcube_ai_app.storage.uri.local_file_uri_path`. It percent-decodes the URI
path exactly once. `%40` represents `@`, `%20` represents a space, and `%2540`
represents the literal characters `%40`. Raw filesystem paths retain their
existing handling and receive no percent decoding; S3 bucket/prefix/key
handling also retains its existing semantics. Local backends use the file
URI's path component; this contract does not add remote filesystem support.

When a secret-descriptor reader separates a physical file into a backend root
and leaf, it re-encodes the root with `Path.as_uri()`. This preserves literal
percent sequences across the next URI boundary. The leaf remains a physical
filename, not an encoded URI component.

### Existing stores and activation

Earlier versions could interpret encoded characters literally and read/write
a sibling directory such as `author%40home` instead of `author@home`. A
successful read after a write did not prove that the intended physical path
was used. Corrected source selects the decoded physical path and provides no
fallback to, or automatic migration of, a misplaced store.

Before activating corrected source for an affected deployment, the operator
must establish which physical store is authoritative and arrange any required
reconciliation through the existing secret-storage procedure. Keep secret
values out of diagnostics and source; protect both stores until that decision
is made. The source correction alone neither relocates existing values nor
changes the configured provider. Installing or staging corrected source and
verifying the actual loaded runtime are separate from source-test evidence.

## 1) Conversation artifacts (per turn)

```
<kdcube storage path>/cb/tenants/<tenant>/projects/<project>/conversation/<user_id>/<conversation_id>/<turn_id>/
  artifact-<ts>-<id>-turn.log.json
  artifact-<ts>-<id>-perf-steps.json
  artifact-<ts>-<id>-conv.user_shortcuts.json
  artifact-<ts>-<id>-conv.artifacts.stream.json
  artifact-<ts>-<id>-conv.thinking.stream.json
```

Notes:
- Filenames use `artifact-<timestamp>-<id>-<kind>.json`.
- The set of artifact files depends on what was produced in the turn.

## 2) Conversation attachments (user + assistant)

Attachments are stored in the **same turn directory** as artifacts. Example:

```
<kdcube storage path>/cb/tenants/<tenant>/projects/<project>/conversation/<user_id>/<conversation_id>/<turn_id>/
  20260113015047-oracle-oxy-tank.png
```

Notes:
- Both **user uploads** and **assistant‑produced files** are stored here.
- To distinguish source, use the turn log for the turn where the file appeared.

## 3) Execution snapshots (reactive agent workdir)

The full reactive workdir (tool calls, logs, outputs) is stored per execution:

```
<kdcube storage path>/cb/tenants/<tenant>/projects/<project>/executions/<user_id>/<conversation_id>/<turn_id>/<exec_id>/
  out.zip
  pkg.zip
```

## 4) Accounting events (raw)

Per‑service accounting events (LLM / embeddings / web_search, etc.):

```
<kdcube storage path>/accounting/<tenant>/project/<YYYY.MM.DD>/<service_name>/<bundle_id>/
  cb|<user_id>|<conversation_id>|<turn_id>|answer.generator.regular|<timestamp>.json
```

## 5) Accounting aggregates

Aggregated accounting metrics:

```
<kdcube storage path>/analytics/<tenant>/project/
  accounting/
    daily/
    weekly/
    monthly/
```
