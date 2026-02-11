# Sources Pool (source-pool-README.md)

This document describes how the **sources pool** is maintained, persisted, and rendered.

## What it is
The sources pool is a per‑conversation registry of sources collected by tools:
- `web_tools.web_search`, `web_tools.web_fetch` results (URL/title/snippet/etc.)
- user attachments that are treated as sources
- files produced by tools that are eligible to be cited

Each item in the pool has a stable `sid` and metadata (url/title/snippet/…).

## Source item shapes (examples)

### Web search / fetch
```json
{
  "sid": 12,
  "source_type": "web",
  "url": "https://example.com/article",
  "title": "Article title",
  "snippet": "Short extract or summary...",
  "domain": "example.com",
  "published_at": "2026-02-10",
  "score": 0.83
}
```

### User attachment (pdf/png)
```json
{
  "sid": 5,
  "source_type": "attachment",
  "title": "menu.pdf",
  "url": "rn:.../menu.pdf",
  "rn": "rn:.../menu.pdf",
  "local_path": "turn_123/attachments/menu.pdf",
  "mime": "application/pdf",
  "size_bytes": 183942
}
```

### Files produced by bot (pdf/png)
```json
{
  "sid": 8,
  "source_type": "file",
  "title": "report.pdf",
  "url": "rn:.../report.pdf",
  "rn": "rn:.../report.pdf",
  "local_path": "turn_123/files/report.pdf",
  "mime": "application/pdf",
  "size_bytes": 45921
}
```

## Where it lives (source of truth)
The pool is stored inside `timeline.json`:
```
{
  "version": 1,
  "blocks": [ ... ],
  "sources_pool": [ {sid: 1, ...}, {sid: 2, ...} ],
  ...
}
```

This means:
- It is persisted with the timeline artifact (conv.timeline.v1).
- It is available for `react.read` and `ctx_tools.fetch_ctx` via `so:sources_pool[...]`.

## How it is updated
The pool grows when tools return sources (search/fetch) or when attachments/files are registered as sources.
When new sources are merged, they are assigned new SIDs, and the timeline is flushed to disk.

## How agents see it
When `timeline.render(include_sources=True)` is used, the sources pool is rendered as a **single uncached tail block**:

```
SOURCES POOL (3 sources)
[S:1] example.com  |  "Title..."
[S:2] another.com  |  "Snippet..."
[S:3] local.pdf    |  "User attachment"
```

This block is **always the last non‑cached block**, and appears **after announce** when both are included.

## How agents cite sources
- Agents **must** cite sources using tokens like `[[S:1]]`, `[[S:1,2]]`, or `[[S:1-3]]`.
- We do **not** embed raw links into generated text blocks (saves tokens, reduces errors).
- When the system can trace citations for an artifact, it records `sources_used` in the artifact’s timeline metadata.

## Streaming behavior
- Streaming output replaces `[[S:...]]` tokens in‑flight using the sources_pool resolution.
- This keeps user-facing streams concise while preserving source attribution internally.

## How to access sources (agent guidance)
- Use `react.read(paths=["so:sources_pool[1,2]"])` to load sources into visible context.
- Use `ctx_tools.fetch_ctx("so:sources_pool[1,2]")` inside exec code for programmatic access.
- Prefer ranges: `so:sources_pool[1:5]`.

## Example
Rendered order when include_sources + include_announce:

```
... timeline blocks ...
[ANNOUNCE]  (uncached)
SOURCES POOL (uncached)
```

## Notes for clients
Clients may rehydrate citations by matching used SIDs in turn logs with the pool
from `timeline.json`.
