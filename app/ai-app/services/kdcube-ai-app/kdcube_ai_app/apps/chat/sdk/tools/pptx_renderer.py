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

# --- Styling constants --------------------------------------------------------
from pptx.util import Pt, Inches
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_PARAGRAPH_ALIGNMENT

PALETTE = {
    "fg": RGBColor(20, 24, 31),          # near-black
    "muted": RGBColor(95, 106, 121),     # gray-600
    "accent": RGBColor(31, 111, 235),    # brand blue
    "quote_bg": RGBColor(245, 247, 250), # light canvas
    "code_bg": RGBColor(250, 250, 252),
    "rule": RGBColor(220, 224, 230),
    "table_header_bg": RGBColor(240, 244, 252),
}

TYPE_SCALE = {
    "title": Pt(44),
    "slide_title": Pt(32),
    "h3": Pt(28),
    "body": Pt(20),
    "code": Pt(16),
    "caption": Pt(14),
}

PAGE = {
    "content_left": Inches(0.8),
    "content_top": Inches(1.4),
    "content_width": Inches(12.0 - 0.8 - 0.8),   # 10.4"
    "content_height": Inches(7.0),
}

MONO_FALLBACK = "Consolas"

# --- Mini MD classifiers ------------------------------------------------------
_CODE_FENCE_RE = re.compile(r"^```(\w+)?\s*$")
_TABLE_ROW_RE  = re.compile(r"^\s*\|.+\|\s*$")
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s+(.*)$")

def _style_run(run, *, size: Pt, bold=False, italic=False, color: RGBColor | None = None, mono=False):
    run.font.size = size
    run.font.bold = bold
    run.font.italic = italic
    if mono:
        run.font.name = MONO_FALLBACK
        run._r.get_or_add_rPr().set('eastAsia', MONO_FALLBACK)  # better Windows fallback
    if color is not None:
        run.font.color.rgb = color

def _style_paragraph(p, *, level=0, space_before=0, space_after=6, align=PP_ALIGN.LEFT):
    p.level = max(0, min(level, 4))
    p.space_before = Pt(space_before)
    p.space_after = Pt(space_after)
    p.line_spacing = 1.25
    p.alignment = align

def _clear_and_get_textframe(shape):
    tf = shape.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = Inches(0.0)
    tf.margin_right = Inches(0.0)
    tf.margin_top = Inches(0.02)
    tf.margin_bottom = Inches(0.02)
    return tf

def _add_textbox(slide, left, top, width, height):
    return slide.shapes.add_textbox(left, top, width, height)

def _add_title_slide(prs: Presentation, text: str) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
    # Title
    tbox = _add_textbox(slide, PAGE["content_left"], Inches(1.4), PAGE["content_width"], Inches(1.5))
    tf = _clear_and_get_textframe(tbox)
    p = tf.paragraphs[0]
    _style_paragraph(p, space_after=2, align=PP_ALIGN.LEFT)
    r = p.add_run()
    r.text = (text or "Presentation").strip()
    _style_run(r, size=TYPE_SCALE["title"], bold=True, color=PALETTE["fg"])
    # Subtle rule
    rule = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        PAGE["content_left"], Inches(2.6),
        PAGE["content_width"], Inches(0.04)
    )
    rule.fill.solid()
    rule.fill.fore_color.rgb = PALETTE["rule"]
    rule.line.fill.background()  # no stroke

def _add_slide_title(slide, title: str):
    tbox = _add_textbox(slide, PAGE["content_left"], PAGE["content_top"], PAGE["content_width"], Inches(0.8))
    tf = _clear_and_get_textframe(tbox)
    p = tf.paragraphs[0]
    _style_paragraph(p, space_after=0, align=PP_ALIGN.LEFT)
    r = p.add_run()
    r.text = (title or "").strip()
    _style_run(r, size=TYPE_SCALE["slide_title"], bold=True, color=PALETTE["fg"])
    # tiny accent bar
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        PAGE["content_left"], PAGE["content_top"] + Inches(0.8),
        Inches(0.7), Inches(0.06)
    )
    bar.fill.solid()
    bar.fill.fore_color.rgb = PALETTE["accent"]
    bar.line.fill.background()

