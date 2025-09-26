# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# tools/parser.py
from abc import ABC, abstractmethod
from bs4 import BeautifulSoup

from urllib.parse import urljoin
from bs4 import BeautifulSoup, NavigableString

from typing import List, Dict, Any
import logging
import uuid
import re

from kdcube_ai_app.tools.datasource import BaseDataSource

logger = logging.getLogger("Parser")

class WebPageParser(ABC):
    """Interface for HTML parsers."""

    @abstractmethod
    def parse(self, html: str, url: str, title: str|None = None) -> str:
        """Parse HTML to markdown."""
        pass


class SimpleHtmlParser(WebPageParser):
    """Basic HTML → Markdown with link/image preservation and H1 fallback."""

    def parse(self, html: str, url: str, title: str | None = None) -> str:
        soup = BeautifulSoup(html or "", "html.parser")

        # ---- title resolution: prefer <title>, else provided 'title', else first <h1>, else 'Untitled'
        h1 = soup.find("h1")
        resolved_title = (
                (soup.title.string.strip() if soup.title and soup.title.string else None)
                or (title.strip() if title else None)
                or (h1.get_text(strip=True) if h1 else None)
                or "Untitled"
        )

        md_lines = [f"# {resolved_title}", ""]

        def _resolve_href(href: str | None) -> str | None:
            if not href: return None
            try: return urljoin(url or "", href)
            except Exception: return href

        def _inline_md(node) -> str:
            if isinstance(node, NavigableString):
                return str(node)
            if not hasattr(node, "name"):
                return ""

            name = node.name.lower()

            # inline conversions
            if name == "a":
                text = "".join(_inline_md(c) for c in node.contents).strip() or node.get("href", "").strip()
                href = _resolve_href(node.get("href"))
                return f"[{text}]({href})" if href else text
            if name in ("strong", "b"):
                return f"**{''.join(_inline_md(c) for c in node.contents)}**"
            if name in ("em", "i"):
                return f"*{''.join(_inline_md(c) for c in node.contents)}*"
            if name == "code":
                return f"`{''.join(_inline_md(c) for c in node.contents)}`"
            if name == "br":
                return "  \n"  # Markdown line break
            if name == "img":
                alt = (node.get("alt") or "").strip()
                src = _resolve_href(node.get("src"))
                return f"![{alt}]({src})" if src else ""

            # generic: concatenate children
            return "".join(_inline_md(c) for c in node.contents)

        # block-level traversal (headings, paragraphs, lists, blockquotes)
        for el in soup.find_all(["h1","h2","h3","h4","h5","h6","p","ul","ol","blockquote"]):
            tag = el.name.lower()

            if tag.startswith("h"):
                level = int(tag[1])
                text = _inline_md(el).strip()
                if text:
                    md_lines.append(f"{'#' * level} {text}")
                    md_lines.append("")
            elif tag == "p":
                text = _inline_md(el).strip()
                if text:
                    md_lines.append(text)
                    md_lines.append("")
            elif tag in ("ul", "ol"):
                ordered = (tag == "ol")
                for i, li in enumerate(el.find_all("li", recursive=False), start=1):
                    li_text = _inline_md(li).strip()
                    if not li_text: continue
                    bullet = f"{i}." if ordered else "-"
                    md_lines.append(f"{bullet} {li_text}")
                md_lines.append("")
            elif tag == "blockquote":
                q = _inline_md(el).strip()
                if q:
                    md_lines.append("\n".join([f"> {line}" for line in q.splitlines()]))
                    md_lines.append("")

        # optional: source URL footer (kept)
        if url:
            md_lines.append(f"\nSource: {url}")

        return "\n".join(md_lines).rstrip() + "\n"


class RawTextWebParser(WebPageParser):
    """Parser that returns HTML content as-is without processing."""

    def parse(self, html: str, url: str, title: str|None = None) -> str:
        """Return HTML content without modification."""
        return html


class MediumHtmlParser(WebPageParser):
    """HTML parser optimized for Medium articles."""

    def parse(self, html: str, url: str, title: str|None = None) -> str:
        """Parse HTML from Medium to markdown."""
        soup = BeautifulSoup(html, 'html.parser')

        # Extract title
        title = soup.title.text if soup.title else "Untitled Medium Article"

        # Start with the title
        markdown = f"# {title}\n\n"

        # Extract article content (Medium specific)
        article = soup.find('article')
        if article:
            # Extract headings and paragraphs from the article
            for element in article.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p']):
                tag_name = element.name

                if tag_name.startswith('h'):
                    level = int(tag_name[1])
                    markdown += f"{'#' * level} {element.text.strip()}\n\n"
                elif tag_name == 'p':
                    markdown += f"{element.text.strip()}\n\n"
        else:
            # Fallback to simple parser if article tag not found
            return SimpleHtmlParser().parse(html, url)

        # Add source URL at the end
        # markdown += f"\n\nSource: {url}"

        return markdown

