# SPDX-License-Identifier: MIT

"""Tests for the KDCUBE_DISABLE_DOC_RENDERING hard-gate in SharedBrowserService.

The gate lets slim / render-less images (built with --build-arg
INSTALL_CHROMIUM=0) fail loudly instead of silently auto-downloading a browser
at runtime. Default (env unset) leaves rendering ENABLED so upstream behavior is
unchanged.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from kdcube_ai_app.infra.rendering import shared_browser
from kdcube_ai_app.infra.rendering.shared_browser import (
    SharedBrowserService,
    _doc_rendering_disabled,
)


# ---------------------------------------------------------------------------
# _doc_rendering_disabled() env parsing
# ---------------------------------------------------------------------------

def test_gate_default_off_when_unset(monkeypatch):
    monkeypatch.delenv("KDCUBE_DISABLE_DOC_RENDERING", raising=False)
    assert _doc_rendering_disabled() is False


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "Yes", "on", " On "])
def test_gate_truthy_values(monkeypatch, truthy):
    monkeypatch.setenv("KDCUBE_DISABLE_DOC_RENDERING", truthy)
    assert _doc_rendering_disabled() is True


@pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off", "anything"])
def test_gate_falsy_values(monkeypatch, falsy):
    monkeypatch.setenv("KDCUBE_DISABLE_DOC_RENDERING", falsy)
    assert _doc_rendering_disabled() is False


# ---------------------------------------------------------------------------
# start() behavior
# ---------------------------------------------------------------------------

async def test_start_hard_gates_and_never_launches_browser(monkeypatch):
    """When gated, start() raises and Playwright is never touched."""
    monkeypatch.setenv("KDCUBE_DISABLE_DOC_RENDERING", "1")

    # Spy: if start() reached the launch path, this would be called.
    spy = MagicMock(name="async_playwright")
    monkeypatch.setattr(shared_browser, "async_playwright", spy)

    svc = SharedBrowserService()
    with pytest.raises(RuntimeError, match="KDCUBE_DISABLE_DOC_RENDERING"):
        await svc.start()

    spy.assert_not_called()
    assert svc._playwright is None
    assert svc._browser is None


async def test_start_launches_when_gate_unset(monkeypatch):
    """When the gate is unset, start() proceeds into the normal launch path
    exactly as before — the gate is transparent (default = rendering enabled)."""
    monkeypatch.delenv("KDCUBE_DISABLE_DOC_RENDERING", raising=False)
    # Keep the launch args deterministic and avoid touching /opt.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/tmp/does-not-matter")

    fake_browser = MagicMock(name="browser")
    fake_pw = MagicMock(name="playwright")
    fake_pw.chromium.launch = AsyncMock(return_value=fake_browser)

    fake_pw_cm = MagicMock(name="async_playwright()")
    fake_pw_cm.start = AsyncMock(return_value=fake_pw)
    monkeypatch.setattr(
        shared_browser, "async_playwright", MagicMock(return_value=fake_pw_cm)
    )

    svc = SharedBrowserService()
    await svc.start()  # must NOT raise the gate error

    fake_pw.chromium.launch.assert_awaited_once()
    assert svc._browser is fake_browser