def _parse_bullet_level(line: str) -> tuple[int, str]:
    m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
    if not m:
        return 0, line.strip()
    spaces, _bullet, text = m.groups()
    lvl = min(len(spaces) // 2, 4)
    return lvl, text.strip()

def _emit_link_or_text(paragraph, text: str, sources_map: Dict[int, Dict[str, str]], resolve_citations: bool):
    # tokens: link [t](u) and [[S:n]]
    idx = 0
    while text:
        m_link = _LINK_RE.search(text)
        m_cit  = _CIT_RE.search(text) if resolve_citations else None
        ms = [x for x in [("link", m_link), ("cit", m_cit)] if x[1]]
        if not ms:
            r = paragraph.add_run()
            r.text = text
            return
        kind, m = min(ms, key=lambda t: t[1].start())
        if m.start() > 0:
            r = paragraph.add_run()
            r.text = text[:m.start()]
        if kind == "link":
            r = paragraph.add_run()
            r.text = m.group(1)
            r.font.color.rgb = PALETTE["accent"]
            try:
                r.hyperlink.address = m.group(2)
            except Exception:
                pass
        else:
            sid = int(m.group(1))
            rec = sources_map.get(sid, {})
            label = rec.get("title") or f"[{sid}]"
            url = rec.get("url", "")
            r = paragraph.add_run()
            r.text = label
            r.font.color.rgb = PALETTE["accent"]
            if url:
                try:
                    r.hyperlink.address = url
                except Exception:
                    pass
        text = text[m.end():]

def _add_code_block(slide, code_lines: list[str], y_offset: float):
    # card
    left, top = PAGE["content_left"], PAGE["content_top"] + Inches(y_offset)
    width = PAGE["content_width"]
    height = Inches(1.0 + 0.35 * max(1, len(code_lines)))
    rect = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    rect.fill.solid()
    rect.fill.fore_color.rgb = PALETTE["code_bg"]
    rect.line.color.rgb = PALETTE["rule"]
    rect.line.width = Pt(0.75)
    tf = _clear_and_get_textframe(rect)
    p = tf.paragraphs[0]
    _style_paragraph(p, space_after=2)
    for i, ln in enumerate(code_lines):
        if i == 0:
            r = p.add_run()
        else:
            p = tf.add_paragraph()
            _style_paragraph(p, space_after=2)
            r = p.add_run()
        r.text = ln.rstrip("\n")
        _style_run(r, size=TYPE_SCALE["code"], mono=True, color=PALETTE["fg"])
    return float(height.inches)

def _add_blockquote(slide, lines: list[str], y_offset: float):
    text = "\n".join(lines)
    left, top = PAGE["content_left"], PAGE["content_top"] + Inches(y_offset)
    width = PAGE["content_width"]
    height = Inches(0.6 + 0.28 * max(1, len(lines)))
    rect = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    rect.fill.solid()
    rect.fill.fore_color.rgb = PALETTE["quote_bg"]
    rect.line.fill.background()

    # left rule
    rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, Inches(0.12), height)
    rule.fill.solid()
    rule.fill.fore_color.rgb = PALETTE["rule"]
    rule.line.fill.background()

    tf = _clear_and_get_textframe(rect)
    p = tf.paragraphs[0]
    _style_paragraph(p, space_after=4)
    r = p.add_run()
    r.text = text
    _style_run(r, size=TYPE_SCALE["body"], italic=True, color=PALETTE["muted"])
    return float(height.inches)