class MarkdownParser:
    """Parse markdown files into segments based on headings."""

    def __init__(self, min_tokens: int = 50):
        """
        Initialize parser with minimum token threshold for segments.

        Args:
            min_tokens: Minimum number of tokens a segment should have before being merged
        """
        self.md_parser = None
        self.min_tokens = min_tokens
        try:
            from markdown_it import MarkdownIt
            self.md_parser = MarkdownIt()
        except ImportError:
            logger.error("markdown-it not installed. Please install with 'pip install markdown-it'")
    def _count_tokens(self, text: str) -> int:
        """
        Simple token counting using whitespace and punctuation splitting.
        This provides a reasonable approximation for token count.
        """
        if not text or not text.strip():
            return 0

        # Split on whitespace and common punctuation, filter empty strings
        tokens = re.findall(r'\b\w+\b', text.lower())
        return len(tokens)

    def _merge_small_segments(self, segments: List[Dict]) -> List[Dict]:
        """
        Merge consecutive segments that are below the minimum token threshold.

        Strategy:
        1. Go through segments sequentially
        2. If a segment is below threshold, merge it with the next segment
        3. When merging, preserve the heading of the first segment
        4. Continue until all segments meet the minimum threshold or no more merging is possible
        """
        if not segments:
            return segments

        merged_segments = []
        i = 0

        while i < len(segments):
            current_segment = segments[i].copy()
            current_tokens = self._count_tokens(current_segment.get("text", ""))

            # If current segment is large enough, keep it as-is
            if current_tokens >= self.min_tokens:
                merged_segments.append(current_segment)
                i += 1
                continue

            # Current segment is too small, try to merge with following segments
            j = i + 1
            accumulated_text = current_segment.get("text", "")

            while j < len(segments) and self._count_tokens(accumulated_text) < self.min_tokens:
                next_segment = segments[j]
                next_text = next_segment.get("text", "")

                # Add a separator if both have content
                if accumulated_text.strip() and next_text.strip():
                    accumulated_text += "\n\n"
                accumulated_text += next_text
                j += 1

            # Update the current segment with accumulated text
            current_segment["text"] = accumulated_text.strip()

            # Add metadata about the merge
            if j > i + 1:
                merged_count = j - i
                current_segment["merged_segments"] = merged_count
                current_segment["original_segments"] = [
                    {
                        "heading": segments[k].get("heading", ""),
                        "subheading": segments[k].get("subheading", ""),
                        "token_count": self._count_tokens(segments[k].get("text", ""))
                    }
                    for k in range(i, j)
                ]
                logger.debug(f"Merged {merged_count} segments: {current_segment['original_segments']}")

            merged_segments.append(current_segment)
            i = j

        # Log the merging results
        original_count = len(segments)
        final_count = len(merged_segments)
        if original_count != final_count:
            logger.info(f"Merged {original_count} segments into {final_count} segments "
                        f"(reduction: {original_count - final_count})")

        return merged_segments


    def parse_markdown(self,
                       md_content: str,
                       data_source: BaseDataSource,
                       min_tokens: int = None) -> List[Dict]:
        """
        Convert a Markdown document to segments with structure:
        {
            "segment_id": str,
            "url": str,            # source URL from data source
            "name": str,           # name derived from source
            "heading": str,        # h1 heading
            "subheading": str,     # h2 heading
            "text": str            # content under the h2 heading
        }
        """
        # Use instance default or provided override
        effective_min_tokens = min_tokens if min_tokens is not None else self.min_tokens or 0

        if not self.md_parser:
            from markdown_it import MarkdownIt
            self.md_parser = MarkdownIt()

        tokens = self.md_parser.parse(md_content)
        lines = md_content.splitlines()

        # Get headings from tokens
        headings = self._token_headings(tokens)

        # Parse content into initial rows
        # if any(h["level"] > 2 for h in headings):
        #     logger.warning(
        #         "Data source %s contains ### or deeper headings – using generic parser.",
        #         data_source.get_source_id(),
        #     )
        #     rows = self._rows_generic(headings, lines)
        # else:
        #     rows = self._rows_simple(headings, lines)
        rows = self._rows_improved(headings, lines)

        # Convert rows to initial segments
        segments = []
        url = data_source.to_url()
        name = data_source.get_source_id()

        for row in rows:
            segment = {
                "segment_id": str(uuid.uuid4()),
                "url": url,
                "name": name,
                "heading": row["heading"],
                "subheading": row["subheading"],
                "text": row["text"],
                "heading_level": row.get("heading_level"),      # NEW: preserve level
                "subheading_level": row.get("subheading_level"), # NEW: preserve level
                "token_count": self._count_tokens(row["text"])
            }
            segments.append(segment)

        # Apply merging logic if minimum token threshold is set
        if effective_min_tokens > 0:
            segments = self._merge_small_segments(segments)

            # Update token counts after merging
            for segment in segments:
                segment["token_count"] = self._count_tokens(segment.get("text", ""))

        # Remove token_count from final segments (it was just for processing)
        for segment in segments:
            segment.pop("token_count", None)

        return segments

    def _token_headings(self, tokens) -> List[Dict]:
        """
        Return a list of heading descriptors:
            {
                "level": int,            # 1 == h1, 2 == h2, ...
                "title": str,            # heading text
                "body_start": int,       # first line AFTER the heading
                "heading_start": int     # line index of the heading itself
            }
        markdown‑it token.map == [heading_start, body_start]
        """
        out = []
        for i, tok in enumerate(tokens):
            if tok.type == "heading_open" and tok.map:
                level = int(tok.tag[1])             # h1 → 1 …
                heading_start, body_start = tok.map
                title = tokens[i + 1].content       # inline token follows
                out.append(
                    {
                        "level": level,
                        "title": title,
                        "body_start": body_start,
                        "heading_start": heading_start,
                    }
                )
        return out

    def _rows_simple(self, headings, lines) -> List[Dict]:
        """
        Build rows assuming only h1 + h2 exist.
        heading = last seen h1, subheading = current h2
        """
        curr_h1 = ""
        rows = []

        for idx, h in enumerate(headings):
            lvl = h["level"]

            # determine slice for this heading's body -----------------------
            heading_start = h["heading_start"]
            if idx == 0 and heading_start > 0:
                rows.append(
                    {
                        "heading": "",
                        "subheading": "",
                        "text": "\n".join(lines[:heading_start]).strip(),
                    }
                )
            body_start = h["body_start"]
            body_end = (
                headings[idx + 1]["heading_start"]
                if idx + 1 < len(headings)
                else len(lines)
            )
            body = "\n".join(lines[body_start:body_end]).strip()

            # skip empty bodies
            if not body:
                continue

            # update stacks & emit rows
            if lvl == 1:                         # new top‑level heading
                curr_h1 = h["title"]
                rows.append(
                    {
                        "heading": curr_h1,
                        "subheading": "",
                        "text": body
                    }
                )

            elif lvl == 2:                       # subheading row
                rows.append(
                    {
                        "heading": curr_h1,
                        "subheading": h["title"],
                        "text": body
                    }
                )
        return rows

    def _rows_generic(self, headings, lines) -> List[Dict]:
        """
        Generic stack‑based parser used when deeper levels (### …) are present.
        Ensures bodies never contain following heading lines.
        """
        rows, stack = [], []

        for idx, h in enumerate(headings):
            lvl, title = h["level"], h["title"]

            if idx == 0 and h["heading_start"] > 0:
                rows.append(
                    {
                        "heading": "",
                        "subheading": "",
                        "text": "\n".join(lines[: h["heading_start"]]).strip(),
                    }
                )

            body_start = h["body_start"]
            body_end = (
                headings[idx + 1]["heading_start"]
                if idx + 1 < len(headings)
                else len(lines)
            )

            # maintain stack so stack[lvl‑1] is current title
            if len(stack) < lvl:
                stack.append(title)
            else:
                stack = stack[: lvl - 1] + [title]

            body = "\n".join(lines[body_start:body_end]).strip()
            if not body:
                continue

            parent = stack[-2] if len(stack) >= 2 else ""
            child = stack[-1]
            rows.append({"heading": parent, "subheading": child, "text": body, "body_start": body_start,
                         "body_end": body_end})

        return rows

    def _rows_improved(self, headings, lines) -> List[Dict]:
        """
        FIXED: Improved parser that correctly identifies hierarchical relationships.

        Strategy:
        1. Create segments based on actual heading structure
        2. Only create parent-child relationships for genuine hierarchies
        3. Recognize peer-level headings correctly
        """
        rows = []

        # Handle content before first heading
        if headings and headings[0]["heading_start"] > 0:
            rows.append({
                "heading": "",
                "subheading": "",
                "text": "\n".join(lines[:headings[0]["heading_start"]]).strip(),
                "heading_level": None,
                "subheading_level": None
            })

        # Track the current context hierarchy
        heading_stack = []  # Stack of (level, title) tuples

        for idx, h in enumerate(headings):
            level = h["level"]
            title = h["title"]

            # Get the body content for this heading
            body_start = h["body_start"]
            body_end = (
                headings[idx + 1]["heading_start"]
                if idx + 1 < len(headings)
                else len(lines)
            )
            body = "\n".join(lines[body_start:body_end]).strip()

            # Skip empty bodies
            if not body:
                continue

            # Update the heading stack to maintain proper hierarchy
            # Remove any headings at this level or deeper
            heading_stack = [(stack_level, stack_title) for stack_level, stack_title in heading_stack
                             if stack_level < level]

            # Add this heading to the stack
            heading_stack.append((level, title))

            # Determine parent-child relationship
            parent_heading = ""
            parent_level = None

            if len(heading_stack) >= 2:
                # The parent is the second-to-last item in the stack
                parent_level, parent_heading = heading_stack[-2]

            # Create the segment with proper level information
            if level == 1 or not parent_heading:
                # Top-level heading or orphaned heading
                rows.append({
                    "heading": title,
                    "subheading": "",
                    "text": body,
                    "heading_level": level,
                    "subheading_level": None
                })
            else:
                # This is a genuine subheading with a parent
                rows.append({
                    "heading": parent_heading,
                    "subheading": title,
                    "text": body,
                    "heading_level": parent_level,
                    "subheading_level": level
                })

        return rows
