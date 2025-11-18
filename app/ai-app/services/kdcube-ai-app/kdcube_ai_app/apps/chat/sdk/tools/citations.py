# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# chat/sdk/tools/citations.py
#
# Unified citations utilities:
# - Map builders (from sources_json or citations[])
# - Token expansion [[S:n]], [[S:1,3]], [[S:2-5]]
# - Rendering (links by default, optional superscripts or image embedding)
# - Streaming-safe helpers (don’t cut tokens across chunk boundaries)
# - Batch & streaming replacement
# - References sections
# - Presence checks for inline citations (Markdown/HTML)
# - Back-compat wrappers for older call-site names

# How to use (summary)
# Streaming: in your streamer, keep a tail buffer and call
# safe_prefix, dangling = split_safe_citation_prefix(slice)
# emit( replace_citation_tokens_streaming(safe_prefix, cmap) )
#
# Batch: when you’re done, call
# replace_citation_tokens_batch(full_text, cmap, CitationRenderOptions(...))
#
# Map building:
# From sources_json: build_citation_map_from_sources(sources_json)
# From citations[]: build_citation_map_from_citations(citations)

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Iterable, Any, Set

# ---------------------------------------------------------------------------
# Public regex / constants
# ---------------------------------------------------------------------------

# [[S:1]] / [[S:1,3]] / [[S:2-5]] (whitespace tolerant)
# CITE_TOKEN_RE = re.compile(r"\[\[\s*S\s*:\s*([0-9,\s\-]+)\s*\]\]", re.I)
# In citations.py, update the pattern:
CITE_TOKEN_RE = re.compile(r"(\s?)\[\[\s*S\s*:\s*([0-9,\s\-]+)\s*\]\]", re.I)

# HTML inline cite (your protocol)
HTML_CITE_RE = re.compile(
    r'<sup[^>]*class="cite"[^>]*data-sids="[^"]+"[^>]*>.*?</sup>',
    re.I | re.S
)

HTML_DATASIDS_RE = re.compile(
    r'(?is)<sup[^>]*class="[^"]*\bcite\b[^"]*"[^>]*\sdata-sids\s*=\s*"([^"]+)"[^>]*>.*?</sup>'
)
HTML_BRACKET_S_RE = re.compile(r'\[S:\s*([0-9,\s\-–]+)\]', re.I)


# Markdown inline cite presence (fast check)
MD_CITE_RE = re.compile(r"\[\[\s*S\s*:\s*\d+(?:\s*,\s*\d+)*\s*\]\]", re.I)

# Suffix patterns used to avoid cutting tokens at streaming chunk boundaries
# CITATION_SUFFIX_PATS = [
#     re.compile(r"\s?\[\[$"),                           # optional space + "[[" at end
#     re.compile(r"\s?\[\[S:$", re.I),                   # optional space + "[[S:" at end
#     re.compile(r"\s?\[\[S:\s*[0-9,\s\-]*$", re.I),     # optional space + "[[S:1, 2-5"
#     re.compile(r"\s?\[\[S:\s*[0-9,\s\-]*\]$", re.I),   # optional space + "[[S:1]" (missing final ']')
# ]

CITATION_SUFFIX_PATS = [
    re.compile(r"(?:\u200b|\s)?\[\[$"),                           # "[[" at end
    re.compile(r"(?:\u200b|\s)?\[\[S:$", re.I),
    re.compile(r"(?:\u200b|\s)?\[\[S:\s*[0-9,\s\-]*$", re.I),
    re.compile(r"(?:\u200b|\s)?\[\[S:\s*[0-9,\s\-]*\]$", re.I),
]

# ---- shared optional attributes carried through citations ----
CITATION_OPTIONAL_ATTRS = (
    "provider", "published_time_iso", "modified_time_iso", "expiration",
    "mime", "source_type", "rn", "author",
    "content_length", "fetch_status",
    "objective_relevance", "query_relevance",
)

canonical_source_shape_reference = {
    "sid": int,
    "url": str,
    "title": str,
    # short snippet/preview (from search result)
    "text": str,
    # full content (if fetched)
    "content": str,              # optional
    "content_length": int,       # optional

    # metadata
    "provider": str, # "web" | "kb" | ...
    "source_type": str, # "web_search" | "kb" | ...
    "published_time_iso": str | None,
    "modified_time_iso": str | None,
    "fetch_status": str | None,

    # scoring
    "objective_relevance": float | None,
    "query_relevance": float | None,
}

