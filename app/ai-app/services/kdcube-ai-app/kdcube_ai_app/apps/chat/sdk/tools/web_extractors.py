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

from typing import Dict, Any
import aiohttp
from bs4 import BeautifulSoup
import asyncio
import logging
import json

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re

from kdcube_ai_app.tools.scrap_utils import html_title, make_clean_content_html, html_fragment_to_markdown

logger = logging.getLogger(__name__)

# --- Date parsing helpers ---

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_ISO_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")

def _parse_human_date(s: str) -> Optional[str]:
    s = (s or "").strip()
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    try:
        dt = datetime(int(m.group(3)), month, int(m.group(2)))
        return dt.date().isoformat()
    except Exception:
        return None

def _parse_date_any(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    # ISO 8601
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    # RFC 2822/HTTP
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    # "September 19, 2025"
    iso = _parse_human_date(s)
    if iso:
        try:
            y, m, d = map(int, iso.split("-"))
            return datetime(y, m, d)
        except Exception:
            pass
    # "2025-9-19" or "2025/09/19"
    m = _ISO_DATE_RE.search(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass
    return None

def _extract_publication_dates_from_html(html: str, url: str, last_modified_header: Optional[str] = None) -> Dict[str, Optional[str]]:
    """
    Returns a dict:
      {
        'published_time_raw', 'published_time_iso',
        'modified_time_raw',  'modified_time_iso',
        'date_method', 'date_confidence'
      }
    """
    out = {
        "published_time_raw": None, "published_time_iso": None,
        "modified_time_raw":  None, "modified_time_iso":  None,
        "date_method": None, "date_confidence": 0.0
    }

    def set_pub(raw, method, conf):
        dt = _parse_date_any(raw)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            out["published_time_raw"] = raw
            out["published_time_iso"] = dt.isoformat()
            out["date_method"] = method
            out["date_confidence"] = conf
            return True
        return False

    def set_mod(raw):
        dt = _parse_date_any(raw)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            out["modified_time_raw"] = raw
            out["modified_time_iso"] = dt.isoformat()

    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        soup = None

    # 1) JSON-LD
    if soup:
        for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(node.string or "")
            except Exception:
                continue
            objs = data if isinstance(data, list) else [data]
            for obj in objs:
                if not isinstance(obj, dict):
                    continue
                types = obj.get("@type") or obj.get("type")
                if isinstance(types, str):
                    types = [types]
                types = [t.lower() for t in (types or []) if isinstance(t, str)]
                if any(t in ("article", "newsarticle", "blogposting") for t in types):
                    pub = obj.get("datePublished") or obj.get("dateCreated")
                    mod = obj.get("dateModified") or obj.get("dateUpdated")
                    if pub and set_pub(pub, "jsonld", 0.98):
                        if mod: set_mod(mod)
                        return out
                    if mod: set_mod(mod)

    # 2) Meta tags (OpenGraph/Article/DC)
    if soup:
        meta_keys = [
            ("property", "article:published_time"),
            ("name", "article:published_time"),
            ("property", "og:published_time"),
            ("name", "pubdate"),
            ("name", "publish-date"),
            ("name", "date"),
            ("name", "DC.date"), ("name", "DC.date.issued"), ("name", "DC.date.published"),
            ("name", "dcterms.created"), ("name", "dcterms.issued"),
            ("name", "citation_publication_date"),
        ]
        for attr, key in meta_keys:
            el = soup.find("meta", attrs={attr: key})
            if el and el.get("content"):
                if set_pub(el["content"].strip(), f"meta:{key}", 0.9):
                    break

        # Modified variants
        for attr, key in [
            ("property", "article:modified_time"),
            ("name", "article:modified_time"),
            ("property", "og:updated_time"),
            ("name", "dcterms.modified"),
            ("name", "DC.date.modified"),
        ]:
            el = soup.find("meta", attrs={attr: key})
            if el and el.get("content"):
                set_mod(el["content"].strip())

        # <time> / itemprop / common classes
        if not out["published_time_iso"]:
            sel = [
                'time[datetime]', 'time[content]',
                'meta[itemprop="datePublished"][content]',
                '[itemprop="datePublished"]',
                '.entry-date', '.post-date', '.published', 'time.published'
            ]
            for el in soup.select(",".join(sel)):
                v = el.get("datetime") or el.get("content") or el.get_text(" ", strip=True)
                if v and set_pub(v, "dom:time/byline", 0.75):
                    break

    # URL pattern (/YYYY/MM/DD/)
    if not out["published_time_iso"]:
        m = re.search(r"/(20\d{2})/([01]?\d)/([0-3]?\d)/", url or "")
        if m:
            try:
                y, mo, d = map(int, m.groups())
                dt = datetime(y, mo, d, tzinfo=timezone.utc)
                out.update({
                    "published_time_raw": f"{y:04d}-{mo:02d}-{d:02d}",
                    "published_time_iso": dt.isoformat(),
                    "date_method": "url_path",
                    "date_confidence": 0.6
                })
            except Exception:
                pass

    # HTTP Last-Modified as last resort (low confidence, not publish time)
    if not out["published_time_iso"] and last_modified_header:
        if set_pub(last_modified_header, "http:last-modified", 0.4):
            # keep confidence low
            pass

    return out

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
    # any explicit year in queries/objective equals current year or the next?
    year_matches = re.findall(r"\b(20\d{2})\b", text)
    try:
        now_year = datetime.now(timezone.utc).year
        if any(int(y) >= now_year - 1 for y in year_matches):
            return True
    except Exception:
        pass
    return False


class WebContentFetcher:
    """
    Minimal content fetcher that can be integrated into existing web_search function.
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
        self.session = None

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
                return await self.fetch_one(url, max_length)

        tasks = [fetch_with_semaphore(url) for url in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def fetch_one(self, url: str, max_length: int = 15000) -> Dict[str, Any]:
        result = {
            "url": url,
            "content": "",
            "status": "failed",
            "content_length": 0,
            # date fields
            "published_time_raw": None, "published_time_iso": None,
            "modified_time_raw": None,  "modified_time_iso": None,
            "date_method": None, "date_confidence": 0.0,
        }

        try:
            content, status, meta = await self._fetch_direct(url)
            logger.debug(f"Direct fetch for {url}: status={status}, content_len={len(content)}")

            if content:
                result["content"] = self._truncate(content, max_length)
                result["content_length"] = len(result["content"])
                result["status"] = status
                result.update(meta or {})
                logger.info(f"Successfully fetched {url}: {result['content_length']} chars")
                return result

            if self.enable_archive and status in ["paywall", "error", "insufficient_content", "blocked_403"]:
                logger.info(f"Trying archive fallback for: {url} (reason: {status})")
                content, meta = await self._fetch_archive(url)
                if content:
                    result["content"] = self._truncate(content, max_length)
                    result["content_length"] = len(result["content"])
                    result["status"] = "archive"
                    result.update(meta or {})
                    logger.info(f"Archive fetch succeeded for {url}: {result['content_length']} chars")
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

            async with self.session.get(url, headers=headers, allow_redirects=True) as response:
                if response.status == 403:
                    if retry:
                        logger.info(f"Got 403 for {url} with bot ID, retrying with standard headers")
                        await asyncio.sleep(1)
                        return await self._fetch_direct(url, retry=False)
                    else:
                        logger.warning(f"Got 403 for {url} even with standard headers - site blocks bots")
                        return "", "blocked_403", {}

                if response.status == 401:
                    return "", "paywall", {}

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
                            meta = _extract_publication_dates_from_html(
                                html, url, last_modified_header=response.headers.get("Last-Modified")
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
                # Generic extraction
                text = self._extract_text(html)
                # Choose the best available representation for `content`
                chosen = markdown if len(markdown) >= 200 else text
                if len((chosen or "").strip()) < 100:
                    logger.warning(f"Extracted content too short ({len(chosen)}) for {url}")
                    return "", "insufficient_content", {}
                meta = _extract_publication_dates_from_html(
                    html, url, last_modified_header=response.headers.get("Last-Modified")
                )
                return chosen, "success", meta

        except asyncio.TimeoutError:
            return "", "timeout", {}
        except Exception as e:
            logger.warning(f"Direct fetch failed for {url}: {e}")
            return "", "error", {}

    async def _fetch_archive(self, url: str) -> tuple[str, Dict[str, Any]]:
        archives = [f"https://web.archive.org/web/{url}"]
        for archive_url in archives:
            try:
                content, status, meta = await self._fetch_direct(archive_url)
                if content and status == "success":
                    logger.info(f"Archive fetch succeeded: {archive_url}")
                    # mark method to reflect archive origin if we didn't already have one
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

        # Medium specific
        if 'medium.com' in url and 'metered-paywall' in html_lower:
            return True

        return any(ind in html_lower for ind in indicators)

    @staticmethod
    def _extract_text(html: str) -> str:
        """Extract clean text from HTML."""
        try:
            soup = BeautifulSoup(html, 'lxml')

            # Remove unwanted elements (but keep header/main content)
            for element in soup(['script', 'style', 'iframe', 'noscript']):
                element.decompose()

            # Try multiple strategies to find main content
            main = None

            # Strategy 1: Look for main/article tags
            main = soup.find('main') or soup.find('article')

            # Strategy 2: Look for content-like divs
            if not main or len(main.get_text(strip=True)) < 200:
                content_candidates = soup.find_all('div', class_=lambda x: x and any(
                    keyword in str(x).lower()
                    for keyword in ['content', 'article', 'post', 'entry', 'body', 'main', 'text']
                ))
                # Pick the div with most text
                if content_candidates:
                    main = max(content_candidates, key=lambda x: len(x.get_text(strip=True)))

            # Strategy 3: Look for role="main"
            if not main or len(main.get_text(strip=True)) < 200:
                main = soup.find(attrs={'role': 'main'})

            # Strategy 4: Fall back to body, but remove nav/footer/aside
            if not main or len(main.get_text(strip=True)) < 200:
                main = soup.body
                if main:
                    # Remove navigation elements from body
                    for unwanted in main.find_all(['nav', 'footer', 'aside', 'header']):
                        unwanted.decompose()

            # Extract text
            if main:
                text = main.get_text(separator='\n', strip=True)
            else:
                text = soup.get_text(separator='\n', strip=True)

            # Clean up whitespace but don't be too aggressive
            lines = []
            for line in text.split('\n'):
                line = line.strip()
                if line and len(line) > 2:  # Keep lines with more than 2 chars
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

    All extractors should:
    - Take html (str) as input
    - Return str (extracted text) or empty string on failure
    - Handle exceptions gracefully
    """

    @staticmethod
    def extract_medium(html: str) -> str:
        """
        Extract from Medium articles using JSON-LD embedded content.
        Medium embeds full article text in JSON-LD for SEO, bypassing the paywall.
        """
        try:
            soup = BeautifulSoup(html, 'lxml')

            # Look for JSON-LD scripts
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(script.string)

                    # Medium articles have articleBody
                    if 'articleBody' in data:
                        logger.debug("Found Medium article in JSON-LD")
                        return data['articleBody']

                    # Sometimes it's nested
                    if isinstance(data, dict) and '@graph' in data:
                        for item in data['@graph']:
                            if isinstance(item, dict) and 'articleBody' in item:
                                logger.debug("Found Medium article in JSON-LD @graph")
                                return item['articleBody']

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    logger.debug(f"Error parsing JSON-LD: {e}")
                    continue

            logger.debug("No articleBody found in Medium JSON-LD")
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

            # Remove unwanted sections
            for unwanted in content.find_all(['table', 'sup', 'div'], class_=['infobox', 'navbox', 'reflist', 'reference']):
                unwanted.decompose()

            # Get paragraphs
            paragraphs = content.find_all('p')
            text = '\n\n'.join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

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