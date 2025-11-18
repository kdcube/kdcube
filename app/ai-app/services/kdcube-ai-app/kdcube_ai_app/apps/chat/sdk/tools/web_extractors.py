# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/web_extractors.py


# 1. Install required dependencies
# pip install aiohttp beautifulsoup4 lxml
#
# # Optional but recommended:
# pip install playwright  # For JavaScript-heavy sites
# pip install redis  # For caching
# pip install aiolimiter  # For rate limiting
# pip install tenacity  # For retry logic

from typing import Dict, Any, Optional, List
import aiohttp
from bs4 import BeautifulSoup
import asyncio
import logging
import json, os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re

from kdcube_ai_app.tools.scrap_utils import (
    html_title,
    make_clean_content_html,
    html_fragment_to_markdown,
    extract_publication_dates_core,
)

logger = logging.getLogger(__name__)

# --- auxiliary freshness helpers (still used elsewhere if needed) ---

def _age_days_from_iso(dt_iso: Optional[str]) -> Optional[float]:
    if not dt_iso:
        return None
    try:
        dt = datetime.fromisoformat(dt_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - dt).total_seconds() / 86400.0)
    except Exception:
        return None

def _infer_time_sensitivity(objective: Optional[str], queries: List[str]) -> bool:
    text = " ".join([objective or ""] + queries).lower()
    keywords = [
        "latest", "today", "yesterday", "this week", "this month", "breaking",
        "recent", "now", "current", "update", "updated"
    ]
    if any(k in text for k in keywords):
        return True
    # any explicit year in queries/objective equals current year or near?
    year_matches = re.findall(r"\b(20\d{2})\b", text)
    try:
        now_year = datetime.now(timezone.utc).year
        if any(int(y) >= now_year - 1 for y in year_matches):
            return True
    except Exception:
        pass
    return False

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
            'towardsdatascience.com': 'MEDIUM',  # Uses same Medium cookies
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
                        logger.info(f"✓ Loaded cookies for {domain} from {env_var}")
                        logger.debug(f"  Cookie keys: {list(cookies.keys())}")
                    else:
                        logger.warning(f"⚠ {env_var} exists but has no cookies")

                except json.JSONDecodeError as e:
                    logger.error(f"❌ Failed to parse {env_var}: {e}")
                except Exception as e:
                    logger.error(f"❌ Error loading {env_var}: {e}")

    @classmethod
    def get_cookies_for_url(cls, url: str) -> Optional[Dict[str, str]]:
        """
        Get cookies for a specific URL if available.
        """
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
# Web Content Fetcher
# ============================================================================

