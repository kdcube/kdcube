# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/web/web_extractors.py
"""
Web content extraction with adaptive header selection and smart fallbacks.

Key improvements:
1. Adaptive header selection based on site type (bot vs browser headers)
2. Automatic fallback from custom -> trafilatura if content extraction fails
3. Smart Discourse forum detection

Architecture:
- SourceCookieManager: Handles authentication cookies for paywalled sites
- HeaderSelector: Chooses appropriate headers based on URL patterns
- ContentExtractor: Strategy selection with automatic fallback
- WebContentFetcher: HTTP fetching with authentication and content extraction
- SiteSpecificExtractors: Custom extractors for Medium, GitHub, StackOverflow, etc.
"""

from typing import Dict, Any, Optional, List, Literal
import aiohttp
import asyncio
import logging
import json
import os
import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from kdcube_ai_app.apps.chat.sdk.tools.text_proc_utils import truncate_text

# Trafilatura for main content extraction
try:
    import trafilatura
    from trafilatura.settings import use_config
    TRAFILATURA_AVAILABLE = True

    # Configure trafilatura for better extraction
    trafilatura_config = use_config()
    trafilatura_config.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
    trafilatura_config.set("DEFAULT", "MIN_EXTRACTED_SIZE", "200")
    trafilatura_config.set("DEFAULT", "MIN_OUTPUT_SIZE", "100")
except ImportError:
    TRAFILATURA_AVAILABLE = False
    trafilatura = None
    trafilatura_config = None

from kdcube_ai_app.tools.scrap_utils import (
    extract_publication_dates_core,
    html_title,
    make_clean_content_html,
    html_fragment_to_markdown,
)
import kdcube_ai_app.utils.text as text_utils

logger = logging.getLogger(__name__)


# ============================================================================
# Header Selection for Different Site Types
# ============================================================================

class HeaderSelector:
    """
    Chooses appropriate HTTP headers based on URL patterns.

    Bot headers (Googlebot): For bot-friendly sites like Discourse forums
    Browser headers: For sites that might block bots
    """

    # Bot-friendly headers (for Discourse, documentation sites)
    BOT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    # Regular browser headers (for sites that block bots)
    BROWSER_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Sec-Ch-Ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }

    @classmethod
    def should_use_bot_headers(cls, url: str) -> bool:
        """
        Determine if we should use bot headers for this URL.

        Bot headers work better for:
        - Discourse forums (they serve crawler-friendly HTML)
        - Community/documentation sites
        - Forums and discussion platforms

        Returns:
            True if bot headers should be used, False for browser headers
        """
        domain = urlparse(url).netloc.lower()
        path = urlparse(url).path.lower()

        # Discourse forums - these WANT to be crawled
        if 'discourse' in domain:
            return True

        # Common Discourse-powered sites
        discourse_sites = [
            'community.openai.com',
            'discuss.pytorch.org',
            'community.fly.io',
            'forum.cursor.com',
            'meta.discourse.org',
            'discuss.elastic.co',
        ]
        if any(site in domain for site in discourse_sites):
            return True

        # Other forum indicators (be conservative - only if clearly a forum)
        if any(indicator in domain for indicator in ['forum.', 'discuss.', 'community.']):
            return True

        # Documentation sites that are bot-friendly
        if any(indicator in domain or indicator in path
               for indicator in ['/docs/', '/documentation/']):
            # Only for known bot-friendly doc platforms
            if any(platform in domain for platform in ['github.com', 'gitlab.com', 'readthedocs']):
                return True

        # Default to browser headers (safer - avoids 403s)
        return False

    @classmethod
    def get_headers(cls, url: str) -> Dict[str, str]:
        """Get appropriate headers for the URL."""
        if cls.should_use_bot_headers(url):
            logger.debug(f"Using bot headers for: {url}")
            return cls.BOT_HEADERS.copy()
        else:
            logger.debug(f"Using browser headers for: {url}")
            return cls.BROWSER_HEADERS.copy()


# ============================================================================
# Source-Specific Cookie Manager
# ============================================================================

class SourceCookieManager:
    """
    Manages authentication cookies for different content sources.

    Reads from environment variables in format:
    WEB_FETCH_RESOURCES_<SOURCE>='{"cookies": {"cookie_name": "cookie_value", ...}}'

    Example:
        WEB_FETCH_RESOURCES_MEDIUM='{"cookies": {"uid": "abc123", "sid": "xyz789"}}'
    """

    _cookie_cache: Dict[str, Dict[str, str]] = {}
    _loaded = False

    @classmethod
    def _load_cookies(cls):
        """Load all cookies from environment variables once."""
        if cls._loaded:
            return

        cls._loaded = True

        # Map of source identifiers to environment variable suffixes
        source_mappings = {
            'medium.com': 'MEDIUM',
            'towardsdatascience.com': 'MEDIUM',
            'nytimes.com': 'NYT',
            'wsj.com': 'WSJ',
            # Add more sources as needed
        }

        for domain, env_suffix in source_mappings.items():
            env_var = f'WEB_FETCH_RESOURCES_{env_suffix}'
            env_value = os.getenv(env_var)

            if env_value:
                try:
                    config = json.loads(env_value)
                    cookies = config.get('cookies', {})

                    if cookies:
                        cls._cookie_cache[domain] = cookies
                        logger.info(f"✓ Loaded cookies for {domain}")
                        logger.debug(f"  Cookie keys: {list(cookies.keys())}")
                    else:
                        logger.warning(f"⚠ {env_var} exists but has no cookies")

                except json.JSONDecodeError as e:
                    logger.error(f"❌ Failed to parse {env_var}: {e}")
                except Exception as e:
                    logger.error(f"❌ Error loading {env_var}: {e}")

    @classmethod
    def get_cookies_for_url(cls, url: str) -> Optional[Dict[str, str]]:
        """Get cookies for a specific URL if available."""
        cls._load_cookies()
        url_lower = url.lower()

        for domain, cookies in cls._cookie_cache.items():
            if domain in url_lower:
                logger.debug(f"Using cookies for {domain}: {list(cookies.keys())}")
                return cookies

        return None

    @classmethod
    def has_cookies_for_url(cls, url: str) -> bool:
        """Check if cookies are available for this URL."""
        return cls.get_cookies_for_url(url) is not None


