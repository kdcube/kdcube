# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/rendering/link_preview.py

from dataclasses import dataclass
from typing import Optional, Literal, List, Dict
from urllib.parse import urlparse
import base64, asyncio, sys, os
from kdcube_ai_app.infra.rendering.shared_browser import SharedBrowserService

PreviewMode = Literal["minimal", "standard", "full"]

# ============================================================================
# Module-level shared instance (lazy-initialized)
# ============================================================================

_SHARED_INSTANCE: Optional['AsyncLinkPreview'] = None
_SHARED_INSTANCE_LOCK = asyncio.Lock()

async def get_shared_link_preview() -> 'AsyncLinkPreview':
    """
    Get or create the shared module-level AsyncLinkPreview instance.

    Automatically uses the shared browser from shared_browser.get_shared_browser().
    Lazy-initialized on first call. Safe for concurrent access.

    Returns:
        Shared AsyncLinkPreview instance (already started)
    """
    global _SHARED_INSTANCE

    if os.getenv("LINK_PREVIEW_ENABLED", "1").lower() in ("0", "false", "no"):
        raise RuntimeError("Link preview is disabled (LINK_PREVIEW_ENABLED=0)")

    if _SHARED_INSTANCE is None:
        async with _SHARED_INSTANCE_LOCK:
            if _SHARED_INSTANCE is None:
                # Import here to avoid circular dependency
                from kdcube_ai_app.infra.rendering.shared_browser import get_shared_browser

                # Get the shared browser instance
                shared_browser = await get_shared_browser()

                _SHARED_INSTANCE = AsyncLinkPreview(
                    timeout=5000,
                    shared_browser=shared_browser,  # Use shared browser!
                )
                await _SHARED_INSTANCE.start()

    return _SHARED_INSTANCE

async def close_shared_link_preview():
    """
    Close the shared link preview instance (cleanup).

    Typically called during app shutdown. Optional in most cases
    as the browser process will be cleaned up on process exit.
    """
    global _SHARED_INSTANCE

    if _SHARED_INSTANCE is not None:
        async with _SHARED_INSTANCE_LOCK:
            if _SHARED_INSTANCE is not None:
                await _SHARED_INSTANCE.close()
                _SHARED_INSTANCE = None

