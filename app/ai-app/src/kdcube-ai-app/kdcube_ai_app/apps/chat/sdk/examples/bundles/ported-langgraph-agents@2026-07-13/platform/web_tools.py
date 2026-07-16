# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── platform/web_tools.py ── model-callable `web_search` / `web_fetch` tools ──
#
# Plain LangChain `@tool` wrappers over KDCube's web backends
# (`sdk/tools/backends/web/search_backends.py`, `fetch_backends.py`). The
# backends run PAID services, and BOTH accountable providers bill through the
# platform seams this bundle already stands on:
#
#   - `web_search` (the search provider, e.g. Brave): metered by the
#     `search_many()` accounting decorators INSIDE the backend, against the
#     AMBIENT accounting context the platform binds around every turn — the
#     same context this agent's chat model calls bill under.
#   - `llm` (snippet reconciliation + content filtering/segmentation of the
#     retrieved results): runs on the MODEL SERVICE passed to the factory —
#     the entrypoint's accounted `models_service` — so those calls bill like
#     any other model call of this app.
#
# Nothing extra to wire: pass the entrypoint's model service in, run inside a
# platform turn, and both meters tick.
#
# The backends also stream their own progress/results widgets over the ambient
# communicator (search results panel, fetch progress) — those render in the
# chat client exactly as they do for the built-in agent.
#
# The tool RESULTS are shaped for a chat model's context: accounting-only
# fields are dropped, binary payloads are never inlined (mime + size stay),
# and page content is truncated under per-row and per-call character budgets
# with the truncation stated in-band.

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

LOGGER = logging.getLogger("kdcube.ported_langgraph_agents.web_tools")

# Character budgets for what a single tool result folds into the model's
# context. Full pages can be huge; the model asks again (narrower, or via
# `web_fetch` with an objective) when a truncated row matters.
_ROW_CONTENT_CAP = 12_000
_CALL_BUDGET = 48_000
_SNIPPET_CAP = 700

# Fields that never belong in the model-visible rows: `provider` is for
# accounting only; `base64` is a binary payload (mime/size remain so the model
# knows what the URL serves); favicons feed the results widget, not the model.
_DROP_FIELDS = ("provider", "base64", "favicon", "favicon_status", "content_blocks")


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    text = text or ""
    if len(text) <= cap:
        return text, False
    return text[:cap] + " ...[truncated]", True


def _shape_rows(rows: List[Dict[str, Any]]) -> str:
    """Fold backend result rows into ONE JSON string under the call budget:
    every row keeps url/title/snippet + metadata; `content` is kept per-row up
    to the row cap while the call budget lasts, then later rows keep only
    their snippet (stated per row, so the model knows to fetch selectively)."""
    spent = 0
    shaped: List[Dict[str, Any]] = []
    truncated_any = False
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = {k: v for k, v in row.items() if v is not None and k not in _DROP_FIELDS}
        snippet, _ = _truncate(str(item.get("text") or ""), _SNIPPET_CAP)
        if snippet:
            item["text"] = snippet
        content = str(item.pop("content", "") or "")
        if content:
            room = min(_ROW_CONTENT_CAP, max(0, _CALL_BUDGET - spent))
            kept, cut = _truncate(content, room) if room > 0 else ("", True)
            if kept:
                item["content"] = kept
                spent += len(kept)
            if cut:
                item["content_truncated"] = True
                truncated_any = True
        shaped.append(item)
    payload: Dict[str, Any] = {"ok": True, "results": shaped}
    if truncated_any:
        payload["note"] = (
            "Some page content was truncated to fit this result. If a truncated "
            "page matters, call web_fetch on that URL with a concrete objective "
            "and refinement to get the relevant sections."
        )
    return json.dumps(payload, ensure_ascii=False)


def _error_payload(where: str, exc: Exception) -> str:
    msg = str(exc).strip() or f"{where} failed"
    return json.dumps({"ok": False, "error": {"where": where, "message": msg}}, ensure_ascii=False)


def _kv_cache() -> Any:
    """The backends' optional cross-call cache (page/favicon caching).
    Best-effort — the tools work without it."""
    try:
        from kdcube_ai_app.infra.service_hub.cache import create_kv_cache
        return create_kv_cache()
    except Exception:
        return None