# ============================================================================
# Site-Specific Extractors
# ============================================================================

class SiteSpecificExtractors:
    """
    Specialized extractors for sites with paywalls, special formatting, or
    content that trafilatura doesn't handle well.

    Each extractor returns markdown-formatted text with images, or empty string on failure.
    """

    def extract_medium(self, html: str) -> str:
        """
        Extract Medium content with full markdown formatting and images.
        Handles Apollo State, Next.js data, and direct HTML parsing.
        """
        try:
            soup = BeautifulSoup(html, 'lxml')

            # Check for paywall redirect
            if soup.find('input', attrs={'name': 'signInRedirect'}):
                logger.warning("Medium: session expired")
                return ""

            # Helper functions for image handling
            def _best_srcset_value(srcset: str) -> str:
                """Choose the largest image from srcset."""
                parts = [p.strip() for p in (srcset or "").split(",") if p.strip()]
                if not parts:
                    return ""
                return parts[-1].split()[0]  # "<url> <width>w"

            def _best_src_from_picture(pic_tag):
                """Extract best image URL from <picture> tag."""
                best = ""
                for src in pic_tag.find_all("source"):
                    s = src.get("srcset") or src.get("srcSet") or ""
                    cand = _best_srcset_value(s)
                    if cand:
                        best = cand
                if not best:
                    img = pic_tag.find("img")
                    if img:
                        best = _best_src_from_img_tag(img)
                return best

            def _best_src_from_img_tag(img):
                """Extract best image URL from <img> tag."""
                src = img.get('src') or img.get('data-src') or ''
                if not src:
                    srcset = img.get('srcset', '')
                    if srcset:
                        parts = [p.strip() for p in srcset.split(',') if p.strip()]
                        if parts:
                            src = parts[-1].split()[0]
                return src

            def _append_image(lines, src, alt=None, caption=None):
                """Append image markdown to lines."""
                if not src:
                    return
                alt = (alt or caption or "").strip()
                lines.append(f"![{alt}]({src})\n")
                if caption:
                    cap = caption.strip()
                    if cap and cap != alt:
                        lines.append(f"*{cap}*\n")

            def _miro_url_from_id(image_id: str, max_width: int = 1400) -> str:
                """Build Medium CDN URL from image ID."""
                return f"https://miro.medium.com/v2/resize:fit:{max_width}/{image_id}"

            def _image_url_from_apollo(apollo_state: dict, image_id: str) -> str:
                """Resolve image URL from Apollo State or build from ID."""
                try:
                    for k, v in apollo_state.items():
                        if isinstance(v, dict) and v.get('id') == image_id:
                            url = v.get('originalUrl') or v.get('url')
                            if url:
                                return url
                except Exception:
                    pass
                return _miro_url_from_id(image_id)

            def _apply_markups(text: str, markups: list) -> str:
                """Apply inline formatting markups (bold, italic, code, links) to text."""
                if not markups:
                    return text

                sorted_markups = sorted(markups, key=lambda m: m.get('start', 0), reverse=True)

                for markup in sorted_markups:
                    m_type = markup.get('type')
                    start = markup.get('start', 0)
                    end = markup.get('end', len(text))

                    if start >= end or end > len(text):
                        continue

                    segment = text[start:end]

                    if m_type == 'STRONG':
                        replacement = f"**{segment}**"
                    elif m_type == 'EM':
                        replacement = f"*{segment}*"
                    elif m_type == 'CODE':
                        replacement = f"`{segment}`"
                    elif m_type == 'A':
                        href = markup.get('href', '#')
                        replacement = f"[{segment}]({href})"
                    else:
                        replacement = segment

                    text = text[:start] + replacement + text[end:]

                return text

            def paragraphs_to_markdown(paragraphs: list, apollo_state: Optional[dict] = None) -> str:
                """Convert Medium paragraphs structure to markdown with images."""
                lines = []
                for para in paragraphs:
                    if not isinstance(para, dict):
                        continue

                    p_type = para.get('type', 'P')
                    text = para.get('text', '') or ''
                    markups = para.get('markups', []) or []

                    # Handle images
                    if p_type in ('IMG', 'Image', 'FIGURE'):
                        meta = para.get('metadata', {}) or {}
                        image_id = meta.get('id') or meta.get('imageId') or ''
                        src = (_image_url_from_apollo(apollo_state or {}, image_id)
                               if image_id else (para.get('href', '') or ''))
                        caption = (para.get('caption') or meta.get('caption') or '').strip()
                        alt = (meta.get('alt') or meta.get('title') or
                               para.get('text') or caption)
                        _append_image(lines, src, alt, caption)
                        continue

                    # Apply inline formatting
                    if markups:
                        text = _apply_markups(text, markups)

                    # Format by paragraph type
                    if p_type == 'H2':
                        lines.append(f"## {text}\n")
                    elif p_type == 'H3':
                        lines.append(f"### {text}\n")
                    elif p_type == 'H4':
                        lines.append(f"#### {text}\n")
                    elif p_type in ['PRE', 'CODE']:
                        lines.append(f"```\n{text}\n```\n")
                    elif p_type == 'PQ':
                        lines.append(f"> {text}\n")
                    elif p_type == 'OLI':
                        lines.append(f"1. {text}")
                    elif p_type == 'ULI':
                        lines.append(f"* {text}")
                    else:
                        lines.append(f"{text}\n")

                return '\n'.join(lines)

            # Strategy 1: Apollo State (best for structured content with images)
            for script in soup.find_all('script'):
                script_text = script.string or ""
                if 'window.__APOLLO_STATE__' in script_text:
                    try:
                        match = re.search(
                            r'window\.__APOLLO_STATE__\s*=\s*(\{.+?\});',
                            script_text,
                            re.DOTALL
                        )
                        if match:
                            apollo_state = json.loads(match.group(1))
                            for key, value in apollo_state.items():
                                if isinstance(value, dict) and 'content' in value:
                                    body_model = value.get('content', {}).get('bodyModel', {})
                                    if isinstance(body_model, dict) and 'paragraphs' in body_model:
                                        md = paragraphs_to_markdown(
                                            body_model['paragraphs'],
                                            apollo_state
                                        )
                                        if md and len(md) > 500:
                                            logger.debug("Medium: extracted via Apollo State")
                                            return md
                    except Exception as e:
                        logger.debug(f"Apollo state parsing failed: {e}")

            # Strategy 2: Next.js data
            for script in soup.find_all('script', id='__NEXT_DATA__'):
                try:
                    data = json.loads(script.string or '{}')
                    props = data.get('props', {}).get('pageProps', {})
                    for key in ['post', 'article', 'story', 'initialState']:
                        item = props.get(key)
                        if isinstance(item, dict):
                            body = item.get('content', {}).get('bodyModel', {})
                            if isinstance(body, dict) and 'paragraphs' in body:
                                md = paragraphs_to_markdown(
                                    body['paragraphs'],
                                    props.get('apolloState') or {}
                                )
                                if md and len(md) > 500:
                                    logger.debug("Medium: extracted via Next.js data")
                                    return md
                except Exception as e:
                    logger.debug(f"Next.js data parsing failed: {e}")

            # Strategy 3: Direct HTML parsing with images
            article = soup.find('article')
            if article:
                markdown_lines = []
                image_found = False

                for element in article.find_all([
                    'h1', 'h2', 'h3', 'h4', 'p', 'pre', 'blockquote',
                    'ul', 'ol', 'figure', 'img', 'picture'
                ]):
                    # Handle images
                    if element.name == 'picture':
                        src = _best_src_from_picture(element)
                        cap_el = element.find_parent('figure')
                        caption = (cap_el.find('figcaption').get_text(strip=True)
                                   if cap_el and cap_el.find('figcaption') else None)
                        img = element.find('img')
                        alt = ((img.get('alt') if img else None) or
                               element.get('aria-label') or
                               element.get('title') or caption)
                        if src:
                            _append_image(markdown_lines, src, alt, caption)
                            image_found = True

                    elif element.name == 'figure':
                        pic = element.find('picture')
                        if pic:
                            src = _best_src_from_picture(pic)
                            img = pic.find('img')
                            alt_attr = img.get('alt') if img else None
                        else:
                            img = element.find('img')
                            src = _best_src_from_img_tag(img) if img else ""
                            alt_attr = img.get('alt') if img else None
                        cap_el = element.find('figcaption')
                        caption = cap_el.get_text(strip=True) if cap_el else None
                        alt = (alt_attr or element.get('aria-label') or
                               element.get('title') or caption)
                        if src:
                            _append_image(markdown_lines, src, alt, caption)
                            image_found = True

                    elif element.name == 'img':
                        src = _best_src_from_img_tag(element)
                        alt = (element.get('alt') or element.get('aria-label') or
                               element.get('title'))
                        if src:
                            _append_image(markdown_lines, src, alt)
                            image_found = True

                    # Handle text content
                    elif element.name == 'h1':
                        markdown_lines.append(f"# {element.get_text(strip=True)}\n")
                    elif element.name == 'h2':
                        markdown_lines.append(f"## {element.get_text(strip=True)}\n")
                    elif element.name == 'h3':
                        markdown_lines.append(f"### {element.get_text(strip=True)}\n")
                    elif element.name == 'h4':
                        markdown_lines.append(f"#### {element.get_text(strip=True)}\n")
                    elif element.name == 'pre':
                        code_text = element.get_text(strip=True)
                        markdown_lines.append(f"```\n{code_text}\n```\n")
                    elif element.name == 'blockquote':
                        quote_text = element.get_text(strip=True)
                        markdown_lines.append(f"> {quote_text}\n")
                    elif element.name == 'p':
                        # Apply inline formatting
                        p_html = str(element)
                        p_soup = BeautifulSoup(p_html, 'lxml')
                        for strong in p_soup.find_all(['strong', 'b']):
                            strong.replace_with(f"**{strong.get_text()}**")
                        for em in p_soup.find_all(['em', 'i']):
                            em.replace_with(f"*{em.get_text()}*")
                        for code in p_soup.find_all('code'):
                            code.replace_with(f"`{code.get_text()}`")
                        for link in p_soup.find_all('a'):
                            href = link.get('href', '#')
                            link.replace_with(f"[{link.get_text()}]({href})")
                        text = p_soup.get_text(separator=' ', strip=True)
                        if text:
                            markdown_lines.append(f"{text}\n")
                    elif element.name == 'ul':
                        for li in element.find_all('li', recursive=False):
                            markdown_lines.append(f"* {li.get_text(strip=True)}")
                        markdown_lines.append("")
                    elif element.name == 'ol':
                        for idx, li in enumerate(element.find_all('li', recursive=False), 1):
                            markdown_lines.append(f"{idx}. {li.get_text(strip=True)}")
                        markdown_lines.append("")

                markdown = '\n'.join(markdown_lines)

                # Fallback: add cover image if no inline images found
                if not image_found:
                    og = soup.find('meta', attrs={'property': 'og:image'})
                    if og and og.get('content'):
                        cover = og['content'].strip()
                        if cover:
                            markdown = f"![cover]({cover})\n\n" + markdown

                if len(markdown) > 500:
                    logger.debug("Medium: extracted via article tag")
                    return markdown

            logger.debug("Medium: no content found with any strategy")
            return ""

        except Exception as e:
            logger.warning(f"Medium extraction failed: {e}")
            return ""

    def extract_github(self, html: str) -> str:
        """Extract README or main content from GitHub."""
        try:
            soup = BeautifulSoup(html, 'lxml')

            # Find README content (newer GitHub layout)
            readme = soup.find('article', class_='markdown-body')
            if readme:
                logger.debug("GitHub: found README in article.markdown-body")
                text = readme.get_text(separator='\n', strip=True)
                return text_utils.strip_surrogates(text)

            # Try alternative selectors
            readme = soup.find('div', {'id': 'readme'})
            if readme:
                logger.debug("GitHub: found README in div#readme")
                text = readme.get_text(separator='\n', strip=True)
                return text_utils.strip_surrogates(text)

            # Look for any markdown-body class
            markdown = soup.find(class_='markdown-body')
            if markdown:
                logger.debug("GitHub: found markdown-body")
                text = markdown.get_text(separator='\n', strip=True)
                return text_utils.strip_surrogates(text)

            logger.debug("GitHub: no README found")
            return ""

        except Exception as e:
            logger.warning(f"GitHub extraction failed: {e}")
            return ""

    def extract_stackoverflow(self, html: str) -> str:
        """Extract question and answers from StackOverflow."""
        try:
            soup = BeautifulSoup(html, 'lxml')
            parts = []

            # Get the question
            question = soup.find('div', class_='question')
            if question:
                q_title = question.find('h1', class_='fs-headline1')
                if q_title:
                    parts.append(f"QUESTION: {q_title.get_text(strip=True)}\n")

                q_body = question.find('div', class_='s-prose')
                if q_body:
                    parts.append(q_body.get_text(separator='\n', strip=True))
                    parts.append("\n---\n")

            # Get accepted answer if exists
            accepted = soup.find('div', class_='accepted-answer')
            if accepted:
                a_body = accepted.find('div', class_='s-prose')
                if a_body:
                    parts.append("ACCEPTED ANSWER:\n")
                    parts.append(a_body.get_text(separator='\n', strip=True))

            result = '\n'.join(parts)
            if result:
                logger.debug("StackOverflow: extracted Q&A")
                return result

            return ""

        except Exception as e:
            logger.warning(f"StackOverflow extraction failed: {e}")
            return ""

    def extract_wikipedia(self, html: str) -> str:
        """
        Extract main content from Wikipedia, excluding navigation and references.
        """
        try:
            soup = BeautifulSoup(html, 'lxml')

            # Find main content
            content = soup.find('div', {'id': 'mw-content-text'})
            if not content:
                logger.debug("Wikipedia: no content div found")
                return ""

            # Remove unwanted elements
            for unwanted in content.find_all(
                    ['table', 'sup', 'div'],
                    class_=['infobox', 'navbox', 'reflist', 'reference', 'metadata']
            ):
                unwanted.decompose()

            # Extract paragraphs
            paragraphs = content.find_all('p')
            text = '\n\n'.join(
                p.get_text(strip=True)
                for p in paragraphs
                if p.get_text(strip=True)
            )

            if text:
                logger.debug(f"Wikipedia: extracted {len(text)} chars")
                return text

            return ""

        except Exception as e:
            logger.warning(f"Wikipedia extraction failed: {e}")
            return ""

    def extract_ibm_docs(self, html: str) -> str:
        """Extract content from IBM documentation sites."""
        try:
            soup = BeautifulSoup(html, 'lxml')

            # IBM docs often use these patterns
            content = (
                    soup.find('div', class_='bx--content') or
                    soup.find('div', class_='ibm-content') or
                    soup.find('div', {'id': 'content'}) or
                    soup.find('article') or
                    soup.find('main')
            )

            if content:
                # Remove navigation and sidebars
                for unwanted in content.find_all(
                        ['nav', 'aside', 'div'],
                        class_=lambda x: x and any(
                            keyword in str(x).lower()
                            for keyword in ['nav', 'sidebar', 'toc', 'breadcrumb']
                        )
                ):
                    unwanted.decompose()

                text = content.get_text(separator='\n', strip=True)
                if text:
                    logger.debug(f"IBM Docs: extracted {len(text)} chars")
                    return text

            return ""

        except Exception as e:
            logger.warning(f"IBM docs extraction failed: {e}")
            return ""

    def get_extractor(self, url: str):
        """
        Get appropriate extractor function for URL.
        Returns a callable that takes html and returns text, or None.
        """
        url_lower = url.lower()

        if 'medium.com' in url_lower or 'towardsdatascience.com' in url_lower:
            return self.extract_medium
        elif 'github.com' in url_lower:
            return self.extract_github
        elif 'stackoverflow.com' in url_lower or 'stackexchange.com' in url_lower:
            return self.extract_stackoverflow
        elif 'wikipedia.org' in url_lower:
            return self.extract_wikipedia
        elif 'ibm.com/docs' in url_lower or 'ibm.com/support' in url_lower:
            return self.extract_ibm_docs

        # No specific extractor
        return None


