# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""The web tools connection (``platform/web_tools.py`` + the picker wiring).

web_search / web_fetch are the platform's PAID web backends bound as plain
LangChain tools: the LLM (filter/segment) side rides the model service passed
to the factory; the search side meters through the ambient turn accounting —
so the tool layer's job is (a) passing the service through, (b) shaping the
backend rows for a chat model (accounting/binary/widget fields dropped,
content bounded with truncation stated), and (c) binding through the same
declared-connection picker as every other tool. Offline: backends are faked.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _module(name: str):
    _n, m = load_dynamic_module_for_path(BUNDLE_ROOT / "platform" / f"{name}.py")
    return m


# ── result shaping (what the model actually sees) ────────────────────────────

def test_shape_rows_drops_accounting_binary_and_widget_fields() -> None:
    wt = _module("web_tools")
    out = json.loads(wt._shape_rows([{
        "url": "https://a.example", "title": "A", "text": "snippet",
        "content": "body", "provider": "brave", "base64": "AAAA",
        "favicon": "x", "favicon_status": "ok", "content_blocks": [{"type": "text"}],
        "mime": "text/html", "size_bytes": 123, "published_time_iso": None,
    }]))
    row = out["results"][0]
    assert out["ok"] is True
    assert row["content"] == "body" and row["mime"] == "text/html"
    for gone in ("provider", "base64", "favicon", "favicon_status", "content_blocks", "published_time_iso"):
        assert gone not in row


def test_shape_rows_bounds_content_and_states_truncation() -> None:
    wt = _module("web_tools")
    big = "x" * (wt._ROW_CONTENT_CAP + 5000)
    out = json.loads(wt._shape_rows([
        {"url": f"https://{i}.example", "title": str(i), "text": "s", "content": big}
        for i in range(6)
    ]))
    rows = out["results"]
    assert len(rows) == 6
    # Per-row cap holds; the call budget then exhausts and later rows keep no content.
    assert all(len(r.get("content", "")) <= wt._ROW_CONTENT_CAP + len(" ...[truncated]") for r in rows)
    spent = sum(len(r.get("content", "")) for r in rows)
    assert spent <= wt._CALL_BUDGET + len(" ...[truncated]") * len(rows)
    assert any("content" not in r for r in rows)
    assert all(r.get("content_truncated") for r in rows)  # every row was cut
    assert "web_fetch" in out["note"]


# ── the service pass-through (the "bound to model service" seam) ─────────────

def test_web_search_passes_the_model_service_to_the_backend(monkeypatch) -> None:
    wt = _module("web_tools")
    import kdcube_ai_app.apps.chat.sdk.tools.backends.web.search_backends as sb

    seen = {}

    async def fake_search(**kwargs):
        seen.update(kwargs)
        return [{"url": "https://a.example", "title": "A", "text": "s", "provider": "brave"}]

    monkeypatch.setattr(sb, "web_search", fake_search)
    svc = object()
    tool = wt.build_web_search_tool(svc)
    out = json.loads(asyncio.run(tool.ainvoke({"queries": "hello", "objective": "why", "n": 20})))

    assert seen["_SERVICE"] is svc
    assert seen["fetch_content"] is True and seen["refinement"] == "balanced"
    assert seen["n"] == 8  # clamped
    assert out["ok"] is True and "provider" not in out["results"][0]


def test_web_fetch_passes_the_model_service_and_maps_url_rows(monkeypatch) -> None:
    wt = _module("web_tools")
    import kdcube_ai_app.apps.chat.sdk.tools.backends.web.fetch_backends as fb

    seen = {}

    async def fake_fetch(**kwargs):
        seen.update(kwargs)
        return {"https://a.example": {"status": "success", "content": "body", "title": "A"}}

    monkeypatch.setattr(fb, "fetch_url_contents", fake_fetch)
    svc = object()
    tool = wt.build_web_fetch_tool(svc)
    out = json.loads(asyncio.run(tool.ainvoke({
        "urls": "https://a.example", "objective": "why", "refinement": "balanced",
    })))

    assert seen["_SERVICE"] is svc
    assert seen["use_archive_fallback"] is True and seen["refinement"] == "balanced"
    row = out["results"][0]
    assert row["url"] == "https://a.example" and row["content"] == "body" and row["title"] == "A"


def test_tool_errors_return_a_message_never_raise(monkeypatch) -> None:
    wt = _module("web_tools")
    import kdcube_ai_app.apps.chat.sdk.tools.backends.web.search_backends as sb

    async def boom(**kwargs):
        raise RuntimeError("backend down")

    monkeypatch.setattr(sb, "web_search", boom)
    tool = wt.build_web_search_tool(object())
    out = json.loads(asyncio.run(tool.ainvoke({"queries": "hello"})))
    assert out["ok"] is False and "backend down" in out["error"]["message"]


# ── the picker wiring (declared connection -> bound tool) ────────────────────

_WEB_CONN = {"name": "web", "kind": "python", "alias": "web", "allowed": ["web_search", "web_fetch"]}


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _bind(mod, connections, disabled):
    tools = mod.select_bound_tools(
        connections, disabled,
        plain_registry={"calc": _FakeTool("calc")},
        run_python_factory=lambda: _FakeTool("run_python"),
        extra_factories={
            "web_search": lambda: _FakeTool("web_search"),
            "web_fetch": lambda: _FakeTool("web_fetch"),
        },
    )
    return [t.name for t in tools]


def test_web_tools_bind_only_when_declared() -> None:
    tp = _module("tool_pick")
    calc_only = [{"name": "calc", "kind": "python", "alias": "calc", "allowed": ["calc"]}]
    assert _bind(tp, calc_only, {}) == ["calc"]
    assert _bind(tp, calc_only + [_WEB_CONN], {}) == ["calc", "web_search", "web_fetch"]


def test_web_tools_honor_the_user_deny_map() -> None:
    tp = _module("tool_pick")
    conns = [_WEB_CONN]
    assert _bind(tp, conns, {"web": True}) == []                       # whole-alias opt-out
    assert _bind(tp, conns, {"web": ["web_fetch"]}) == ["web_search"]  # per-tool opt-out