def _parse_table(block_lines: list[str]) -> Optional[list[list[str]]]:
    # very small pipe-table: header sep row must contain --- cells
    rows = [ln.strip() for ln in block_lines if _TABLE_ROW_RE.match(ln)]
    if len(rows) < 2:
        return None
    def split_row(r: str): return [c.strip() for c in r.strip("|").split("|")]
    cells = [split_row(r) for r in rows]
    if not any(set(c) & {"---", ":---", "---:", ":---:"} for c in cells[1]):
        return None
    # header + remaining data rows
    hdr = cells[0]
    data = cells[2:] if len(cells) > 2 else []
    return [hdr] + data

def _add_table(slide, data: list[list[str]], y_offset: float):
    rows, cols = len(data), len(data[0])
    left, top = PAGE["content_left"], PAGE["content_top"] + Inches(y_offset)
    col_w = PAGE["content_width"] / cols
    height = Inches(0.5 + 0.35 * rows)
    tbl = slide.shapes.add_table(rows, cols, left, top, PAGE["content_width"], height).table
    # column widths
    for j in range(cols):
        tbl.columns[j].width = col_w
    # header
    for j, txt in enumerate(data[0]):
        cell = tbl.cell(0, j)
        cell.text_frame.clear()
        p = cell.text_frame.paragraphs[0]
        _style_paragraph(p, space_after=0)
        r = p.add_run()
        r.text = txt
        _style_run(r, size=TYPE_SCALE["body"], bold=True, color=PALETTE["fg"])
        cell.fill.solid()
        cell.fill.fore_color.rgb = PALETTE["table_header_bg"]
    # body
    for i in range(1, rows):
        for j, txt in enumerate(data[i]):
            cell = tbl.cell(i, j)
            cell.text_frame.clear()
            p = cell.text_frame.paragraphs[0]
            _style_paragraph(p, space_after=0)
            r = p.add_run()
            r.text = txt
            _style_run(r, size=TYPE_SCALE["body"], color=PALETTE["fg"])
    return float(height.inches)

def _add_content_slide(
        prs: Presentation,
        title: str,
        body_lines: List[str],
        sources_map: Dict[int, Dict[str, str]],
        resolve_citations: bool
) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    _add_slide_title(slide, title)

    # content area container
    y = 0.95  # inches below title zone
    # state machines for blocks
    in_code = False
    code_lang = ""
    code_buf: list[str] = []
    quote_buf: list[str] = []
    table_buf: list[str] = []

    def flush_quote():
        nonlocal y, quote_buf
        if quote_buf:
            y += _add_blockquote(slide, quote_buf, y) + 0.2
            quote_buf = []

    def flush_code():
        nonlocal y, code_buf
        if code_buf:
            y += _add_code_block(slide, code_buf, y) + 0.2
            code_buf = []

    def flush_table():
        nonlocal y, table_buf
        if table_buf:
            data = _parse_table(table_buf)
            if data:
                y += _add_table(slide, data, y) + 0.2
            else:
                # fallback: dump as paragraphs if malformed
                _emit_paragraphs("\n".join(table_buf))
            table_buf = []

    # main textbox for normal paragraphs/bullets
    tb = _add_textbox(
        slide,
        PAGE["content_left"],
        PAGE["content_top"] + Inches(y),
        PAGE["content_width"],
        PAGE["content_height"] - Inches(y),
        )
    tf = _clear_and_get_textframe(tb)
    cur_p = tf.paragraphs[0]
    _style_paragraph(cur_p, space_after=6)
    first_para_used = False

    def _emit_paragraphs(text_block: str):
        nonlocal first_para_used, cur_p
        for raw in text_block.splitlines():
            if not raw.strip():
                continue
            lvl, plain = _parse_bullet_level(raw)
            if not first_para_used:
                p = cur_p
                first_para_used = True
            else:
                p = tf.add_paragraph()
            _style_paragraph(p, level=lvl, space_after=4)
            # basic emphasis: **bold**, *italic*
            parts = re.split(r"(\*\*.*?\*\*|\*.*?\*)", plain)
            for part in parts:
                if not part:
                    continue
                if part.startswith("**") and part.endswith("**"):
                    r = p.add_run()
                    r.text = part[2:-2]
                    _style_run(r, size=TYPE_SCALE["body"], bold=True, color=PALETTE["fg"])
                elif part.startswith("*") and part.endswith("*"):
                    r = p.add_run()
                    r.text = part[1:-1]
                    _style_run(r, size=TYPE_SCALE["body"], italic=True, color=PALETTE["fg"])
                else:
                    # links & citations inside
                    _emit_link_or_text(p, part, sources_map, resolve_citations)
                    for rr in p.runs:
                        if rr.font.size is None:
                            _style_run(rr, size=TYPE_SCALE["body"], color=PALETTE["fg"])

    for ln in body_lines:
        # detect block transitions
        if _CODE_FENCE_RE.match(ln):
            # toggle code
            m = _CODE_FENCE_RE.match(ln)
            if not in_code:
                flush_quote()
                flush_table()
                in_code = True
                code_lang = (m.group(1) or "").lower()
                code_buf = []
            else:
                flush_code()
                in_code = False
                code_lang = ""
            continue

        if in_code:
            code_buf.append(ln)
            continue

        mt = _TABLE_ROW_RE.match(ln)
        mq = _BLOCKQUOTE_RE.match(ln)

        if mt:
            flush_quote()
            table_buf.append(ln)
            continue
        else:
            flush_table()

        if mq:
            flush_table()
            quote_buf.append(mq.group(1))
            continue
        else:
            flush_quote()

        # normal text/bullets
        _emit_paragraphs(ln)

    # flush any pending blocks
    flush_code()
    flush_table()
    flush_quote()