# ============================================================================
# Content Extraction Strategy
# ============================================================================

class ContentExtractor:
    """
    Handles content extraction with smart fallback strategy:
    1. Site-specific extractors (for known paywalled sites like Medium)
    2. Custom extraction (HTML->markdown) [if mode="custom"]
    3. Trafilatura fallback (if custom extraction fails or returns insufficient content)
    4. BeautifulSoup fallback (generic)
    """

    ExtractionMethod = Literal["trafilatura", "custom", "custom_with_trafilatura_fallback", "site_specific", "fallback", "none"]

    def __init__(self):
        self.site_extractors = SiteSpecificExtractors()

    async def extract(
            self,
            html: str,
            url: str,
            *,
            prefer_markdown: bool = True,
            fallback_to_text: bool = True,
            extraction_mode: Literal["trafilatura", "custom"] = "trafilatura",
    ) -> tuple[str, ExtractionMethod]:
        """
        Extract content using best available method with TRUE parallel extraction.

        Args:
            html: Raw HTML content
            url: Source URL (used for site detection)
            prefer_markdown: If True, request markdown output from extractors
            fallback_to_text: If True, use BeautifulSoup fallback
            extraction_mode: "trafilatura" or "custom" for generic site extraction
                - "custom": Run both custom and trafilatura in TRUE parallel, pick best

        Returns:
            (content, method) where method indicates which extractor succeeded:
            - "site_specific": Custom extractor (Medium, GitHub, etc.)
            - "trafilatura": Trafilatura extraction
            - "custom": Custom HTML->markdown extraction
            - "custom_with_trafilatura_fallback": Custom insufficient, used trafilatura
            - "fallback": BeautifulSoup fallback
            - "none": All methods failed
        """
        if not html:
            return "", "none"

        # STEP 1: Try site-specific first (handles special cases like Medium)
        site_extractor = self.site_extractors.get_extractor(url)
        if site_extractor:
            try:
                content = site_extractor(html)
                if content and len(content.strip()) >= 200:
                    logger.debug(f"✓ Site-specific extractor succeeded: {url}")
                    return text_utils.strip_surrogates(content), "site_specific"
            except Exception as e:
                logger.debug(f"Site-specific extraction failed for {url}: {e}")

        # STEP 2: Generic extraction based on mode
        if extraction_mode == "custom":
            # Run BOTH custom and trafilatura in TRUE PARALLEL using asyncio.gather
            async def run_custom():
                try:
                    # Run in thread pool since it's CPU-bound
                    loop = asyncio.get_event_loop()
                    return await loop.run_in_executor(None, self._extract_custom, html, url)
                except Exception as e:
                    logger.debug(f"Custom extraction failed for {url}: {e}")
                    return ""

            async def run_trafilatura():
                if not TRAFILATURA_AVAILABLE:
                    return ""
                try:
                    # Run in thread pool since it's CPU-bound
                    loop = asyncio.get_event_loop()
                    output_format = 'markdown' if prefer_markdown else 'txt'
                    return await loop.run_in_executor(
                        None,
                        self._extract_trafilatura,
                        html,
                        url,
                        output_format
                    )
                except Exception as e:
                    logger.debug(f"Trafilatura extraction failed for {url}: {e}")
                    return ""

            # Run BOTH in parallel and wait for both to complete
            custom_content, trafilatura_content = await asyncio.gather(
                run_custom(),
                run_trafilatura()
            )

            # Decision logic: prefer custom if it has sufficient content
            custom_len = len(custom_content.strip()) if custom_content else 0
            trafilatura_len = len(trafilatura_content.strip()) if trafilatura_content else 0

            if custom_len >= 200:
                logger.debug(f"✓ Custom extraction succeeded: {url} ({custom_len} chars)")
                return custom_content, "custom"
            elif trafilatura_len >= 200:
                logger.info(f"✓ Using trafilatura (custom insufficient: {custom_len} chars, trafilatura: {trafilatura_len} chars)")
                return trafilatura_content, "custom_with_trafilatura_fallback"
            elif custom_len > 0:
                # Custom got something, even if short
                logger.debug(f"Using short custom content ({custom_len} chars)")
                return custom_content, "custom"
            elif trafilatura_len > 0:
                logger.debug(f"Using short trafilatura content ({trafilatura_len} chars)")
                return trafilatura_content, "custom_with_trafilatura_fallback"

        elif extraction_mode == "trafilatura" and TRAFILATURA_AVAILABLE:
            # Try trafilatura directly (best for general articles)
            try:
                loop = asyncio.get_event_loop()
                output_format = 'markdown' if prefer_markdown else 'txt'
                content = await loop.run_in_executor(
                    None,
                    self._extract_trafilatura,
                    html,
                    url,
                    output_format
                )
                if content:
                    logger.debug(f"✓ Trafilatura extraction succeeded: {url}")
                    return content, "trafilatura"
            except Exception as e:
                logger.debug(f"Trafilatura extraction failed for {url}: {e}")

        # STEP 3: Fallback to BeautifulSoup
        if fallback_to_text:
            try:
                content = self._extract_fallback(html)
                if content:
                    logger.debug(f"✓ Fallback extraction succeeded: {url}")
                    return content, "fallback"
            except Exception as e:
                logger.warning(f"Fallback extraction failed for {url}: {e}")

        return "", "none"

    def _extract_trafilatura(
            self,
            html: str,
            url: str,
            output_format: Literal['txt', 'markdown'] = 'markdown'
    ) -> str:
        """Extract using trafilatura with images and tables."""
        if not TRAFILATURA_AVAILABLE:
            return ""

        # Create a custom config for better table/content preservation
        from trafilatura.settings import use_config
        custom_config = use_config()

        # Critical: Preserve tables and structured content
        custom_config.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
        custom_config.set("DEFAULT", "MIN_EXTRACTED_SIZE", "100")  # Lower threshold
        custom_config.set("DEFAULT", "MIN_OUTPUT_SIZE", "50")       # Lower threshold
        custom_config.set("DEFAULT", "MIN_EXTRACTED_COMM_SIZE", "10")
        custom_config.set("DEFAULT", "MIN_OUTPUT_COMM_SIZE", "10")

        # Try with favor_precision=False first (more inclusive)
        content = trafilatura.extract(
            html,
            url=url,
            output_format=output_format,
            config=custom_config,
            include_comments=False,
            include_tables=True,        # CRITICAL for tables
            include_images=True,
            include_links=True,
            deduplicate=True,
            with_metadata=False,
            favor_precision=False,      # MORE INCLUSIVE - captures more content
            favor_recall=True,          # PRIORITIZE RECALL - don't miss content
        )

        # If we got very little content, try with even more lenient settings
        if not content or len(content.strip()) < 200:
            logger.debug(f"Trafilatura first pass too short ({len(content or '')} chars), trying lenient mode")

            # Even more lenient
            lenient_config = use_config()
            lenient_config.set("DEFAULT", "MIN_EXTRACTED_SIZE", "50")
            lenient_config.set("DEFAULT", "MIN_OUTPUT_SIZE", "30")

            content = trafilatura.extract(
                html,
                url=url,
                output_format=output_format,
                config=lenient_config,
                include_comments=False,
                include_tables=True,
                include_images=True,
                include_links=True,
                deduplicate=False,      # Don't deduplicate - keep all content
                with_metadata=False,
                favor_precision=False,
                favor_recall=True,
            )

        if not content:
            return ""

        content = text_utils.strip_surrogates(content)

        # Basic quality check - but be lenient
        if len(content.strip()) < 50:
            return ""

        image_count = content.count('![') if output_format == 'markdown' else 0
        logger.debug(f"Trafilatura extracted {len(content)} chars with {image_count} images")

        return content

    def _extract_custom(self, html: str, url: str) -> str:
        """
        Custom HTML->markdown extraction (original Elena's method).
        Uses make_clean_content_html + html_fragment_to_markdown.

        Removes UI chrome and boilerplate while KEEPING useful metadata like:
        - Usernames (who said what)
        - Timestamps (when discussions happened)
        - Reaction counts (community sentiment)
        """
        try:
            title = html_title(html)

            # Pass ORIGINAL HTML to make_clean_content_html
            # Let the scoring system find content properly!
            clean_fragment = make_clean_content_html(
                post_url=url,
                raw_html=html,  # ← CRITICAL: Pass original HTML, not pre-cleaned!
                title=title or "",
            )
            markdown = html_fragment_to_markdown(clean_fragment)

            if markdown and len(markdown.strip()) >= 200:
                markdown = text_utils.strip_surrogates(markdown)

                # STAGE 3: Minimal post-processing - only remove clear UI noise
                lines = markdown.split('\n')
                cleaned_lines = []

                # Track title to avoid duplication
                title_seen = False

                for line in lines:
                    line_stripped = line.strip()
                    line_lower = line_stripped.lower()

                    # Skip duplicate title
                    if not title_seen and title and line_stripped == title:
                        title_seen = True
                        cleaned_lines.append(line)
                        continue
                    elif title_seen and title and line_stripped == title:
                        continue

                    # Only skip OBVIOUS UI noise, keep metadata
                    skip_patterns = [
                        # Navigation instructions
                        line_lower.startswith('skip to'),
                        line_lower.startswith('jump to'),
                        line_lower.startswith('go to'),
                        # Action words (isolated, not in sentences)
                        line_lower in ['reply', 'edit', 'delete', 'share', 'report', 'flag'],
                        line_lower in ['like', 'unlike', 'upvote', 'downvote'],
                        line_lower.startswith('sign up'),
                        line_lower.startswith('log in'),
                        line_lower.startswith('subscribe'),
                        # UI element labels
                        line_lower in ['menu', 'search', 'close', 'back', 'next', 'previous'],
                        line_lower.startswith('show more'),
                        line_lower.startswith('load more'),
                        line_lower.startswith('see all'),
                        # Empty lines
                        len(line_stripped) <= 1,
                        ]

                    if not any(skip_patterns):
                        cleaned_lines.append(line)

                markdown = '\n'.join(cleaned_lines)

                # Remove excessive blank lines (more than 2 consecutive)
                while '\n\n\n' in markdown:
                    markdown = markdown.replace('\n\n\n', '\n\n')

                markdown = markdown.strip()

                if len(markdown) >= 200:
                    logger.debug(f"Custom extraction: {len(markdown)} chars (cleaned, metadata preserved)")
                    return markdown

            # Fallback to simpler text extraction if markdown conversion failed
            soup = BeautifulSoup(html, 'lxml')
            for element in soup(['script', 'style', 'iframe', 'noscript']):
                element.decompose()

            main = soup.find('main') or soup.find('article')
            if not main or len(main.get_text(strip=True)) < 200:
                content_candidates = soup.find_all(
                    'div',
                    class_=lambda x: x and any(
                        keyword in str(x).lower()
                        for keyword in ['content', 'article', 'post', 'entry', 'body', 'main', 'text']
                    ),
                )
                if content_candidates:
                    main = max(content_candidates, key=lambda x: len(x.get_text(strip=True)))

            if main:
                text = main.get_text(separator='\n', strip=True)
            else:
                text = soup.get_text(separator='\n', strip=True)

            lines = [line.strip() for line in text.split('\n') if line.strip() and len(line.strip()) > 2]
            result = '\n'.join(lines)
            result = text_utils.strip_surrogates(result)

            if len(result) >= 100:
                logger.debug(f"Custom extraction (text fallback): {len(result)} chars")
                return result

            return ""

        except Exception as e:
            logger.warning(f"Custom extraction failed: {e}")
            return ""

    def _extract_fallback(self, html: str) -> str:
        """Fallback extraction using BeautifulSoup (text only, no images)."""
        soup = BeautifulSoup(html, 'lxml')

        # Remove noise
        for element in soup(['script', 'style', 'iframe', 'noscript', 'nav', 'footer', 'aside']):
            element.decompose()

        # Try to find main content
        main = (
                soup.find('main') or
                soup.find('article') or
                soup.find('div', class_=lambda x: x and any(
                    kw in str(x).lower()
                    for kw in ['content', 'article', 'post', 'entry']
                ))
        )

        if main and len(main.get_text(strip=True)) >= 200:
            text = main.get_text(separator='\n', strip=True)
        else:
            text = soup.get_text(separator='\n', strip=True)

        # Clean up text
        lines = [line.strip() for line in text.split('\n') if line.strip() and len(line.strip()) > 2]
        result = '\n'.join(lines)

        return text_utils.strip_surrogates(result)


