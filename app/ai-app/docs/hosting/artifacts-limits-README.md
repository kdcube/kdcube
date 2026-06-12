---
id: repo:kdcube-ai-app/app/ai-app/docs/hosting/artifacts-limits-README.md
title: "Artifacts Limits"
summary: "Hard limits and truncation policies for artifacts, sources, and tool outputs."
tags: ["hosting", "artifacts", "limits", "truncation", "sources"]
keywords: ["base64 limits", "timeline limits", "tool output truncation", "artifact size"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/hosting/attachments-system.md
  - repo:kdcube-ai-app/app/ai-app/docs/citations-system.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/eco-kickoff-README.md
---
# Artifacts Limits

This document summarizes the hard limits and truncation policies applied to artifacts, sources, and tool outputs.

**Scope**
- Upload preflight rules (attachments and files).
- Sources pool and search-result base64 caps.
- Timeline truncation for tool results vs user/assistant text.

---

**Upload Preflight Limits**
(From `kdcube_ai_app/infra/gateway/safe_preflight.py`)

- PDF
  - Max pages: `pdf_max_pages = 500`
  - Max objects (hint): `pdf_max_objects_hint = 100_000`
  - Max object streams: `pdf_max_objstm = 2_000`
  - Max incremental updates: `pdf_max_updates = 5`
  - Max declared stream total: `pdf_total_declared_stream_len_max = 100 MB`
- ZIP / OOXML
  - Max entries: `zip_max_files = 2_000`
  - Max uncompressed total: `zip_max_uncompressed_total = 120 MB`
  - Max compression ratio: `zip_max_ratio = 200.0`
  - Nested ZIPs disallowed by default
- Text files
  - `text_max_bytes = 10 MB`
- OOXML allowlist
  - `allow_docx`, `allow_pptx`, `allow_xlsx` are enabled by default
  - Macros are blocked by default (`allow_macros = False`)

---

**Sources Pool Base64 Caps**
Sources pool rows may include `base64` for binary/pdf/image items. To avoid prompt blowups, base64 is capped at normalization time and in web search results.

- **Search results** (web_search backend)
  - Env: `WEB_SEARCH_MAX_BASE64_CHARS`
  - Fallback: `SOURCES_POOL_MAX_BASE64_CHARS`
  - Default: `4000`
  - Effect: if `len(base64) > limit` then `base64` is dropped from the search row before it reaches the timeline/sources_pool.

- **Sources pool normalization** (all tools)
  - Env: `SOURCES_POOL_MAX_BASE64_CHARS`
  - Default: `4000`
  - Effect: base64 bigger than the limit is removed when sources are normalized.

- **Attachments / file-like sources**
  - Env: `SOURCES_POOL_MAX_BASE64_CHARS_ATTACHMENTS`
  - Default: falls back to `SOURCES_POOL_MAX_BASE64_CHARS`
  - Applies to sources marked with `source_type=attachment|file` or paths containing `attachments/`.

---

**Declared File Tool Results**

Custom tools can ask React to host files by returning a marked file payload
inside the standard `{ok, error, ret}` envelope:

```json
{
  "ok": true,
  "error": null,
  "ret": {
    "artifact_type": "files",
    "files": [
      {
        "type": "file",
        "visibility": "external",
        "physical_path": "turn_123/outputs/report.pdf",
        "filename": "report.pdf",
        "mime_type": "application/pdf"
      }
    ]
  }
}
```

`artifact_type` currently recognizes only the declared-file family. The only
valid value is `files`. Other artifact concepts such as `display`,
`search`, or normal `file` artifact kinds exist in timeline/artifact metadata,
but they are not separate `artifact_type` values.

Declared files still follow the same artifact hosting and prompt-safety limits:

- The file must already exist where `physical_path` points or be identified by
  an existing hosted `rn`/`key`/`hosted_uri`.
- `visibility=external` files are hosted as conversation artifacts.
- Base64 included in timeline/sources rows is capped by the source-pool limits
  above.

Trusted tools can also host with `bundle_tool_context.host_files(...)`, but only
after the SDK has prepared the tool runtime. That preparation must provide an
active tool subsystem with a hosting service, communicator scope for
tenant/project/user/conversation/turn, conversation storage, and an output
directory. Normal React workflows prepare it in `BaseWorkflow.build_react(...)`;
isolated execution prepares it in `bootstrap_bind_all(...)`. Without that
context, the helper raises a runtime error and no hosted artifact is created.

---

**Timeline Truncation (TTL / Smart Pruning)**
(From `kdcube_ai_app/apps/chat/sdk/solutions/react/v2/session.py`)

- **User/Assistant text**
  - `cache_truncation_max_text_chars` (default `4000`)
  - Used for generic truncation of user/assistant blocks in old turns.

- **Tool results**
  - `cache_truncation_max_tool_text_chars` (default `400`)
  - Applied to tool result replacement payloads.
  - Tool result lists/dicts are further capped by:
    - `cache_truncation_max_list_items` (default `50`)
    - `cache_truncation_max_dict_keys` (default `80`)

- **Base64 in timeline blocks**
  - `cache_truncation_max_base64_chars` (default `4000`)
  - Oversized base64 blocks are replaced with a file placeholder.

- **Skills loaded by `react.read`**
  - Pruned in old turns with:
    - `[content removed by pruning, reread with react.read if needed: sk:...]`

---

**Notes**
- These limits are independent from storage retention or hosting. They only control what is included in the timeline context or sources_pool.
- Use `react.read` to rehydrate hidden artifacts when needed.
