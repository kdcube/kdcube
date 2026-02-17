# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/rendering_tools.py

from __future__ import annotations

import pathlib
import os
import re
import json
import textwrap
import asyncio
from typing import Annotated, Optional, Any

import semantic_kernel as sk
import logging

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

import kdcube_ai_app.apps.chat.sdk.tools.md_utils as md_utils
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir, resolve_workdir, \
    load_sources_pool_from_disk
from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_citation_sids_any, extract_local_paths_any
from kdcube_ai_app.apps.chat.sdk.tools.ctx_tools import SourcesUsedStore
from kdcube_ai_app.apps.chat.sdk.tools.docx_renderer import render_docx
from kdcube_ai_app.apps.chat.sdk.tools.pptx_renderer import render_pptx
from kdcube_ai_app.apps.chat.sdk.tools.md2pdf_async import AsyncMarkdownPDF, PDFOptions, get_shared_md2pdf
from kdcube_ai_app.apps.chat.sdk.util import _defence

# Bound at runtime by ToolManager
_SERVICE = None
_INTEGRATIONS = None

def bind_service(svc):
    global _SERVICE
    _SERVICE = svc

def bind_integrations(integrations):
    global _INTEGRATIONS
    _INTEGRATIONS = integrations or {}

logger = logging.getLogger("rendering_tools")
_DATA_URI_RE = re.compile(r"data:[^;]+;base64,", re.IGNORECASE)


def _warn_on_data_uri(content: str, tool_name: str) -> None:
    if not content:
        return
    if _DATA_URI_RE.search(content):
        logger.warning(
            "%s: detected base64 data URIs in content. Use file paths from OUT_DIR/base_dir instead.",
            tool_name,
        )

def _update_sources_used_for_filename(filename: str, content: str) -> None:
    if not filename or not isinstance(content, str) or not content.strip():
        return
    try:
        sids = extract_citation_sids_any(content)
    except Exception:
        return
    if not sids:
        return
    store = SourcesUsedStore()
    store.load()
    store.upsert([{"filename": filename, "sids": sids}])


def _log_asset_resolution(content: str, base_dir: pathlib.Path, *, tool_name: str) -> None:
    """
    Debug helper: log where relative assets will resolve from (base_dir + cwd)
    and whether they exist.
    """
    if not isinstance(content, str) or not content.strip():
        return
    try:
        paths = extract_local_paths_any(content)
    except Exception:
        return
    if not paths:
        return
    try:
        cwd = pathlib.Path(os.getcwd()).resolve()
    except Exception:
        cwd = pathlib.Path(os.getcwd())
    try:
        base_dir = base_dir.resolve()
    except Exception:
        base_dir = pathlib.Path(base_dir)
    logger.info("%s: asset resolution base_dir=%s cwd=%s", tool_name, base_dir, cwd)
    for rel in paths:
        if not rel or rel.startswith(("/", "\\")):
            continue
        abs_base = (base_dir / rel).resolve()
        abs_cwd = (cwd / rel).resolve()
        logger.info(
            "%s: asset %s base_dir=%s exists=%s cwd=%s exists=%s",
            tool_name,
            rel,
            abs_base,
            abs_base.exists(),
            abs_cwd,
            abs_cwd.exists(),
        )


def _ensure_html_wrapper(content: str, *, title: Optional[str] = None) -> str:
    if not content:
        return content
    lowered = content.lstrip().lower()
    if "<html" in lowered and "</html>" in lowered:
        return content
    safe_title = title or "Document"
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head>\n"
        "  <meta charset=\"UTF-8\">\n"
        f"  <title>{safe_title}</title>\n"
        "</head>\n"
        "<body>\n"
        f"{content}\n"
        "</body>\n"
        "</html>\n"
    )

PROFESSIONAL_PDF_CSS = """
/* Clean Professional PDF Stylesheet - Lightweight like reference */
@page {
  size: A4;
  margin: 25mm 20mm 30mm 20mm;
  
  @bottom-center {
    content: counter(page);
    font-family: Arial, Helvetica, sans-serif;
    font-size: 10pt;
    color: #666;
  }
}

body {
  font-family: Arial, Helvetica, sans-serif;
  font-size: 10pt;
  line-height: 1.5;
  color: #111;
}

h1 { font-size: 18pt; margin: 8pt 0 10pt; }
h2 { font-size: 14pt; margin: 10pt 0 8pt; }
h3 { font-size: 12pt; margin: 8pt 0 6pt; }

p { margin: 0 0 8pt; }

ul, ol { margin: 0 0 8pt 18pt; padding: 0; }
li { margin: 0 0 4pt; }

code, pre { font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace; }
pre { background: #f6f8fa; padding: 8pt; border-radius: 4pt; overflow: hidden; }

table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
th, td { border: 1px solid #ddd; padding: 4pt 6pt; font-size: 9pt; }
tr:nth-child(even) { background: #f9f9f9; }
"""