@dataclass
class AsyncLinkPreview:
    """Fast link preview generator - optimized for chat applications."""

    headless: bool = True
    auto_install_browser: bool = False
    timeout: int = 5000

    # Optional shared browser (only used when needed)
    shared_browser: Optional[SharedBrowserService] = None

    # Runtime state
    _own_browser_service: Optional[SharedBrowserService] = None
    _browser = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def start(self):
        """Ensure browser is available if needed."""
        if self.shared_browser:
            self._browser = await self.shared_browser.get_browser()
        elif self._own_browser_service is None:

            self._own_browser_service = SharedBrowserService(
                headless=self.headless,
                auto_install_browser=self.auto_install_browser,
            )
            # Don't start yet - only when actually needed

    async def close(self):
        """Close only if we own the browser."""
        if self._own_browser_service is not None:
            await self._own_browser_service.close()
            self._own_browser_service = None
        self._browser = None

    async def _fetch_minimal(self, url: str) -> Optional[dict]:
        """
        Ultra-fast fetch using plain HTTP - no browser needed.
        Extracts only: favicon, title, domain
        Response time: ~200-800ms
        """
        try:
            import aiohttp
            from html.parser import HTMLParser

            class QuickParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.title = None
                    self.favicon = None
                    self.og_title = None
                    self.og_image = None
                    self.description = None
                    self.in_head = False
                    self.in_title = False

                def handle_starttag(self, tag, attrs):
                    attrs_dict = dict(attrs)

                    if tag == 'head':
                        self.in_head = True
                    elif tag == 'title':
                        self.in_title = True

                    if not self.in_head:
                        return

                    # Favicon
                    if tag == 'link':
                        rel = attrs_dict.get('rel', '').lower()
                        if 'icon' in rel and attrs_dict.get('href'):
                            self.favicon = attrs_dict['href']

                    # Meta tags
                    if tag == 'meta':
                        prop = attrs_dict.get('property', '').lower()
                        name = attrs_dict.get('name', '').lower()
                        content = attrs_dict.get('content', '')

                        if prop == 'og:title' and content:
                            self.og_title = content
                        elif prop == 'og:image' and content:
                            self.og_image = content
                        elif prop == 'og:description' and content:
                            self.description = content
                        elif name == 'description' and content and not self.description:
                            self.description = content

                def handle_data(self, data):
                    if self.in_title and data.strip():
                        self.title = data.strip()

                def handle_endtag(self, tag):
                    if tag == 'head':
                        self.in_head = False
                    elif tag == 'title':
                        self.in_title = False

            # Make HTTP request with minimal headers
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=self.timeout / 1000),
                        headers={
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                            'Accept': 'text/html',
                            'Accept-Language': 'de-DE,de;q=0.9',
                        }
                ) as response:
                    if response.status != 200:
                        return None

                    # Read only first 50KB (enough for <head>)
                    content = await response.content.read(50000)
                    html = content.decode('utf-8', errors='ignore')

                    # Parse HTML (only <head> section)
                    parser = QuickParser()
                    parser.feed(html)

                    # Build URL helpers
                    parsed = urlparse(url)
                    base_url = f"{parsed.scheme}://{parsed.netloc}"

                    # Normalize favicon URL
                    favicon = parser.favicon
                    if favicon:
                        if not favicon.startswith(('http://', 'https://', '//')):
                            if favicon.startswith('/'):
                                favicon = f"{base_url}{favicon}"
                            else:
                                favicon = f"{base_url}/{favicon}"
                    else:
                        favicon = f"{base_url}/favicon.ico"

                    # Normalize og:image if present
                    image = parser.og_image
                    if image and not image.startswith(('http://', 'https://', '//')):
                        if image.startswith('/'):
                            image = f"{base_url}{image}"
                        else:
                            image = f"{base_url}/{image}"

                    return {
                        "url": url,
                        "domain": parsed.netloc,
                        "title": (parser.og_title or parser.title or parsed.netloc)[:200],
                        "description": (parser.description or "")[:300],
                        "image": image,
                        "favicon": favicon,
                        "site_name": parsed.netloc,
                        "success": True,
                        "method": "http"
                    }

        except Exception as e:
            print(f"Minimal fetch failed: {e}")
            return None

    async def _fetch_with_browser(
            self,
            url: str,
            include_screenshot: bool = False
    ) -> Optional[dict]:
        """
        Browser-based fetch for sites that need JavaScript.
        Used as fallback or when screenshot is needed.
        """
        # Re-acquire on every fetch: the service replaces a browser whose
        # connection dropped or that it closed while idle.
        if self.shared_browser:
            self._browser = await self.shared_browser.get_browser()
        else:
            if self._own_browser_service is None:
                self._own_browser_service = SharedBrowserService(
                    headless=self.headless,
                    auto_install_browser=self.auto_install_browser,
                )
            self._browser = await self._own_browser_service.get_browser()

        context = None
        page = None

        try:
            context = await self._browser.new_context(
                viewport={"width": 1200, "height": 630},
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
                ),
                locale='de-DE',
            )

            page = await context.new_page()

            # Block heavy resources
            await page.route("**/*", lambda route: (
                route.abort() if route.request.resource_type in [
                    "font", "media", "video", "stylesheet"
                ] else route.continue_()
            ))

            # Quick navigation
            await page.goto(url, wait_until="commit", timeout=self.timeout)
            await page.wait_for_timeout(800)  # Brief JS wait

            # Extract metadata
            metadata = await page.evaluate("""() => {
                const getMeta = (selectors) => {
                    for (const selector of selectors) {
                        const el = document.querySelector(selector);
                        if (el) {
                            const content = el.getAttribute('content');
                            if (content && content.trim()) return content.trim();
                        }
                    }
                    return null;
                };

                return {
                    title: getMeta(['meta[property="og:title"]']) ||
                        document.title ||
                        document.querySelector('h1')?.textContent || '',
                    description: getMeta(['meta[property="og:description"]', 'meta[name="description"]']) || '',
                    image: getMeta(['meta[property="og:image"]']),
                    favicon: document.querySelector('link[rel*="icon"]')?.href ||
                        new URL('/favicon.ico', window.location.origin).href,
                };
            }""")

            # Screenshot only if requested
            screenshot_data = None
            if include_screenshot:
                try:
                    screenshot_bytes = await page.screenshot(
                        full_page=False,
                        type="jpeg",
                        quality=75,
                        timeout=3000,
                    )
                    screenshot_data = base64.b64encode(screenshot_bytes).decode('utf-8')
                except Exception:
                    pass

            parsed = urlparse(url)
            return {
                "url": url,
                "domain": parsed.netloc,
                "title": (metadata.get("title", "") or parsed.netloc)[:200],
                "description": metadata.get("description", "")[:300],
                "image": metadata.get("image"),
                "favicon": metadata.get("favicon"),
                "site_name": parsed.netloc,
                "screenshot": f"data:image/jpeg;base64,{screenshot_data}" if screenshot_data else None,
                "success": True,
                "method": "browser"
            }

        except Exception as e:
            print(f"Browser fetch failed: {e}")
            return None

        finally:
            if page:
                try:
                    await page.close()
                except:
                    pass
            if context:
                try:
                    await context.close()
                except:
                    pass

    async def generate_preview(
            self,
            url: str,
            mode: PreviewMode = "minimal",
            include_screenshot: bool = False,
            screenshot_width: Optional[int] = None,
            screenshot_height: Optional[int] = None,
    ) -> dict:
        """
        Generate link preview with configurable detail level.

        Args:
            url: URL to fetch preview for
            mode: Detail level - "minimal" | "standard" | "full"
                - minimal: favicon + title only, HTTP request, ~200-800ms
                - standard: + description + og:image, tries HTTP first, ~1-3s
                - full: uses browser (for JS-heavy sites), ~2-5s
            include_screenshot: Add screenshot (only works with full mode), adds ~1-2s

        Returns:
            dict with preview data and success status
        """

        # Determine strategy based on mode
        if mode == "full" or include_screenshot:
            # Full mode or screenshot requested - use browser
            result = await self._fetch_with_browser(url, include_screenshot)
            if result:
                # Filter response based on mode
                if mode == "minimal":
                    result = {
                        "url": result["url"],
                        "domain": result["domain"],
                        "title": result["title"],
                        "favicon": result["favicon"],
                        "site_name": result["site_name"],
                        "success": result["success"],
                        "method": result["method"],
                    }
                return result

            # Browser failed, try HTTP fallback
            result = await self._fetch_minimal(url)
            if result:
                if mode == "minimal":
                    result = {
                        "url": result["url"],
                        "domain": result["domain"],
                        "title": result["title"],
                        "favicon": result["favicon"],
                        "site_name": result["site_name"],
                        "success": result["success"],
                        "method": result["method"],
                    }
                return result

        else:
            # Minimal or standard mode - try HTTP first
            result = await self._fetch_minimal(url)
            if result:
                # Filter response based on mode
                if mode == "minimal":
                    result = {
                        "url": result["url"],
                        "domain": result["domain"],
                        "title": result["title"],
                        "favicon": result["favicon"],
                        "site_name": result["site_name"],
                        "success": result["success"],
                        "method": result["method"],
                    }
                return result

            # HTTP failed, try browser fallback
            result = await self._fetch_with_browser(url, False)
            if result:
                if mode == "minimal":
                    result = {
                        "url": result["url"],
                        "domain": result["domain"],
                        "title": result["title"],
                        "favicon": result["favicon"],
                        "site_name": result["site_name"],
                        "success": result["success"],
                        "method": result["method"],
                    }
                return result

        # All methods failed - return minimal fallback
        parsed = urlparse(url)
        return {
            "url": url,
            "domain": parsed.netloc,
            "title": parsed.netloc,
            "description": "" if mode != "minimal" else None,
            "image": None if mode != "minimal" else None,
            "favicon": f"{parsed.scheme}://{parsed.netloc}/favicon.ico",
            "site_name": parsed.netloc,
            "screenshot": None,
            "success": False,
            "error": "Failed to fetch preview"
        }

    async def generate_preview_batch(
            self,
            urls: List[str],
            mode: PreviewMode = "minimal",
    ) -> Dict[str, dict]:
        """
        Generate previews for multiple URLs in one batch (FAST).

        Uses a single aiohttp session with connection pooling for all requests.
        Much faster than calling generate_preview() in a loop.

        Args:
            urls: List of URLs to fetch previews for
            mode: Detail level - "minimal" (default) is fastest

        Returns:
            Dict mapping URL -> preview result
            {
                "https://example.com": {
                    "url": "https://example.com",
                    "domain": "example.com",
                    "title": "Example",
                    "favicon": "https://example.com/favicon.ico",
                    "success": True
                },
                ...
            }
        """
        if not urls:
            return {}

        # Deduplicate and normalize
        unique_urls = []
        seen = set()
        for url in urls:
            url = url.strip()
            if not url or url in seen:
                continue
            if not (url.startswith("http://") or url.startswith("https://")):
                continue
            seen.add(url)
            unique_urls.append(url)

        if not unique_urls:
            return {}

        # Use HTTP batch method for minimal/standard mode
        if mode in ["minimal", "standard"]:
            return await self._fetch_minimal_batch(unique_urls, mode)

        # Fall back to individual browser fetches for full mode
        tasks = [self.generate_preview(url, mode=mode) for url in unique_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for url, result in zip(unique_urls, results):
            if isinstance(result, Exception):
                output[url] = {
                    "url": url,
                    "domain": urlparse(url).netloc,
                    "success": False,
                    "error": str(result)
                }
            else:
                output[url] = result

        return output

    async def _fetch_minimal_batch(
            self,
            urls: List[str],
            mode: PreviewMode = "minimal"
    ) -> Dict[str, dict]:
        """
        Batch fetch using a single aiohttp session (FAST).

        Benefits:
        - Single connection pool
        - Connection reuse
        - ~5-10x faster than individual requests
        - Optimal for 10-100 URLs
        """
        import aiohttp
        from html.parser import HTMLParser

        class QuickParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.title = None
                self.favicon = None
                self.og_title = None
                self.og_image = None
                self.description = None
                self.in_head = False
                self.in_title = False

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)

                if tag == 'head':
                    self.in_head = True
                elif tag == 'title':
                    self.in_title = True

                if not self.in_head:
                    return

                if tag == 'link':
                    rel = attrs_dict.get('rel', '').lower()
                    if 'icon' in rel and attrs_dict.get('href'):
                        self.favicon = attrs_dict['href']

                if tag == 'meta':
                    prop = attrs_dict.get('property', '').lower()
                    name = attrs_dict.get('name', '').lower()
                    content = attrs_dict.get('content', '')

                    if prop == 'og:title' and content:
                        self.og_title = content
                    elif prop == 'og:image' and content:
                        self.og_image = content
                    elif prop == 'og:description' and content:
                        self.description = content
                    elif name == 'description' and content and not self.description:
                        self.description = content

            def handle_data(self, data):
                if self.in_title and data.strip():
                    self.title = data.strip()

            def handle_endtag(self, tag):
                if tag == 'head':
                    self.in_head = False
                elif tag == 'title':
                    self.in_title = False

        async def fetch_one(session: aiohttp.ClientSession, url: str) -> dict:
            """Fetch a single URL using the shared session."""
            try:
                async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=self.timeout / 1000),
                ) as response:
                    if response.status != 200:
                        return {
                            "url": url,
                            "domain": urlparse(url).netloc,
                            "success": False,
                            "error": f"HTTP {response.status}"
                        }

                    # Read only first 50KB
                    content = await response.content.read(50000)
                    html = content.decode('utf-8', errors='ignore')

                    # Parse
                    parser = QuickParser()
                    parser.feed(html)

                    # Build result
                    parsed = urlparse(url)
                    base_url = f"{parsed.scheme}://{parsed.netloc}"

                    # Normalize favicon
                    favicon = parser.favicon
                    if favicon:
                        if not favicon.startswith(('http://', 'https://', '//')):
                            if favicon.startswith('/'):
                                favicon = f"{base_url}{favicon}"
                            else:
                                favicon = f"{base_url}/{favicon}"
                    else:
                        favicon = f"{base_url}/favicon.ico"

                    # Normalize image (if mode != minimal)
                    image = None
                    if mode != "minimal" and parser.og_image:
                        image = parser.og_image
                        if not image.startswith(('http://', 'https://', '//')):
                            if image.startswith('/'):
                                image = f"{base_url}{image}"
                            else:
                                image = f"{base_url}/{image}"

                    return {
                        "url": url,
                        "domain": parsed.netloc,
                        "title": (parser.og_title or parser.title or parsed.netloc)[:200],
                        "description": (parser.description or "")[:300] if mode != "minimal" else "",
                        "image": image,
                        "favicon": favicon,
                        "site_name": parsed.netloc,
                        "success": True,
                        "method": "http_batch"
                    }

            except asyncio.TimeoutError:
                return {
                    "url": url,
                    "domain": urlparse(url).netloc,
                    "success": False,
                    "error": "Timeout"
                }
            except Exception as e:
                return {
                    "url": url,
                    "domain": urlparse(url).netloc,
                    "success": False,
                    "error": str(e)
                }

        # Create single session with connection pooling
        connector = aiohttp.TCPConnector(
            limit=20,  # Max 20 concurrent connections
            limit_per_host=5,  # Max 5 per domain
            ttl_dns_cache=300,  # Cache DNS for 5 min
        )

        async with aiohttp.ClientSession(
                connector=connector,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'text/html',
                    'Accept-Language': 'de-DE,de;q=0.9',
                }
        ) as session:
            # Fetch all URLs concurrently with the shared session
            tasks = [fetch_one(session, url) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Build output dict
        output = {}
        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                output[url] = {
                    "url": url,
                    "domain": urlparse(url).netloc,
                    "success": False,
                    "error": str(result)
                }
            else:
                output[url] = result

        return output
