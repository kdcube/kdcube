#!/usr/bin/env python3
"""
md2pdf_async.py — Async Markdown ➜ PDF converter (Playwright/Chromium).

- Async & reusable: keep one Chromium instance for many conversions
- GitHub-flavored Markdown via markdown-it-py + mdit-py-plugins
- Pygments code highlighting (server-side, no JS needed)
- Solid default CSS + optional custom CSS layers
- Headers/footers with page numbers and date
- MathJax (optional)
- Handles relative images/links via <base> (and file:// HTML)
- Async CLI for scripting

Usage (library):
    from md2pdf_async import AsyncMarkdownPDF, PDFOptions
    import asyncio

    async def run():
        async with AsyncMarkdownPDF(enable_mathjax=False) as conv:
            await conv.convert_file("README.md", "out.pdf", title="My Doc")

    asyncio.run(run())

Usage (CLI):
    python md2pdf_async.py README.md out.pdf --title "My Doc" --css theme.css --format A4 --margin 16mm
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import html
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from jinja2 import Template
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.attrs import attrs_plugin
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

from kdcube_ai_app.infra.rendering.shared_browser import SharedBrowserService
try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
except Exception:
    async_playwright = None  # We'll error with a clear message later.

async def get_shared_md2pdf() -> 'AsyncMarkdownPDF':
    """
    Get an AsyncMarkdownPDF instance that uses the shared browser.

    More efficient than creating individual instances when doing
    multiple PDF conversions.

    Example:
        converter = await get_shared_md2pdf()
        await converter.convert_file("doc.md", "out.pdf")
    """
    from kdcube_ai_app.infra.rendering.shared_browser import get_shared_browser

    shared_browser = await get_shared_browser()

    converter = AsyncMarkdownPDF(
        enable_mathjax=False,
        shared_browser=shared_browser,
    )
    await converter.start()
    return converter

def _use_anchors_compat(md: MarkdownIt) -> None:
    """Apply anchors_plugin with args compatible across mdit-py-plugins versions - NO PARAGRAPH SYMBOLS."""
    for opts in (
            dict(permalink=False),                                              # Disable permalinks entirely
            dict(permalink=True, permalink_symbol="", permalink_space=False),   # Empty symbol (snake_case)
            dict(permalink=True, permalinkSymbol="", permalinkSpace=False),     # Empty symbol (camelCase)
            dict(permalink=True),                                               # Minimal fallback
    ):
        try:
            md.use(anchors_plugin, **opts)
            return
        except TypeError:
            continue
    # Final fallback - try without any options
    try:
        md.use(anchors_plugin)
    except Exception:
        # If anchors plugin fails entirely, skip it
        pass
# -----------------------------
# Defaults / Templates / CSS
# -----------------------------

_DEFAULT_CSS = """
@page { size: A4; margin: 16mm; }
html, body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, "Helvetica Neue", Arial, "Apple Color Emoji", "Segoe UI Emoji"; font-size: 11pt; line-height: 1.55; color: #111; font-weight: normal; }
main { max-width: 180mm; margin: 0 auto; }
h1, h2, h3, h4, h5, h6 { line-height: 1.25; margin: 1.2em 0 0.5em; font-weight: 700; }
h1 { font-size: 1.9rem; border-bottom: 1px solid #e6e6e6; padding-bottom: 0.2em; }
h2 { font-size: 1.5rem; margin-top: 1.6em; }
h3 { font-size: 1.2rem; }
p { margin: 0.7em 0; font-weight: normal; }
a { color: #005bbb; text-decoration: none; font-weight: normal; }
a:hover { text-decoration: underline; }
ul, ol { margin: 0.6em 0 0.6em 1.4em; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
pre { padding: 0.8em; overflow: auto; border-radius: 8px; background: #f6f8fa; border: 1px solid #eee; }
code { background: #f2f4f7; padding: 0.1em 0.3em; border-radius: 4px; }
blockquote { border-left: 3px solid #ddd; margin: 0.8em 0; padding: 0.1em 1em; color: #555; background: #fafafa; font-weight: normal; }
table { border-collapse: collapse; margin: 1em 0; width: 100%; }
th, td { border: 1px solid #e5e7eb; padding: 0.5em 0.6em; vertical-align: top; font-weight: normal; }
th { background: #f8fafc; text-align: left; font-weight: bold; }
hr { border: 0; border-top: 1px solid #e5e7eb; margin: 2em 0; }

img { max-width: 100%; }

.task-list-item { list-style-type: none; }
.task-list-item input[type="checkbox"] { margin-right: 0.4em; transform: scale(1.1); }

.admonition { border: 1px solid #e5e7eb; background: #f9fafb; padding: 0.8em 1em; border-radius: 8px; margin: 1em 0; }
.admonition .admonition-title { font-weight: 700; margin-bottom: 0.4em; }

/* Footnotes */
section.footnotes { font-size: 0.92em; color: #333; }
section.footnotes hr { display: none; }

/* Pygments baseline */
{{ pygments_css }}

/* Print tweaks */
@media print {
  a[href^="http"]::after { content: " (" attr(href) ")"; font-size: 0.85em; color: #6b7280; }
}

/* Header/Footer blocks used by Chromium */
.header, .footer { font-size: 9pt; color: #6b7280; width: 100%; padding: 0 10mm; }
.header { border-bottom: 1px solid #e5e7eb; }
.footer { border-top: 1px solid #e5e7eb; }
.header .title { float: left; }
.header .date { float: right; }
.footer .pagenum:before { content: counter(page); }
.footer .total:before { content: counter(pages); }
"""

_HTML_TEMPLATE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{{ title }}</title>
  {% if base_href %}<base href="{{ base_href }}">{% endif %}
  <style>
  {{ css }}
  </style>
  {% if enable_mathjax %}
  <script>
    window.__MD2PDF_MATHJAX_READY__ = false;
  </script>
  <script id="MathJax-script" async
    src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"
    onload="window.__MD2PDF_MATHJAX_READY__ = true;">
  </script>
  <script>
    window.addEventListener('load', () => {
      if (window.MathJax && MathJax.typesetPromise) {
        MathJax.typesetPromise().then(() => {
          window.__MD2PDF_MATHJAX_TYPESet__ = true;
        });
      } else {
        window.__MD2PDF_MATHJAX_TYPESet__ = true;
      }
    });
  </script>
  {% endif %}
</head>
<body>
  <main>
  {{ body }}
  </main>
</body>
</html>
"""

_DEFAULT_HEADER = """
<div class="header">
  <span class="title">{{ title|e }}</span>
  <span class="date">{{ date }}</span>
</div>
"""

_DEFAULT_FOOTER = """
<div class="footer">
  <span>Page <span class="pagenum"></span> / <span class="total"></span></span>
</div>
"""


# -----------------------------
# Markdown ➜ HTML (with Pygments)
# -----------------------------

class _PygmentsRenderer:
    def __init__(self, formatter: Optional[HtmlFormatter] = None):
        self.formatter = formatter or HtmlFormatter(nowrap=True)

    def fence(self, code: str, info: str | None) -> str:
        lang = (info or "").strip().split()[0] if info else ""
        try:
            if lang:
                lexer = get_lexer_by_name(lang, stripall=False)
            else:
                lexer = guess_lexer(code)
            highlighted = highlight(code, lexer, self.formatter)
        except ClassNotFound:
            highlighted = html.escape(code)
        except Exception:
            highlighted = html.escape(code)
        class_attr = f' class="language-{html.escape(lang)}"' if lang else ""
        return f'<pre><code{class_attr}>{highlighted}</code></pre>'


def _build_markdown_parser(renderer: _PygmentsRenderer) -> MarkdownIt:
    # md = MarkdownIt("commonmark", {"linkify": True, "html": True})
    md = MarkdownIt("gfm-like", {"linkify": True, "html": True})
    _use_anchors_compat(md)

    md.use(attrs_plugin)
    md.use(deflist_plugin)
    md.use(footnote_plugin)
    md.use(tasklists_plugin, enabled=True)
    md.use(container_plugin, name="info")
    md.use(container_plugin, name="note")
    md.use(container_plugin, name="tip")
    md.use(container_plugin, name="warning")

    fence_orig = md.renderer.rules.get("fence")
    def fence_rule(tokens, idx, options, env):
        token = tokens[idx]
        return renderer.fence(token.content, token.info)
    md.renderer.rules["fence"] = fence_rule or fence_orig
    return md


# -----------------------------
# Options & Converter
# -----------------------------

@dataclass
class PDFOptions:
    format: str = "A4"
    margin_top: str = "16mm"
    margin_right: str = "16mm"
    margin_bottom: str = "16mm"
    margin_left: str = "16mm"
    print_background: bool = True
    display_header_footer: bool = True
    header_html: Optional[str] = None
    footer_html: Optional[str] = None
    prefer_css_page_size: bool = False  # Use @page size if provided
    scale: float = 1.0
    landscape: bool = False


@dataclass
class AsyncMarkdownPDF:
    # Service-specific configuration
    enable_mathjax: bool = False
    extra_css: Iterable[str] | None = None
    pdf_options: PDFOptions = field(default_factory=PDFOptions)

    # Optional shared browser (if None, creates own)
    shared_browser: Optional[SharedBrowserService] = None

    # Standalone browser settings (only used if shared_browser is None)
    headless: bool = True
    auto_install_browser: bool = False

    # Runtime state (only used if NOT using shared browser)
    _own_browser_service: Optional[SharedBrowserService] = None
    _browser = None

    # ---------- lifecycle ----------
    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def start(self):
        """Ensure browser is available (either shared or own)."""
        if self.shared_browser:
            # Use shared browser - just ensure it's started
            self._browser = await self.shared_browser.get_browser()
        else:
            # Create own browser service
            if self._own_browser_service is None:
                self._own_browser_service = SharedBrowserService(
                    headless=self.headless,
                    auto_install_browser=self.auto_install_browser,
                )
            self._browser = await self._own_browser_service.get_browser()

    async def close(self):
        """Close only if we own the browser (not shared)."""
        if self._own_browser_service is not None:
            await self._own_browser_service.close()
            self._own_browser_service = None
        self._browser = None
    # ---------- helpers ----------
    @staticmethod
    def _read_css(paths: Iterable[str] | None) -> str:
        parts = []
        if paths:
            for p in paths:
                text = Path(p).read_text(encoding="utf-8")
                parts.append(text)
        return "\n\n".join(parts)

    def _compose_css(self) -> str:
        pygments_css = HtmlFormatter().get_style_defs(".highlight")
        return Template(_DEFAULT_CSS).render(pygments_css=pygments_css) + \
            (("\n\n" + self._read_css(self.extra_css)) if self.extra_css else "")

    @staticmethod
    def _base_href_for(path: Optional[Path]) -> Optional[str]:
        if not path:
            return None
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        try:
            if resolved.exists() and resolved.is_dir():
                return resolved.as_uri() + "/"
        except Exception:
            pass
        # Treat as file path (or unknown) -> use parent directory
        try:
            if resolved.suffix:
                return resolved.parent.as_uri() + "/"
        except Exception:
            pass
        # Fallback: assume directory
        return resolved.as_uri() + "/"

    def markdown_to_html(self, markdown_source: str, base_href: Optional[str], title: str) -> str:
        renderer = _PygmentsRenderer()
        md = _build_markdown_parser(renderer)
        body = md.render(markdown_source)
        return Template(_HTML_TEMPLATE).render(
            title=title or "Document",
            css=self._compose_css(),
            body=body,
            base_href=base_href,
            enable_mathjax=self.enable_mathjax,
        )

    async def _render_pdf(
            self,
            html_content: str,
            output_pdf: Path,
            title: str,
            base_dir: Optional[Path],
            render_delay_ms: int = 0
    ):
        """Render HTML to PDF via Chromium. JavaScript executes (Chart.js, Tailwind, etc).
        Limitations:
        A single very tall element (e.g., a 2000px fixed-height div/canvas) can’t be split across pages—Chromium can only break at block boundaries.
        If page CSS uses very specific !important rules, it can override our print rules.
        Heavy use of position:fixed intended for web headers/footers is neutralized (by design) to avoid overlays in print; if a page truly needs fixed elements, you’d have to opt-in for that case.
        Some SPAs keep long-polling/open sockets, so networkidle may not fire; you already have render_delay_ms as a manual guard.
        """
        await self.start()  # ensure browser exists

        header_tpl = self.pdf_options.header_html or _DEFAULT_HEADER
        footer_tpl = self.pdf_options.footer_html or _DEFAULT_FOOTER

        header_html = Template(header_tpl).render(
            title=title or "Document",
            date=_dt.datetime.now().strftime("%Y-%m-%d"),
        )
        footer_html = Template(footer_tpl).render()

        # Write HTML to a temp file so relative assets and fonts resolve reliably
        tmp_dir = None
        html_dir = base_dir
        if html_dir is None:
            tmp_dir = Path(tempfile.mkdtemp(prefix="md2pdf_"))
            html_dir = tmp_dir
        try:
            if html_dir is None:
                raise RuntimeError("HTML temp directory could not be resolved")
            if base_dir is not None:
                tmp_file = tempfile.NamedTemporaryFile(
                    prefix="md2pdf_",
                    suffix=".html",
                    dir=str(html_dir),
                    delete=False,
                )
                html_path = Path(tmp_file.name)
                tmp_file.close()
            else:
                html_path = html_dir / "index.html"
            html_path.write_text(html_content, encoding="utf-8")

            # High-DPI context for crisp canvas/bitmaps
            context = await self._browser.new_context(device_scale_factor=3)
            page = await context.new_page()

            # 1) Before any page JS runs: spoof DPR and kill animations to avoid mid-print shifts
            await page.add_init_script("""
                Object.defineProperty(window, 'devicePixelRatio', { get: () => 3 });
            """)
            await page.add_init_script("""
                (() => {
                  const s = document.createElement('style');
                  s.textContent = `*{animation:none!important;transition:none!important}`;
                  document.head.appendChild(s);
                })();
            """)

            # 2) Load the page and let external assets settle
            await page.goto(html_path.as_uri(), wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle")  # Tailwind / Chart.js / fonts from CDN

            # 3) Fonts first (prevents later reflow that causes overlaps)
            try:
                await page.wait_for_function(
                    "document.fonts && document.fonts.status === 'loaded'",
                    timeout=7000
                )
            except Exception:
                pass
            orient = "landscape" if self.pdf_options.landscape else "portrait"
            page_size = self.pdf_options.format or "A4"

            # 4) Add **generic** print CSS (not tailored to any particular HTML)
            PRINT_SAFE_CSS = f"""
            @page {{ size: {page_size} {orient}; margin: 16mm 16mm 20mm 16mm; }}
            
            * {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
            
            @media print {{
              /* Tables: repeat headers/footers; avoid splitting rows/cells */
              thead {{ display: table-header-group; }}
              tfoot {{ display: table-footer-group; }}
              tr, th, td {{ break-inside: avoid; page-break-inside: avoid; }}
            
              /* Only elements that break poorly should be protected */
              figure, pre, table, blockquote, img, svg, canvas {{
                break-inside: avoid;
                page-break-inside: avoid;
              }}
            
              /* NEW: protect common wrappers that contain media (when :has is supported) */
              div:has(> img),     div:has(> svg),     div:has(> canvas),
              section:has(> img), section:has(> svg), section:has(> canvas),
              article:has(> img), article:has(> svg), article:has(> canvas),
              figure:has(img),    figure:has(svg),    figure:has(canvas),
              .card:has(img), .panel:has(img), .box:has(img), .content:has(img) {{
                break-inside: avoid;
                page-break-inside: avoid;
              }}
            
              /* Keep headings with the next block to avoid orphan titles */
              h1, h2, h3, h4 {{ break-after: avoid-page; page-break-after: avoid; }}
              h1 + *, h2 + *, h3 + *, h4 + * {{ break-before: avoid-page; page-break-before: auto; }}
            
              /* Make paragraphs/lists less likely to orphan/widow lines */
              p, li {{ orphans: 2; widows: 2; }}
            
              /* Sticky/fixed often overlays in print output */
              .sticky, *[style*="position:sticky"] {{ position: static !important; top: auto !important; }}
              *[style*="position:fixed"] {{ position: static !important; top: auto !important; bottom: auto !important; }}
            
              /* Transforms can create odd stacking across page breaks */
              [style*="transform"] {{ transform: none !important; }}
            
              /* Safe media defaults for print */
              video, audio {{ display: none !important; }}
            
              /* Commonly needed spacing and sizing for canvases */
              canvas {{ margin-bottom: 18px; }}
              img, svg, canvas {{
                max-width: 100% !important;
                height: auto !important;
                object-fit: contain;             /* NEW: keep full media visible if constrained */
              }}
            
              /* NEW: cap media height so it doesn't exceed a page */
              img, svg, canvas {{ max-height: 90vh; }}
            
              /* Viewport wrappers often cause clipping on A4 landscape */
              html, body {{ width:auto !important; height:auto !important; max-width:100% !important; overflow:visible !important; }}
              [style*="100vw"], [class*="w-screen"] {{ width:100% !important; }}
              [style*="100vh"], [class*="h-screen"] {{ height:auto !important; min-height:auto !important; }}
            
              /* NEW: frameworks frequently set overflow hidden; let contents print */
              *[style*="overflow:hidden"], .overflow-hidden {{ overflow: visible !important; }}
            
              /* Optional helper the author can use when needed */
              .no-break {{ break-inside: avoid; page-break-inside: avoid; }}
            }}
            """

            await page.add_style_tag(content=PRINT_SAFE_CSS)

            # 5) Switch to print media (so @media print rules apply) and give layout a moment
            await page.emulate_media(media="print")
            if render_delay_ms and render_delay_ms > 0:
                await page.wait_for_timeout(min(render_delay_ms, 10000))
            await page.wait_for_timeout(200)

            # === 6) ✅ RESIZE HINT (your question) ===
            try:
                await page.evaluate("""
                  (() => {
                    window.dispatchEvent(new Event('resize'));  // many canvas libs listen
                  })();
                """)
                # Chart.js specific: force a resize/update if present (generic, harmless if not)
                await page.evaluate("""
                  (() => {
                    if (window.Chart && Chart.instances) {
                      for (const c of Object.values(Chart.instances)) {
                        try { c.resize(); c.update('resize'); } catch {}
                      }
                    }
                  })();
                """)
                await page.wait_for_timeout(300)
            except Exception:
                pass

            # 7) Generic Chart/canvas relief: if a canvas container is tight, give it a bit more height.
            #    This helps Chart.js when legends at the bottom visually collide with captions.
            try:
                await page.evaluate("""
                (() => {
                  const EXTRA = 36; // px reserved for legends / labels at the bottom
    
                  const bump = (el) => {
                    const parent = el.parentElement || el;
                    const h = parent.clientHeight || el.clientHeight || 0;
                    if (h && h < 380) parent.style.height = (h + EXTRA) + 'px';
                  };
    
                  document.querySelectorAll('canvas').forEach(bump);
    
                  // If Chart.js is present, ask charts to resize after the bump.
                  try {
                    const reg = (window.Chart && (Chart.instances || (Chart.registry && (Chart.registry._charts || Chart.registry.items)))) || {};
                    const list = Array.isArray(reg) ? reg : Object.values(reg);
                    for (const entry of list) {
                      const inst = entry?.chart || entry;
                      if (inst && typeof inst.resize === 'function') inst.resize();
                    }
                  } catch (e) {}
                })();
                """)
                await page.wait_for_timeout(150)
            except Exception:
                pass

            # 8) MathJax (only if you enabled it on the converter)
            if self.enable_mathjax:
                try:
                    await page.wait_for_function("window.__MD2PDF_MATHJAX_READY__ === true", timeout=10000)
                    await page.wait_for_function("window.__MD2PDF_MATHJAX_TYPESet__ === true", timeout=10000)
                except PWTimeout:
                    # continue; math may show as raw TeX if offline
                    pass

            needs_shrink = await page.evaluate(
                "document.scrollingElement.scrollWidth > document.scrollingElement.clientWidth"
            )
            if self.pdf_options.landscape and needs_shrink and (self.pdf_options.scale or 1.0) >= 1.0:
                self.pdf_options.scale = 0.98

            # 9) Print to PDF
            pdf_bytes = await page.pdf(
                format=self.pdf_options.format,
                landscape=self.pdf_options.landscape,
                print_background=self.pdf_options.print_background,
                display_header_footer=self.pdf_options.display_header_footer,
                header_template=header_html,
                footer_template=footer_html,
                margin={
                    "top": self.pdf_options.margin_top,
                    "right": self.pdf_options.margin_right,
                    "bottom": self.pdf_options.margin_bottom,
                    "left": self.pdf_options.margin_left,
                },
                prefer_css_page_size=True,  # honor the @page we injected above
                scale=self.pdf_options.scale,
            )
            output_pdf.write_bytes(pdf_bytes)
            await context.close()
        finally:
            # Best-effort cleanup
            try:
                if base_dir is not None:
                    if "html_path" in locals():
                        html_path.unlink(missing_ok=True)
                elif tmp_dir is not None:
                    for p in tmp_dir.glob("*"):
                        p.unlink(missing_ok=True)
                    tmp_dir.rmdir()
            except Exception:
                pass

    async def convert_html_string(
            self,
            html: str,
            output_pdf: str | Path,
            *,
            title: str = "Document",
            base_dir: Optional[str | Path] = None,
    ) -> Path:
        """
        Render raw HTML (as-is) to PDF. No Markdown conversion, no template CSS injected.
        Header/footer behavior is controlled by self.pdf_options (set by caller).
        Relative asset resolution:
          - If base_dir is provided, the temp HTML is written inside base_dir so that
            file:// relative paths resolve naturally without injecting <base>.
          - Otherwise, a temporary directory is used (relative paths may not resolve).
        """
        out = Path(output_pdf)
        await self._render_pdf(
            html_content=html,
            output_pdf=out,
            title=title,
            base_dir=Path(base_dir) if base_dir else None,
        )
        return out

    # ---------- public API ----------
    async def convert_string(
            self,
            markdown_text: str,
            output_pdf: str | Path,
            *,
            title: str = "Document",
            base_dir: Optional[str | Path] = None,
            extra_css: Optional[Iterable[str]] = None,
    ) -> Path:
        out = Path(output_pdf)
        base_href = self._base_href_for(Path(base_dir) if base_dir else None)
        html_content = self.markdown_to_html(markdown_text, base_href, title)
        await self._render_pdf(html_content, out, title=title, base_dir=Path(base_dir) if base_dir else None)
        return out

    async def convert_file(
            self,
            input_markdown: str | Path,
            output_pdf: str | Path,
            *,
            title: Optional[str] = None,
            encoding: str = "utf-8",
    ) -> Path:
        md_path = Path(input_markdown)
        if not md_path.exists():
            raise FileNotFoundError(f"Markdown file not found: {md_path}")
        text = md_path.read_text(encoding=encoding)

        # Derive title from first H1 if not supplied
        auto_title = None
        m = re.search(r"^\s*#\s+(.+)$", text, flags=re.MULTILINE)
        if m:
            auto_title = m.group(1).strip()
        effective_title = title or auto_title or md_path.stem

        base_href = self._base_href_for(md_path)
        html_content = self.markdown_to_html(text, base_href, effective_title)
        out = Path(output_pdf)
        await self._render_pdf(html_content, out, title=effective_title, base_dir=md_path.parent)
        return out


# -----------------------------
# Async CLI
# -----------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="md2pdf-async",
        description="Async Markdown ➜ PDF with Playwright/Chromium.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("input", help="Input Markdown file (or '-' for stdin)")
    p.add_argument("output", help="Output PDF path")
    p.add_argument("--title", default=None, help="Document title (defaults to first H1 or filename)")
    p.add_argument("--format", default="A4", help="Page format (A4, Letter, etc.)")
    p.add_argument("--margin", default="16mm", help="Uniform page margin")
    p.add_argument("--margin-top", default=None)
    p.add_argument("--margin-right", default=None)
    p.add_argument("--margin-bottom", default=None)
    p.add_argument("--margin-left", default=None)
    p.add_argument("--no-header-footer", action="store_true", help="Disable header/footer")
    p.add_argument("--header-html", default=None, help="Path to custom header HTML snippet")
    p.add_argument("--footer-html", default=None, help="Path to custom footer HTML snippet")
    p.add_argument("--css", action="append", default=None, help="Additional CSS file(s)")
    p.add_argument("--scale", type=float, default=1.0, help="Print scale")
    p.add_argument("--prefer-css-page-size", action="store_true", help="Use @page size from CSS")
    p.add_argument("--mathjax", action="store_true", help="Enable MathJax (loads from CDN)")
    p.add_argument("--auto-install-browser", action="store_true", help="Attempt chromium install if missing")
    return p.parse_args(argv)

def _load_optional_file(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return p.read_text(encoding="utf-8")

async def _main_async(argv: list[str] | None = None):
    args = _parse_args(argv or sys.argv[1:])

    # Build options
    margin_top = args.margin_top or args.margin
    margin_right = args.margin_right or args.margin
    margin_bottom = args.margin_bottom or args.margin
    margin_left = args.margin_left or args.margin

    pdf_opts = PDFOptions(
        format=args.format,
        margin_top=margin_top,
        margin_right=margin_right,
        margin_bottom=margin_bottom,
        margin_left=margin_left,
        display_header_footer=not args.no_header_footer,
        header_html=_load_optional_file(args.header_html),
        footer_html=_load_optional_file(args.footer_html),
        prefer_css_page_size=args.prefer_css_page_size,
        scale=args.scale,
    )

    async with AsyncMarkdownPDF(
            enable_mathjax=args.mathjax,
            extra_css=args.css,
            pdf_options=pdf_opts,
            auto_install_browser=args.auto_install_browser,
    ) as converter:
        if args.input == "-":
            md_text = sys.stdin.read()
            await converter.convert_string(
                md_text, args.output, title=args.title or "Document", base_dir=os.getcwd()
            )
        else:
            await converter.convert_file(args.input, args.output, title=args.title)

    print(f"✅ Wrote PDF: {args.output}")

if __name__ == "__main__":
    asyncio.run(_main_async())
