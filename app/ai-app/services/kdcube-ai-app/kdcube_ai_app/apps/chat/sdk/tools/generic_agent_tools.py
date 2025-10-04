# chat/sdk/tools/generic_agent_tools.py
import json, time, math, pathlib, os
from typing import Annotated, Optional

from ddgs import DDGS
import semantic_kernel as sk
import asyncio

import kdcube_ai_app.apps.chat.sdk.tools.md_utils as md_utils
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir
from kdcube_ai_app.apps.chat.sdk.tools.pptx_renderer import render_pptx
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV

def _claim_sid_block(n: int) -> int:
    st = SOURCE_ID_CV.get()
    if not st:
        st = {"next": 1}
        SOURCE_ID_CV.set(st)
    base = int(st.get("next") or 1)
    st["next"] = base + n
    return base

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

from kdcube_ai_app.apps.chat.sdk.tools.md2pdf_async import AsyncMarkdownPDF, PDFOptions

def _outdir() -> pathlib.Path:
    return resolve_output_dir()

def _sanitize_filename(s: str, default_name: str = "output.pdf") -> str:
    """
    Keep only the basename; strip dirs/..; ensure not empty.
    """
    name = pathlib.Path(s or default_name).name
    # Extra guard: forbid traversal or empties
    if not name or name in (".", ".."):
        name = default_name
    return name

def _basename_only(s: str, default_name: str) -> str:
    name = pathlib.Path(s or default_name).name
    return default_name if not name or name in (".","..") else name

