# SDK Storage Layout

This document summarizes the **storage paths** used by the Chat SDK. It reflects the current production layout (local FS or S3).

`<kdcube storage path>` example (configured via `KDCUBE_STORAGE_PATH`):
- `s3://<bucket>/<path>/kdcube/ai-app/<deployment>`

## 1) Conversation artifacts (per turn)

```
<kdcube storage path>/cb/tenants/<tenant>/projects/<project>/conversation/<user_role>/<user_id>/<conversation_id>/<turn_id>/
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
<kdcube storage path>/cb/tenants/<tenant>/projects/<project>/conversation/<user_role>/<user_id>/<conversation_id>/<turn_id>/
  20260113015047-oracle-oxy-tank.png
```

Notes:
- Both **user uploads** and **assistant‑produced files** are stored here.
- To distinguish source, use the turn log for the turn where the file appeared.

## 3) Execution snapshots (reactive agent workdir)

The full reactive workdir (tool calls, logs, outputs) is stored per execution:

```
<kdcube storage path>/cb/tenants/<tenant>/projects/<project>/executions/privileged/<user_id>/<conversation_id>/<turn_id>/<exec_id>/
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