# ---- URL normalization (canonical; strips UTM/gclid/fbclid; stable ordering) ----
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
_UTM_PARAMS = {"utm_source", "utm_medium", "utm_campaign","utm_term","utm_content","utm_id","gclid","fbclid"}

def normalize_url(u: str) -> str:
    try:
        if not u: return ""
        s = urlsplit(u.strip())
        scheme = (s.scheme or "https").lower()
        netloc = s.netloc.lower().rstrip(":80").rstrip(":443")
        path = s.path or "/"
        # drop anchors
        fragment = ""
        # drop tracking params & keep stable order
        q = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True) if k.lower() not in _UTM_PARAMS]
        query = urlencode(q, doseq=True)
        # strip trailing slash for canonicalization (except root)
        if path != "/" and path.endswith("/"):
            path = path[:-1]
        return urlunsplit((scheme, netloc, path, query, fragment))
    except Exception:
        return (u or "").strip()

# ---- item and collection normalizers ----
def normalize_citation_item(it: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Accepts loose shapes with keys like url|href|value, title|description, text|body|content.
    Returns {url,title,text,sid?,+optional_attrs} or None if url missing.
    """
    if not isinstance(it, dict):
        return None
    url = (it.get("url") or it.get("href") or it.get("value") or "").strip()
    if not url:
        return None
    url = normalize_url(url)

    title = (it.get("title") or it.get("description") or url).strip()
    # Short snippet / preview
    snippet = (
        it.get("text") or
        it.get("body") or
        it.get("value_preview") or
        ""
    )
    snippet = snippet.strip()

    # Full content (if present)
    full_content = (it.get("content") or "").strip()

    sid = it.get("sid")
    try:
        sid = int(sid) if sid is not None and str(sid).strip() != "" else None
    except Exception:
        sid = None

    # ⬇️ carry rich attrs if present
    out = {"url": url, "title": title, "text": snippet}
    if sid is not None:
        out["sid"] = sid
    if full_content:
        out["content"] = full_content

    for k in CITATION_OPTIONAL_ATTRS:
        if it.get(k) not in (None, ""):
            out[k] = it[k]
    return out

def normalize_sources_any(val: Any) -> List[Dict[str, Any]]:
    """
    Accepts list|dict|JSON-string and returns a normalized list of {sid?,title,url,text,+optional_attrs}.
    (Consolidates _as_rows / _norm_sources behavior)
    """
    if not val:
        return []
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return []

    rows: List[Dict[str, Any]] = []
    if isinstance(val, dict):
        for k, v in val.items():
            if not isinstance(v, dict):
                continue
            row = normalize_citation_item({**v, "sid": v.get("sid") or (int(k) if str(k).isdigit() else None)})
            if row:
                rows.append(row)
        return rows

    if isinstance(val, list):
        for v in val:
            row = normalize_citation_item(v) if isinstance(v, dict) else None
            if row:
                rows.append(row)
        return rows

    return []

def dedupe_sources_by_url(prior: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicate by normalized URL; preserve richer title/text; carry optional attrs; assign/keep sids.
    Returns sorted by sid ascending.
    """
    by_url: Dict[str, Dict[str, Any]] = {}
    max_sid = 0

    def _touch(row: Dict[str, Any]):
        nonlocal max_sid
        url = normalize_url(row.get("url",""))
        if not url:
            return
        if url in by_url:
            existing = by_url[url]
            if len(row.get("title","")) > len(existing.get("title","")):
                existing["title"] = row.get("title","")
            if len(row.get("text","")) > len(existing.get("text","")):
                existing["text"] = row.get("text","")

            # Richer full content wins
            if len(row.get("content", "")) > len(existing.get("content", "")):
                existing["content"] = row.get("content", "")

            # copy metadata if missing
            for k in CITATION_OPTIONAL_ATTRS:
                if not existing.get(k) and row.get(k):
                    existing[k] = row[k]

            # best scoring
            try:
                if row.get("objective_relevance") is not None:
                    existing["objective_relevance"] = max(
                        float(existing.get("objective_relevance") or 0.0),
                        float(row["objective_relevance"]),
                    )
            except Exception:
                pass

            try:
                if row.get("query_relevance") is not None:
                    existing["query_relevance"] = max(
                        float(existing.get("query_relevance") or 0.0),
                        float(row["query_relevance"]),
                    )
            except Exception:
                pass
            if isinstance(row.get("sid"), int):
                existing["sid"] = existing.get("sid") or row["sid"]
                max_sid = max(max_sid, int(existing["sid"]))
            return
        # NEW URL path
        sid = row.get("sid")
        try:
            sid = int(sid) if sid is not None and sid > 0 else None
        except Exception:
            sid = None
        if not sid:
            max_sid += 1
            sid = max_sid
        else:
            max_sid = max(max_sid, sid)

        kept = {"sid": sid, "url": url, "title": row.get("title",""), "text": row.get("text","")}
        if row.get("content"):
            kept["content"] = row["content"]
        for k in CITATION_OPTIONAL_ATTRS:
            if row.get(k):
                kept[k] = row[k]
        by_url[url] = kept

    for r in prior or []:
        _touch(r)
    for r in new or []:
        _touch(r)

    return sorted(by_url.values(), key=lambda x: x["sid"])

# ---- SID utilities (aliases / helpers) ----
def sids_in_text(text: str) -> List[int]:
    return extract_citation_sids_from_text(text)

def rewrite_citation_tokens(md: str, sid_map: Dict[int, int]) -> str:
    """
    Replace [[S:1,2]] with [[S:a,b]] using sid_map; drop tokens that fully fail to map.
    """
    if not md or not sid_map:
        return md or ""
    def repl(m: re.Match) -> str:
        body = m.group(1)
        nums = []
        for p in (body or "").split(","):
            p = p.strip()
            if not p.isdigit():
                continue
            old = int(p)
            new = sid_map.get(old)
            if new:
                nums.append(str(new))
        return f"[[S:{','.join(nums)}]]" if nums else ""
    return re.sub(r"\[\[\s*S\s*:\s*([0-9,\s]+)\s*\]\]", repl, md, flags=re.I)

# ---------------------------------------------------------------------------
# Render options
# ---------------------------------------------------------------------------

@dataclass
class CitationRenderOptions:
    """
    Controls how citations render when replacing [[S:...]].

    mode:
      - "links"       → [Title](url) (default)
      - "superscript" → ¹ ² (if url exists, we still render as links with superscripts)
    embed_images: if True and url looks like an image, embed as ![alt](url) + caption
    keep_unresolved: if True, keep the original [[S:...]] if none resolve. If False, drop it.
    first_only: if True, only render the first SID from a group [[S:1,2,3]] → only S:1
    """
    mode: str = "links"
    embed_images: bool = False
    keep_unresolved: bool = True
    first_only: bool = False

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _superscript_num(n: int) -> str:
    _map = {"0":"⁰","1":"¹","2":"²","3":"³","4":"⁴","5":"⁵","6":"⁶","7":"⁷","8":"⁸","9":"⁹"}
    return "".join(_map.get(ch, ch) for ch in str(n))

def _looks_like_image(url: str) -> bool:
    if not url:
        return False
    image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tiff")
    clean = url.split("?", 1)[0].lower()
    return any(clean.endswith(ext) for ext in image_exts)

# --- extend _expand_ids to support en dash (–) ranges safely ---
def _expand_ids(ids_str: str) -> List[int]:
    out: List[int] = []
    if not ids_str:
        return out
    for part in ids_str.split(","):
        p = part.strip()
        if not p:
            continue
        # support hyphen-minus and en dash
        if "-" in p or "–" in p:
            p2 = p.replace("–", "-")
            try:
                a, b = [int(x.strip()) for x in p2.split("-", 1)]
                lo, hi = (a, b) if a <= b else (b, a)
                out.extend(range(lo, hi + 1))
            except Exception:
                continue
        else:
            if p.isdigit():
                out.append(int(p))
    # in-order de-dupe
    seen = set()
    uniq: List[int] = []
    for i in out:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq

def _normalize_sources(sources_json: Optional[str]) -> Tuple[Dict[int, Dict[str, Any]], List[int]]:
    """
    Accepts:
      - JSON array: [{sid?, title?, url?, ...}, ...] (sid is 1-based; if missing, index+1 is used)
      - or JSON object: { "1": {...}, "2": {...}, ... }

    Returns: (by_id, order_ids)
    """
    if not sources_json:
        return {}, []
    try:
        src = json.loads(sources_json)
    except Exception:
        return {}, []

    by_id: Dict[int, Dict[str, Any]] = {}
    order: List[int] = []

    if isinstance(src, list):
        for i, row in enumerate(src):
            if not isinstance(row, dict):
                continue
            sid = row.get("sid", i + 1)
            try:
                sid = int(sid)
            except Exception:
                continue
            by_id[sid] = row
            order.append(sid)
    elif isinstance(src, dict):
        for k, row in src.items():
            try:
                sid = int(k)
            except Exception:
                continue
            if isinstance(row, dict):
                by_id[sid] = row
                order.append(sid)
    return by_id, order

# ---------------------------------------------------------------------------
# Map builders
# ---------------------------------------------------------------------------

def build_citation_map_from_sources(sources_json: Optional[str]) -> Dict[int, Dict[str, str]]:
    """
    Build map {sid: {"title": ..., "url": ...}} from sources_json.
    """
    by_id, _ = _normalize_sources(sources_json)
    out: Dict[int, Dict[str, str]] = {}
    for sid, row in by_id.items():
        out[sid] = {
            "title": (row.get("title") or "").strip(),
            "url": (row.get("url") or row.get("href") or "").strip(),
            "text": row.get("text") or row.get("body") or row.get("content") or ""
        }
    return out

def build_citation_map_from_citations(citations: Iterable[Dict[str, Any]]) -> Dict[int, Dict[str, str]]:
    """
    Build map {sid: {"title": ..., "url": ..., "text": ...?}} from a citations[]
    where each item can be like: {sid, title, url, text?}.
    Unknown / missing sids are ignored.
    """
    out: Dict[int, Dict[str, str]] = {}
    for c in citations or []:
        sid = c.get("sid")
        try:
            sid = int(sid)
        except Exception:
            continue
        out[sid] = {
            "title": (c.get("title") or c.get("text") or "").strip(),
            "url": (c.get("url") or "").strip(),
            "text": (c.get("text") or c.get("body") or "").strip(),
        }
    return out

def extract_sids(sources_json: Optional[str]) -> List[int]:
    """Convenience for validation layers."""
    by_id, order = _normalize_sources(sources_json)
    return order or list(by_id.keys())

# ---------------------------------------------------------------------------
# Streaming-safe helpers
# ---------------------------------------------------------------------------

def split_safe_citation_prefix(chunk: str) -> Tuple[str, int]:
    """
    Given a partial chunk, return (safe_prefix, dangling_len).
    If the end of chunk looks like a truncated [[S:...]] token, we clip it off
    and report how many chars were withheld (dangling).
    """
    if not chunk:
        return "", 0
    for pat in CITATION_SUFFIX_PATS:
        m = pat.search(chunk)
        if m and m.end() == len(chunk):
            return chunk[:m.start()], len(chunk) - m.start()
    return chunk, 0

# ---------------------------------------------------------------------------
# Replacement / rendering (batch & streaming)
# ---------------------------------------------------------------------------

@dataclass
class _LegacyOpts:
    # used for back-compat wrapper
    embed_images: bool = True
    first_only: bool = True
    keep_unresolved: bool = False

def _render_links(ids: List[int], cmap: Dict[int, Dict[str, str]], opts: CitationRenderOptions) -> str:
    if not ids:
        return ""
    if opts.first_only:
        ids = ids[:1]

    out: List[str] = []

    for sid in ids:
        rec = cmap.get(sid) or cmap.get(str(sid))
        if not rec:
            continue

        url = (rec.get("url") or "").strip()
        title = (rec.get("title") or url or f"Source {sid}").replace('"', "'")

        if not url:
            # no URL: in links mode → just title; in superscript mode → superscript numeral
            if opts.mode == "superscript":
                out.append(_superscript_num(sid))
            else:
                out.append(title)
            continue

        # image embedding if asked
        if opts.embed_images and _looks_like_image(url):
            alt = title[:100] + "..." if len(title) > 100 else title
            out.append(f"\n\n![{alt}]({url})\n*Source {sid}: {title}*\n")
            continue

        if opts.mode == "superscript":
            sup = _superscript_num(sid)
            out.append(f"[{sup}]({url} \"{title}\")")
        else:
            out.append(f"[{title}]({url})")

    return " ".join(out)

def replace_citation_tokens_batch(
        text: str,
        citation_map: Dict[int, Dict[str, str]],
        options: Optional[CitationRenderOptions] = None,
) -> str:
    """
    Batch (non-streaming) replacement of [[S:...]] tokens.
    Default rendering is links; configure via CitationRenderOptions.
    """
    if not citation_map:
        return text
    opts = options or CitationRenderOptions()

    def _sub(m: re.Match) -> str:
        preceding_space = m.group(1)  # Captured optional space
        ids = _expand_ids(m.group(2))  # Now group 2 contains the IDs
        rendered = _render_links(ids, citation_map, opts)
        if rendered:
            return preceding_space + rendered  # Preserve the space
        return m.group(0) if opts.keep_unresolved else ""

    return CITE_TOKEN_RE.sub(_sub, text)

def replace_citation_tokens_streaming(
        text: str,
        citation_map: Dict[int, Dict[str, str]],
        options: Optional[CitationRenderOptions] = None,
) -> str:
    """
    Streaming-safe replacement: idempotent on the chunk received.
    You still need to AVOID cutting tokens: use split_safe_citation_prefix()
    in your streamer before calling this function on the prefix you emit.
    """
    return replace_citation_tokens_batch(text, citation_map, options)

# ---------------------------------------------------------------------------
# Presence checks
# ---------------------------------------------------------------------------

def citations_present_inline(content: str, fmt: str) -> bool:
    """
    Minimal presence test for inline citations in a rendered document.
    - markdown/text: looks for [[S:n...]] tokens
    - html: looks for <sup class="cite" data-sids="...">…</sup>
    """
    if fmt in ("markdown", "text"):
        return bool(MD_CITE_RE.search(content))
    if fmt == "html":
        return bool(HTML_CITE_RE.search(content))
    return False

# ---------------------------------------------------------------------------
# References sections
# ---------------------------------------------------------------------------

def create_clean_references_section(by_id: Dict[int, Dict[str, Any]], order: List[int]) -> str:
    """
    Build a compact '## References' section (clean titles, trimmed).
    """
    if not by_id or not order:
        return ""
    lines = ["", "---", "", "## References", ""]
    for sid in order:
        meta = by_id.get(sid) or {}
        url = (meta.get("url") or meta.get("href") or "").strip()
        title = (meta.get("title") or "").strip() or f"Source {sid}"
        if not url:
            continue
        if len(title) > 80:
            title = title[:77] + "..."
        lines.append(f"{sid}. [{title}]({url})")
    return "\n" + "\n".join(lines) + "\n"

def append_sources_section_if_missing(text: str, by_id: Dict[int, Dict[str, Any]], order: List[int]) -> str:
    """
    Append a '## Sources' section if the doc doesn't already contain it.
    """
    if not by_id or not order:
        return text
    if re.search(r"^##\s+Sources\b", text, flags=re.IGNORECASE | re.MULTILINE):
        return text
    lines = ["", "---", "", "## Sources", ""]
    for sid in order:
        meta = by_id.get(sid) or {}
        url = (meta.get("url") or meta.get("href") or "").strip()
        title = (meta.get("title") or url or f"Source {sid}").strip()
        if not url:
            continue
        lines.append(f"{sid}. [{title}]({url})")
    return text + "\n".join(lines) + "\n"

# ---------------------------------------------------------------------------
# Extract SIDs from text
# ---------------------------------------------------------------------------

def extract_citation_sids_from_text(text: str) -> List[int]:
    """
    Extract all SID references from text like [[S:1]], [[S:2,3]], [[S:4-6]].
    Returns sorted list of unique SIDs.
    """
    if not text or not isinstance(text, str):
        return []

    pattern = r'\[\[S:([0-9,\-\s]+)\]\]'
    matches = re.findall(pattern, text)
    sids: Set[int] = set()

    for match in matches:
        # Handle comma-separated: "1,2,3"
        for part in match.split(','):
            part = part.strip()
            if not part:
                continue
            # Handle ranges: "4-6"
            if '-' in part:
                try:
                    start, end = part.split('-', 1)
                    sids.update(range(int(start), int(end) + 1))
                except ValueError:
                    pass
            elif part.isdigit():
                sids.add(int(part))

    return sorted(sids)

def extract_citation_sids_from_html(html: str) -> List[int]:
    """
    Extract SIDs from <sup class="cite" ... data-sids="..."> and/or [S:...] text inside HTML.
    Returns a sorted list of unique ints.
    """
    if not isinstance(html, str) or not html:
        return []
    sids: Set[int] = set()

    # 1) Prefer authoritative data-sids
    for m in HTML_DATASIDS_RE.finditer(html):
        sids.update(_expand_ids(m.group(1)))

    # 2) Fallback: [S:...] markers inside the HTML (including inside <sup>)
    #    This also catches stray markdown-style markers left in HTML.
    for m in HTML_BRACKET_S_RE.finditer(html):
        sids.update(_expand_ids(m.group(1)))

    # 3) And as a last fallback, reuse the markdown token extractor
    #    in case there are raw [[S:...]] tokens in HTML text nodes.
    if not sids:
        sids.update(extract_citation_sids_from_text(html))

    return sorted(sids)

# --- convenience for any content (markdown/text/html) ---
def extract_citation_sids_any(text: str) -> List[int]:
    """
    Heuristic extractor that handles either HTML <sup class="cite"...> or [[S:...]] tokens.
    """
    if not isinstance(text, str) or not text:
        return []
    # If it looks like HTML with <sup class="cite"...>, use the HTML path
    if HTML_CITE_RE.search(text):
        return extract_citation_sids_from_html(text)
    # Otherwise, treat as markdown/text
    return extract_citation_sids_from_text(text)

# ---------------------------------------------------------------------------
# Back-compat wrappers (aliases used by existing code)
# ---------------------------------------------------------------------------

# Builders
_build_citation_map_from_sources = build_citation_map_from_sources
_extract_sids = extract_sids

# Replacement (streaming)
_replace_citation_tokens_streaming = replace_citation_tokens_streaming
_split_safe_citation_prefix = split_safe_citation_prefix

# Presence
_citations_present_inline = citations_present_inline

def _strip_invisible(text: str) -> str:
    # Remove ZWSP/BOM that commonly appear in LLM output and break regex matches
    if not isinstance(text, str):
        return text
    return text.replace("\u200b", "").replace("\ufeff", "")

# Legacy MD-only batch replacer (first-only + embed images)
def _replace_citation_tokens(md: str, by_id: Dict[int, Dict[str, str]], embed_images: bool = True) -> str:
    """
    Legacy behavior: keep only first sid, render links (or images if embed_images),
    and drop unresolved tokens. Preserved for compatibility with older callers.
    """
    opts = CitationRenderOptions(
        mode="links",
        embed_images=bool(embed_images),
        keep_unresolved=False,
        first_only=True,
    )
    return replace_citation_tokens_batch(md, by_id, opts)

def _append_sources_section(md: str, by_id: Dict[int, Dict[str, Any]], order: List[int]) -> str:
    return append_sources_section_if_missing(md, by_id, order)

def _create_clean_sources_section(by_id: Dict[int, Dict[str, Any]], order: List[int]) -> str:
    return create_clean_references_section(by_id, order)


# --- HTML helpers: turn [S:...] markers into links/superscripts -------------
def _render_html_sup_links(ids: List[int], cmap: Dict[int, Dict[str, str]]) -> str:
    """
    Render a <sup class="cite" data-sids="1,3">…</sup> whose content is a series
    of clickable [S:n] anchors (target=_blank). If URL missing, keep plain [S:n].
    """
    if not ids:
        return ""
    parts = []
    for sid in ids:
        rec = cmap.get(sid) or cmap.get(str(sid)) or {}
        url = (rec.get("url") or "").strip()
        label = f"[S:{sid}]"
        if url:
            parts.append(
                f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
            )
        else:
            parts.append(label)
    inner = " ".join(parts)
    data_sids = ",".join(str(i) for i in ids)
    return f'<sup class="cite" data-sids="{data_sids}">{inner}</sup>'

def replace_html_citations(
        html: str,
        citation_map: Dict[int, Dict[str, str]],
        keep_unresolved: bool = False,
        first_only: bool = True,
) -> str:
    """
    HTML processor:
      1) Replace [[S:...]] tokens with a concise <sup class="cite" data-sids="...">
         whose content is clickable [S:n] anchors ONLY (no titles inline).
      2) Replace existing <sup class="cite" data-sids="...">…</sup> with the same concise form.

    Notes:
    - Honors an optional leading space captured by CITE_TOKEN_RE to avoid visual overlap.
    - Setting first_only=True keeps just the first SID from a group for on-slide brevity.
    """
    if not isinstance(html, str) or not html or not citation_map:
        return html or ""

    html = _strip_invisible(html)

    # 1) Replace [[S:...]] tokens (CITE_TOKEN_RE: group(1)=optional leading space, group(2)=ids)
    def _sub_tokens(m: re.Match) -> str:
        ids = _expand_ids(m.group(2))
        if first_only and ids:
            ids = ids[:1]
        rendered = _render_html_sup_links(ids, citation_map)
        if rendered:
            return (m.group(1) or "") + rendered
        return m.group(0) if keep_unresolved else ""

    out = CITE_TOKEN_RE.sub(_sub_tokens, html)

    # 2) Replace existing <sup class="cite" data-sids="...">…</sup> placeholders
    def _sub_html_sup(m: re.Match) -> str:
        tag = m.group(0)
        m_ids = re.search(r'data-sids="([^"]+)"', tag, flags=re.I)
        if not m_ids:
            return tag if keep_unresolved else ""
        ids = _expand_ids(m_ids.group(1))
        if first_only and ids:
            ids = ids[:1]
        rendered = _render_html_sup_links(ids, citation_map)
        return rendered or (tag if keep_unresolved else "")

    out = HTML_CITE_RE.sub(_sub_html_sup, out)
    return out

def _to_str_for_llm(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def adapt_source_for_llm(
        src: Dict[str, Any],
        *,
        include_full_content: bool = True,
        max_text_len: int = 2000,
) -> Dict[str, Any]:
    """
    Normalize a raw source object into the canonical shape used by LLM tools.

    Output (subset of canonical_source_shape_reference):
      {
        "sid": int,
        "url": str,
        "title": str,
        "text"?: str,      # short snippet / preview
        "content"?: str,   # full body (optional)
        ...CITATION_OPTIONAL_ATTRS
      }

    Semantics:
      - We reuse normalize_citation_item() for URL/title/text/content + metadata.
      - `text`  is a short snippet (and may be derived from `content`).
      - `content` is the full body; only emitted when include_full_content=True.
      - We never emit legacy fields like `body`/`snippet`/`preview`.
    """
    if not isinstance(src, dict):
        return {}

    # First, reuse the existing normalization logic (url/title/text/content + metadata).
    # Note: normalize_citation_item may ignore entries without URL; that's OK here.
    base = normalize_citation_item(src) or {}
    if not base:
        # if no url, but upstream already guarantees sid/title, we can fallback:
        url = (src.get("url") or src.get("href") or "").strip()
        if not url:
            return {}
        base = {
            "url": url,
            "title": (src.get("title") or src.get("description") or url).strip(),
            "text": (src.get("text") or "").strip(),
            "content": (src.get("content") or "").strip(),
        }
        for k in CITATION_OPTIONAL_ATTRS:
            if src.get(k) not in (None, ""):
                base[k] = src[k]

    out: Dict[str, Any] = {}

    # sid: we expect upstream to set it; if not, try to coerce
    sid_raw = src.get("sid") or base.get("sid")
    try:
        sid = int(sid_raw)
    except Exception:
        sid = 0
    out["sid"] = sid

    # url + title
    url = (base.get("url") or "").strip()
    title = (base.get("title") or url or f"Source {sid}").strip()
    out["url"] = url
    out["title"] = title

    # snippet vs full content
    snippet_raw: Optional[str] = base.get("text") or src.get("text")
    full_raw: Optional[str] = base.get("content") or src.get("content")

    if snippet_raw is None and full_raw is not None:
        snippet_raw = full_raw

    snippet = _to_str_for_llm(snippet_raw) if snippet_raw is not None else ""
    full = _to_str_for_llm(full_raw) if full_raw is not None else ""

    if snippet and max_text_len > 0 and len(snippet) > max_text_len:
        snippet = snippet[:max_text_len]

    if snippet:
        out["text"] = snippet
    if include_full_content and full:
        out["content"] = full

    # carry standard metadata
    for k in CITATION_OPTIONAL_ATTRS:
        if base.get(k) not in (None, ""):
            out[k] = base[k]

    # also keep content_length if upstream set it
    if src.get("content_length") is not None:
        out["content_length"] = src["content_length"]

    return out