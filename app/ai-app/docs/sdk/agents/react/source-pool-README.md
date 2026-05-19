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
For local turn and exec access, the full current pool is also written into `timeline.json`
under `sources_pool`. The rendered timeline tail remains compact.

The pool is rendered as a single “SOURCES POOL” tail block when timeline rendering includes sources.

### Rendered compact view

The visible “SOURCES POOL” tail block is lightweight and shows essential fields
(e.g., `sid`, `title`, `url`, short `text`, and limited metadata like `published_time_iso`
or `favicon`). The stored rows remain full so `react.read` and exec `fetch_ctx` can recover
fetched `content`.

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
- `physical_path` (str): artifact-root-relative `turn_...` file path
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
- `react.read` returns an `application/json` list of source rows, not a prose
  rendering. It also records `items_stats` in the read status/result metadata so
  the model can see row counts and content sizes.
- Source rows are returned in full by default. If `max_text_symbols` is supplied
  explicitly, only text-bearing fields such as `content`/`text` are capped; the
  JSON remains valid and all rows remain present.
- `so:sources_pool[...]` result blocks are exempt from the generic
  `tool_result_preview_max_text_symbols` prompt cap.
- For web rows, use `content` first when full fetched text is needed. `text` is
  the search preview/snippet.

### In code (exec)
- Use `context_tools.fetch_ctx("so:sources_pool[1,3]")`.
- `fetch_ctx` returns the raw list of source rows (not a canonical artifact object).
- For web rows, use `row.get("content") or row.get("text")` when you need source text.
  `text` is the search preview; `content` is the fetched page body when available.
- For files/attachments, read from `Path(OUTPUT_DIR) / physical_path`.
  `OUTPUT_DIR` is the artifact root; in local runtime storage that root is
  `out/workdir`.

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
