# Citations & Sources System

*Scope: Web search, source reconciliation, LLM generation, citations.*

---

## 1. Goals & Design Principles

We want our agents to:

* Use **web and internal sources** in a structured way.
* Keep a **clear trace** from any claim back to the sources that support it.
* **Control token usage** by differentiating between short snippets and full content.
* Allow different tools to consume sources in a consistent format.

The system is built around:

1. A **canonical source object model** (`sid`, `title`, `url`, `text`, `content`, + metadata).
2. A set of **tools** that produce or consume sources:

    * `web_tools.web_search`
    * `web_tools.fetch_url_contents`
    * `llm_tools.generate_content_llm`
    * `write_*` renderers (for parsing citations from output content)
3. A **citation protocol** that encodes how sources are referenced in different output formats.

---

## 2. Canonical Source Object Model

All components should converge on this shape when passing sources around:

```jsonc
{
  "sid": 123,                      // required, int
  "title": "Some page title",      // required, best-effort
  "url": "https://example.com",    // optional but recommended

  // Short snippet / preview (cheap, for reconcilers, UI, and fallback):
  "text": "Short excerpt or summary…",

  // Full body (expensive, used when deep reasoning is needed):
  "content": "Full extracted article or page text…",

  // Optional: relevance annotation from reconciler
  "objective_relevance": 0.95,
  "query_relevance": 0.87,         // or per-query details upstream
  "reasoning": "Why this source is relevant…",

  // Optional: freshness metadata
  "published_time_iso": "2025-01-02T12:34:56Z",
  "modified_time_iso": "2025-01-03T10:00:00Z"
}
```

### 2.1 `text` vs `content`

* **`text`**

    * Short snippet / summary / first chunk of content.
    * Used in:

        * `sources_reconciler` as the “body” it scores,
        * UI previews,
        * cheap LLM calls.
    * Typical length: one or a few paragraphs (or a trimmed version of the full article).

* **`content`**

    * Full extracted body (article, doc, long page).
    * Used when:

        * we explicitly fetched full content (`web_search(fetch_content=True)`),
        * or we want deep, high-quality synthesis in `generate_content_llm`.

**Important:** Tools and agents can control LLM cost vs quality by deciding whether to populate `content` when calling downstream LLM tools.

---

## 3. Source Lifecycle & Tools

### 3.1 `web_tools.web_search`

`web_search` is responsible for:

* Running multiple queries and interleaving results.
* Deduplicating by URL.
* Assigning SIDs.
* Returning a list of canonical sources (`sid`, `title`, `url`, `text`, optionally `content`).

The results are merged into the turn’s `sources_pool`.

---

### 3.2 `web_tools.fetch_url_contents`

`fetch_url_contents` fetches full content for specific URLs and returns canonical sources
with `sid`, `url`, `title`, and `content`/`text`. These rows are merged into `sources_pool`.

---

### 3.3 `llm_tools.generate_content_llm`

`generate_content_llm` is the general LLM wrapper that:

* Takes `sources_list` (list of sources).
* Builds a **sources digest** to feed into the prompt.
* Enforces:

    * Output format (markdown, html, json, yaml, xml, mermaid),
    * Optional JSON Schema,
    * Citation requirements (if enabled).

#### 3.4.1 How it ingests sources

Relevant part (simplified):

```python
raw_sources = sources_list or []

rows = []
for s in raw_sources or []:
    if not isinstance(s, dict):
        continue
    sid = s.get("sid")
    title = s.get("title") or ""
    body = s.get("content") or s.get("text") or ""
    if sid is None:
        continue
    rows.append({"sid": int(sid), "title": title, "text": body})

# Build sid_map and digest
sid_map = "\n".join([f"- {r['sid']}: {r['title'][:160]}" for r in rows])
total_budget = 10000
per = max(600, total_budget // max(1, len(rows))) if rows else 0
parts = []
for r in rows:
    t = (r["text"] or "")[:per]
    parts.append(f"[sid:{r['sid']}] {r['title']}\n{t}".strip())
digest = "\n\n---\n\n".join(parts)[:total_budget]
```

**Important:**

* If `content` is present, it is preferred for the digest.
  → Full documents yield a detailed context for the LLM.

`generate_content_llm` returns `sources_used` (SIDs) extracted from citations or sidecar
citations. These SIDs are persisted into the produced artifacts and into `sources_used.json`
by artifact name.

---

### 3.4 `write_*` renderers

Write tools parse citations from the input content and update `sources_used.json`
by filename. This lets file artifacts inherit accurate `sources_used` even when the
renderer is called from generated code.

---

### 3.5 File & Attachment Sources

Files and attachments (e.g., images) can appear as sources in the pool:

* `source_type: "file"` or `"attachment"`
* `local_path` (OUT_DIR-relative)
* `mime`, `size_bytes`, `title`

