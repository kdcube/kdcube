# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# tools/scrap_utils.py
import json, re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup, NavigableString, Tag

import base64, os, mimetypes, requests
from urllib.parse import urljoin, urlparse
from typing import Dict, Optional, List, Any

from kdcube_ai_app.apps.utils.sql_dt_utils import _parse_utc_instant
from kdcube_ai_app.tools.extraction_types import ImageSpec

try:
    from markdownify import markdownify as _md_convert
except Exception:
    _md_convert = None

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def _get_soup(url: str, session: Optional[requests.Session] = None, **kwargs) -> BeautifulSoup:
    s = session or requests.Session()
    headers = {"User-Agent": UA, "Accept-Language": "en"}
    resp = s.get(url, headers=headers, timeout=kwargs.get("timeout", 20))
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    # make headers available to extractors (no API break)
    try:
        soup._http_last_modified = resp.headers.get("Last-Modified")
    except Exception:
        pass
    return soup

def _post_soup(url: str, data: Dict[str, str], session: Optional[requests.Session] = None, **kwargs) -> BeautifulSoup:
    s = session or requests.Session()
    headers = {"User-Agent": UA, "Accept-Language": "en"}
    resp = s.post(url, headers=headers, data=data, timeout=kwargs.get("timeout", 20))
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")

# ============================================================================
# Date parsing helpers (shared)
# ============================================================================

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_ISO_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")

def _parse_human_date(s: str) -> Optional[str]:
    """
    Parse dates like 'September 19, 2025' -> ISO date '2025-09-19'.
    Returns None if parsing fails.
    """
    s = (s or "").strip()
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    try:
        dt = datetime(int(m.group(3)), month, int(m.group(2)))
        return dt.date().isoformat()
    except Exception:
        return None