class WebContentFetcher:
    """
    Minimal content fetcher that can be integrated into existing web_search function.
    Supports source-specific authentication via cookies.
    """

    def __init__(
            self,
            timeout: int = 15,
            max_concurrent: int = 5,
            enable_archive: bool = False
    ):
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self.enable_archive = enable_archive
        self.session: Optional[aiohttp.ClientSession] = None

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

        Returns:
            List of {url, content, status, content_length, error?}
        """
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def fetch_with_semaphore(url: str):
            async with semaphore:
                return await self.fetch_single(url, max_length)

        tasks = [fetch_with_semaphore(url) for url in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_sitemap_date(self, url: str) -> Optional[str]:
        """
        Try to find the page's lastmod date in sitemap.xml.

        Returns ISO datetime string or None (string from <lastmod>, *not* parsed).
        """
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

            sitemap_urls = [
                f"{base_url}/sitemap.xml",
                f"{base_url}/sitemap_index.xml",
                f"{base_url}/sitemap-index.xml",
            ]

            for sitemap_url in sitemap_urls:
                try:
                    async with self.session.get(sitemap_url, timeout=5) as response:
                        if response.status != 200:
                            continue

                        xml_text = await response.text()
                        soup = BeautifulSoup(xml_text, "lxml-xml")  # Use XML parser

                        for url_elem in soup.find_all("url"):
                            loc = url_elem.find("loc")
                            lastmod = url_elem.find("lastmod")
                            if loc and lastmod:
                                if loc.get_text(strip=True) == url:
                                    return lastmod.get_text(strip=True)

                except Exception as e:
                    logger.debug(f"Sitemap check failed for {sitemap_url}: {e}")
                    continue

            return None

        except Exception as e:
            logger.debug(f"Sitemap date extraction failed: {e}")
            return None

    async def _get_archive_dates(self, url: str) -> Dict[str, Optional[str]]:
        """
        Query Archive.org's Availability API for first/last snapshot dates.

        Returns dict with 'archive_snapshot_date' and 'archive_url', or {}.
        Useful for pages without explicit dates; timestamp is when Wayback
        captured the page (NOT publish date).
        """
        try:
            api_url = f"https://archive.org/wayback/available?url={url}"

            async with self.session.get(api_url, timeout=5) as response:
                if response.status != 200:
                    return {}

                data = await response.json()

                archived = data.get("archived_snapshots", {})
                closest = archived.get("closest", {})

                if closest.get("available"):
                    timestamp_str = closest.get("timestamp", "")
                    if len(timestamp_str) >= 8:
                        # "20240115120000" -> "2024-01-15T12:00:00Z"
                        year = timestamp_str[0:4]
                        month = timestamp_str[4:6]
                        day = timestamp_str[6:8]
                        hour = timestamp_str[8:10] if len(timestamp_str) >= 10 else "00"
                        minute = timestamp_str[10:12] if len(timestamp_str) >= 12 else "00"
                        second = timestamp_str[12:14] if len(timestamp_str) >= 14 else "00"

                        iso_date = f"{year}-{month}-{day}T{hour}:{minute}:{second}Z"

                        return {
                            "archive_snapshot_date": iso_date,
                            "archive_url": closest.get("url"),
                        }

                return {}

        except Exception as e:
            logger.debug(f"Archive.org date check failed for {url}: {e}")
            return {}

    async def _infer_dates_from_html(
        self,
        html: str,
        url: str,
        last_modified_header: Optional[str],
    ) -> Dict[str, Any]:
        """
        Use the shared extract_publication_dates_core + optional sitemap/Wayback.

        Strategy:
        - First run core with only HTML + HTTP Last-Modified (cheap).
        - If no published/modified found, optionally enrich with sitemap + archive
          and run core again with those signals.
        - Archive snapshot is always exposed as archive_snapshot_date/url.
        """
        try:
            soup = BeautifulSoup(html or "", "lxml")
        except Exception:
            soup = None

        # First pass: only HTML + HTTP Last-Modified
        meta = extract_publication_dates_core(
            soup=soup,
            url=url,
            last_modified_header=last_modified_header,
        )

        have_date = meta.get("published_time_iso") or meta.get("modified_time_iso")
        sitemap_date: Optional[str] = None
        archive_info: Dict[str, Optional[str]] = {}

        # Only pay network cost for sitemap/Wayback if we still have nothing.
        if not have_date:
            try:
                sitemap_date = await self._fetch_sitemap_date(url)
            except Exception as e:
                logger.debug(f"Sitemap fetch failed for {url}: {e}")

            if self.enable_archive:
                try:
                    archive_info = await self._get_archive_dates(url)
                except Exception as e:
                    logger.debug(f"Archive fetch failed for {url}: {e}")
                    archive_info = {}

            meta = extract_publication_dates_core(
                soup=soup,
                url=url,
                last_modified_header=last_modified_header,
                sitemap_lastmod=sitemap_date,
                archive_snapshot_date=archive_info.get("archive_snapshot_date"),
                archive_snapshot_url=archive_info.get("archive_url"),
            )
        else:
            # We already have a real date. Optionally just attach archive info
            # as a "seen alive" hint, without using it as publish/modified.
            if self.enable_archive:
                try:
                    archive_info = await self._get_archive_dates(url)
                    if archive_info.get("archive_snapshot_date"):
                        meta["archive_snapshot_date"] = archive_info["archive_snapshot_date"]
                        meta["archive_snapshot_url"] = archive_info.get("archive_url")
                except Exception as e:
                    logger.debug(f"Archive fetch (enrich) failed for {url}: {e}")

        return meta

    async def fetch_single(self, url: str, max_length: int = 15000) -> Dict[str, Any]:
        """
        Fetch and extract content from a single URL.

        Returns:
            Dict with keys: url, content, status, content_length,
            published_time_iso/published_time_raw,
            modified_time_iso/modified_time_raw,
            archive_snapshot_date/archive_snapshot_url,
            date_method/date_confidence, error?
        """
        result: Dict[str, Any] = {
            "url": url,
            "content": "",
            "status": "failed",
            "content_length": 0,
            "published_time_raw": None,
            "published_time_iso": None,
            "modified_time_raw": None,
            "modified_time_iso": None,
            "archive_snapshot_date": None,
            "archive_snapshot_url": None,
            "date_method": None,
            "date_confidence": 0.0,
        }

        try:
            content, status, meta = await self._fetch_direct(url)
            logger.debug(
                f"Direct fetch for {url}: status={status}, "
                f"len={len(content)}, pub={meta.get('published_time_iso') if meta else None}"
            )

            if content:
                result["content"] = self._truncate(content, max_length)
                result["content_length"] = len(result["content"])
                result["status"] = status
                result.update(meta or {})
                logger.info(
                    f"Fetched {url}: {result['content_length']} chars, "
                    f"pub={result.get('published_time_iso')}, "
                    f"mod={result.get('modified_time_iso')}, "
                    f"method={result.get('date_method')}"
                )
                return result

            # Archive fallback for failed fetches
            if self.enable_archive and status in [
                "paywall", "error", "insufficient_content",
                "blocked_403", "session_expired"
            ]:
                logger.info(f"Trying archive fallback for: {url} (reason: {status})")
                content, meta = await self._fetch_archive(url)
                if content:
                    result["content"] = self._truncate(content, max_length)
                    result["content_length"] = len(result["content"])
                    result["status"] = "archive"
                    result.update(meta or {})
                    logger.info(
                        f"Archive fetch succeeded for {url}: {result['content_length']} chars, "
                        f"pub={result.get('published_time_iso')}"
                    )
                    return result
                else:
                    logger.warning(f"Archive fetch also failed for {url}")

            result["status"] = status
            result["error"] = f"Fetch failed: {status}"
            logger.warning(f"Failed to fetch {url}: {status}")

        except Exception as e:
            logger.exception(f"Error fetching {url}")
            result["error"] = str(e)

        return result

    async def _fetch_direct(self, url: str, retry: bool = True) -> tuple[str, str, Dict[str, Any]]:
        try:
            if 'arxiv.org/abs/' in url:
                logger.info(f"ArXiv PDF detected: {url}")
                return "", "pdf_redirect", {}

            user_agent = (
                'Test-Research-Bot/1.0 (Web content indexing for research; support@example.com)'
                if retry else
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )

            headers = {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            }

            cookies = SourceCookieManager.get_cookies_for_url(url)
            if cookies:
                logger.debug(f"Using authenticated cookies for {url}")

            async with self.session.get(
                url,
                headers=headers,
                allow_redirects=True,
                cookies=cookies
            ) as response:
                # expired/invalid session?
                if response.status == 401:
                    if cookies:
                        logger.warning(f"Got 401 for {url} - session may be expired")
                        return "", "session_expired", {}
                    return "", "paywall", {}

                final_url = str(response.url)
                if cookies and any(ind in final_url.lower() for ind in ['/signin', '/login', '/auth']):
                    logger.warning(f"Redirected to login page for {url} - session expired")
                    return "", "session_expired", {}

                if response.status == 403:
                    if retry:
                        logger.info(f"Got 403 for {url} with bot ID, retrying with standard headers")
                        await asyncio.sleep(1)
                        return await self._fetch_direct(url, retry=False)
                    else:
                        logger.warning(f"Got 403 for {url} even with standard headers - site blocks bots")
                        return "", "blocked_403", {}

                if response.status != 200:
                    return "", f"http_{response.status}", {}

                content_type = (response.headers.get('Content-Type') or '').lower()
                if 'text/html' not in content_type and 'text/plain' not in content_type:
                    return "", "non_html", {}

                html = await response.text()

                extractor = SiteSpecificExtractors.get_extractor(url)
                if extractor:
                    try:
                        extracted_text = extractor(html)
                        if extracted_text:
                            meta = await self._infer_dates_from_html(
                                html,
                                url,
                                response.headers.get("Last-Modified"),
                            )
                            logger.info(f"Site-specific extractor succeeded for: {url}")
                            return extracted_text, "success", meta
                        logger.debug(f"Site-specific extractor returned empty for: {url}")
                    except Exception as e:
                        logger.warning(f"Site-specific extractor failed for {url}: {e}")

                # Check paywalls first
                if self._is_paywalled(html, url):
                    return "", "paywall", {}

                title = html_title(html)
                try:
                    clean_fragment = make_clean_content_html(
                        post_url=url,
                        raw_html=html,
                        title=title or "",
                    )
                    markdown = html_fragment_to_markdown(clean_fragment)
                except Exception:
                    markdown = ""
                text = self._extract_text(html)
                chosen = markdown if len(markdown) >= 200 else text
                if len((chosen or "").strip()) < 100:
                    logger.warning(f"Extracted content too short ({len(chosen)}) for {url}")
                    return "", "insufficient_content", {}

                meta = await self._infer_dates_from_html(
                    html,
                    url,
                    response.headers.get("Last-Modified"),
                )
                return chosen, "success", meta

        except asyncio.TimeoutError:
            return "", "timeout", {}
        except Exception as e:
            logger.warning(f"Direct fetch failed for {url}: {e}")
            return "", "error", {}

    async def _fetch_archive(self, url: str) -> tuple[str, Dict[str, Any]]:
        """
        Try to fetch content via the Wayback Machine.
        """
        archives = [f"https://web.archive.org/web/{url}"]
        for archive_url in archives:
            try:
                content, status, meta = await self._fetch_direct(archive_url)
                if content and status == "success":
                    logger.info(f"Archive fetch succeeded: {archive_url}")
                    if not meta.get("date_method"):
                        meta["date_method"] = "archive"
                    return content, meta
            except Exception as e:
                logger.debug(f"Archive fetch failed for {archive_url}: {e}")
                continue
        return "", {}

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
    def _extract_text(html: str) -> str:
        """Extract clean text from HTML."""
        try:
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

            if not main or len(main.get_text(strip=True)) < 200:
                main = soup.find(attrs={'role': 'main'})

            if not main or len(main.get_text(strip=True)) < 200:
                main = soup.body
                if main:
                    for unwanted in main.find_all(['nav', 'footer', 'aside', 'header']):
                        unwanted.decompose()

            if main:
                text = main.get_text(separator='\n', strip=True)
            else:
                text = soup.get_text(separator='\n', strip=True)

            lines = []
            for line in text.split('\n'):
                line = line.strip()
                if line and len(line) > 2:
                    lines.append(line)

            result = '\n'.join(lines)

            logger.debug(f"Extracted {len(result)} chars of text")
            return result

        except Exception as e:
            logger.warning(f"HTML parsing failed: {e}")
            return ""

    @staticmethod
    def _truncate(content: str, max_length: int) -> str:
        """Truncate content intelligently."""
        if max_length <= 0 or len(content) <= max_length:
            return content

        truncated = content[:max_length]

        # Find last sentence boundary
        for char in ['.', '\n', '!', '?']:
            pos = truncated.rfind(char)
            if pos > max_length * 0.8:
                return truncated[:pos + 1] + "\n\n[... truncated ...]"

        return truncated + "\n\n[... truncated ...]"


# ============================================================================
# Site-Specific Extractors
# ============================================================================

class SiteSpecificExtractors:
    """
    Specialized extractors for common sites with paywalls or special formatting.
    All extractors return plain/markdown text or "" on failure.
    """

    @staticmethod
    def extract_medium(html: str) -> str:
        """Enhanced Medium extraction with markdown formatting."""
        try:
            soup = BeautifulSoup(html, 'lxml')

            # Strategy 0: Check for paywall redirect
            if soup.find('input', attrs={'name': 'signInRedirect'}):
                logger.warning("Detected sign-in page - session expired")
                return ""

            # Strategy 1: JSON-LD with markdown conversion
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(script.string)
                    if 'articleBody' in data:
                        logger.debug("Found Medium article in JSON-LD articleBody (plain text)")
                        # Fall through to try other methods for better formatting
                    if isinstance(data, dict) and '@graph' in data:
                        for item in data['@graph']:
                            if isinstance(item, dict) and 'articleBody' in item:
                                logger.debug("Found Medium article in JSON-LD @graph (plain text)")
                                # Fall through to try other methods
                except:
                    continue

                def _best_srcset_value(srcset: str) -> str:
                    # choose the last candidate (usually largest width)
                    parts = [p.strip() for p in (srcset or "").split(",") if p.strip()]
                    if not parts:
                        return ""
                    return parts[-1].split()[0]  # "<url> <w>"

                def _best_src_from_picture(pic_tag):
                    # look at all <source> children; prefer the last srcset (largest)
                    best = ""
                    for src in pic_tag.find_all("source"):
                        # BeautifulSoup lowercases attribute names, so srcset works even if HTML has srcSet
                        s = src.get("srcset") or src.get("srcSet") or ""
                        cand = _best_srcset_value(s)
                        if cand:
                            best = cand
                    # sometimes an <img> is still present as a fallback
                    if not best:
                        img = pic_tag.find("img")
                        if img:
                            best = _best_src_from_img_tag(img)
                    return best

            def _best_src_from_img_tag(img):
                # prefer src; fall back to data-src or the largest candidate in srcset
                src = img.get('src') or img.get('data-src') or ''
                if not src:
                    srcset = img.get('srcset', '')
                    if srcset:
                        # pick the last (usually largest)
                        parts = [p.strip() for p in srcset.split(',') if p.strip()]
                        if parts:
                            src = parts[-1].split()[0]
                return src

            def _append_image(lines, src, alt=None, caption=None):
                if not src:
                    return
                alt = (alt or caption or "").strip()
                lines.append(f"![{alt}]({src})\n")
                # print caption only if it's non-empty and different from alt
                if caption:
                    cap = caption.strip()
                    if cap and cap != alt:
                        lines.append(f"*{cap}*\n")

            def _miro_url_from_id(image_id: str, max_width: int = 1400) -> str:
                # Medium’s stable CDN format; image_id is something like '0*weAr9MNo0tbNpNMu'
                # Using v2 + resize:fit yields a portable URL that doesn't need cookies.
                return f"https://miro.medium.com/v2/resize:fit:{max_width}/{image_id}"

            def _image_url_from_apollo(apollo_state: dict, image_id: str) -> str:
                # Try to resolve to originalUrl if present; else build from id
                try:
                    for k, v in apollo_state.items():
                        if isinstance(v, dict) and v.get('id') == image_id:
                            url = v.get('originalUrl') or v.get('url')
                            if url:
                                return url
                except Exception:
                    pass
                return _miro_url_from_id(image_id)

            def paragraphs_to_markdown(paragraphs: List[Dict], apollo_state: Optional[dict] = None) -> str:
                lines = []
                for para in paragraphs:
                    if not isinstance(para, dict):
                        continue
                    p_type = para.get('type', 'P')
                    text = para.get('text', '') or ''
                    markups = para.get('markups', []) or []

                    if p_type in ('IMG', 'Image', 'FIGURE'):
                        meta = para.get('metadata', {}) or {}
                        image_id = meta.get('id') or meta.get('imageId') or ''
                        src = _image_url_from_apollo(apollo_state or {}, image_id) if image_id else (para.get('href', '') or '')
                        caption = (para.get('caption') or meta.get('caption') or '').strip()
                        alt = (
                                meta.get('alt')
                                or meta.get('title')
                                or para.get('text')              # sometimes carries a label
                                or caption
                        )
                        _append_image(lines, src, alt, caption)
                        continue

                    # existing inline formatting for non-image paragraphs
                    if markups:
                        text = _apply_markups(text, markups)

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

            def _apply_markups(text: str, markups: List[Dict]) -> str:
                """Apply inline formatting markups to text."""
                if not markups:
                    return text

                # Sort markups by start position (reverse order for replacement)
                sorted_markups = sorted(markups, key=lambda m: m.get('start', 0), reverse=True)

                for markup in sorted_markups:
                    m_type = markup.get('type')
                    start = markup.get('start', 0)
                    end = markup.get('end', len(text))

                    if start >= end or end > len(text):
                        continue

                    segment = text[start:end]

                    if m_type == 'STRONG' or m_type == 'EM':
                        # Bold
                        replacement = f"**{segment}**"
                    elif m_type == 'EM':
                        # Italic
                        replacement = f"*{segment}*"
                    elif m_type == 'CODE':
                        # Inline code
                        replacement = f"`{segment}`"
                    elif m_type == 'A':
                        # Link
                        href = markup.get('href', '#')
                        replacement = f"[{segment}]({href})"
                    else:
                        # Unknown markup, keep as-is
                        replacement = segment

                    text = text[:start] + replacement + text[end:]

                return text

            # Strategy 2: Apollo State with markdown conversion
            for script in soup.find_all('script'):
                script_text = script.string or ""
                if 'window.__APOLLO_STATE__' in script_text:
                    try:
                        match = re.search(r'window\.__APOLLO_STATE__\s*=\s*(\{.+?\});', script_text, re.DOTALL)
                        if match:
                            apollo_state = json.loads(match.group(1))
                            for key, value in apollo_state.items():
                                if isinstance(value, dict) and 'content' in value:
                                    body_model = value.get('content', {}).get('bodyModel', {})
                                    if isinstance(body_model, dict) and 'paragraphs' in body_model:
                                        md = paragraphs_to_markdown(body_model['paragraphs'], apollo_state)
                                        if md and len(md) > 500:
                                            return md
                    except Exception as e:
                        logger.debug(f"Apollo state parsing failed: {e}")
                        continue

            # Strategy 3: Next.js data with markdown conversion
            for script in soup.find_all('script', id='__NEXT_DATA__'):
                try:
                    data = json.loads(script.string or '{}')
                    props = data.get('props', {}).get('pageProps', {})
                    for key in ['post', 'article', 'story', 'initialState']:
                        item = props.get(key)
                        if isinstance(item, dict):
                            body = item.get('content', {}).get('bodyModel', {})
                            if isinstance(body, dict) and 'paragraphs' in body:
                                md = paragraphs_to_markdown(body['paragraphs'], props.get('apolloState') or {})
                                if md and len(md) > 500:
                                    return md
                except Exception as e:
                    logger.debug(f"Next.js data parsing failed: {e}")
                    continue

            # Strategy 4: Direct HTML parsing with markdown conversion
            article = soup.find('article')
            if article:
                markdown_lines = []
                image_found = False  # track if we captured any image

                for element in article.find_all(['h1','h2','h3','h4','p','pre','blockquote','ul','ol','figure','img','picture']):
                    if element.name == 'picture':
                        src = _best_src_from_picture(element)
                        # look up a caption from a wrapping figure if present
                        cap_el = element.find_parent('figure')
                        caption = (cap_el.find('figcaption').get_text(strip=True) if cap_el and cap_el.find('figcaption') else None)
                        # try to find alt from a descendant img or attributes
                        img = element.find('img')
                        alt = (img.get('alt') if img else None) or element.get('aria-label') or element.get('title') or caption
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
                        alt = alt_attr or element.get('aria-label') or element.get('title') or caption
                        if src:
                            _append_image(markdown_lines, src, alt, caption)
                            image_found = True

                    elif element.name == 'img':
                        src = _best_src_from_img_tag(element)
                        alt = element.get('alt') or element.get('aria-label') or element.get('title')
                        if src:
                            _append_image(markdown_lines, src, alt)
                            image_found = True

                    # ... keep your existing heading/para/list handling exactly as-is ...
                    if element.name == 'h1':
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
                        # your inline formatting block ... unchanged
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

                # Fallback cover image (og:image) if we saw no inline article images
                if not image_found:
                    og = soup.find('meta', attrs={'property':'og:image'})
                    if og and og.get('content'):
                        cover = og['content'].strip()
                        if cover:
                            markdown = f"![cover]({cover})\n\n" + markdown

                if len(markdown) > 500:
                    logger.debug("Found Medium article in <article> tag (with images)")
                    return markdown

            logger.debug("No Medium article content found with any strategy")
            return ""

        except Exception as e:
            logger.warning(f"Medium extractor failed: {e}")
            return ""

    @staticmethod
    def extract_github(html: str) -> str:
        """
        Extract README or main content from GitHub.
        """
        try:
            soup = BeautifulSoup(html, 'lxml')

            # Find README content (newer GitHub layout)
            readme = soup.find('article', class_='markdown-body')
            if readme:
                logger.debug("Found GitHub README in article.markdown-body")
                return readme.get_text(separator='\n', strip=True)

            # Try alternative selectors
            readme = soup.find('div', {'id': 'readme'})
            if readme:
                logger.debug("Found GitHub README in div#readme")
                return readme.get_text(separator='\n', strip=True)

            # Look for any markdown-body class
            markdown = soup.find(class_='markdown-body')
            if markdown:
                logger.debug("Found GitHub markdown-body")
                return markdown.get_text(separator='\n', strip=True)

            logger.debug("No GitHub README found")
            return ""

        except Exception as e:
            logger.warning(f"GitHub extractor failed: {e}")
            return ""

    @staticmethod
    def extract_stackoverflow(html: str) -> str:
        """
        Extract question and answers from StackOverflow.
        """
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
                logger.debug("Extracted StackOverflow Q&A")
                return result

            return ""

        except Exception as e:
            logger.warning(f"StackOverflow extractor failed: {e}")
            return ""

    @staticmethod
    def extract_wikipedia(html: str) -> str:
        """
        Extract main content from Wikipedia, excluding navigation and references.
        """
        try:
            soup = BeautifulSoup(html, 'lxml')

            # Find main content
            content = soup.find('div', {'id': 'mw-content-text'})
            if not content:
                return ""

            for unwanted in content.find_all(
                ['table', 'sup', 'div'],
                class_=['infobox', 'navbox', 'reflist', 'reference']
            ):
                unwanted.decompose()

            paragraphs = content.find_all('p')
            text = '\n\n'.join(
                p.get_text(strip=True)
                for p in paragraphs
                if p.get_text(strip=True)
            )

            if text:
                logger.debug("Extracted Wikipedia content")
                return text

            return ""

        except Exception as e:
            logger.warning(f"Wikipedia extractor failed: {e}")
            return ""

    @staticmethod
    def extract_ibm_docs(html: str) -> str:
        """
        Extract content from IBM documentation sites.
        IBM docs often have content in specific div structures.
        """
        try:
            soup = BeautifulSoup(html, 'lxml')

            # IBM docs often use these patterns
            content = None

            # Try common IBM docs selectors
            content = (
                    soup.find('div', class_='bx--content') or
                    soup.find('div', class_='ibm-content') or
                    soup.find('div', {'id': 'content'}) or
                    soup.find('article') or
                    soup.find('main')
            )

            if content:
                # Remove navigation and sidebars
                for unwanted in content.find_all(['nav', 'aside', 'div'], class_=lambda x: x and any(
                        keyword in str(x).lower() for keyword in ['nav', 'sidebar', 'toc', 'breadcrumb']
                )):
                    unwanted.decompose()

                text = content.get_text(separator='\n', strip=True)
                if text:
                    logger.debug(f"Extracted {len(text)} chars from IBM docs")
                    return text

            return ""

        except Exception as e:
            logger.warning(f"IBM docs extractor failed: {e}")
            return ""

    @classmethod
    def get_extractor(cls, url: str):
        """
        Get appropriate extractor function for URL.
        Returns a callable that takes html and returns text, or None.
        """
        url_lower = url.lower()

        if 'medium.com' in url_lower or 'towardsdatascience.com' in url_lower:
            return cls.extract_medium
        elif 'github.com' in url_lower:
            return cls.extract_github
        elif 'stackoverflow.com' in url_lower or 'stackexchange.com' in url_lower:
            return cls.extract_stackoverflow
        elif 'wikipedia.org' in url_lower:
            return cls.extract_wikipedia
        elif 'ibm.com/docs' in url_lower or 'ibm.com/support' in url_lower:
            return cls.extract_ibm_docs

        # No specific extractor
        return None