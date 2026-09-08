from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from kdcube_ai_app.apps.chat.sdk.tools import web_tools


@pytest.mark.asyncio
async def test_web_search_forwards_snippet_only_selection(monkeypatch) -> None:
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(web_tools.search_backends, "web_search", search)

    result = await web_tools.tools.web_search(
        queries="current Python release",
        objective="Find the current release",
        n=3,
        fetch_content=False,
        use_llm=False,
    )

    assert result == {"ok": True, "error": None, "ret": []}
    assert search.await_args.kwargs["fetch_content"] is False
    assert search.await_args.kwargs["use_llm"] is False


@pytest.mark.asyncio
async def test_web_search_keeps_content_fetching_as_the_default(monkeypatch) -> None:
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(web_tools.search_backends, "web_search", search)

    await web_tools.tools.web_search(
        queries="current Python release",
        objective=None,
    )

    assert search.await_args.kwargs["fetch_content"] is True
    assert search.await_args.kwargs["use_llm"] is True


@pytest.mark.asyncio
async def test_web_search_forwards_explicit_llm_selection(monkeypatch) -> None:
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(web_tools.search_backends, "web_search", search)

    await web_tools.tools.web_search(
        queries="current Python release",
        objective="Find the current release",
        use_llm=True,
    )

    assert search.await_args.kwargs["use_llm"] is True


@pytest.mark.asyncio
async def test_web_search_keeps_objective_optional(monkeypatch) -> None:
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(web_tools.search_backends, "web_search", search)

    result = await web_tools.tools.web_search(
        queries="current Python release",
        use_llm=False,
    )

    assert result["ok"] is True
    assert search.await_args.kwargs["objective"] is None