def build_web_search_tool(service: Any) -> Any:
    """The `web_search` LangChain tool, bound to this app's accounted model
    service (`service` powers the LLM reconciliation/refinement of results;
    the search provider itself meters through the ambient turn accounting)."""

    @tool
    async def web_search(
        queries: str | list,
        objective: Optional[str] = None,
        n: int = 5,
        freshness: Optional[str] = None,
        country: Optional[str] = None,
    ) -> str:
        """Search the web and return relevant pages with their content.

        Use this to FIND pages you do not have URLs for. Provide 1-2 query
        variants (`queries`: a string or a small list of rephrases) and,
        whenever you are answering a concrete question, an `objective` — it
        drives relevance scoring and content refinement, so results arrive
        already filtered to what you need.

        INPUTS
        - `queries` (REQUIRED): one query string or a small list of rephrases.
        - `objective` (recommended): the goal/question you are trying to answer.
        - `n`: max unique results (1-8; default 5 — prefer small).
        - `freshness`: 'day' | 'week' | 'month' | 'year' to restrict recency.
        - `country`: ISO2 country code (e.g. 'DE', 'US') to localize results.

        RESULT
        - JSON: {ok, results: [{url, title, text, content?, published_time_iso?,
          objective_relevance?, ...}], note?}. `text` is the snippet; `content`
          is fetched page text (may be marked truncated — fetch the URL with
          web_fetch if a truncated page matters). Non-HTML files report their
          mime and size instead of content. Cite result URLs in your answer
          when you rely on them."""
        # Lazy import: the paid backends load only when the tool actually runs.
        from kdcube_ai_app.apps.chat.sdk.tools.backends.web import search_backends

        try:
            if isinstance(queries, str):
                try:
                    parsed = json.loads(queries)
                    queries = parsed if isinstance(parsed, list) else queries
                except Exception:
                    pass
            n_eff = max(1, min(int(n or 5), 8))
            LOGGER.info("[ported-langgraph] web_search: queries=%r objective=%r n=%d", queries, objective, n_eff)
            rows = await search_backends.web_search(
                _SERVICE=service,
                queries=queries,
                objective=objective,
                refinement="balanced",
                n=n_eff,
                freshness=freshness,
                country=country,
                safesearch="moderate",
                fetch_content=True,
                namespaced_kv_cache=_kv_cache(),
            )
            return _shape_rows(rows if isinstance(rows, list) else [])
        except Exception as e:  # noqa: BLE001 - tool errors return a message, never crash the turn
            LOGGER.warning("[ported-langgraph] web_search failed", exc_info=True)
            return _error_payload("web_search", e)

    return web_search


def build_web_fetch_tool(service: Any) -> Any:
    """The `web_fetch` LangChain tool, bound to this app's accounted model
    service (`service` powers the objective-guided refinement of fetched
    pages)."""

    @tool
    async def web_fetch(
        urls: str | list,
        objective: Optional[str] = None,
        refinement: str = "none",
    ) -> str:
        """Fetch the content of URLs you ALREADY KNOW (no search).

        Use this only for concrete http(s) URLs — from the user, from an
        earlier web_search result whose content was missing or truncated, or
        from a page you already read. To FIND pages, use web_search instead;
        do not re-fetch URLs whose web_search row already carries usable
        content.

        INPUTS
        - `urls` (REQUIRED): one absolute URL or a small list of them.
        - `objective` (optional): what you are looking for in these pages.
        - `refinement` (needs `objective`): 'none' returns full pages (default);
          'balanced' keeps the target + context; 'recall' keeps most of the
          body; 'precision' keeps only directly relevant sections.

        RESULT
        - JSON: {ok, results: [{url, title?, text?, content?, status?,
          published_time_iso?, ...}]}. Blocked/paywalled pages fall back to an
          archive mirror when possible; failures report status/error per URL.
          Content may be marked truncated — refine with an objective to get
          the relevant sections. Non-HTML files report mime and size instead
          of content."""
        from kdcube_ai_app.apps.chat.sdk.tools.backends.web import fetch_backends

        try:
            if isinstance(urls, str):
                try:
                    parsed = json.loads(urls)
                    urls = parsed if isinstance(parsed, list) else urls
                except Exception:
                    pass
            LOGGER.info("[ported-langgraph] web_fetch: urls=%r objective=%r refinement=%r", urls, objective, refinement)
            ret = await fetch_backends.fetch_url_contents(
                _SERVICE=service,
                urls=urls,
                max_content_length=-1,
                use_archive_fallback=True,
                extraction_mode="custom",
                refinement=refinement,
                objective=objective,
                namespaced_kv_cache=_kv_cache(),
            )
            rows: List[Dict[str, Any]] = []
            if isinstance(ret, dict):
                for url, row in ret.items():
                    if not isinstance(row, dict):
                        continue
                    item: Dict[str, Any] = {"url": url, **row}
                    title = (row.get("title") or row.get("name") or "").strip()
                    if title:
                        item["title"] = title
                    text = (row.get("content") or row.get("text") or "").strip()
                    if text:
                        item["text"] = text
                        item["content"] = text
                    rows.append(item)
            return _shape_rows(rows)
        except Exception as e:  # noqa: BLE001
            LOGGER.warning("[ported-langgraph] web_fetch failed", exc_info=True)
            return _error_payload("web_fetch", e)

    return web_fetch
