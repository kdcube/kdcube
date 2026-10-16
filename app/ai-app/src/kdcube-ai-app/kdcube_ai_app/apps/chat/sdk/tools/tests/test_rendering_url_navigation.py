# SPDX-License-Identifier: MIT
"""Tests for the additive URL-navigation mode of ``RenderingTools.write_png``.

Three optional kwargs (``url``, ``eval_js``, ``ready_js``) let ``write_png``
capture a live/headless URL instead of rendering inline ``content``. The tests
cover the two contracts that matter:

* ``url`` set   -> the page is navigated, ``eval_js`` runs before the readiness
  gate, ``ready_js`` is awaited, and the ``eval_js`` side-effect is visible in
  the PNG.
* ``url is None`` -> the legacy path is byte-identical: ``content`` is written to
  a temp ``_render_*.html`` and screenshotted; no navigation happens.

Event-loop contract
-------------------
``write_png`` reaches Chromium through the process-global shared browser
(``get_shared_browser()``), which caches a Playwright browser bound to the event
loop that first launched it. pytest-asyncio (``asyncio_mode = auto``) gives every
test a *fresh* function-scoped loop, so a second browser-backed test in the same
session would otherwise reuse a browser bound to the first test's now-closed
loop. The ``_reset_shared_browser`` autouse fixture closes+resets that singleton
after every test *in the same loop that used it*, so each browser-backed test
re-initialises Chromium on its own loop. The two browser tests are therefore
``async def`` (they run on the managed loop) rather than nesting ``asyncio.run``.

The browser-backed tests are marked ``playwright`` and, via the
``requires_browser`` fixture, skip cleanly where no Chromium is available (e.g.
slim CI images) -- unless ``KDCUBE_REQUIRE_BROWSER=1`` is set, in which case a
missing browser is a hard failure so a CI job can assert the playwright tests
actually ran. The Chromium probe lives in that fixture (not at collection time),
so ``-m "not playwright"`` never launches a browser. ``importorskip`` keeps
collection working where the package itself is not installed.
"""

import asyncio
import functools
import inspect
import os
import pathlib

import pytest

# Skip the whole module when the SDK package is not importable (e.g. a bare
# checkout without the app installed). In CI where kdcube_ai_app is installed
# this imports normally.
rendering_tools = pytest.importorskip(
    "kdcube_ai_app.apps.chat.sdk.tools.rendering_tools"
)
RenderingTools = rendering_tools.RenderingTools

from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir


