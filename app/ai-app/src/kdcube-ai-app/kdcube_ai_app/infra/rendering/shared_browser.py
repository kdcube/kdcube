# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/rendering/shared_browser.py

from dataclasses import dataclass
from typing import Optional
import asyncio
import os
import sys
from pathlib import Path

import logging

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright, Browser
except ImportError:
    async_playwright = None
    Browser = None

# ============================================================================
# Module-level shared instance (lazy-initialized)
# ============================================================================

_SHARED_BROWSER: Optional['SharedBrowserService'] = None
_SHARED_BROWSER_LOCK = asyncio.Lock()

async def get_shared_browser() -> 'SharedBrowserService':
    """
    Get or create the shared module-level SharedBrowserService instance.

    Lazy-initialized on first call. Safe for concurrent access.
    The instance persists for the lifetime of the module/process.

    Returns:
        Shared SharedBrowserService instance (already started)

    Example:
        browser_service = await get_shared_browser()
        browser = await browser_service.get_browser()
        # Use browser...
    """
    global _SHARED_BROWSER

    if _SHARED_BROWSER is None:
        async with _SHARED_BROWSER_LOCK:
            # Double-check after acquiring lock
            if _SHARED_BROWSER is None:
                _SHARED_BROWSER = SharedBrowserService(
                    headless=True,
                    auto_install_browser=True,  # Self-heal on first use
                )
                await _SHARED_BROWSER.start()

    return _SHARED_BROWSER

async def close_shared_browser():
    """
    Close the shared browser instance (cleanup).

    Typically called during app shutdown. Optional in most cases
    as the browser process will be cleaned up on process exit.

    Example:
        # In your app shutdown handler
        await close_shared_browser()
    """
    global _SHARED_BROWSER

    if _SHARED_BROWSER is not None:
        async with _SHARED_BROWSER_LOCK:
            if _SHARED_BROWSER is not None:
                await _SHARED_BROWSER.close()
                _SHARED_BROWSER = None

# ============================================================================
# SharedBrowserService class
# ============================================================================

@dataclass
class SharedBrowserService:
    """Shared Playwright browser instance for multiple rendering services."""

    headless: bool = True
    auto_install_browser: bool = False

    # Runtime state
    _playwright = None
    _browser = None
    _ref_count: int = 0  # Track how many services are using this

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def start(self):
        """Launch Playwright + Chromium once."""
        if not os.getenv("PLAYWRIGHT_BROWSERS_PATH") and Path("/opt/ms-playwright").exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/opt/ms-playwright"

        if async_playwright is None:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && "
                "python -m playwright install chromium"
            )

        if self._playwright is None:
            try:
                self._playwright = await async_playwright().start()
            except Exception as e:
                raise RuntimeError(f"Failed to start Playwright: {e}") from e

        if self._browser is None:
            launch_args = []
            if os.getenv("PLAYWRIGHT_DOCKER_ARGS", "1") != "0":
                launch_args = [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-zygote",
                ]
            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless,
                    args=launch_args,
                )
            except Exception as e:
                if self.auto_install_browser:
                    # Try to install chromium on the fly
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "playwright", "install", "chromium",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
                    self._browser = await self._playwright.chromium.launch(
                        headless=self.headless
                    )
                else:
                    raise RuntimeError(
                        "Chromium not available for Playwright. "
                        "Run: python -m playwright install chromium"
                    ) from e

    async def get_browser(self):
        """Get the browser instance, starting if needed."""
        await self.start()
        self._ref_count += 1
        return self._browser

    async def close(self):
        """Close the browser and Playwright driver."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as e:
                # Typical when driver is already gone at process shutdown
                logger.warning(
                    "SharedBrowserService: error closing browser (ignored during shutdown): %s",
                    e,
                )
            finally:
                self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning(
                    "SharedBrowserService: error stopping Playwright (ignored during shutdown): %s",
                    e,
                )
            finally:
                self._playwright = None

        self._ref_count = 0