def _parse_date_any(s: str) -> Optional[datetime]:
    """
    Robust-ish parse for the typical things we see in HTML:
    - ISO 8601 (optionally with 'Z')
    - RFC 822/2822 / HTTP date (via email.utils.parsedate_to_datetime)
    - 'September 19, 2025'
    - '2025-9-19', '2025/09/19'
    """
    if not s:
        return None
    s = s.strip()

    # ISO 8601 (allow 'Z')
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass

    # RFC 2822/822 (e.g. RSS, HTTP)
    try:
        dt = parsedate_to_datetime(s)
        return dt
    except Exception:
        pass
    # "September 19, 2025"
    iso = _parse_human_date(s)
    if iso:
        try:
            y, m, d = map(int, iso.split("-"))
            return datetime(y, m, d)
        except Exception:
            pass
    # "2025-9-19" or "2025/09/19"
    m = _ISO_DATE_RE.search(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass

    return None

def _normalize_to_utc_iso(dt: datetime) -> str:
    """Ensure datetime is timezone-aware (UTC) and return isoformat()."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

# ============================================================================
# Misc helpers
# ============================================================================

def _text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""

def _safe_filename_from_url(src: str, i: int) -> str:
    name = (urlparse(src).path.rsplit("/", 1)[-1] or "").split("?")[0]
    if not name:
        name = f"img_{i:03d}"
    # strip path separators just in case
    name = name.replace("/", "_").replace("\\", "_")
    return name[:128]

def _guess_ext(content_type: Optional[str], fallback: str = ".bin") -> str:
    if not content_type:
        return fallback
    ct = content_type.split(";")[0].strip().lower()
    overrides = {"image/jpeg": ".jpg", "image/jpg": ".jpg"}
    return overrides.get(ct) or mimetypes.guess_extension(ct) or fallback

def _ensure_extension(name: str, content_type: Optional[str]) -> str:
    root, ext = os.path.splitext(name)
    if ext:
        return name
    return f"{root}{_guess_ext(content_type, '.bin')}"

def build_imagespecs_from_urls(
        post_url: str,
        image_urls: List[str],
        *,
        max_images: int = 64,
        timeout_sec: int = 10,
        max_image_bytes: int = 5 * 1024 * 1024,
) -> List[ImageSpec]:
    """Resolve, fetch, and package images as ImageSpec for HtmlPostPayload."""
    s = requests.Session()
    headers = {"User-Agent": UA, "Accept": "*/*"}
    seen = set()
    out: List[ImageSpec] = []

    for i, raw in enumerate(image_urls):
        if len(out) >= max_images:
            break

        # resolve relative → absolute
        resolved = urljoin(post_url, raw.strip())
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)

        content: bytes = b""
        content_type: Optional[str] = None
        filename = _safe_filename_from_url(resolved, i)

        # data: URI?
        if resolved.startswith("data:"):
            try:
                header, b64 = resolved.split(",", 1)
                content_type = header.split(";")[0][5:]  # after "data:"
                content = base64.b64decode(b64, validate=False)
            except Exception:
                continue
        else:
            # HTTP(S) fetch
            try:
                r = s.get(resolved, headers=headers, timeout=timeout_sec)
                r.raise_for_status()
                content = r.content
                content_type = r.headers.get("Content-Type")
            except Exception:
                continue

        if not content or len(content) > max_image_bytes:
            continue

        filename = _ensure_extension(filename, content_type)

        out.append(ImageSpec(
            src=resolved,
            filename=filename,
            content=content,               # bytes are OK (Pydantic will handle)
            content_type=content_type or None,
        ))
    return out

# ============================================================================
# Shared publication/modified date extraction
# ============================================================================

PublicationDateMeta = Dict[str, Optional[str] | float]

def extract_publication_dates_core(
    *,
    soup: Optional[BeautifulSoup],
    url: str,
    last_modified_header: Optional[str] = None,
    sitemap_lastmod: Optional[str] = None,
    archive_snapshot_date: Optional[str] = None,
    archive_snapshot_url: Optional[str] = None,
) -> PublicationDateMeta:
    """
    Shared date extractor used by both sync tools and async fetchers.

    It tries, in order:
    - JSON-LD Article/NewsArticle/BlogPosting
    - Meta tags (OpenGraph, article:*, DC/dcterms, citation_publication_date)
    - <time> / itemprop / common date classes
    - Parsely/Sailthru JSON blobs
    - Visible "Last updated" / "Published on" text patterns
    - schema.org WebPage/Website with dateModified / datePublished
    - URL patterns (/YYYY/MM/DD/)
    - HTTP Last-Modified header
    - Sitemap <lastmod> (mainly as modified, weakly as publish if nothing else)
    - Copyright footer year (weak publish)
    - Archive snapshot date (only as very weak modified if nothing else)

    Returns dict with keys:
      - published_time_raw / published_time_iso
      - modified_time_raw / modified_time_iso
      - archive_snapshot_date / archive_snapshot_url
      - date_method / date_confidence  (for the "main" content date)
    """
    out: PublicationDateMeta = {
        "published_time_raw": None,
        "published_time_iso": None,
        "modified_time_raw": None,
        "modified_time_iso": None,
        "archive_snapshot_date": archive_snapshot_date,
        "archive_snapshot_url": archive_snapshot_url,
        "date_method": None,
        "date_confidence": 0.0,
    }

    def _set(kind: str, raw: str, method: str, conf: float) -> bool:
        """
        Set published/modified time with best-method tracking.
        kind: "published" | "modified"
        """
        dt = _parse_date_any(raw)
        if not dt:
            return False
        iso = _normalize_to_utc_iso(dt)

        if kind == "published":
            out["published_time_raw"] = raw
            out["published_time_iso"] = iso
        elif kind == "modified":
            out["modified_time_raw"] = raw
            out["modified_time_iso"] = iso
        else:
            return False

        # Track best overall content date (for freshness)
        if conf > (out["date_confidence"] or 0.0):
            out["date_method"] = method
            out["date_confidence"] = conf
        return True

    # ----------------------------- 1) JSON-LD Article -------------------------
    if soup:
        try:
            for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
                try:
                    data = json.loads(node.string or "")
                except Exception:
                    continue
                objs = data if isinstance(data, list) else [data]
                for obj in objs:
                    if not isinstance(obj, dict):
                        continue
                    types = obj.get("@type") or obj.get("type")
                    if isinstance(types, str):
                        types = [types]
                    types = [t.lower() for t in (types or []) if isinstance(t, str)]

                    if any(t in ("article", "newsarticle", "blogposting") for t in types):
                        pub = obj.get("datePublished") or obj.get("dateCreated")
                        mod = obj.get("dateModified") or obj.get("dateUpdated")
                        if pub:
                            _set("published", pub, "jsonld:article", 0.98)
                        if mod:
                            _set("modified", mod, "jsonld:article:modified", 0.95)
        except Exception:
            pass

    # -------------------------- 2) Meta tags (OG/Article/DC) ------------------
    if soup:
        try:
            meta_pub_keys = [
                ("property", "article:published_time"),
                ("name", "article:published_time"),
                ("property", "og:published_time"),
                ("name", "pubdate"),
                ("name", "publish-date"),
                ("name", "date"),
                ("name", "DC.date"),
                ("name", "DC.date.issued"),
                ("name", "DC.date.published"),
                ("name", "dcterms.created"),
                ("name", "dcterms.issued"),
                ("name", "citation_publication_date"),
            ]
            for attr, key in meta_pub_keys:
                el = soup.find("meta", attrs={attr: key})
                if el and el.get("content"):
                    _set("published", el["content"].strip(), f"meta:{key}", 0.9)

            meta_mod_keys = [
                ("property", "article:modified_time"),
                ("name", "article:modified_time"),
                ("property", "og:updated_time"),
                ("name", "dcterms.modified"),
                ("name", "DC.date.modified"),
            ]
            for attr, key in meta_mod_keys:
                el = soup.find("meta", attrs={attr: key})
                if el and el.get("content"):
                    _set("modified", el["content"].strip(), f"meta:{key}", 0.9)

            # Parsely / Sailthru blobs
            for key in ["parsely-page", "sailthru.date"]:
                el = soup.find("meta", attrs={"name": key})
                if el and el.get("content"):
                    try:
                        blob = json.loads(el["content"])
                        pub = blob.get("pub_date") or blob.get("date")
                        if pub:
                            _set("published", pub, f"meta:{key}", 0.85)
                    except Exception:
                        # some sites put raw date here
                        _set("published", el["content"].strip(), f"meta:{key}", 0.7)
        except Exception:
            pass

    # --------------- 3) <time> / itemprop / common classes --------------------
    if soup:
        try:
            sel = [
                'time[datetime]',
                'time[content]',
                'meta[itemprop="datePublished"][content]',
                '[itemprop="datePublished"]',
                '.entry-date',
                '.post-date',
                '.published',
                'time.published',
            ]
            for el in soup.select(",".join(sel)):
                v = el.get("datetime") or el.get("content") or el.get_text(" ", strip=True)
                if v:
                    _set("published", v, "dom:time/byline", 0.75)
        except Exception:
            pass

    # --------------- 4) Visible text patterns (published/updated) -------------
    if soup:
        try:
            page_text = soup.get_text(" ", strip=True)

            # Published-like patterns
            published_patterns = [
                (r'published\s+on\s*:?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', 0.75),
                (r'posted\s+on\s*:?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', 0.7),
                (r'first\s+published\s*:?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', 0.7),
            ]
            for pat, conf in published_patterns:
                m = re.search(pat, page_text, re.IGNORECASE)
                if m:
                    _set("published", m.group(1), "text:published_pattern", conf)
                    break

            # Updated-like patterns
            updated_patterns = [
                (r'last\s+updated?\s*:?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', 0.7),
                (r'updated\s+on\s*:?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', 0.7),
                (r'modified\s*:?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', 0.6),
                # very generic numeric pattern, low confidence
                (r'(\d{1,2}[/.]\d{1,2}[/.]\d{2,4})', 0.4),
            ]
            for pat, conf in updated_patterns:
                m = re.search(pat, page_text, re.IGNORECASE)
                if m:
                    _set("modified", m.group(1), "text:updated_pattern", conf)
                    break
        except Exception:
            pass

    # --------------- 5) schema.org WebPage/Website dateModified --------------
    if soup:
        try:
            for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
                try:
                    data = json.loads(node.string or "")
                except Exception:
                    continue
                objs = data if isinstance(data, list) else [data]
                for obj in objs:
                    if not isinstance(obj, dict):
                        continue
                    types = obj.get("@type") or obj.get("type")
                    if isinstance(types, str):
                        types = [types]
                    types = [t.lower() for t in (types or []) if isinstance(t, str)]

                    if any(t in ("webpage", "website") for t in types):
                        d_mod = obj.get("dateModified")
                        d_pub = obj.get("datePublished")
                        if d_mod:
                            _set("modified", d_mod, "jsonld:webpage:modified", 0.55)
                        if d_pub:
                            _set("published", d_pub, "jsonld:webpage:published", 0.5)
        except Exception:
            pass

    # --------------- 6) URL pattern (/YYYY/MM/DD/) ---------------------------
    if not out["published_time_iso"]:
        try:
            m = re.search(r"/(20\d{2})/([01]?\d)/([0-3]?\d)/", url or "")
            if m:
                y, mo, d = map(int, m.groups())
                dt = datetime(y, mo, d)
                _set("published", f"{y:04d}-{mo:02d}-{d:02d}", "url_path", 0.6)
        except Exception:
            pass

    # --------------- 7) HTTP Last-Modified header ----------------------------
    if last_modified_header:
        # treat primarily as modified
        if _set("modified", last_modified_header, "http:last-modified", 0.75):
            # if we still have no published date at all, we can use it as weak proxy
            if not out["published_time_iso"]:
                _set("published", last_modified_header, "http:last-modified_as_pub", 0.5)

    # --------------- 8) Sitemap <lastmod> ------------------------------------
    if sitemap_lastmod:
        # treat primarily as modified – crawl hint for "content changed"
        if _set("modified", sitemap_lastmod, "sitemap:lastmod", 0.65):
            if not out["published_time_iso"]:
                # if we truly have nothing better, we *may* also treat this as a weak publish date
                _set("published", sitemap_lastmod, "sitemap:lastmod_as_pub", 0.5)

    # --------------- 9) Copyright year in footer/text ------------------------
    if soup and not out["published_time_iso"]:
        try:
            # Prefer footer if present
            footer = soup.find(['footer', 'div'], id=lambda x: x and 'footer' in x.lower())
            search_text = footer.get_text(" ", strip=True) if footer else soup.get_text(" ", strip=True)

            copyright_patterns = [
                r'©\s*(\d{4})',
                r'copyright\s+©?\s*(\d{4})',
                r'\(c\)\s*(\d{4})',
            ]
            for pat in copyright_patterns:
                m = re.search(pat, search_text, re.IGNORECASE)
                if m:
                    year = int(m.group(1))
                    current_year = datetime.now(timezone.utc).year
                    if 2000 <= year <= current_year + 1:
                        dt = datetime(year, 1, 1)
                        iso = _normalize_to_utc_iso(dt)
                        out["published_time_raw"] = str(year)
                        out["published_time_iso"] = iso
                        if 0.3 > (out["date_confidence"] or 0.0):
                            out["date_method"] = "copyright_year"
                            out["date_confidence"] = 0.3
                        break
        except Exception:
            pass

    # --------------- 10) Archive snapshot date (Wayback) ---------------------
    # We never treat archive snapshot as publish date by default.
    # If we *still* have neither published nor modified, it can serve as a
    # very weak "seen alive at" / modified-ish signal. It is always exposed
    # separately as archive_snapshot_date/url.
    if archive_snapshot_date:
        if not out["published_time_iso"] and not out["modified_time_iso"]:
            if _set("modified", archive_snapshot_date, "archive:wayback", 0.2):
                # do NOT set published_time_* from archive snapshot
                pass

    return out

def extract_publication_dates_from_html(
    html: str,
    url: str,
    last_modified_header: Optional[str] = None
) -> Dict[str, Optional[str]]:
    """
    Synchronous convenience wrapper around extract_publication_dates_core.
    Does *not* attempt sitemap/wayback since that requires async/network.
    """
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        soup = None

    meta = extract_publication_dates_core(
        soup=soup,
        url=url,
        last_modified_header=last_modified_header,
    )
    return meta

def extract_publication_dates(
    soup: BeautifulSoup,
    url: str
) -> Dict[str, Optional[str]]:
    """
    Backwards-compatible wrapper for older call sites.

    Returns:
      {
        'published_time_raw', 'published_time_iso',
        'modified_time_raw',  'modified_time_iso',
        'method', 'confidence'
      }
    """
    meta = extract_publication_dates_core(
        soup=soup,
        url=url,
        last_modified_header=getattr(soup, "_http_last_modified", None),
    )
    return {
        "published_time_raw": meta.get("published_time_raw"),
        "published_time_iso": meta.get("published_time_iso"),
        "modified_time_raw": meta.get("modified_time_raw"),
        "modified_time_iso": meta.get("modified_time_iso"),
        "method": meta.get("date_method"),
        "confidence": meta.get("date_confidence") or 0.0,
    }

def _effective_post_instant(post) -> datetime | None:
    """
    Prefer modified, else published, else archive snapshot, using the richest
    field available. This is used as a single "content date" for ranking/
    freshness when we don't care about exact provenance.
    """
    meta: Dict[str, Any] = getattr(post, "meta", {}) or {}
    candidates = [
        meta.get("modified_time_iso"),
        meta.get("published_time_iso"),
        meta.get("archive_snapshot_date"),
        getattr(post, "modified", None),
        getattr(post, "date_raw", None),
        getattr(post, "date", None),
    ]
    for c in candidates:
        dt = _parse_utc_instant(c)
        if dt:
            return dt
    return None

# ============================================================================
# HTML → cleaned article / markdown / title
# ============================================================================

def to_full_html_document(*, fragment_html: str, title: str,
                          canonical: str | None = None,
                          og_url: str | None = None,
                          og_title: str | None = None) -> str:
    doc = BeautifulSoup("", "lxml")
    html = doc.new_tag("html", lang="en"); doc.append(html)
    head = doc.new_tag("head"); html.append(head)
    meta = doc.new_tag("meta", charset="utf-8"); head.append(meta)
    t = doc.new_tag("title"); t.string = (title or "").strip(); head.append(t)
    if canonical:
        head.append(doc.new_tag("link", rel="canonical", href=canonical))
    if og_title or title:
        head.append(doc.new_tag("meta", property="og:title", content=og_title or title))
    if og_url or canonical:
        head.append(doc.new_tag("meta", property="og:url", content=og_url or canonical))
    body = doc.new_tag("body"); html.append(body)
    # insert your already-clean <article> fragment
    frag = BeautifulSoup(fragment_html or "", "lxml")
    for el in list(frag.contents):
        body.append(el.extract())
    return str(doc)

def make_clean_content_html(
        *,
        post_url: str,
        raw_html: str,
        title: str,
        author: Optional[str] = None,
        date_display: Optional[str] = None,   # iso date or nice date
        canonical: Optional[str] = None,
        categories: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
) -> str:
    """
    Return a cleaned HTML article fragment with a canonical <h1> at the top.
    This guarantees the title survives into Markdown as an H1 for segmentation.
    """
    soup = BeautifulSoup(raw_html or "", "lxml")

    # Choose a container that actually has the content
    container: Tag = soup.body or soup  # raw_html may be a fragment (no <body>)

    # 1) Strip obvious noise that breaks MD/segmentation
    for bad in container.select("script, style, noscript, form, iframe[data-aa], .share, .social, .related-posts"):
        bad.decompose()

    # 2) Resolve links/images to absolute URLs; keep alt text, drop heavy attrs
    for a in container.select("a[href]"):
        href = a.get("href")
        if href:
            a["href"] = urljoin(post_url, href)
    for img in container.select("img[src]"):
        src = img.get("src")
        if src:
            img["src"] = urljoin(post_url, src)
        # remove noisy attrs that don't help retrieval
        for attr in ("srcset", "sizes", "loading", "decoding"):
            if img.has_attr(attr):
                del img[attr]

    # 3) Build a canonical article with <h1> + optional byline
    art = soup.new_tag("article", **{"class": "clean-post"})
    art["data-source-url"] = post_url
    if canonical:
        art["data-canonical"] = canonical

    header = soup.new_tag("header")
    h1 = soup.new_tag("h1")
    h1.string = (title or "").strip()
    header.append(h1)

    byline_parts = []
    if author: byline_parts.append(author.strip())
    if date_display: byline_parts.append(date_display.strip())
    if byline_parts:
        byline = soup.new_tag("p", **{"class": "byline"})
        byline.string = " | ".join(byline_parts)
        header.append(byline)

    # Optional: keep categories/tags in a light-touch way (helps later heuristics)
    if categories:
        nav = soup.new_tag("nav", **{"aria-label": "categories"})
        ul = soup.new_tag("ul", **{"class": "categories"})
        for c in categories:
            li = soup.new_tag("li"); li.string = c
            ul.append(li)
        nav.append(ul)
        header.append(nav)

    art.append(header)

    # 4) Append cleaned content into <section>
    body = soup.new_tag("section", **{"class": "post-body"})
    content_parent = container if container.name != "[document]" else soup
    # Move all top-level children (preserves headings/substructure)
    for child in list(content_parent.contents):
        # Skip empty whitespace-only strings
        if isinstance(child, NavigableString) and not child.strip():
            continue
        body.append(child.extract())
    art.append(body)

    return str(art)

def html_fragment_to_markdown(fragment_html: str) -> str:
    """
    Convert a cleaned HTML fragment (e.g., from make_clean_content_html) to Markdown.
    Uses markdownify if available; otherwise a light fallback that preserves basic structure.
    """
    fragment_html = fragment_html or ""
    if not fragment_html.strip():
        return ""

    if _md_convert:
        try:
            return _md_convert(fragment_html, heading_style="ATX", strip=["style", "script"]).strip()
        except Exception:
            pass

    # very small fallback: keep headings/paragraphs/lists/code reasonably
    soup = BeautifulSoup(fragment_html, "lxml")
    out = []

    def txt(el): return el.get_text(" ", strip=True)

    # Write headings as ATX
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        level = int(h.name[1])
        t = txt(h)
        if t:
            h.replace_with(soup.new_string(("#" * level) + " " + t + "\n"))

    # Code blocks
    for pre in soup.find_all("pre"):
        code = pre.get_text("", strip=False)
        pre.replace_with(soup.new_string("```\n" + code.strip("\n") + "\n```\n"))

    # Lists → simple bullets
    for ul in soup.find_all("ul"):
        lines = []
        for li in ul.find_all("li", recursive=False):
            t = txt(li)
            if t:
                lines.append(f"- {t}")
        ul.replace_with(soup.new_string("\n".join(lines) + ("\n" if lines else "")))

    # Tables → plain text rows (tiny)
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [txt(td) for td in tr.find_all(["td","th"])]
            if cells:
                rows.append(" | ".join(cells))
        table.replace_with(soup.new_string("\n".join(rows) + ("\n" if rows else "")))

    # Links: keep visible text; markdownify is better, but fallback keeps it simple
    for a in soup.find_all("a"):
        t = txt(a)
        href = a.get("href")
        a.replace_with(soup.new_string(f"[{t}]({href})" if (t and href) else (t or href or "")))

    # Images → alt (avoid noise)
    for img in soup.find_all("img"):
        alt = img.get("alt") or ""
        src = img.get("src") or ""
        img.replace_with(soup.new_string(f"![{alt}]({src})" if src else alt))

    md = soup.get_text("\n", strip=True)
    # normalize excessive blank lines
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()

def html_title(html: str) -> str:
    try:
        s = BeautifulSoup(html or "", "lxml")
        t = s.find("title")
        return (t.get_text(strip=True) if t else "").strip()
    except Exception:
        return ""