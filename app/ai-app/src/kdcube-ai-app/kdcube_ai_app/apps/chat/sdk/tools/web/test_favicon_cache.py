import asyncio
import sys
import types

import pytest

from kdcube_ai_app.apps.chat.sdk.tools.web.favicon_cache import enrich_sources_pool_with_favicons


class _Log:
    def debug(self, *_args, **_kwargs):
        pass

    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def exception(self, *_args, **_kwargs):
        pass


@pytest.mark.asyncio
async def test_enrich_sources_pool_with_favicons_can_be_disabled(monkeypatch):
    monkeypatch.setenv("WEB_FAVICON_ENRICH_ENABLED", "0")

    rows = [{"url": "https://example.com/article", "title": "Example"}]
    count = await enrich_sources_pool_with_favicons(rows, log=_Log())

    assert count == 0
    assert "favicon" not in rows[0]


@pytest.mark.asyncio
async def test_enrich_sources_pool_with_favicons_does_not_start_shared_browser(monkeypatch):
    monkeypatch.setenv("WEB_FAVICON_ENRICH_ENABLED", "1")

    class _MinimalPreview:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def _fetch_minimal(self, url):
            assert url == "https://example.com/article"
            return {
                "success": True,
                "favicon": "https://example.com/favicon.ico",
                "title": "Example Title",
            }

    fake_module = types.ModuleType("kdcube_ai_app.infra.rendering.link_preview")

    async def _get_shared_link_preview():
        raise AssertionError("favicon enrichment must not initialize shared browser")

    fake_module.AsyncLinkPreview = _MinimalPreview
    fake_module.get_shared_link_preview = _get_shared_link_preview
    monkeypatch.setitem(sys.modules, "kdcube_ai_app.infra.rendering.link_preview", fake_module)
    monkeypatch.delenv("WEB_FAVICON_ENRICH_TIMEOUT_S", raising=False)

    rows = [{"url": "https://example.com/article", "title": ""}]
    count = await enrich_sources_pool_with_favicons(rows, log=_Log())

    assert count == 1
    assert rows[0]["favicon"] == "https://example.com/favicon.ico"
    assert rows[0]["favicon_status"] == "success"
    assert rows[0]["title"] == "Example Title"


@pytest.mark.asyncio
async def test_enrich_sources_pool_with_favicons_keeps_partial_success_on_timeout(monkeypatch):
    monkeypatch.setenv("WEB_FAVICON_ENRICH_ENABLED", "1")

    class _PartialPreview:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def _fetch_minimal(self, url):
            if url.endswith("/slow"):
                await asyncio.sleep(10)
            return {
                "success": True,
                "favicon": f"{url}/favicon.ico",
                "title": "Fetched",
            }

    fake_module = types.ModuleType("kdcube_ai_app.infra.rendering.link_preview")

    async def _get_shared_link_preview():
        raise AssertionError("favicon enrichment must not initialize shared browser")

    fake_module.AsyncLinkPreview = _PartialPreview
    fake_module.get_shared_link_preview = _get_shared_link_preview
    monkeypatch.setitem(sys.modules, "kdcube_ai_app.infra.rendering.link_preview", fake_module)

    rows = [
        {"url": "https://example.com/fast", "title": "Fast"},
        {"url": "https://example.com/slow", "title": "Slow"},
    ]
    count = await enrich_sources_pool_with_favicons(rows, log=_Log(), timeout_seconds=0.01)

    assert count == 1
    assert rows[0]["favicon"] == "https://example.com/fast/favicon.ico"
    assert rows[0]["favicon_status"] == "success"
    assert "favicon" not in rows[1]
    assert "favicon_status" not in rows[1]
