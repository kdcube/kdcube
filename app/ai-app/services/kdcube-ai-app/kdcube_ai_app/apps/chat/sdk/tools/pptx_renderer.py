# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/pptx_renderer.py

from __future__ import annotations

import pathlib
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import json
import re

from pptx import Presentation
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir
import kdcube_ai_app.apps.chat.sdk.tools.md_utils as md_utils

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CIT_RE  = re.compile(r"\[\[S:(\d+)\]\]")  # [[S:3]]

def _outdir() -> pathlib.Path:
    return resolve_output_dir()

def _basename_only(path: str, default_ext: str = ".pptx") -> str:
    name = Path(path).name
    if default_ext and not name.lower().endswith(default_ext):
        name += default_ext
    return name

def _ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)

def _domain_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        net = urlparse(url).netloc
        return net or url
    except Exception:
        return url

def _split_markdown_sections(md: str) -> List[Tuple[str, List[str]]]:
    """
    Very small MD -> slides splitter.
    - New slide on '## ' heading (or '# ' for title if first)
    - Accumulate following lines as content until next heading.
    Returns list of (title, lines).
    """
    lines = (md or "").splitlines()
    slides: List[Tuple[str, List[str]]] = []
    cur_title: Optional[str] = None
    cur_body: List[str] = []

    for ln in lines:
        if ln.startswith("## "):
            # flush previous
            if cur_title is not None:
                slides.append((cur_title.strip(), cur_body))
            cur_title = ln[3:]
            cur_body = []
        elif ln.startswith("# "):
            # Treat as first slide title if nothing started yet
            if cur_title is None and not slides:
                cur_title = ln[2:]
                cur_body = []
            else:
                # otherwise just content
                cur_body.append(ln)
        else:
            cur_body.append(ln)

    if cur_title is None:
        # fallback: derive title from first non-empty line
        nonempty = next((l for l in lines if l.strip()), "Slides")
        cur_title = nonempty.lstrip("# ").strip() or "Slides"

    slides.append((cur_title.strip(), cur_body))
    return slides

def _add_title_slide(prs: Presentation, text: str) -> None:
    layout = prs.slide_layouts[0]  # Title slide
    slide = prs.slides.add_slide(layout)
    title = slide.shapes.title
    if title:
        title.text = text

def _add_content_slide(prs: Presentation, title: str, body_lines: List[str],
                       sources_map: Dict[int, Dict[str, str]],
                       resolve_citations: bool) -> None:
    layout = prs.slide_layouts[1]  # Title & Content
    slide = prs.slides.add_slide(layout)
    title_placeholder = slide.shapes.title
    if title_placeholder:
        title_placeholder.text = title

    body = slide.shapes.placeholders[1].text_frame
    body.clear()

    def add_paragraph(raw: str, level: int = 0):
        p = body.add_paragraph() if len(body.paragraphs) else body.paragraphs[0]
        p.level = max(0, min(level, 4))
        # build runs with hyperlinks for [text](url) and [[S:n]]
        idx = 0
        line = raw

        # Replace simple bullets "- " / "  - " with level inference
        bullet_level = 0
        m = re.match(r"^(\s*)([-*])\s+", line)
        if m:
            spaces = len(m.group(1) or "")
            bullet_level = min(spaces // 2, 4)
            p.level = bullet_level
            line = line[m.end():]

        # process links & citations into run segments
        # We create tokens {type:'text'|'link'|'cit', text:'...', url:'...'}
        tokens: List[Dict[str, str]] = []
        while line:
            lnk = _LINK_RE.search(line)
            cit = _CIT_RE.search(line) if resolve_citations else None

            # choose earliest
            candidates = [(lnk, "link"), (cit, "cit")]
            candidates = [(m, t) for m, t in candidates if m]
            if not candidates:
                tokens.append({"type": "text", "text": line})
                break
            m0, t0 = min(candidates, key=lambda t: t[0].start())
            if m0.start() > 0:
                tokens.append({"type": "text", "text": line[:m0.start()]})
            if t0 == "link":
                tokens.append({"type": "link", "text": m0.group(1), "url": m0.group(2)})
            else:
                sid = int(m0.group(1))
                source = sources_map.get(sid, {})
                url = source.get("url", "")
                # Use title if available, otherwise just [n]
                link_text = source.get("title", f"[{sid}]")
                tokens.append({"type": "link", "text": link_text, "url": url or ""})
            line = line[m0.end():]

        # emit runs
        # NOTE: python-pptx run hyperlink via r = p.add_run(); r.hyperlink.address = url
        # But add_run is not public on TextFrame Paragraph; we use text + hyperlink spans
        # Workaround: create runs by slicing p.runs via adding text incrementally
        for tok in tokens:
            if tok["type"] == "text":
                run = p.add_run()
                run.text = tok["text"]
            elif tok["type"] == "link":
                run = p.add_run()
                run.text = tok["text"]
                if tok.get("url"):
                    run.hyperlink.address = tok["url"]

    # populate paragraphs
    first = True
    for ln in body_lines:
        if first:
            # first paragraph
            p = body.paragraphs[0]
            p.level = 0
            txt = ln.strip()
            if not txt:
                p.text = ""
            else:
                # reuse add_paragraph logic to get links
                body.clear()
                add_paragraph(txt)
            first = False
        else:
            add_paragraph(ln.strip())

def _add_sources_slide(prs: Presentation, sources_map: Dict[int, Dict[str, str]], order: List[int]) -> None:
    layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = "Sources"
    tf = slide.shapes.placeholders[1].text_frame
    tf.clear()

    # Use the order from normalization to display sources in the right sequence
    for sid in order:
        if sid not in sources_map:
            continue
        info = sources_map[sid]
        title = info.get("title") or info.get("url") or f"Source {sid}"
        url = info.get("url", "")
        p = tf.add_paragraph() if len(tf.paragraphs) else tf.paragraphs[0]
        p.level = 0
        run = p.add_run()
        run.text = f"[{sid}] {title} — {_domain_of(url)}"
        if url:
            run.hyperlink.address = url

def render_pptx(
        path: str,
        content_md: str,
        *,
        title: Optional[str] = None,
        base_dir: Optional[str] = None,
        sources: Optional[str] = None,
        resolve_citations: bool = False,
        include_sources_slide: bool = False
) -> str:
    """
    Render a PowerPoint deck (PPTX) from Markdown.

    Returns the **basename only** (e.g., 'deck.pptx').
    The file is always written inside OUTPUT_DIR (or '.' if not set).
    """
    # Resolve filename in OUTPUT_DIR and ensure we return only basename
    basename = _basename_only(path, ".pptx")
    outdir = _outdir()
    outfile = outdir / basename
    _ensure_parent(outfile)

    # Normalize sources using the same method as write_pdf
    sources_map: Dict[int, Dict[str, str]] = {}
    order: List[int] = []

    if sources:
        sources_map, order = md_utils._normalize_sources(sources)

    # Minimal markdown -> slides
    sections = _split_markdown_sections(content_md or "")
    prs = Presentation()

    # Title slide
    if title:
        _add_title_slide(prs, title)
    else:
        # if first section looks like title-only, use it (otherwise add default)
        fst_title, _ = sections[0]
        _add_title_slide(prs, fst_title)

    # Content slides
    for i, (stitle, body) in enumerate(sections):
        _add_content_slide(prs, stitle, body, sources_map, resolve_citations)

    # Sources slide
    if include_sources_slide and sources_map:
        _add_sources_slide(prs, sources_map, order)

    prs.save(str(outfile))
    return basename
