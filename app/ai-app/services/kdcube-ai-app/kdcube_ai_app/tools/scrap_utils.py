# tools/scrap_utils.py
import json, re
from datetime import datetime
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup, NavigableString, Tag

import base64, os, mimetypes, requests
from urllib.parse import urljoin, urlparse
from typing import Dict, Optional, List

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

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

def _parse_human_date(s: str) -> Optional[str]:
    """
    Parse dates like 'September 19, 2025' -> ISO date '2025-09-19'.
    Returns None if parsing fails.
    """
    s = s.strip()
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
    if not m:
        return None
    month_name, day, year = m.group(1), m.group(2), m.group(3)
    month = _MONTHS.get(month_name.lower())
    if not month:
        return None
    try:
        dt = datetime(int(year), month, int(day))
        return dt.date().isoformat()
    except Exception:
        return None

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

_ISO_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")

def _parse_date_any(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    # ISO 8601 (allow 'Z')
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    # RFC 2822/822 (e.g., RSS/HTTP)
    try:
        dt = parsedate_to_datetime(s)
        return dt
    except Exception:
        pass
    # "September 19, 2025"
    iso = _parse_human_date(s)  # your existing helper → 'YYYY-MM-DD'
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

def extract_publication_dates_from_html(html: str, url: str, last_modified_header: Optional[str] = None) -> Dict[str, Optional[str]]:
    """
    Returns a dict:
      {
        'published_time_raw', 'published_time_iso',
        'modified_time_raw',  'modified_time_iso',
        'date_method', 'date_confidence'
      }
    """
    out = {
        "published_time_raw": None, "published_time_iso": None,
        "modified_time_raw":  None, "modified_time_iso":  None,
        "date_method": None, "date_confidence": 0.0
    }

    def set_pub(raw, method, conf):
        dt = _parse_date_any(raw)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            out["published_time_raw"] = raw
            out["published_time_iso"] = dt.isoformat()
            out["date_method"] = method
            out["date_confidence"] = conf
            return True
        return False

    def set_mod(raw):
        dt = _parse_date_any(raw)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            out["modified_time_raw"] = raw
            out["modified_time_iso"] = dt.isoformat()

    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        soup = None

    # 1) JSON-LD
    if soup:
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
                    if pub and set_pub(pub, "jsonld", 0.98):
                        if mod: set_mod(mod)
                        return out
                    if mod: set_mod(mod)

    # 2) Meta tags (OpenGraph/Article/DC)
    if soup:
        meta_keys = [
            ("property", "article:published_time"),
            ("name", "article:published_time"),
            ("property", "og:published_time"),
            ("name", "pubdate"),
            ("name", "publish-date"),
            ("name", "date"),
            ("name", "DC.date"), ("name", "DC.date.issued"), ("name", "DC.date.published"),
            ("name", "dcterms.created"), ("name", "dcterms.issued"),
            ("name", "citation_publication_date"),
        ]
        for attr, key in meta_keys:
            el = soup.find("meta", attrs={attr: key})
            if el and el.get("content"):
                if set_pub(el["content"].strip(), f"meta:{key}", 0.9):
                    break

        # Modified variants
        for attr, key in [
            ("property", "article:modified_time"),
            ("name", "article:modified_time"),
            ("property", "og:updated_time"),
            ("name", "dcterms.modified"),
            ("name", "DC.date.modified"),
        ]:
            el = soup.find("meta", attrs={attr: key})
            if el and el.get("content"):
                set_mod(el["content"].strip())

        # <time> / itemprop / common classes
        if not out["published_time_iso"]:
            sel = [
                'time[datetime]', 'time[content]',
                'meta[itemprop="datePublished"][content]',
                '[itemprop="datePublished"]',
                '.entry-date', '.post-date', '.published', 'time.published'
            ]
            for el in soup.select(",".join(sel)):
                v = el.get("datetime") or el.get("content") or el.get_text(" ", strip=True)
                if v and set_pub(v, "dom:time/byline", 0.75):
                    break

    # URL pattern (/YYYY/MM/DD/)
    if not out["published_time_iso"]:
        m = re.search(r"/(20\d{2})/([01]?\d)/([0-3]?\d)/", url or "")
        if m:
            try:
                y, mo, d = map(int, m.groups())
                dt = datetime(y, mo, d, tzinfo=timezone.utc)
                out.update({
                    "published_time_raw": f"{y:04d}-{mo:02d}-{d:02d}",
                    "published_time_iso": dt.isoformat(),
                    "date_method": "url_path",
                    "date_confidence": 0.6
                })
            except Exception:
                pass

    # HTTP Last-Modified as last resort (low confidence, not publish time)
    if not out["published_time_iso"] and last_modified_header:
        if set_pub(last_modified_header, "http:last-modified", 0.4):
            # keep confidence low
            pass

    return out
def extract_publication_dates(soup: BeautifulSoup, url: str) -> Dict[str, Optional[str]]:
    """
    Returns {
      'published_time_raw', 'published_time_iso',
      'modified_time_raw',  'modified_time_iso',
      'method', 'confidence'
    }
    """
    out = {
        "published_time_raw": None, "published_time_iso": None,
        "modified_time_raw":  None, "modified_time_iso":  None,
        "method": None, "confidence": 0.0
    }

    def set_pub(raw, method, conf):
        dt = _parse_date_any(raw)
        if dt:
            out["published_time_raw"] = raw
            out["published_time_iso"] = dt.isoformat()
            out["method"] = method
            out["confidence"] = conf
            return True
        return False

    def set_mod(raw):
        dt = _parse_date_any(raw)
        if dt:
            out["modified_time_raw"] = raw
            out["modified_time_iso"] = dt.isoformat()

    # 1) JSON-LD (highest confidence)
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(node.string or "")
        except Exception:
            continue
        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            if not isinstance(obj, dict): continue
            types = obj.get("@type") or obj.get("type")
            if isinstance(types, str): types = [types]
            types = [t.lower() for t in (types or []) if isinstance(t, str)]
            if any(t in ("article","newsarticle","blogposting") for t in types):
                pub = obj.get("datePublished") or obj.get("dateCreated")
                mod = obj.get("dateModified") or obj.get("dateUpdated")
                if pub and set_pub(pub, "jsonld", 0.98):
                    if mod: set_mod(mod)
                    return out
                if mod: set_mod(mod)

    # 2) Meta tags (OpenGraph/Article/DC)
    meta_keys = [
        ("property","article:published_time"),
        ("name","article:published_time"),
        ("property","og:published_time"),
        ("name","pubdate"),
        ("name","publish-date"),
        ("name","date"),
        ("name","DC.date"), ("name","DC.date.issued"), ("name","DC.date.published"),
        ("name","dcterms.created"), ("name","dcterms.issued"),
        ("name","citation_publication_date"),
    ]
    for attr, key in meta_keys:
        el = soup.find("meta", attrs={attr: key})
        if el and el.get("content"):
            if set_pub(el["content"].strip(), f"meta:{key}", 0.9):
                break

    # 2b) Modified variants
    for attr, key in [("property","article:modified_time"), ("name","article:modified_time"), ("property","og:updated_time"),
                      ("name","dcterms.modified"), ("name","DC.date.modified")]:
        el = soup.find("meta", attrs={attr: key})
        if el and el.get("content"):
            set_mod(el["content"].strip())

    # 2c) Parsely/Sailthru JSON blobs
    for key in ["parsely-page", "sailthru.date"]:
        el = soup.find("meta", attrs={"name": key})
        if el and el.get("content"):
            try:
                blob = json.loads(el["content"])
                pub = blob.get("pub_date") or blob.get("date")
                if pub and not out["published_time_iso"]:
                    set_pub(pub, f"meta:{key}", 0.85)
            except Exception:
                # some sites put raw date here
                if not out["published_time_iso"]:
                    set_pub(el["content"].strip(), f"meta:{key}", 0.7)

    # 3) <time> / itemprop / common classes
    sel = [
        'time[datetime]', 'time[content]',
        'meta[itemprop="datePublished"][content]',
        '[itemprop="datePublished"]',
        '.entry-date', '.post-date', '.published', 'time.published'
    ]
    for el in soup.select(",".join(sel)):
        v = el.get("datetime") or el.get("content") or el.get_text(" ", strip=True)
        if v and not out["published_time_iso"]:
            if set_pub(v, "dom:time/byline", 0.75):
                break

    # 4) Visible byline fallback you already parse (keep if not set)
    if not out["published_time_iso"]:
        # allow your existing author/date extraction to populate date_raw
        pass

    # 5) URL pattern (/YYYY/MM/DD/)
    if not out["published_time_iso"]:
        m = re.search(r"/(20\d{2})/([01]?\d)/([0-3]?\d)/", url)
        if m:
            y, mo, d = map(int, m.groups())
            try:
                dt = datetime(y, mo, d)
                out.update({
                    "published_time_raw": f"{y:04d}-{mo:02d}-{d:02d}",
                    "published_time_iso": dt.isoformat(),
                    "method": "url_path",
                    "confidence": 0.6
                })
            except Exception:
                pass

    # 6) HTTP Last-Modified as last resort
    if not out["published_time_iso"]:
        lm = getattr(soup, "_http_last_modified", None)
        if lm and set_pub(lm, "http:last-modified", 0.4):
            # Keep confidence low—this is not publish time.
            pass

    return out

def _effective_post_instant(post) -> datetime | None:
    """Prefer modified, else published, using the richest field available."""
    meta = getattr(post, "meta", {}) or {}
    candidates = [
        getattr(post, "modified", None),
        getattr(post, "date_raw", None),
        meta.get("modified_time_iso"),
        meta.get("published_time_iso"),
        getattr(post, "date", None),
    ]
    for c in candidates:
        dt = _parse_utc_instant(c)
        if dt:
            return dt
    return None

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