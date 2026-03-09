---
id: ks:docs/sdk/agents/react/source-pool-README.md
title: "Source Pool"
summary: "Sources pool population, storage, and access patterns."
tags: ["sdk", "agents", "react", "sources", "citations"]
keywords: ["sources_pool", "citations", "dedupe", "source entries"]
see_also:
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/artifact-storage-README.md
  - ks:docs/sdk/agents/react/compaction-README.md
---
# Sources Pool

This document describes how the **sources pool** is populated, stored, and accessed in the ReAct runtime.

## What it is

The sources pool is a per‑conversation registry of canonical source rows collected from:
- `web_tools.web_search` and `web_tools.web_fetch`
- user attachments **(images only)** for rendering/embedding
- files produced by tools **(images only)** for rendering/embedding
- skill sources (`sources.yaml`) when a skill is loaded via `react.read`

## Where it lives

The full pool is stored as a separate conversation‑level artifact `conv:sources_pool`.
When a turn starts, the runtime loads that artifact and hydrates the in‑memory pool.
For local turn access, a compact snapshot is written into `timeline.json` under `sources_pool`.

The pool is rendered as a single “SOURCES POOL” tail block when timeline rendering includes sources.

### Compact snapshot contents

The snapshot stored in the timeline artifact / local `timeline.json` is lightweight and
keeps only essential fields (e.g., `sid`, `title`, `url`, short `text`, and limited metadata like
`published_time_iso` or `favicon`). The full source rows remain in `conv:sources_pool`.

## Dedupe and SID behavior

- Sources are merged by normalized URL (or `physical_path` for local files).
- Existing rows keep their SID.
- Duplicate URLs/paths reuse the existing SID (no new row).
- New unique rows receive the next SID.

SIDs are stable within a conversation once assigned.

## Eligibility (what is included)

Attachments and produced files are included only if MIME is `image/*`.

Other file types (e.g., `.xlsx`, `.pptx`, `.docx`, archives, PDFs) are not added to the pool.

## Canonical source row fields

All rows are dictionaries; fields are additive. Common fields:
- `sid` (int): source identifier
- `source_type` (str): `web` | `file` | `attachment` | `manual`
- `title` (str)
- `url` (str) for web sources
- `text` (str): short snippet/preview
- `content` (str): full body (optional)
- `mime` (str)
- `size_bytes` (int)
- `artifact_path` (str): logical path (e.g., `fi:<turn>.files/report.pdf`)
- `physical_path` (str): OUT_DIR‑relative file path
- `rn`, `hosted_uri`, `key` (str): hosting references (not rendered to the model)

Optional metadata for web sources:
- `domain`, `published_time_iso`, `modified_time_iso`, `fetched_time_iso`
- `provider_rank`, `weighted_rank`, `objective_relevance`, `query_relevance`, `authority`

Notes:
- Prefer `physical_path` for attachments/files.
- `content` is expensive and should be used sparingly.

## Accessing sources

### In the ReAct timeline
- The SOURCES POOL block shows title, mime, domain/artifact path, and a short snippet.
- For binary sources (`image/*`, `application/pdf`), the snippet is rendered as `<base64>`.

### In the model context
- Load sources with `react.read(["so:sources_pool[1-5]"])` or a comma list
  like `react.read(["so:sources_pool[1,3,7]"])`.

### In code (exec)
- Use `context_tools.fetch_ctx("so:sources_pool[1,3]")`.
- `fetch_ctx` returns the raw list of source rows (not a canonical artifact object).
- For files/attachments, read from `OUT_DIR / physical_path`.

## Citing sources

Use citation tokens in generated text:

```
[[S:1]]
[[S:1,3]]
[[S:2-4]]
```

Do not place citations inside fenced code blocks.

Only **web sources** (http/https URLs) should be cited.  
Image sources in the pool are for rendering/embedding. They may appear as SIDs
inside HTML/Markdown passed to rendering tools, but they are **not** evidence citations.

## Rendering behavior

Streaming output replaces `[[S:...]]` in markdown/text/html channels using the current
`sources_pool`. HTML uses `<sup class="cite" data-sids="...">` markers when present.

## Notes for clients

Clients can rehydrate citations by matching `sources_used` SIDs in artifacts with the
current `sources_pool` in `timeline.json`.
