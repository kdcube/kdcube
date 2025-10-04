# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/md_utils.py

import re, json
from typing import Optional, Dict, List, Set

from kdcube_ai_app.apps.chat.sdk.tools.citations import CITE_TOKEN_RE


def _superscript_num(n: int) -> str:
    _map = {"0":"⁰","1":"¹","2":"²","3":"³","4":"⁴","5":"⁵","6":"⁶","7":"⁷","8":"⁸","9":"⁹"}
    return "".join(_map.get(ch, ch) for ch in str(n))

def _is_image_url(url: str) -> bool:
    """Check if URL points to an image based on extension"""
    if not url:
        return False
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.tiff'}
    # Remove query parameters and check extension
    clean_url = url.split('?')[0].lower()
    return any(clean_url.endswith(ext) for ext in image_extensions)

def build_citation_map(citations: List[Dict]) -> Dict[int, Dict]:
    """Build citation map from sr.citations() format"""
    by_id = {}
    for c in citations:
        sid = c.get("sid")
        if sid is not None:
            by_id[int(sid)] = {
                "url": c.get("url", ""),
                "title": c.get("title", ""),
                "text": c.get("text", "")
            }
    return by_id

def _normalize_sources(sources_json: Optional[str]) -> tuple[dict[int, dict], list[int]]:
    """
    Accepts:
      - JSON array of objects: [{sid?, title, url, ...}, ...] (sid is 1-based; if missing, index+1 is used)
      - or JSON object: { "1": {title,url}, "2": {...}, ... }
    Returns:
      (by_id, order_ids) where by_id: {sid:int -> {title,url,...}}, order_ids is the ordered list of sids.
    """
    if not sources_json:
        return {}, []
    try:
        src = json.loads(sources_json)
    except Exception:
        return {}, []
    by_id: dict[int, dict] = {}
    order: list[int] = []

    if isinstance(src, list):
        for i, row in enumerate(src):
            if not isinstance(row, dict):
                continue
            sid = row.get("sid")
            if sid is None:
                sid = i + 1
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

def _replace_citation_tokens(md: str, by_id: dict[int, dict], embed_images: bool = True) -> str:
    """
    Replace [[S:1]] or [[S:1,4]] or [[S:1-15]] with inline links:
      [[S:3]] -> [³](https://example "Title")
      [[S:1,4]] -> [¹](url1 "Title1") [⁴](url4 "Title4")
      [[S:1-15]] -> [¹](url1 "Title1") [²](url2 "Title2") ... [¹⁵](url15 "Title15")
    Can embed images or create links
    Unknown ids are dropped from the replacement; if none are known, the token is removed.
    Args:
        md: Markdown content with [[S:n]] tokens
        by_id: Citation map {id: {url, title, text}}
        embed_images: If True, convert image URLs to embedded images instead of links
    """
    if not by_id:
        return md

    pat = re.compile(r"\[\[S:([0-9,\s\-]+)]]")

    def _one(m: re.Match) -> str:
        ids_str = m.group(1)
        ids = []

        # Handle both comma-separated and ranges
        parts = ids_str.split(",")
        for part in parts:
            part = part.strip()
            if "-" in part:
                # Handle range like "1-15"
                try:
                    start, end = part.split("-", 1)
                    start_num = int(start.strip())
                    end_num = int(end.strip())
                    ids.extend(range(start_num, end_num + 1))
                except ValueError:
                    continue
            elif part.isdigit():
                ids.append(int(part))

        # Only use the FIRST citation number from the group
        if ids:
            first_id = ids[0]  # Take only the first ID
            meta = by_id.get(first_id)
            if meta:
                url = meta.get("url") or meta.get("href", "")
                title = (meta.get("title") or meta.get("text", "") or url or "").replace('"', "'")

                if url:
                    if embed_images and _is_image_url(url):
                        # Embed as image with caption
                        alt_text = title[:100] + "..." if len(title) > 100 else title
                        return f"\n\n![{alt_text}]({url})\n*Source {first_id}: {title}*\n"
                    else:
                        # Regular link with superscript
                        sup = _superscript_num(first_id)
                        return f"[{sup}]({url} \"{title}\")"

        return ""

    return pat.sub(_one, md)

def _create_clean_sources_section(by_id: dict[int, dict], order: list[int]) -> str:
    """
    Create a much cleaner sources section with better formatting
    """
    if not by_id or not order:
        return ""

    lines = ["", "---", "", "## References", ""]

    for sid in order:
        meta = by_id.get(sid) or {}
        url = meta.get("url") or meta.get("href") or ""
        title = meta.get("title") or ""

        if not url:
            continue

        # Clean up title
        if not title or title == url:
            title = f"Source {sid}"

        # Truncate very long titles
        if len(title) > 80:
            title = title[:77] + "..."

        lines.append(f"{sid}. [{title}]({url})")

    return "\n" + "\n".join(lines) + "\n"

def _append_sources_section(md: str, by_id: dict[int, dict], order: list[int]) -> str:
    """
    If the doc doesn't already contain a '## Sources' header, append one with numbered links.
    """
    if not by_id or not order:
        return md
    # rough check to avoid duplicating a section the caller already added
    if re.search(r"^##\s+Sources\b", md, flags=re.IGNORECASE | re.MULTILINE):
        return md
    lines = ["", "---", "", "## Sources", ""]
    for sid in order:
        meta = by_id.get(sid) or {}
        url = meta.get("url") or meta.get("href") or ""
        title = meta.get("title") or url
        if not url:
            continue
        lines.append(f"{sid}. [{title}]({url})")
    return md + "\n".join(lines) + "\n"

def replace_citation_tokens_streaming(text: str, citation_map: Dict) -> str:
    if not citation_map:
        return text

    def _expand_ids(ids_str: str):
        out = []
        for part in ids_str.split(","):
            p = part.strip()
            if not p:
                continue
            if "-" in p:
                try:
                    a, b = [int(x.strip()) for x in p.split("-", 1)]
                    if a <= b:
                        out.extend(range(a, b + 1))
                    else:
                        out.extend(range(b, a + 1))
                except ValueError:
                    pass
            else:
                if p.isdigit():
                    out.append(int(p))
        # in-order dedupe
        seen = set()
        uniq = []
        for i in out:
            if i not in seen:
                seen.add(i)
                uniq.append(i)
        return uniq

    def _sub(m: re.Match) -> str:
        ids = _expand_ids(m.group(1))
        links = []
        for i in ids:
            rec = citation_map.get(i) or citation_map.get(str(i))
            if not rec:
                continue
            url = (rec.get("url") or "").strip()
            title = (rec.get("title") or url or f"Source {i}").replace('"', "'")
            if url:
                links.append(f"[{title}]({url})")
        # If nothing resolved, leave the original token (debuggability > silent drop)
        return " ".join(links) if links else m.group(0)

    return CITE_TOKEN_RE.sub(_sub, text)

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