def _add_sources_slide(prs: Presentation, sources_map: Dict[int, Dict[str, str]], order: List[int]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    _add_slide_title(slide, "Sources")
    tb = _add_textbox(
        slide,
        PAGE["content_left"],
        PAGE["content_top"] + Inches(1.0),
        PAGE["content_width"],
        PAGE["content_height"] - Inches(1.0),
        )
    tf = _clear_and_get_textframe(tb)
    p0 = tf.paragraphs[0]
    _style_paragraph(p0, space_after=4)
    first = True
    for sid in order:
        src = sources_map.get(sid)
        if not src:
            continue
        title = src.get("title") or f"Source {sid}"
        url = src.get("url","")
        p = p0 if first else tf.add_paragraph()
        first = False
        _style_paragraph(p, level=0, space_after=4)
        r1 = p.add_run()
        r1.text = f"[{sid}] "
        _style_run(r1, size=TYPE_SCALE["body"], bold=True, color=PALETTE["fg"])
        r2 = p.add_run()
        r2.text = title + " "
        _style_run(r2, size=TYPE_SCALE["body"], color=PALETTE["fg"])
        if url:
            r3 = p.add_run()
            r3.text = _domain_of(url)
            _style_run(r3, size=TYPE_SCALE["body"], color=PALETTE["accent"])
            try: r3.hyperlink.address = url
            except Exception: pass

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
    Render a PowerPoint deck (PPTX) from Markdown with opinionated, modern styling.
    - New slide on '## ' headings; supports code fences, blockquotes, pipe tables, bullets.
    """
    basename = _basename_only(path, ".pptx")
    outdir = _outdir()
    outfile = outdir / basename
    _ensure_parent(outfile)

    sources_map: Dict[int, Dict[str, str]] = {}
    order: List[int] = []
    if sources:
        sources_map, order = md_utils._normalize_sources(sources)

    sections = _split_markdown_sections(content_md or "")
    prs = Presentation()

    # Title slide
    if title:
        _add_title_slide(prs, title)
    else:
        fst_title, _ = sections[0]
        _add_title_slide(prs, fst_title)

    # Content slides
    for stitle, body in sections:
        _add_content_slide(prs, stitle, body, sources_map, resolve_citations)

    # Sources slide
    if include_sources_slide and sources_map:
        _add_sources_slide(prs, sources_map, order)

    prs.save(str(outfile))
    return basename
