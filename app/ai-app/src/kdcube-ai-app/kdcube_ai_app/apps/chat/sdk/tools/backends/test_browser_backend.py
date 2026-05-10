from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.tools.backends import browser_backend


class _FakeContext:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeLocator:
    def __init__(self) -> None:
        self.waits: list[dict] = []
        self.scrolled_into_view = False
        self.evaluations: list[tuple[str, object]] = []

    @property
    def first(self):
        return self

    async def wait_for(self, **kwargs) -> None:
        self.waits.append(kwargs)

    async def scroll_into_view_if_needed(self, **kwargs) -> None:
        self.scrolled_into_view = True
        self.waits.append(kwargs)

    async def evaluate(self, script, arg=None) -> None:
        self.evaluations.append((script, arg))


class _FakePage:
    def __init__(self) -> None:
        self.evaluations: list[tuple[str, object]] = []
        self.waits: list[int] = []
        self.locator_obj = _FakeLocator()

    async def evaluate(self, script, arg=None) -> None:
        self.evaluations.append((script, arg))

    async def wait_for_timeout(self, value: int) -> None:
        self.waits.append(value)

    def locator(self, selector: str):
        self.selector = selector
        return self.locator_obj


@pytest.mark.asyncio
async def test_close_current_browser_session_by_runtime_context() -> None:
    browser_backend._SESSIONS.clear()
    ctx = RuntimeCtx(
        tenant="t",
        project="p",
        user_id="u",
        conversation_id="c",
        turn_id="turn_1",
        bundle_id="bundle",
    )
    key, label = browser_backend.derive_session_identity(bound_context=ctx)
    fake_context = _FakeContext()
    browser_backend._SESSIONS[key] = browser_backend.BrowserSession(
        key=key,
        label=label,
        context=fake_context,
    )

    result = await browser_backend.close_browser_sessions_for_current_context(
        bound_context=ctx,
        reason="test",
    )

    assert result["closed_count"] == 1
    assert fake_context.closed is True
    assert key not in browser_backend._SESSIONS


@pytest.mark.asyncio
async def test_close_current_browser_session_scans_matching_turn_when_key_differs() -> None:
    browser_backend._SESSIONS.clear()
    ctx = RuntimeCtx(
        tenant="t",
        project="p",
        user_id="u",
        conversation_id="c",
        turn_id="turn_1",
        bundle_id="bundle",
    )
    fake_context = _FakeContext()
    browser_backend._SESSIONS["browser:other"] = browser_backend.BrowserSession(
        key="browser:other",
        label="tenant=t|project=p|user_id=u|conversation_id=c|turn_id=turn_1|request_id=req|bundle_id=bundle",
        context=fake_context,
    )

    result = await browser_backend.close_browser_sessions_for_current_context(
        bound_context=ctx,
        reason="test",
    )

    assert result["closed_count"] == 1
    assert fake_context.closed is True
    assert "browser:other" not in browser_backend._SESSIONS


@pytest.mark.asyncio
async def test_scroll_dispatches_page_delta_and_returns_state(monkeypatch) -> None:
    page = _FakePage()
    session = browser_backend.BrowserSession(
        key="browser:test",
        label="turn_id=turn_1",
        context=_FakeContext(),
    )

    async def _get_session(self, **kwargs):
        return session

    async def _get_page(self, *args, **kwargs):
        return page

    async def _state(self, **kwargs):
        return {"tab_id": kwargs["tab_id"], "actions": page.evaluations}

    monkeypatch.setattr(browser_backend.BrowserBackend, "_get_session", _get_session)
    monkeypatch.setattr(browser_backend.BrowserBackend, "_get_page", _get_page)
    monkeypatch.setattr(browser_backend.BrowserBackend, "_state", _state)

    result = await browser_backend.run_browser_action(
        "scroll",
        {"tab_id": "main", "delta_y": 321, "settle_ms": 0},
    )

    assert result["tab_id"] == "main"
    assert page.evaluations[0][1] == {"deltaX": 0, "deltaY": 321}


@pytest.mark.asyncio
async def test_scroll_selector_defaults_to_into_view(monkeypatch) -> None:
    page = _FakePage()
    session = browser_backend.BrowserSession(
        key="browser:test",
        label="turn_id=turn_1",
        context=_FakeContext(),
    )

    async def _get_session(self, **kwargs):
        return session

    async def _get_page(self, *args, **kwargs):
        return page

    async def _state(self, **kwargs):
        return {"selector": getattr(page, "selector", None)}

    monkeypatch.setattr(browser_backend.BrowserBackend, "_get_session", _get_session)
    monkeypatch.setattr(browser_backend.BrowserBackend, "_get_page", _get_page)
    monkeypatch.setattr(browser_backend.BrowserBackend, "_state", _state)

    result = await browser_backend.run_browser_action(
        "scroll",
        {"tab_id": "main", "selector": "#target", "settle_ms": 0},
    )

    assert result["selector"] == "#target"
    assert page.locator_obj.scrolled_into_view is True