# ============================================================================
# Web Content Fetcher
# ============================================================================

class WebContentFetcher:
    """
    Fetches web content with smart header selection and authentication support.
    Uses ContentExtractor for extraction strategy with automatic fallbacks.

    Usage:
        async with WebContentFetcher(prefer_markdown=True) as fetcher:
            results = await fetcher.fetch_multiple(['https://example.com'])

        # With custom extraction mode (includes automatic trafilatura fallback)
        async with WebContentFetcher(extraction_mode="custom") as fetcher:
            result = await fetcher.fetch_single('https://discourse.example.com/topic')
    """

    def __init__(
            self,
            timeout: int = 15,
            max_concurrent: int = 5,
            enable_archive: bool = False,
            prefer_markdown: bool = True,
            extraction_mode: Literal["trafilatura", "custom"] = "trafilatura",
    ):
        """
        Initialize fetcher.

        Args:
            timeout: Request timeout in seconds
            max_concurrent: Maximum concurrent requests
            enable_archive: Try Wayback Machine if fetch fails
            prefer_markdown: Request markdown output from extractors
            extraction_mode: "trafilatura" or "custom" for generic site extraction
                - "custom": Tries custom extraction first, automatically falls back to trafilatura
        """
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self.enable_archive = enable_archive
        self.prefer_markdown = prefer_markdown
        self.extraction_mode = extraction_mode
        self.session: Optional[aiohttp.ClientSession] = None
        self.extractor = ContentExtractor()

    async def __aenter__(self):
        """Create aiohttp session."""
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close aiohttp session."""
        if self.session:
            await self.session.close()

    async def fetch_multiple(
            self,
            urls: list[str],
            max_length: int = 15000
    ) -> list[Dict[str, Any]]:
        """
        Fetch content for multiple URLs with concurrency control.

        Args:
            urls: List of URLs to fetch
            max_length: Maximum content length per URL

        Returns:
            List of result dicts (see fetch_single for structure)
        """
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def fetch_with_semaphore(url: str):
            async with semaphore:
                return await self.fetch_single(url, max_length)

        tasks = [fetch_with_semaphore(url) for url in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def fetch_single(self, url: str, max_length: int = 15000) -> Dict[str, Any]:
        """
        Fetch and extract content from a single URL with smart header selection.

        Args:
            url: URL to fetch
            max_length: Maximum content length

        Returns:
            Dict with keys:
            - url: Source URL
            - content: Extracted content
            - status: "success", "paywall", "error", etc.
            - content_length: Length of extracted content
            - extraction_method: Method used ("trafilatura", "custom", etc.)
            - published_time_iso: ISO datetime string (if found)
            - modified_time_iso: ISO datetime string (if found)
            - date_method: How date was extracted
            - date_confidence: Confidence score for date (0.0-1.0)
            - error: Error message (if status != "success")
        """
        result: Dict[str, Any] = {
            "url": url,
            "content": "",
            "status": "failed",
            "content_length": 0,
            "extraction_method": "none",
            "published_time_iso": None,
            "modified_time_iso": None,
            "date_method": None,
            "date_confidence": 0.0,
        }

        try:
            # Check for special cases
            if 'arxiv.org/abs/' in url:
                logger.info(f"ArXiv PDF detected: {url}")
                result["status"] = "pdf_redirect"
                return result

            # Fetch HTML with smart header selection
            html, status, last_modified, content_type = await self._fetch_html(url)
            if content_type:
                result["mime"] = content_type

            if not html:
                result["status"] = status
                result["error"] = f"Fetch failed: {status}"

                # Try archive fallback for certain failures
                if self.enable_archive and status in [
                    "paywall", "error", "blocked_403", "session_expired", "insufficient_content"
                ]:
                    logger.info(f"Trying archive fallback for: {url}")
                    html, status, last_modified, content_type = await self._fetch_archive(url)
                    if content_type:
                        result["mime"] = content_type

                    if html:
                        result["status"] = "archive"
                    else:
                        logger.warning(f"Archive fetch also failed for {url}")
                        return result

                if not html:
                    return result

            # Check for paywall
            if self._is_paywalled(html, url):
                result["status"] = "paywall"
                result["error"] = "Paywall detected"
                return result

            # Extract content using strategy (site-specific -> custom/trafilatura -> fallback)
            content, extraction_method = await self.extractor.extract(
                html,
                url,
                prefer_markdown=self.prefer_markdown,
                fallback_to_text=True,
                extraction_mode=self.extraction_mode
            )

            if not content or len(content.strip()) < 100:
                result["status"] = "insufficient_content"
                result["error"] = "Content too short after extraction"
                return result

            # Truncate if needed
            if max_length > 0 and len(content) > max_length:
                content = self._truncate(content, max_length)

            # Extract publication dates
            date_meta = await self._extract_dates(html, url, last_modified)

            # Build result
            result.update({
                "content": content,
                "content_length": len(content),
                "status": "success",
                "extraction_method": extraction_method,
                **date_meta
            })

            # Safety: strip surrogates from all string fields
            for k, v in list(result.items()):
                if isinstance(v, str):
                    result[k] = text_utils.strip_surrogates(v)

            logger.info(
                f"✓ Fetched {url}: {result['content_length']} chars "
                f"via {extraction_method}, pub={result.get('published_time_iso')}"
            )

            return result

        except Exception as e:
            logger.exception(f"Error fetching {url}")
            result["error"] = str(e)
            return result

    async def _fetch_html(self, url: str) -> tuple[str, str, Optional[str], Optional[str]]:
        """
        Fetch HTML from URL with smart header selection.

        Uses bot headers for Discourse forums, browser headers for other sites.

        Returns:
            (html, status, last_modified_header, content_type)
            status: "success", "timeout", "blocked_403", "session_expired", "http_XXX", etc.
        """
        try:
            # Smart header selection based on URL
            headers = HeaderSelector.get_headers(url)

            cookies = SourceCookieManager.get_cookies_for_url(url)
            if cookies:
                logger.debug(f"Using authenticated cookies for {url}")

            async with self.session.get(
                    url,
                    headers=headers,
                    allow_redirects=True,
                    cookies=cookies
            ) as response:
                # Check for authentication issues
                if response.status == 401:
                    return "", "session_expired" if cookies else "paywall", None, None

                final_url = str(response.url)
                if cookies and any(ind in final_url.lower() for ind in ['/signin', '/login', '/auth']):
                    logger.warning(f"Redirected to login page for {url}")
                    return "", "session_expired", None, None

                if response.status == 403:
                    logger.warning(f"Got 403 for {url}")
                    return "", "blocked_403", None, None

                if response.status != 200:
                    return "", f"http_{response.status}", None, None

                content_type = (response.headers.get('Content-Type') or '').lower()
                mime = content_type.split(";")[0].strip() if content_type else None
                if 'text/html' not in content_type and 'text/plain' not in content_type:
                    return "", "non_html", None, mime

                html = await response.text()
                last_modified = response.headers.get("Last-Modified")

                return html, "success", last_modified, mime

        except asyncio.TimeoutError:
            return "", "timeout", None, None
        except Exception as e:
            logger.warning(f"Fetch failed for {url}: {e}")
            return "", "error", None, None

    async def _fetch_archive(self, url: str) -> tuple[str, str, Optional[str], Optional[str]]:
        """Try to fetch content via the Wayback Machine."""
        archive_url = f"https://web.archive.org/web/{url}"
        return await self._fetch_html(archive_url)

    async def _extract_dates(
            self,
            html: str,
            url: str,
            last_modified_header: Optional[str]
    ) -> Dict[str, Any]:
        """Extract publication dates from HTML using shared utility."""
        try:
            soup = BeautifulSoup(html or "", "lxml")
        except Exception:
            soup = None

        meta = extract_publication_dates_core(
            soup=soup,
            url=url,
            last_modified_header=last_modified_header,
        )

        return {
            "published_time_iso": meta.get("published_time_iso"),
            "modified_time_iso": meta.get("modified_time_iso"),
            "date_method": meta.get("date_method"),
            "date_confidence": meta.get("date_confidence", 0.0),
        }

    @staticmethod
    def _is_paywalled(html: str, url: str) -> bool:
        """Detect common paywall indicators."""
        html_lower = html.lower()

        indicators = [
            'paywall',
            'subscriber-only',
            'subscription required',
            'register to read',
            'sign in to continue',
            'member exclusive',
            'premium content',
        ]

        if 'medium.com' in url and 'metered-paywall' in html_lower:
            return True

        return any(ind in html_lower for ind in indicators)

    @staticmethod
    def _truncate(content: str, max_length: int) -> str:
        """Truncate content intelligently at sentence boundaries."""
        return truncate_text(content, max_length)


# ============================================================================
# Convenience Functions
# ============================================================================

async def fetch_urls_simple(
        urls: List[str],
        *,
        timeout: int = 15,
        max_concurrent: int = 5,
        prefer_markdown: bool = True,
        max_length: int = 15000,
        extraction_mode: Literal["trafilatura", "custom"] = "trafilatura",
) -> List[Dict[str, Any]]:
    """
    Simple convenience function to fetch multiple URLs.

    Args:
        urls: List of URLs to fetch
        timeout: Request timeout in seconds
        max_concurrent: Maximum concurrent requests
        prefer_markdown: Request markdown output
        max_length: Maximum content length per URL
        extraction_mode: "trafilatura" or "custom" for generic site extraction
            - "custom": Tries custom first, automatically falls back to trafilatura

    Returns:
        List of result dicts (see WebContentFetcher.fetch_single for structure)

    Example:
        results = await fetch_urls_simple(
            ['https://community.openai.com/t/topic/123'],
            extraction_mode="custom"  # Will use bot headers + custom extraction + trafilatura fallback
        )
        for r in results:
            if r['status'] == 'success':
                print(f"{r['url']}: {len(r['content'])} chars via {r['extraction_method']}")
    """
    async with WebContentFetcher(
            timeout=timeout,
            max_concurrent=max_concurrent,
            prefer_markdown=prefer_markdown,
            extraction_mode=extraction_mode
    ) as fetcher:
        return await fetcher.fetch_multiple(urls, max_length=max_length)
