---
id: repo:kdcube-ai-app/app/ai-app/docs/citations-system.md
title: "Citations System"
summary: "Citations and sources system: normalization, sources_pool, and sources_used tracking."
tags: ["citations", "sources", "tracking", "runtime"]
keywords: ["sources_pool", "citations tokens", "sources_used", "canonical sources", "source ids"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/feedback-system.md
  - repo:kdcube-ai-app/app/ai-app/docs/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/source-pool-README.md
---
# Citations & Sources System

Scope: sources normalization, sources_pool, citation tokens, and sources_used tracking across tools.

---

## Overview

Our system uses a single canonical “source row” shape across web search, fetch, attachments, skills, and generators. Sources are collected into a per-conversation `sources_pool` and referenced in outputs via citation tokens (e.g., `[[S:1]]`). Tools that generate or render content extract citation SIDs and persist `sources_used` for provenance.

Key points:
- Sources are normalized to a canonical row shape and deduped by URL / physical path.
- The in-memory `sources_pool` is the source of truth for current SIDs. It is persisted both in
  `timeline.json` for local/exec recovery and as `artifact:conv:sources_pool` for cross-turn recovery.
- Citations are expressed as `[[S:...]]` (Markdown/Text), HTML `<sup class="cite" ...>`, or JSON/YAML sidecar entries.
- `sources_used` is stored as a list of SIDs (ints) for each artifact.

---

## Canonical Source Row

All tools converge on this shape (subset is OK; fields are additive):

```jsonc
{
  "sid": 123,                    // required int
  "title": "Some page title",    // best-effort
  "url": "https://example.com",  // optional for file/attachment sources

  // Short snippet / preview:
  "text": "Short excerpt or summary…",

  // Full content (optional, expensive):
  "content": "Full extracted body…",

  // Source type and file metadata
  "source_type": "web|file|attachment|manual",
  "mime": "text/html",
  "size_bytes": 12345,
  "artifact_path": "fi:<turn>.files/report.pdf",
  "physical_path": "turn_<id>/files/report.pdf",

  // Optional metadata
  "published_time_iso": "2025-01-02T12:34:56Z",
  "modified_time_iso": "2025-01-03T10:00:00Z",
  "fetched_time_iso": "2025-01-03T10:05:00Z",
  "objective_relevance": 0.95,
  "query_relevance": 0.87,
  "authority": "web",
  "favicon_url": "https://example.com/favicon.ico"
}
```

Notes:
- `physical_path` is the canonical file path for attachments/files.
- `content` is optional and should be used sparingly to control token usage.
- Any extra fields are tolerated and preserved by normalizers.

---

## Sources Pool (Conversation Registry)

`sources_pool` is a per-conversation list of canonical source rows managed as a progressive
conversation‑level artifact. The full pool is stored as `conv:sources_pool`, and the local
`timeline.json` also carries the full current pool so `react.read` and exec `ctx_tools.fetch_ctx`
can recover fetched source content during the same turn. Index text and rendered timeline views
remain compact.

It is merged/deduped by normalized URL or physical path:
- Existing rows keep their SID.
- Duplicates reuse the existing SID.
- New unique sources receive the next SID.

### How items get into the pool
- `web_tools.web_search`: discovery; returns canonical rows with `sid`, `title`, `url`, `text`, and `content` when fetched.
- `web_tools.web_fetch`: fetches known URLs; returns canonical rows with `content`/`text`.
- Attachments and produced files: added only if MIME is `text/*`, `image/*`, or `application/pdf`.
- Skills: if a skill has `sources.yaml`, `react.read` merges them into the pool and rewrites `[[S:...]]` tokens in the skill body to the merged SIDs.

---

## Citation Protocol

### Markdown / Text
Use inline tokens:

```
[[S:1]]
[[S:1,3]]
[[S:2-4]]
```

Rules:
- Place tokens after the sentence/bullet that introduces the claim.
- Use only SIDs that exist in the current `sources_pool`.
- Do NOT put citations inside fenced code blocks.

### HTML
Use `<sup>` citations or `[S:n]` markers:

```html
<sup class="cite" data-sids="1,3">[S:1,3]</sup>
```

Validators also accept a “Sources”/footnotes section containing `[S:n]` markers.

### JSON / YAML (Sidecar)
When `cite_sources=True` for JSON/YAML outputs, the tool expects a sidecar array at `/_citations` (configurable):

```jsonc
{
  "some": { "field": "Claim text" },
  "_citations": [
    { "path": "/some/field", "sids": [1,3] }
  ]
}
```

Rules:
- `path` is a JSON Pointer to a string field.
- `sids` must reference existing SIDs.
- Inline `[[S:n]]` tokens inside strings are allowed only if `allow_inline_citations_in_strings=True`, but the sidecar remains authoritative when required.

---

## LLM Generator: `llm_tools.generate_content_llm`

`generate_content_llm` accepts:
- `sources_list`: list of canonical sources (it prefers `content` over `text` for the digest)
- `cite_sources`: require citations
- `citation_embed`: `auto|inline|sidecar|none`
- `citation_container_path`: JSON Pointer for sidecar
- `allow_inline_citations_in_strings`

When citations are required, the tool validates them and will repair outputs if needed.
It returns:

```jsonc
{
  "ok": true,
  "content": "...",
  "format": "markdown|html|json|yaml|...",
  "stats": { "citations": "present|missing|n/a", ... },
  "sources_used": [1, 3, 5]
}
```

`"sources_used"` is a list of SIDs (ints), not full source objects.

---

## Usage Telemetry

When citations are not required but sources are provided, the model must emit:

```
[[USAGE:1,3,5]]
```

This tag is stripped from final output and used to compute `sources_used`.

---

## Renderers & Artifacts

Renderers (`write_pdf`, `write_png`, `write_pptx`, `write_docx`) extract citation tokens from input content and record `sources_used` for the produced artifact. `SourcesUsedStore` persists these mappings in `sources_used.json` (by artifact name or filename), so downstream tools can retain provenance.

---

## Access & Validation

- Use `react.read(["so:sources_pool[1-5]"])` to load JSON source rows into visible context. Rows are full by default and include item stats metadata; for web rows prefer `content` over `text` when evidence needs full fetched page text.
- Use `context_tools.fetch_ctx("so:sources_pool[1,3]")` for programmatic access (returns raw rows).
- Use `extract_citation_sids_any` or `citations_present_inline` for validation/debug.
