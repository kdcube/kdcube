from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.tools.backends import browser_backend


class _FakeContext:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


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