class AgentTools:
    """Semantic Kernel-native toolset the agent can call."""

    def __init__(self):
        self._notes: list[str] = []

        self._md2pdf: Optional[AsyncMarkdownPDF] = None
        self._md2pdf_lock = asyncio.Lock()

    async def _get_md2pdf(self) -> AsyncMarkdownPDF:
        """Create (once) and reuse a Chromium instance across calls."""
        if self._md2pdf is None:
            async with self._md2pdf_lock:
                if self._md2pdf is None:
                    self._md2pdf = AsyncMarkdownPDF(
                        # Let the tool self-heal the first time if Chromium isn't present
                        auto_install_browser=True,
                    )
                    await self._md2pdf.start()
        return self._md2pdf

    @kernel_function(
        name="web_search",
        description="Search the web for fresh info; returns JSON list of {sid, title, url, body}."
    )
    def web_search(
            self,
            query: Annotated[str, "Query string to search for."],
            n: Annotated[int, "Number of results to return.", {"min": 1, "max": 10}] = 5,
    ) -> Annotated[str, "JSON array: [{sid, title, url, body}, ...]"]:
        n = min(int(n), 10)
        base = _claim_sid_block(n)
        rows = []
        for i, hit in enumerate(DDGS().text(query, max_results=n)):
            # ddgs returns keys like {"title","href","body"...}
            rows.append({
                "sid": base + i,  # globally unique within the run
                "title": hit.get("title", ""),
                "url": hit.get("href", hit.get("url", "")),
                "body": hit.get("body", ""),
            })
            if len(rows) >= n: break
        return json.dumps(rows, ensure_ascii=False)

    @kernel_function(
        name="calc",
        description="Evaluate a safe math expression, e.g., '41*73+5' or 'sin(pi/4)**2'."
    )
    def calc(
            self,
            expression: Annotated[str, "A Python math expression using allowed functions/constants."],
    ) -> Annotated[str, "Stringified numeric result."]:
        allowed = {"__builtins__": {}}
        for n in ("pi","e","tau"): allowed[n] = getattr(math, n)
        for fn in ("sin","cos","tan","asin","acos","atan","sqrt","log","log10","exp","floor","ceil"):
            allowed[fn] = getattr(math, fn)
        return str(eval(expression, allowed, {}))

    @kernel_function(name="now", description="Get the current local time string.")
    def now(self) -> Annotated[str, "Current local time formatted as YYYY-MM-DD HH:MM:SS"]:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    @kernel_function(name="write_file", description="Write text to a file path; returns saved path.")
    def write_file(
            self,
            path: Annotated[str, "Destination file path. '~' is expanded; parent dirs are created."],
            content: Annotated[str, "UTF-8 text to write to the file."],
    ) -> Annotated[str, "Absolute path that was written."]:

        outdir = resolve_output_dir()
        fname = _sanitize_filename(path, "output.txt")
        p = (outdir / fname)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)

    @kernel_function(name="read_file", description="Read text from a file path; returns first n chars.")
    def read_file(
            self,
            path: Annotated[str, "Path to a readable text file. '~' is expanded."],
            n: Annotated[int, "Num of first char to return", {"min": 1, "max": 10}] = 4000,
    ) -> Annotated[str, "First n characters of the file contents."]:
        workdir = resolve_output_dir()
        fname = _sanitize_filename(path, "output.pdf")  # directories ignored
        p = (workdir / fname)
        return p.read_text(encoding="utf-8")[:n]

    @kernel_function(name="add_note", description="Write a short note in memory.")
    def add_note(
            self,
            note: Annotated[str, "free-form note text to store"],
    ) -> Annotated[str, "Total notes count."]:
        self._notes.append(note.strip())
        return f"Notes stored: {len(self._notes)}"

    @kernel_function(
        name="write_pdf",
        description="Render Markdown/plain text to a high-quality PDF via Playwright/Chromium. Returns saved path."
    )
    async def write_pdf(
            self,
            path: Annotated[str, "Destination .pdf path. '~' is expanded; parent dirs are created."],
            content_md: Annotated[str, "Markdown or plain text to render."],
            title: Annotated[Optional[str], "Optional document title."] = None,
            # pass the same sources you used in summarization (order matters for [[S:n]])
            sources: Annotated[Optional[str],
                           "JSON of sources to resolve [[S:n]] tokens. "
                           "Either an array [{sid?, title, url, ...}, ...] (sid=1..N if omitted) "
                           "or an object {\"1\":{title,url}, ...}."] = None,
            # control behaviors (safe defaults)
            resolve_citations: Annotated[bool, "Replace [[S:n]] tokens with inline links."] = True,
            include_sources_section: Annotated[bool, "Append a 'Sources' section listing all passed sources."] = True,

            # author: Annotated[Optional[str], "Optional author metadata (ignored in layout; can embed later)."] = None,
            # ⬇️ Optional power knobs; all default to a sensible layout
            # css: Annotated[Optional[str], "Comma-separated CSS file paths to layer on top of the default theme."] = None,
            # page_format: Annotated[str, "Chromium page format (A4, Letter, etc.)."] = "A4",
            # margin: Annotated[str, "Uniform margin (overrides individual margins if set)."] = "16mm",
            # margin_top: Annotated[Optional[str], "Top margin (e.g., '16mm')."] = None,
            # margin_right: Annotated[Optional[str], "Right margin."] = None,
            # margin_bottom: Annotated[Optional[str], "Bottom margin."] = None,
            # margin_left: Annotated[Optional[str], "Left margin."] = None,
            # display_header_footer: Annotated[bool, "Show header/footer (page numbers & date)."] = True,
            # header_html_path: Annotated[Optional[str], "Optional path to custom header HTML snippet."] = None,
            # footer_html_path: Annotated[Optional[str], "Optional path to custom footer HTML snippet."] = None,
            # scale: Annotated[float, "Chromium print scale (zoom)."] = 1.0,
            # prefer_css_page_size: Annotated[bool, "Use @page size & margins from CSS."] = False,
            # mathjax: Annotated[bool, "Enable MathJax for TeX math (requires internet unless self-hosted)."] = False,
            base_dir: Annotated[Optional[str], "Base directory for resolving relative images/links. Defaults to CWD."] = None,
    ) -> Annotated[str, "Absolute path to the written PDF."]:
        """
        Replaces the previous ReportLab-based PDF writer with Playwright.
        - GitHub-flavored Markdown, code highlighting (Pygments), images, tables, footnotes, admonitions
        - Solid default CSS; layer additional CSS via `css`
        - Reliable pagination + optional header/footer
        """
        # Resolve output path and ensure parent exists

        outdir = resolve_output_dir()
        fname = _basename_only(path, "output.pdf")           # ignore dirs
        out_path = (outdir / fname)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        base_dir = base_dir or str(outdir)

        print(f"[AgentTools.write_pdf]: rendering to {out_path}")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        page_format = "A4"
        margin = "16mm"
        margin_top = None
        margin_right = None
        margin_bottom = None
        margin_left = None
        display_header_footer = True
        header_html_path = None
        footer_html_path = None
        scale = 1.0
        prefer_css_page_size = False
        mathjax = False

        css = ""
        # Build PDF options
        m_top = margin_top or margin
        m_right = margin_right or margin
        m_bottom = margin_bottom or margin
        m_left = margin_left or margin

        hdr_html = pathlib.Path(header_html_path).read_text(encoding="utf-8") if header_html_path else None
        ftr_html = pathlib.Path(footer_html_path).read_text(encoding="utf-8") if footer_html_path else None

        pdf_opts = PDFOptions(
            format=page_format,
            margin_top=m_top,
            margin_right=m_right,
            margin_bottom=m_bottom,
            margin_left=m_left,
            print_background=True,
            display_header_footer=display_header_footer,
            header_html=hdr_html,
            footer_html=ftr_html,
            prefer_css_page_size=prefer_css_page_size,
            scale=scale,
        )

        # Prepare converter singleton & per-call config
        conv = await self._get_md2pdf()
        conv.pdf_options = pdf_opts
        conv.enable_mathjax = bool(mathjax)
        conv.extra_css = [p.strip() for p in (css.split(",") if css else []) if p.strip()]

        by_id, order = md_utils._normalize_sources(sources)
        patched_md = content_md
        if resolve_citations and by_id:
            patched_md = md_utils._replace_citation_tokens(patched_md, by_id)
        if include_sources_section and by_id:
            patched_md = md_utils._append_sources_section(patched_md, by_id, order)

        # Render
        effective_title = title or "Document"
        await conv.convert_string(
            markdown_text=patched_md,
            output_pdf=str(out_path),
            title=effective_title,
            base_dir=base_dir or ".",
        )
        # (Optional) embed simple metadata later if you like; Chromium's print-to-PDF has limited XMP hooks.

        return str(out_path)

    @kernel_function(
        name="write_pptx",
        description="Render Markdown into a PPTX deck. Returns the saved filename (basename only)."
    )
    async def write_pptx(
            self,
            path: Annotated[str, "Destination .pptx filename (name or path; directories ignored — saved in OUTPUT_DIR)."],
            content_md: Annotated[str, "Markdown to render. Use '## ' per-slide headings; bullets with '-'."],
            title: Annotated[Optional[str], "Optional deck title (title slide)."] = None,
            base_dir: Annotated[Optional[str], "Base dir for local assets (currently unused)."] = None,
            sources: Annotated[Optional[str], "JSON array of {sid,title,url,text}. Used for resolving [[S:n]] and sources slide."] = None,
            resolve_citations: Annotated[bool, "Convert [[S:n]] tokens into slide hyperlinks."] = False,
            include_sources_slide: Annotated[bool, "Append a 'Sources' slide if sources are given."] = False,
    ) -> Annotated[str, "Saved PPTX filename (basename)."]:

        outdir = resolve_output_dir()
        fname = _basename_only(path, "deck.pptx")

        return await asyncio.to_thread(
            render_pptx,
            str(outdir / fname),
            content_md,
            title=title,
            base_dir=base_dir,
            sources=sources,
            resolve_citations=resolve_citations,
            include_sources_slide=include_sources_slide,
        )

kernel = sk.Kernel()
tools = AgentTools()
kernel.add_plugin(tools, "agent_tools")

print()