This is how multimodal inputs are represented and traced in citations.

---

### 3.6 Cross-turn carry & reconciliation

The context browser builds a unified `sources_pool` from prior turns and reconciles SIDs
by URL. When current work uses prior artifacts, their `sources_used` SIDs are carried
forward and merged so citations remain stable. The React context marks `sources_pool[*].used`
based on the aggregated `sources_used.json` records.
* If `content` is absent, it falls back to `text`.
  → Cheaper, snippet-based reasoning.

So **whether you set `content` on a source directly controls how deeply the LLM “sees” it**.

---

## 4. Citation Protocol

When `cite_sources=True` and sources are provided, `generate_content_llm` enforces citations in a format-dependent way.

### 4.1 Markdown / Text

Inline tokens of the form:

```text
[[S:1]]
[[S:1,3]]
[[S:2-4]]
```

* Place tokens at the end of sentences or bullet points that introduce **new or materially changed facts**.
* Multiple SIDs:

    * `[[S:1,3]]` → sources 1 and 3
    * `[[S:2-4]]` → sources 2,3,4
* Never use SIDs not present in `sources_list`.

**Code blocks rule:**

* Never put `[[S:n]]` **inside fenced code blocks** (`...`).
* Put citations in surrounding prose.

### 4.2 HTML

Inline `<sup>` tags, e.g.:

```html
<sup class="cite" data-sids="1,3">[S:1,3]</sup>
```

* Same semantic rules as markdown:

    * After factual claims.
    * Use only allowed SIDs.

A “footnotes” or “Sources” section with `[S:n]` markers is also acceptable and detected by validators.

### 4.3 JSON / YAML (Sidecar)

When `target_format` is `json` or `yaml` and citations are required, we use a **sidecar** structure at a JSON Pointer (usually `/_citations`):

```jsonc
{
  "some": {
    "nested": {
      "field": "The CPU is 2.5x faster…"  // <-- claimed text
    }
  },
  "_citations": [
    {
      "path": "/some/nested/field",
      "sids": [3, 5]
    }
  ]
}
```

Rules:

* `path` is a JSON Pointer to a **string field** that holds the claim.
* `sids` is an array of allowed SIDs.
* The sidecar is validated by `_validate_sidecar` to ensure:

    * Paths exist (best effort),
    * SIDs are valid,
    * Types are correct.

Optionally, we can also allow inline `[[S:n]]` **inside** strings, but the sidecar is still authoritative.

### 4.4 Usage Telemetry

When citations are *not* required, but sources are provided, the model is instructed to record which sources it actually used via a hidden tag:

```text
[[USAGE:1,3,5]]
```

This tag is:

* Automatically stripped from the final content.
* Parsed to compute `usage_sids`.
* Combined with inline / sidecar citations into `sources_used`.

---

## 5. `sources_used` in the LLM Envelope

At the end of `generate_content_llm`, the wrapper returns an envelope:

```jsonc
{
  "ok": true,
  "content": "...final rendered answer...",
  "format": "markdown",
  "finished": true,
  "retries": 0,
  "reason": "",
  "stats": {
    "rounds": 1,
    "bytes": 12345,
    "validated": "both",
    "citations": "present"
  },
  "sources_used": [
    {
      "sid": 1,
      "url": "https://example.com/...",
      "title": "Some page title",
      "text": "Short excerpt…"  // or fallback from meta
    },
    ...
  ]
}
```

Currently we populate `sources_used` from `build_citation_map_from_sources(sources_list)`, with:

```python
sources_used.append({
    "sid": sid,
    "url": meta.get("url", ""),
    "title": meta.get("title", ""),
    "text": meta.get("text") or meta.get("body") or meta.get("content") or "",
})
```

If we want richer telemetry (e.g. include `content`, timestamps, etc.), we can extend this structure — the contract is **ours**, internal to the system.

---

## 6. How Agents Should Use This System

### 6.1 When building sources

* Always normalize to:

  ```json
  { "sid": int, "title": str, "url"?: str, "text"?: str, "content"?: str, ... }
  ```

* Use `adapt_source_for_llm` as a single canonical helper for this.

### 6.2 Choosing between `text` and `content`

* Use `include_full_content=True` when:

    * You want detailed synthesis,
    * You’re okay with higher token cost,
    * The number of sources is moderate.

* Use `include_full_content=False` when:

    * You only need high-level answers,
    * You’re calling the LLM frequently (e.g. many small queries),
    * You have a lot of sources and want to keep the context shallow.

### 6.3 Plugging into specific tools

* **Reconciler:** pass `{sid, title, text}` only.
* **Content filter:** pass `{sid, content, ...dates}` only.
* **LLM generator (`generate_content_llm`):** pass full canonical sources and let it decide based on `content` vs `text`.

---
