# SPDX-License-Identifier: MIT

"""SharedBrowserService with a remote Playwright server and idle disconnect.

- ``platform.rendering.browser.ws_endpoint`` set: start() connects to it
  instead of launching Chromium; a failed connect falls back to the launch.
- A browser whose connection dropped is replaced on the next get_browser().
- ``idle_disconnect_s`` closes a browser with no recent get_browser() call
  and no open context; the next call reconnects.
- Consumers that keep a browser handle re-acquire it per use, so they never
  render on a browser the service replaced.

Playwright is never driven: ``_playwright`` is a fake, so these tests run
without the package's browsers installed.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import kdcube_ai_app.apps.chat.sdk.config as sdk_config
import kdcube_ai_app.infra.rendering.shared_browser as shared_browser
from kdcube_ai_app.infra.rendering import browser_settings as settings_mod
from kdcube_ai_app.infra.rendering.browser_settings import BrowserSettings
from kdcube_ai_app.infra.rendering.link_preview import AsyncLinkPreview

WS = "ws://renderer:3000/playwright"


class _FakeBrowser:
    def __init__(self, connected: bool = True, contexts=()):
        self._connected = connected
        self.contexts = list(contexts)
        self.closed = False

    def is_connected(self) -> bool:
        return self._connected

    async def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, connect_result=None, connect_exc=None, launch_result=None):
        self._connect_result = connect_result
        self._connect_exc = connect_exc
        self._launch_result = launch_result if launch_result is not None else _FakeBrowser()
        self.connect_calls: list[str] = []
        self.launch_calls: list[dict] = []

    async def connect(self, ws_endpoint, **kwargs):
        self.connect_calls.append(ws_endpoint)
        if self._connect_exc is not None:
            raise self._connect_exc
        return self._connect_result

    async def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return self._launch_result


class _FakePlaywright:
    def __init__(self, chromium: _FakeChromium):
        self.chromium = chromium
        self.stopped = False

    async def stop(self):
        self.stopped = True


def _settings(monkeypatch, **values):
    monkeypatch.setattr(shared_browser, "browser_settings", lambda: BrowserSettings(**values))


def _service(monkeypatch, chromium: _FakeChromium) -> shared_browser.SharedBrowserService:
    # Satisfies the "Playwright installed" check; the preset _playwright
    # below means async_playwright() is never called.
    monkeypatch.setattr(shared_browser, "async_playwright", lambda: None)
    svc = shared_browser.SharedBrowserService()
    svc._playwright = _FakePlaywright(chromium)
    return svc


# --- descriptor parsing ---------------------------------------------------

def _descriptor(monkeypatch, values: dict):
    def _get_plain(key, default=None):
        prefix = "a:platform.rendering.browser."
        return values.get(key[len(prefix):], default) if key.startswith(prefix) else default

    monkeypatch.setattr(sdk_config, "get_plain", _get_plain)


def test_settings_default_to_local_launch_without_idle_disconnect(monkeypatch):
    _descriptor(monkeypatch, {})

    assert settings_mod.browser_settings() == BrowserSettings(ws_endpoint=None, idle_disconnect_s=None)


def test_settings_read_the_descriptor(monkeypatch):
    _descriptor(monkeypatch, {"ws_endpoint": f"  {WS} ", "idle_disconnect_s": "30"})

    assert settings_mod.browser_settings() == BrowserSettings(ws_endpoint=WS, idle_disconnect_s=30.0)


@pytest.mark.parametrize("bad", ["http://renderer:3000", "renderer:3000"])
def test_a_non_websocket_endpoint_is_ignored(monkeypatch, bad):
    _descriptor(monkeypatch, {"ws_endpoint": bad})

    assert settings_mod.browser_settings().ws_endpoint is None


@pytest.mark.parametrize("bad", [0, -5, "not-a-number", "", True])
def test_a_non_positive_or_malformed_idle_window_disables_it(monkeypatch, bad):
    _descriptor(monkeypatch, {"idle_disconnect_s": bad})

    assert settings_mod.browser_settings().idle_disconnect_s is None


# --- connect / fallback / reconnect --------------------------------------

def test_start_connects_to_the_remote_server(monkeypatch):
    _settings(monkeypatch, ws_endpoint=WS)
    remote = _FakeBrowser()
    chromium = _FakeChromium(connect_result=remote)
    svc = _service(monkeypatch, chromium)

    asyncio.run(svc.start())

    assert svc._browser is remote
    assert chromium.connect_calls == [WS]
    assert chromium.launch_calls == []


def test_a_failed_connect_falls_back_to_a_local_launch(monkeypatch):
    _settings(monkeypatch, ws_endpoint=WS)
    local = _FakeBrowser()
    chromium = _FakeChromium(connect_exc=RuntimeError("connection refused"), launch_result=local)
    svc = _service(monkeypatch, chromium)

    asyncio.run(svc.start())

    assert svc._browser is local
    assert chromium.connect_calls == [WS]
    assert len(chromium.launch_calls) == 1


def test_without_an_endpoint_start_launches_locally(monkeypatch):
    _settings(monkeypatch)
    local = _FakeBrowser()
    chromium = _FakeChromium(launch_result=local)
    svc = _service(monkeypatch, chromium)

    asyncio.run(svc.start())

    assert svc._browser is local
    assert chromium.connect_calls == []


def test_get_browser_replaces_a_disconnected_browser(monkeypatch):
    _settings(monkeypatch, ws_endpoint=WS)
    fresh = _FakeBrowser()
    chromium = _FakeChromium(connect_result=fresh)
    svc = _service(monkeypatch, chromium)
    svc._browser = _FakeBrowser(connected=False)

    browser = asyncio.run(svc.get_browser())

    assert browser is fresh
    assert chromium.connect_calls == [WS]


# --- idle disconnect -----------------------------------------------------

def test_the_idle_watchdog_closes_an_unused_browser(monkeypatch):
    svc = _service(monkeypatch, _FakeChromium())
    browser = _FakeBrowser()
    svc._browser = browser
    svc._last_activity_at = time.monotonic() - 10

    asyncio.run(svc._idle_watchdog(idle_s=0.01))

    assert browser.closed is True
    assert svc._browser is None
    assert svc._playwright is None


def test_the_idle_watchdog_keeps_a_browser_with_an_open_context(monkeypatch):
    svc = _service(monkeypatch, _FakeChromium())
    browser = _FakeBrowser(contexts=[object()])
    svc._browser = browser
    svc._last_activity_at = time.monotonic() - 10

    async def run():
        task = asyncio.ensure_future(svc._idle_watchdog(idle_s=0.01))
        await asyncio.sleep(0.05)
        assert not browser.closed
        browser.contexts.clear()  # the render finished
        await asyncio.wait_for(task, 1)

    asyncio.run(run())

    assert browser.closed is True


def test_get_browser_arms_the_watchdog_only_when_configured(monkeypatch):
    _settings(monkeypatch, idle_disconnect_s=60.0)
    svc = _service(monkeypatch, _FakeChromium())

    async def run():
        await svc.get_browser()
        task = svc._idle_task
        assert task is not None and not task.done()
        await svc.close()
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()

    asyncio.run(run())

    _settings(monkeypatch)
    svc = _service(monkeypatch, _FakeChromium())
    asyncio.run(svc.get_browser())
    assert svc._idle_task is None


# --- consumers re-acquire -------------------------------------------------

class _RecordingBrowser(_FakeBrowser):
    def __init__(self, used: list):
        super().__init__()
        self._used = used

    async def new_context(self, **_kw):
        self._used.append(self)
        raise RuntimeError("stop here")


class _RotatingService:
    """A shared service that hands out a new browser on every call."""

    def __init__(self, used: list):
        self.used = used
        self.handed: list[_RecordingBrowser] = []

    async def get_browser(self):
        browser = _RecordingBrowser(self.used)
        self.handed.append(browser)
        return browser


def test_link_preview_renders_on_the_current_browser(monkeypatch):
    used: list = []
    service = _RotatingService(used)
    preview = AsyncLinkPreview(shared_browser=service)

    async def run():
        await preview.start()  # caches the first browser
        await preview._fetch_with_browser("https://example.test")

    asyncio.run(run())

    assert len(service.handed) == 2
    assert used == [service.handed[-1]]
