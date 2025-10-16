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

try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
except Exception:
    async_playwright = None  # We'll error with a clear message later.

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


@dataclass
class AsyncMarkdownPDF:
    enable_mathjax: bool = False
    extra_css: Iterable[str] | None = None
    pdf_options: PDFOptions = field(default_factory=PDFOptions)
    headless: bool = True
    auto_install_browser: bool = False  # set True to try installing chromium if missing

    # Runtime state
    _playwright = None
    _browser = None

    # ---------- lifecycle ----------
    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def start(self):
        """Launch (or reuse) Playwright + Chromium once."""
        if async_playwright is None:
            raise RuntimeError("Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
        if self._playwright is None:
            try:
                self._playwright = await async_playwright().start()
            except Exception as e:
                raise RuntimeError(f"Failed to start Playwright: {e}") from e
        if self._browser is None:
            try:
                self._browser = await self._playwright.chromium.launch(headless=self.headless)
            except Exception as e:
                if self.auto_install_browser:
                    # Try to install chromium on the fly (best-effort)
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "playwright", "install", "chromium",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
                    self._browser = await self._playwright.chromium.launch(headless=self.headless)
                else:
                    raise RuntimeError(
                        "Chromium not available for Playwright. Run: python -m playwright install chromium"
                    ) from e

    async def close(self):
        """Close the shared browser and Playwright driver."""
        if self._browser is not None:
            try:
                await self._browser.close()
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None

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
        return path.resolve().parent.as_uri() + "/" if path else None

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

    async def _render_pdf(self, html_content: str, output_pdf: Path, title: str, base_dir: Optional[Path], render_delay_ms: int = 0):
        """Render HTML to PDF via Chromium. Uses a temporary file:// URL for robust asset loading."""
        await self.start()  # ensure browser exists
        header_tpl = self.pdf_options.header_html or _DEFAULT_HEADER
        footer_tpl = self.pdf_options.footer_html or _DEFAULT_FOOTER

        header_html = Template(header_tpl).render(
            title=title or "Document",
            date=_dt.datetime.now().strftime("%Y-%m-%d"),
        )
        footer_html = Template(footer_tpl).render()

        # Write HTML to a temporary file so relative file:// images work reliably
        tmp_dir = Path(tempfile.mkdtemp(prefix="md2pdf_"))
        try:
            html_path = tmp_dir / "index.html"
            html_path.write_text(html_content, encoding="utf-8")

            context = await self._browser.new_context()
            page = await context.new_page()

            await page.goto(html_path.as_uri(), wait_until="load")
            # Optional extra wait for client-side chart libs (Chart.js, etc.)
            if render_delay_ms and render_delay_ms > 0:
                await page.wait_for_timeout(min(render_delay_ms, 10000))

            if self.enable_mathjax:
                try:
                    await page.wait_for_function("window.__MD2PDF_MATHJAX_READY__ === true", timeout=10000)
                    await page.wait_for_function("window.__MD2PDF_MATHJAX_TYPESet__ === true", timeout=10000)
                except PWTimeout:
                    # Continue; math may show as raw TeX if offline
                    pass

            pdf_bytes = await page.pdf(
                format=self.pdf_options.format,
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
                prefer_css_page_size=self.pdf_options.prefer_css_page_size,
                scale=self.pdf_options.scale,
            )
            output_pdf.write_bytes(pdf_bytes)
            await context.close()
        finally:
            # Best-effort cleanup (ignore errors)
            try:
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