def _outdir() -> pathlib.Path:
    return resolve_output_dir()


def _safe_relpath(s: str, default_name: str = "output.pdf") -> str:
    s = (s or "").strip()
    if not s:
        return default_name
    s = s.replace("\\", "/")
    p = pathlib.PurePosixPath(s)
    if p.is_absolute() or any(part == ".." for part in p.parts):
        return default_name
    return str(p)


def _resolve_read_path(path: str, default_name: str = "output.pdf") -> pathlib.Path:
    outdir = resolve_output_dir()
    rel = _safe_relpath(path, default_name)
    return outdir / rel


def _basename_only(s: str, default_name: str) -> str:
    return _safe_relpath(s, default_name)

def _ok_result() -> dict[str, Any]:
    return {"ok": True, "error": None}

def _error_result(*, code: str, message: str, where: str, managed: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "where": where,
            "managed": managed,
        },
    }


class RenderingTools:
    def __init__(self):
        self._md2pdf: Optional[AsyncMarkdownPDF] = None

    async def _get_md2pdf(self) -> AsyncMarkdownPDF:
        if self._md2pdf is None:
            self._md2pdf = await get_shared_md2pdf()
        return self._md2pdf

    @kernel_function(
        name="write_pptx",
        description=(
            "Render HTML into a PPTX deck using python-pptx. "
            "Returns an envelope: {ok, error}.\n\n"
            "For professional slide authoring (layouts, color schemes, content budgets, citations):\n"
            "→ Use skill 'pptx-press' (skills.public.pptx-press)\n\n"
            "=== QUICK ESSENTIALS ===\n\n"
            "SLIDE STRUCTURE:\n"
            "• One <section id='slide-N'> per slide\n"
            "• <h1>Slide Title</h1> + optional <p class='subtitle'>Subtitle</p>\n"
            "• Supported body: h2/h3, p, ul/ol+li, tables, callouts, two-column layouts\n"
            "• Inline: <strong>, <em>, <span class='...'>, <sup class='cite'> for citations\n\n"
            "IMAGES (CRITICAL - Use File Paths):\n"
            "• MUST use relative file paths from OUT_DIR, NOT base64 data URIs\n"
            "• Pattern: <img src='turn_id/files/chart.png' width='640'> when file at OUT_DIR/turn_id/files/chart.png\n"
            "• Sizing: width='640' or style='width:5in; height:3in;' (supports px/pt/in)\n"
            "• Base64 URIs will crash - always use file paths\n\n"
            "SUPPORTED CSS (others ignored):\n"
            "• color, background/background-color (hex #RGB or #RRGGBB only)\n"
            "• font-size (px/pt/em), line-height (number like 1.3)\n"
            "• padding (px/pt/in), border-bottom (title underlines), border-left (accent bars)\n"
            "• .two-column { gap }, .column { background; padding; border-left }\n"
            "• Tables: th/td colors, tr:nth-child(even) striping\n\n"
            "CONTENT BUDGETS (to avoid aggressive scaling):\n"
            "• Standard slide: 1 heading + 6 bullets OR 2 paragraphs OR 1 callout (~25-40 words)\n"
            "• Two-column: each column ≤ 1 h3 + 3 bullets OR 2 paragraphs; ~12 lines max/column\n"
            "• Tables: ≤6 columns, ≤8 rows\n"
            "• Titles: ≤8 words; Subtitles: ≤12 words (one sentence)\n\n"
            "CITATIONS (HTML):\n"
            "• Inline: <sup class='cite' data-sids='1,3'>[[S:1,3]]</sup> after factual claim\n"
            "• data-sids: numeric IDs (comma-separated or range 2-4)\n"
            "• Inner text [[S:...]] must mirror data-sids\n"
            "• Alternative: <div class='footnotes'>[[S:n]] markers</div>\n"
            "• Sources slide auto-generated when sources provided and include_sources_slide=True\n\n"
            "LAYOUT TIPS:\n"
            "• Content auto-scales down (min ~70%) if too large - budget content to avoid this\n"
            "• Two-column: <div class='two-column'><div class='column'>...</div><div class='column'>...</div></div>\n"
            "• Callouts: <div class='highlight-box'>...</div> or any div with background+border-left\n"
            "• Lists indent ~0.25in; text wraps automatically\n\n"
            "AVOID (ignored/unsupported):\n"
            "• min-height/100vh, flex/grid beyond .two-column, gradients, box-shadow, border-radius\n"
            "• position:fixed/sticky, transform, page-break properties\n"
            "• Base64 image data URIs\n\n"
            "For comprehensive guidance on professional layouts, color schemes, complete templates → see skill 'pptx-press'"
        )
    )
    async def write_pptx(
        self,
        path: Annotated[str, "Destination .pptx path under OUTPUT_DIR (relative path)."],
        content: Annotated[str, "HTML (only HTML) to render. Use <section> per slide."] = "",
        title: Annotated[Optional[str], "Optional deck title (title slide)."] = None,
        include_sources_slide: Annotated[bool, "Append a 'Sources' slide if sources are given."] = False,
        base_dir: Annotated[Optional[str], "Base dir for resolving relative images in HTML. Defaults to OUTPUT_DIR."] = None,
    ) -> Annotated[dict, "Result envelope: {ok: bool, error: null|{code,message,where,managed}}."]:
        try:
            outdir = resolve_output_dir()
            fname = _basename_only(path, "deck.pptx")
            base_dir = base_dir or str(outdir)
            workdir = str(resolve_workdir())
            out_path = outdir / fname
            out_path.parent.mkdir(parents=True, exist_ok=True)

            content = _defence(content, none_on_failure=False, format="html")
            content = textwrap.dedent(content).strip()
            _update_sources_used_for_filename(fname, content)
            sources = load_sources_pool_from_disk()
            _warn_on_data_uri(content, "write_pptx")
            _log_asset_resolution(content, pathlib.Path(base_dir), tool_name="write_pptx")

            resolve_citations: Annotated[bool, "Convert [[S:n]] tokens into hyperlinks."] = True
            await asyncio.to_thread(
                render_pptx,
                str(out_path),
                content_html=content,
                title=title,
                base_dir=base_dir,
                workdir=workdir,
                sources=sources,
                resolve_citations=resolve_citations,
                include_sources_slide=include_sources_slide,
            )
            if not out_path.exists():
                return _error_result(
                    code="file_not_produced",
                    message="PPTX file was not produced.",
                    where="rendering_tools.write_pptx",
                    managed=True,
                )
            return _ok_result()
        except Exception as e:
            msg = str(e).strip() or "Failed to render PPTX."
            return _error_result(
                code=type(e).__name__,
                message=msg,
                where="rendering_tools.write_pptx",
                managed=False,
            )

    @kernel_function(
        name="write_png",
        description=(
            "Render Markdown, HTML, or Mermaid diagrams to PNG image using Playwright + Chromium. "
            "Supports three formats: 'markdown', 'html' (control sizing via CSS), or 'mermaid'. "
            "Returns an envelope: {ok, error}. "
            "Fitting guidance: prefer full_page=True; increase width (e.g., 2200–3200) for wide diagrams; "
            "use render_delay_ms=1000–2000 to allow Mermaid/layout to settle. File is saved under OUTPUT_DIR."
        )
    )
    async def write_png(
        self,
        path: Annotated[str, "Destination .png path under OUTPUT_DIR (relative path)."],
        content: Annotated[str, (
            "Renderable content. If format='mermaid', supply RAW Mermaid text (no ``` fences). "
            "If 'markdown', supply Markdown (use ```mermaid blocks for diagrams). "
            "If 'html', supply an HTML snippet."
        )],
        format: Annotated[str, "Content format: 'markdown', 'html', or 'mermaid'"] = "mermaid",
        title: Annotated[Optional[str], "Optional title (for Markdown mode)."] = None,
        base_dir: Annotated[Optional[str], "Base directory for resolving relative assets."] = None,
        render_delay_ms: Annotated[int, "Extra delay for JS rendering (useful for charts/diagrams)."] = 1000,
        full_page: Annotated[bool, "Capture full scrollable page vs viewport only."] = True,
        width: Annotated[Optional[int], "Viewport width in pixels (defaults to 1200)."] = 3000,
        height: Annotated[Optional[int], "Viewport height in pixels (only used if full_page=False)."] = 2000,
    ) -> Annotated[dict, "Result envelope: {ok: bool, error: null|{code,message,where,managed}}."]:
        import html as html_lib
        import urllib.parse
        html_path: Optional[pathlib.Path] = None
        try:
            outdir = resolve_output_dir()
            fname = _basename_only(path, "output.png")
            out_path = outdir / fname
            out_path.parent.mkdir(parents=True, exist_ok=True)
            base_dir = base_dir or str(outdir)

            print(f"[RenderingTools.write_png]: rendering {format} to {out_path}")

            conv = await self._get_md2pdf()
            await conv.start()

            if format in ("mermaid", "html", "xml", "yaml"):
                content = _defence(content, none_on_failure=False, format=format)
                content = textwrap.dedent(content).strip()

            _update_sources_used_for_filename(fname, content)
            if format in ("html", "markdown"):
                _log_asset_resolution(content, pathlib.Path(base_dir), tool_name="write_png")

            if format == "mermaid":
                html_content = f"""<!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{html_lib.escape(title or "Mermaid Diagram")}</title>
        <pre id="debug" style="display:none;"></pre>
        <script src="https://cdn.jsdelivr.net/npm/mermaid@11.3.0/dist/mermaid.min.js"></script>
            
        <style>
            body {{
                margin: 0;
                padding: 40px;
                display: flex;
                justify-content: center;
                align-items: center;
                background: white;
            }}
            .mermaid {{
                display: flex;
                justify-content: center;
                align-items: center;
            }}
            #error {{ color: red; white-space: pre-wrap; font-family: monospace; }}
        </style>
    </head>
    <body>
        <div id="error"></div>
        <div class="mermaid">
    {html_lib.escape(content)}
        </div>
        <script>
            mermaid.initialize({{ 
                startOnLoad: true, 
                theme: 'default',
                securityLevel: 'loose',
                logLevel: 'debug'
            }});
           // Capture errors
            window.addEventListener('error', (e) => {{
                document.getElementById('error').textContent = 'Error: ' + e.message + '\\n' + e.error?.stack;
            }});      
            mermaid.parseError = function(err, hash) {{
                document.getElementById('error').textContent = 'Mermaid Parse Error:\\n' + err;
            }};                  
            window.addEventListener('load', () => {{
                setTimeout(() => {{ window.__RENDER_READY__ = true; }}, 500);
            }});
        </script>
    </body>
    </html>"""

            elif format == "html":
                html_content = content

            else:
                base_href = conv._base_href_for(pathlib.Path(base_dir) if base_dir else None)
                html_content = conv.markdown_to_html(content, base_href, title or "Document")

            import time
            html_filename = f"_render_{int(time.time() * 1000000)}.html"
            html_path = outdir / html_filename
            html_path.write_text(html_content, encoding="utf-8")

            try:
                context = await conv._browser.new_context(
                    viewport={"width": width or 1200, "height": height or 800},
                    device_scale_factor=2
                )
                page = await context.new_page()

                screenshot_opts = {
                    "path": str(out_path),
                    "full_page": bool(full_page),
                }

                if format == "mermaid":
                    try:
                        await page.goto(f"file://{html_path}", wait_until="networkidle")
                        await page.wait_for_function("window.__RENDER_READY__ === true", timeout=30000)

                        if full_page:
                            await page.screenshot(**screenshot_opts)
                        else:
                            svg_element = await page.query_selector(".mermaid svg")
                            if svg_element:
                                svg_content = await svg_element.evaluate("(el) => el.outerHTML")
                                svg_html = f"""<!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ margin: 0; padding: 0; background: white; }}
                    svg {{ 
                        width: 100vw; 
                        height: 100vh;
                        display: block;
                    }}
                </style>
            </head>
            <body>
                {svg_content}
            </body>
            </html>"""
                                png_context = await conv._browser.new_context(
                                    viewport={"width": width or 2400, "height": height or 1600},
                                    device_scale_factor=2
                                )
                                png_page = await png_context.new_page()
                                svg_data_url = f"data:text/html;charset=utf-8,{urllib.parse.quote(svg_html)}"
                                await png_page.goto(svg_data_url, wait_until="networkidle")
                                await png_page.screenshot(path=str(out_path), full_page=False)
                                await png_context.close()
                            else:
                                raise Exception("No SVG found")
                        
                    except Exception as e:
                        print(f"⚠️ Mermaid SVG extraction failed: {e}")
                        await page.screenshot(**screenshot_opts)
                else:
                    await page.goto(f"file://{html_path}", wait_until="networkidle")
                    await page.screenshot(**screenshot_opts)

                await context.close()
            finally:
                if html_path is not None:
                    try:
                        html_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            if not out_path.exists():
                return _error_result(
                    code="file_not_produced",
                    message="PNG file was not produced.",
                    where="rendering_tools.write_png",
                    managed=True,
                )
            return _ok_result()
        except Exception as e:
            msg = str(e).strip() or "Failed to render PNG."
            return _error_result(
                code=type(e).__name__,
                message=msg,
                where="rendering_tools.write_png",
                managed=False,
            )

    @kernel_function(
        name="write_pdf",
        description=(
            "Render Markdown, HTML, or Mermaid diagrams to PDF **using Playwright + headless Chromium** "
            "(JavaScript is executed; Chart.js/D3/etc. render). Returns an envelope: {ok, error}.\n\n"
            "For professional PDF layouts (multi-page documents, proper page breaks, compact spacing, "
            "domain-adaptive colors), use skill 'pdf-press' for comprehensive authoring guidance.\n\n"
            "=== QUICK AUTHORING ESSENTIALS ===\n\n"
            "1) PAGE & ORIENTATION\n"
            "   • Define: <style>@page { size: A4 portrait; margin: 20mm; }</style> or @page { size: A4 landscape; }\n"
            "   • Use centered content column ≤ 700px (portrait) or ≤ 1000px (landscape)\n"
            "   • Avoid full-viewport wrappers (100vh/100vw) and fixed-position bars in printable content\n\n"
            "2) LAYOUT & BREAKS (Prevent Content Splitting)\n"
            "   • Wrap headings + content together: <section style='break-inside:avoid; page-break-inside:avoid;'>\n"
            "   • Max content block height: 220mm (portrait) or 150mm (landscape) to avoid forced splits\n"
            "   • Use tight spacing: padding 10-14px, margins 12-16px (not 20-40px)\n"
            "   • Force page break: style='page-break-before:always;' or style='page-break-after:always;'\n\n"
            "3) TABLES\n"
            "   • Always provide <thead> and <tbody>. Avoid row/column spans that split across pages\n"
            "   • Wrap tables: <div style='break-inside:avoid;'><h3>Title</h3><table>...</table></div>\n"
            "   • Use compact sizing: font-size:8.5pt; padding:4px 6px; line-height:1.3;\n"
            "   • Prefer narrower columns; allow wrapping (word-break:break-word)\n"
            "   • If >12-15 rows, split into multiple tables\n\n"
            "4) TYPOGRAPHY\n"
            "   • Body: 10pt, line-height:1.5; Headers: h1=18pt max, h2=14pt, h3=12pt\n"
            "   • Never use fonts >20pt (except magazine covers up to 24pt)\n"
            "   • Include webfonts early; avoid late-loading fonts that reflow at print time\n\n"
            "5) MEDIA (images/svg/canvas)\n"
            "   • Use responsive sizes: max-width:100%; height:auto; display:block;\n"
            "   • For charts/canvas, let them auto-size in CSS; avoid hardcoded pixel heights >200mm\n"
            "   • Wrap in figures: <figure style='break-inside:avoid;'><img src='...'><figcaption>...</figcaption></figure>\n"
            "   • Legends should wrap; avoid cramped spaces\n\n"
            "6) IMAGES (CRITICAL - Use File Paths, NOT Base64)\n"
            "   • MUST use relative file paths from OUT_DIR (the tool's output directory / base_dir)\n"
            "   • HTML mode: <img src='turn_id/files/chart.png' alt='Chart'> when file is at OUT_DIR/turn_id/files/chart.png\n"
            "   • Markdown mode: ![Chart](turn_id/files/chart.png) when file is at OUT_DIR/turn_id/files/chart.png\n"
            "   • Do NOT embed base64 data URIs in HTML/Markdown; they can crash headless Chromium on multi-page PDFs\n"
            "   • Ensure HTML/Markdown generator knows relative paths and their association with visual content\n\n"
            "7) DON'TS (cause clipping, overlaps, or ugly splits)\n"
            "   • Banner-style headers with 30px+ padding (wastes vertical space)\n"
            "   • Headings without break-inside:avoid wrappers (causes mid-text page splits)\n"
            "   • position:fixed/sticky in printable area\n"
            "   • overflow:hidden on large layout wrappers\n"
            "   • transform:scale/translate on containers that cross page boundaries\n"
            "   • Single containers >250mm tall (will force ugly mid-content split)\n\n"
            "FORMATS: 'markdown' (GitHub-flavored), 'html' (custom layouts, JS execution), 'mermaid' (diagrams)\n"
            "SOURCES: Use include_sources_section=True to append references (Markdown mode)\n"
            "ORIENTATION: Set landscape=True for A4 landscape (default: portrait)\n\n"
            "For comprehensive guidance on color schemes, multi-column layouts, scientific papers, "
            "magazine styles, and complete templates → see skill 'pdf-press'"
        )
    )
    async def write_pdf(
        self,
        path: Annotated[str, "Destination .pdf path under OUTPUT_DIR (relative path). Parent dirs are created."],
        content: Annotated[str, "Content to render (Markdown, HTML, or Mermaid code depending on format)."],
        format: Annotated[str, "Content format: 'markdown', 'html', or 'mermaid'"] = "markdown",
        title: Annotated[Optional[str], "Optional document title."] = None,
        include_sources_section: Annotated[bool, "Append a 'Sources' section listing all passed sources. In Markdown mode. Ignored in HTML mode"] = True,
        landscape: Annotated[bool, "Render in landscape orientation"] = False,
    ) -> Annotated[dict, "Result envelope: {ok: bool, error: null|{code,message,where,managed}}."]:
        try:
            resolve_citations: Annotated[bool, "Replace [[S:n]] tokens with inline links. In Markdown mode. Ignored in HTML mode"] = True

            outdir = resolve_output_dir()
            fname = _basename_only(path, "output.pdf")
            out_path = (outdir / fname)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            base_dir = str(outdir)

            use_professional_style = True
            print(f"[RenderingTools.write_pdf]: rendering {format} to {out_path}")
            out_path.parent.mkdir(parents=True, exist_ok=True)

            conv = await self._get_md2pdf()

            if format in ("mermaid", "html", "xml", "yaml", "markdown"):
                content = _defence(content, none_on_failure=False, format=format)
                content = textwrap.dedent(content).strip()
            if format in ("html", "markdown"):
                _warn_on_data_uri(content, "write_pdf")
            _update_sources_used_for_filename(fname, content)
            if format in ("html", "markdown"):
                _log_asset_resolution(content, pathlib.Path(base_dir), tool_name="write_pdf")

            if format == "mermaid":
                import html as html_lib
                mermaid_html = f"""<!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>{html_lib.escape(title or "Mermaid Diagram")}</title>
            <script src="https://cdn.jsdelivr.net/npm/mermaid@11.3.0/dist/mermaid.min.js"></script>
            <style>
                body {{
                    margin: 0;
                    padding: 20px;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    background: white;
                }}
                .mermaid {{
                    display: flex;
                    justify-content: center;
                    align-items: center;
                }}
                #error {{ 
                    color: red; 
                    white-space: pre-wrap; 
                    font-family: monospace; 
                    padding: 20px;
                }}
            </style>
        </head>
        <body>
            <div id="error"></div>
            <div class="mermaid">
        {content}
            </div>
            <script>
                mermaid.initialize({{ 
                    startOnLoad: true, 
                    theme: 'default',
                    securityLevel: 'loose',
                    logLevel: 'debug'
                }});
                
                // Capture errors
                window.addEventListener('error', (e) => {{
                    document.getElementById('error').textContent = 'Error: ' + e.message + '\\n' + (e.error?.stack || '');
                }});
                
                mermaid.parseError = function(err, hash) {{
                    document.getElementById('error').textContent = 'Mermaid Parse Error:\\n' + err;
                }};
                
                // Signal ready for PDF capture
                window.addEventListener('load', () => {{
                    setTimeout(() => {{ window.__MERMAID_READY__ = true; }}, 500);
                }});
            </script>
        </body>
        </html>"""
                conv.pdf_options.display_header_footer = False
                conv.pdf_options.prefer_css_page_size = True
                conv.pdf_options.landscape = landscape
                conv.extra_css = []

                await conv.convert_html_string(
                    html=mermaid_html,
                    output_pdf=str(out_path),
                    title=title or "Mermaid Diagram",
                    base_dir=base_dir,
                )
                if not out_path.exists():
                    return _error_result(
                        code="file_not_produced",
                        message="PDF file was not produced.",
                        where="rendering_tools.write_pdf",
                        managed=True,
                    )
                return _ok_result()

            if format == "html":
                conv.enable_mathjax = False
                conv.pdf_options.prefer_css_page_size = True
                conv.extra_css = []
                conv.pdf_options.display_header_footer = False
                conv.pdf_options.landscape = landscape
                content = _ensure_html_wrapper(content, title=title or "Document")
                if resolve_citations:
                    try:
                        from kdcube_ai_app.apps.chat.sdk.tools.citations import (
                            build_citation_map_from_sources as _build_citation_map_from_sources,
                            replace_html_citations as _replace_html_citations,
                        )
                        sources = load_sources_pool_from_disk()
                        if sources:
                            cmap = _build_citation_map_from_sources(sources)
                            if cmap:
                                content = _replace_html_citations(content, cmap)
                    except Exception:
                        pass

                await conv.convert_html_string(
                    html=content,
                    output_pdf=str(out_path),
                    title=title or "Document",
                    base_dir=base_dir,
                )
                if not out_path.exists():
                    return _error_result(
                        code="file_not_produced",
                        message="PDF file was not produced.",
                        where="rendering_tools.write_pdf",
                        managed=True,
                    )
                return _ok_result()

            css_files = []

            if use_professional_style:
                css_path = outdir / "clean_professional.css"
                pdf_opts = PDFOptions(
                    format="A4",
                    margin_top="25mm",
                    margin_right="20mm",
                    margin_bottom="30mm",
                    margin_left="20mm",
                    print_background=True,
                    display_header_footer=False,
                    prefer_css_page_size=True,
                    scale=1.0,
                    landscape=landscape,
                )
                conv.pdf_options = pdf_opts

                orient = "landscape" if landscape else "portrait"
                css_text = PROFESSIONAL_PDF_CSS.replace(
                    "size: A4;", f"size: {pdf_opts.format} {orient};"
                )
                css_path.write_text(css_text, encoding="utf-8")
                css_files = [str(css_path)]
            else:
                pdf_opts = PDFOptions(
                    format="A4",
                    margin_top="16mm",
                    margin_right="16mm",
                    margin_bottom="16mm",
                    margin_left="16mm",
                    print_background=True,
                    display_header_footer=True,
                    prefer_css_page_size=False,
                    scale=1.0,
                    landscape=landscape,
                )

            conv.pdf_options = pdf_opts

            conv.enable_mathjax = False
            conv.extra_css = css_files

            sources = load_sources_pool_from_disk()
            by_id, order = md_utils._normalize_sources(sources)
            final_md = content

            effective_title = title or "Document"
            if title and not final_md.strip().startswith('#'):
                final_md = f"# {title}\n\n{final_md}"

            if resolve_citations and by_id:
                final_md = md_utils._replace_citation_tokens(final_md, by_id)

            if include_sources_section and by_id:
                final_md += md_utils._create_clean_sources_section(by_id, order)
            await conv.convert_string(
                markdown_text=final_md,
                output_pdf=str(out_path),
                title=effective_title,
                base_dir=base_dir or ".",
            )
            if not out_path.exists():
                return _error_result(
                    code="file_not_produced",
                    message="PDF file was not produced.",
                    where="rendering_tools.write_pdf",
                    managed=True,
                )
            return _ok_result()
        except Exception as e:
            msg = str(e).strip() or "Failed to render PDF."
            return _error_result(
                code=type(e).__name__,
                message=msg,
                where="rendering_tools.write_pdf",
                managed=False,
            )

    # @kernel_function(
    #     name="write_html",
    #     description=(
    #         "Write an HTML file. Optionally resolves citations so [[S:n]] tokens and "
    #         "<sup class=\"cite\" data-sids=\"...\">...</sup> placeholders become clickable links "
    #         "(target=_blank). Returns an envelope: {ok, error}."
    #     )
    # )
    async def write_html(
        self,
        path: Annotated[str, "Destination .html path under OUTPUT_DIR (relative path)."],
        content: Annotated[str, "HTML content to write. Can contain [[S:n]] tokens or <sup class='cite' ...> placeholders."],
        title: Annotated[Optional[str], "Optional <title> if you pass raw body; ignored if full HTML."] = None,
        first_only: Annotated[bool, "When multiple SIDs given, keep only the first when rendering inline."] = False,
    ) -> Annotated[dict, "Result envelope: {ok: bool, error: null|{code,message,where,managed}}."]:
        try:
            resolve_citations: Annotated[bool, "Convert [[S:n]] and <sup class='cite'> placeholders into links."] = True
            from kdcube_ai_app.apps.chat.sdk.tools.citations import (
                build_citation_map_from_sources as _build_citation_map_from_sources,
                replace_html_citations as _replace_html_citations,
            )

            outdir = resolve_output_dir()
            fname = _basename_only(path, "document.html")
            out_path = (outdir / fname)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            html = content
            low = html.strip().lower()
            if "<html" not in low and "<!doctype" not in low:
                safe_title = (title or "Document").replace("<", "").replace(">", "")
                html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{safe_title}</title></head><body>{html}</body></html>"

            _update_sources_used_for_filename(fname, html)

            sources = load_sources_pool_from_disk()
            if resolve_citations and sources:
                cmap = _build_citation_map_from_sources(sources)
                if cmap:
                    html = _replace_html_citations(
                        html,
                        cmap,
                        keep_unresolved=True,
                        first_only=bool(first_only),
                    )

            out_path.write_text(html, encoding="utf-8")
            if not out_path.exists():
                return _error_result(
                    code="file_not_produced",
                    message="HTML file was not produced.",
                    where="rendering_tools.write_html",
                    managed=True,
                )
            return _ok_result()
        except Exception as e:
            msg = str(e).strip() or "Failed to write HTML."
            return _error_result(
                code=type(e).__name__,
                message=msg,
                where="rendering_tools.write_html",
                managed=False,
            )

    @kernel_function(
        name="write_docx",
        description=(
            "Render Markdown into a modern, well-styled DOCX. Returns an envelope: {ok, error}.\n\n"
            "AUTHORING GUIDANCE\n"
            "- Use skills.public.docx-press for Markdown structure, tables, and citation handling.\n"
            "- Load with show_skills when needed: skills.public.docx-press."
        )
    )
    async def write_docx(
        self,
        path: Annotated[str, "Destination .docx path under OUTPUT_DIR (relative path)."],
        content: Annotated[str, "Markdown to render. Use headings (#/##/###), bullets (-/*/1.), code fences, blockquotes, pipe tables."],
        title: Annotated[Optional[str], "Optional document title (top of first page)."] = None,
        include_sources_section: Annotated[bool, "Append a References section listing all provided sources."] = True,
    ) -> Annotated[dict, "Result envelope: {ok: bool, error: null|{code,message,where,managed}}."]:
        try:
            resolve_citations: Annotated[bool, "Resolve [[S:n]] tokens into inline links (title→URL) where possible."] = True

            outdir = resolve_output_dir()
            fname = _basename_only(path, "document.docx")
            if not fname.lower().endswith(".docx"):
                fname += ".docx"
            out_path = outdir / fname
            out_path.parent.mkdir(parents=True, exist_ok=True)

            final_md = _defence(content, none_on_failure=False, format="markdown")
            final_md = textwrap.dedent(final_md).strip()
            _update_sources_used_for_filename(fname, final_md)
            sources = load_sources_pool_from_disk()
            if resolve_citations and sources:
                by_id, order = md_utils._normalize_sources(sources)
                if by_id:
                    final_md = md_utils._replace_citation_tokens(
                        final_md,
                        {k: {"title": v.get("title", ""), "url": v.get("url", "")} for k, v in by_id.items()},
                    )

            await asyncio.to_thread(
                render_docx,
                str(out_path),
                final_md,
                title=title,
                sources=sources,
                resolve_citations=resolve_citations,
                include_sources_section=include_sources_section,
            )
            if not out_path.exists():
                return _error_result(
                    code="file_not_produced",
                    message="DOCX file was not produced.",
                    where="rendering_tools.write_docx",
                    managed=True,
                )
            return _ok_result()
        except Exception as e:
            msg = str(e).strip() or "Failed to render DOCX."
            return _error_result(
                code=type(e).__name__,
                message=msg,
                where="rendering_tools.write_docx",
                managed=False,
            )


kernel = sk.Kernel()
tools = RenderingTools()
kernel.add_plugin(tools, "rendering_tools")