def _require_browser_env() -> bool:
    """True iff KDCUBE_REQUIRE_BROWSER asks CI to hard-require a real browser."""
    return os.getenv("KDCUBE_REQUIRE_BROWSER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@functools.lru_cache(maxsize=1)
def _browser_available() -> bool:
    """True iff Playwright + a launchable Chromium are present locally.

    Only ever called from the ``requires_browser`` fixture (test setup time),
    never at import/collection time -- so collecting or running
    ``-m "not playwright"`` never launches a browser.
    """
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return False

    async def _probe() -> bool:
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                await browser.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


@pytest.fixture
def requires_browser():
    """Gate a browser-backed test on a launchable Chromium.

    Skips when no browser is available, so slim CI images stay green. Set
    ``KDCUBE_REQUIRE_BROWSER=1`` to turn that skip into a hard failure -- a CI
    job can then prove the playwright tests actually executed rather than
    silently skipping (green-with-nothing-verified).
    """
    if _browser_available():
        return
    if _require_browser_env():
        pytest.fail(
            "KDCUBE_REQUIRE_BROWSER is set but no launchable Chromium is "
            "available; the browser-backed write_png tests did not run."
        )
    pytest.skip("Playwright Chromium not available")


@pytest.fixture(autouse=True)
async def _reset_shared_browser():
    """Reset the process-global shared browser after every test.

    The shared browser is cached against the loop that first launched it;
    pytest-asyncio hands each test a fresh loop, so without this a second
    browser-backed test would reuse a browser bound to a closed loop. Tearing it
    down here -- in the same loop that used it -- lets the next test re-init
    Chromium cleanly. No-op when nothing touched the browser (e.g. the sync
    signature test).
    """
    yield
    try:
        from kdcube_ai_app.infra.rendering.shared_browser import (
            close_shared_browser,
        )
        await close_shared_browser()
    except Exception:
        pass


def _load_png_colors(png_path):
    """Return the set of RGB pixels in a PNG (Pillow), or None if unavailable."""
    try:
        from PIL import Image
    except Exception:
        return None
    with Image.open(png_path) as im:
        return set(im.convert("RGB").getdata())


def test_write_png_signature_is_default_off():
    """Additive contract: the new kwargs exist and default to the no-op path.

    Runs anywhere the package imports (no browser needed). Guards the promise
    that existing callers see zero behaviour change: ``url`` defaults to None.
    """
    params = inspect.signature(RenderingTools.write_png).parameters
    assert "url" in params and "eval_js" in params and "ready_js" in params
    assert params["url"].default is None
    assert params["eval_js"].default is None
    # ready_js has a sensible default predicate and is only consulted in URL mode.
    assert isinstance(params["ready_js"].default, str)
    assert params["ready_js"].default.strip() != ""


@pytest.mark.playwright
async def test_write_png_url_navigation_runs_eval_js_before_ready_gate(
    tmp_path, monkeypatch, requires_browser
):
    """url mode: navigate a local file, run eval_js, gate on ready_js, capture.

    The fixture never sets the readiness flag itself, and paints nothing. Only
    ``eval_js`` sets ``window.__RENDER_READY__`` (so a screenshot can only be
    reached if eval_js ran) *and* injects a uniquely-coloured element (so the
    side-effect is provably visible in the PNG).
    """
    runtime_outdir = tmp_path / "out"
    workdir = tmp_path / "work"
    runtime_outdir.mkdir()
    workdir.mkdir()
    OUTDIR_CV.set(str(runtime_outdir))
    WORKDIR_CV.set(str(workdir))

    # Spy on temp-HTML writes so the "url mode writes no _render_ file" claim is
    # non-vacuous: the legacy content path unlinks its temp file in a finally, so
    # a post-hoc glob would pass even if the temp file *had* been written. We
    # instead assert write_text was never invoked for a _render_ file at all.
    render_writes: list[str] = []
    real_write_text = pathlib.Path.write_text

    def _spy_write_text(self, *args, **kwargs):
        if "_render_" in self.name:
            render_writes.append(str(self))
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", _spy_write_text)

    # Fixture: no ready flag, no visible content of its own.
    fixture = tmp_path / "widget.html"
    fixture.write_text(
        "<!doctype html><html><head><meta charset='utf-8'></head>"
        "<body style='margin:0'></body></html>",
        encoding="utf-8",
    )

    marker_rgb = (12, 34, 200)  # distinctive blue, not the default white bg
    eval_js = (
        "() => {"
        "  const d = document.createElement('div');"
        "  d.id = 'injected';"
        "  d.style.width = '120px'; d.style.height = '120px';"
        f"  d.style.background = 'rgb({marker_rgb[0]},{marker_rgb[1]},{marker_rgb[2]})';"
        "  document.body.appendChild(d);"
        "  window.__RENDER_READY__ = true;"
        "}"
    )

    result = await RenderingTools().write_png(
        path="turn_test/outputs/widget.png",
        content="",  # ignored in url mode
        format="html",
        url=fixture.as_uri(),
        eval_js=eval_js,
        ready_js="window.__RENDER_READY__ === true",
        render_delay_ms=0,
        content_selector="#injected",
    )

    assert result.get("ok") is True, result

    png_path = resolve_output_dir() / "turn_test/outputs/widget.png"
    assert png_path.exists() and png_path.stat().st_size > 0

    # url mode must NOT write a temp render file (that is the content path).
    assert render_writes == [], render_writes

    colors = _load_png_colors(png_path)
    if colors is not None:
        # The eval_js-injected element must be present in the captured pixels.
        assert marker_rgb in colors, "eval_js side-effect not visible in PNG"


@pytest.mark.playwright
async def test_write_png_url_none_still_renders_from_content(
    tmp_path, requires_browser
):
    """url=None: the legacy content path is unchanged.

    A temp ``_render_*.html`` is written from ``content`` and screenshotted; no
    navigation seam is exercised. This runs *after* a browser-backed test in the
    same session, exercising the shared-browser reset (a second launch on a
    fresh loop must succeed).
    """
    runtime_outdir = tmp_path / "out"
    workdir = tmp_path / "work"
    runtime_outdir.mkdir()
    workdir.mkdir()
    OUTDIR_CV.set(str(runtime_outdir))
    WORKDIR_CV.set(str(workdir))

    marker_rgb = (200, 20, 40)  # distinctive red
    html = (
        "<div id='content' style='width:100px;height:100px;"
        f"background:rgb({marker_rgb[0]},{marker_rgb[1]},{marker_rgb[2]})'></div>"
    )

    result = await RenderingTools().write_png(
        path="turn_test/outputs/inline.png",
        content=html,
        format="html",
        # url omitted -> defaults to None -> legacy path
        render_delay_ms=0,
        content_selector="#content",
    )

    assert result.get("ok") is True, result

    png_path = resolve_output_dir() / "turn_test/outputs/inline.png"
    assert png_path.exists() and png_path.stat().st_size > 0

    colors = _load_png_colors(png_path)
    if colors is not None:
        assert marker_rgb in colors, "inline content not visible in PNG"
