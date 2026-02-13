# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/web_tools.py

from __future__ import annotations

import json
from typing import Annotated, Optional, Any

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

import kdcube_ai_app.apps.chat.sdk.tools.backends.web.search_backends as search_backends
import kdcube_ai_app.apps.chat.sdk.tools.backends.web.fetch_backends as fetch_backends

# Bound at runtime by ToolManager
_SERVICE = None
_INTEGRATIONS = None

def bind_service(svc):
    global _SERVICE
    _SERVICE = svc

def bind_integrations(integrations):
    global _INTEGRATIONS
    _INTEGRATIONS = integrations or {}

def _ok_ret_result(ret: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, "ret": ret}

def _error_result(*, code: str, message: str, where: str, managed: bool, ret: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "where": where,
            "managed": managed,
        },
        "ret": ret,
    }

def _strip_none_fields(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    return {k: v for k, v in item.items() if v is not None}


class WebTools:
    # @kernel_function(
    #     name="web_search",
    #     description=(
    #         "Web discovery tool (multi-query). Finds and deduplicates pages across query variants. "
    #         "If an objective is provided (and the backend supports it), the tool scores snippet relevance to the objective/queries "
    #         "and may drop clearly irrelevant results. If fetch_content is true, it fetches page text and can refine it "
    #         "to reduce boilerplate while preserving recall.\n\n"
    #         "Use when you need to FIND pages. For known URLs only, use fetch_url_contents.\n"
    #         "Refinement modes (post-fetch, objective-guided, best-effort): "
    #         "- 'none': full pages (exploratory)\n"
    #         "- 'balanced': target + context, 50-70% (default)\n"
    #         "- 'recall': content bodies, 80-95% (comprehensive)\n"
    #         "- 'precision': direct answers only, 20-50% (narrow questions)"
    #     )
    # )
    @kernel_function(
        name="web_search",
        description=(
                "Web discovery tool (multi-query). Finds and deduplicates pages across query variants. "
                "If an objective is provided (and the backend supports it), the tool scores snippet relevance to the objective/queries "
                "and may drop clearly irrelevant results.\n\n"
                "Use when you need to FIND pages. For known URLs only, use web_fetch.\n"
                "Returns an envelope: {ok, error, ret}.\n"
                "Returned items are added to sources_pool with their sid. "
                "You can use them immediately; only read from sources_pool later if a snippet is no longer visible.\n"
        )
    )
    async def web_search(
        self,
        queries: Annotated[str | list[str], (
           "Array of string queries (rephrases/synonyms) or a single query string. Query results might be large. Prefer max 2 queries at a time"
        )],

        # queries: Annotated[str | list[str], (
        #     "Array of string queries (rephrases/synonyms) or a single query string. Variants improve recall/diversity."
        # )],
        objective: Annotated[Optional[str], "Optional search objective (goal/question). Used for snippet relevance scoring and content refinement."],
        # refinement: Annotated[str, "Post-fetch content refinement: 'none'|'balanced'|'recall'|'precision'"] = "balanced",
        n: Annotated[int, "Max unique results (1-8). Prefer max 5", {"min": 1, "max": 20}] = 8,
        # fetch_content: Annotated[bool, "If true, fetch full page content according to 'refinement' option. Increase tokens as stated in refinement modes. Use False if you need to decide the fetch on your own. If false, return ranked snippets/URLs only (no content attr)."] = True,
        freshness: Annotated[Optional[str], "Canonical freshness: 'day'|'week'|'month'|'year' or null."] = None,
        country: Annotated[Optional[str], "Canonical country ISO2, e.g. 'DE', 'US'. Supported only: 'AR', 'AU', 'AT', 'BE', 'BR', 'CA', 'CL', 'DK', 'FI', 'FR', 'DE', 'GR', 'HK',"] = None,
        safesearch: Annotated[str, "Canonical safesearch: 'off'|'moderate'|'strict'."] = "moderate",
    ) -> Annotated[dict, (
            "Envelope: {ok, error, ret}. ret is an array of results: "
            "[{sid,title,url,text,objective_relevance?,query_relevance?,content?,mime?,base64?,size_bytes?,...date/meta...}]. "
            "Scores (0..1) from snippet reconciliation when enabled by backend. "
            "Content present only if fetched; may be refined per mode. Non-HTML supported files return mime/base64 instead of content."
    )]:
        refinement: Annotated[str, "Post-fetch content refinement: 'none'|'balanced'|'recall'|'precision'"] = "balanced"
        fetch_content: Annotated[bool, "If true, fetch full page content according to 'refinement' option. Increase tokens as stated in refinement modes. Use False if you need to decide the fetch on your own. If false, return ranked snippets/URLs only (no content attr)."] = True
        try:
            if isinstance(queries, str):
                try:
                    parsed = json.loads(queries)
                except Exception:
                    parsed = queries
                queries = parsed

            cache = None
            if isinstance(_INTEGRATIONS, dict):
                cache = _INTEGRATIONS.get("kv_cache")
            rows = await search_backends.web_search(
                _SERVICE=_SERVICE,
                queries=queries,
                objective=objective,
                refinement=refinement,
                n=n,
                freshness=freshness,
                country=country,
                safesearch=safesearch,
                fetch_content=fetch_content,
                namespaced_kv_cache=cache,
            )
            for r in rows:
                r.pop("provider", None)
            if isinstance(rows, list):
                rows = [_strip_none_fields(r) for r in rows if isinstance(r, dict)]
            return _ok_ret_result(rows)
        except Exception as e:
            msg = str(e).strip() or "web_search failed"
            managed = isinstance(e, search_backends.SearchBackendError)
            return _error_result(
                code=type(e).__name__,
                message=msg,
                where="web_tools.web_search",
                managed=managed,
                ret=[],
            )

    # @kernel_function(
    #     name="web_search_links",
    #     description=(
    #         "Search web with multiple query variants and return ranked snippets and URLs.\n\n"
    #         "Use this when you want to explore results before fetching full pages.\n"
    #         "For full content of selected URLs, call `fetch_url_contents`."
    #     )
    # )
    async def web_search_links(
        self,
        queries: Annotated[str | list[str], "Array of rephrases/synonyms or a single string."],
        objective: Annotated[Optional[str], "Optional objective for snippet relevance scoring."] = None,
        n: Annotated[int, "Max results (1-20)", {"min": 1, "max": 20}] = 8,
        freshness: Annotated[Optional[str], "Canonical freshness: 'day'|'week'|'month'|'year' or null."] = None,
        country: Annotated[Optional[str], "Canonical country ISO2, e.g. 'DE', 'US'."] = None,
        safesearch: Annotated[str, "Canonical safesearch: 'off'|'moderate'|'strict'."] = "moderate",
    ) -> dict:
        try:
            if isinstance(queries, str):
                try:
                    parsed = json.loads(queries)
                except Exception:
                    parsed = queries
                queries = parsed

            cache = None
            if isinstance(_INTEGRATIONS, dict):
                cache = _INTEGRATIONS.get("kv_cache")
            rows = await search_backends.web_search(
                _SERVICE=_SERVICE,
                queries=queries,
                objective=objective,
                refinement="none",
                n=n,
                freshness=freshness,
                country=country,
                safesearch=safesearch,
                fetch_content=False,
                namespaced_kv_cache=cache,
            )
            for r in rows:
                r.pop("provider", None)
            return _ok_ret_result(rows)
        except Exception as e:
            msg = str(e).strip() or "web_search_links failed"
            managed = isinstance(e, search_backends.SearchBackendError)
            return _error_result(
                code=type(e).__name__,
                message=msg,
                where="web_tools.web_search_links",
                managed=managed,
                ret=[],
            )

    # @kernel_function(
    #     name="fetch_url_contents",
    #     description=(
    #         "Fetch-only URL dereferencer (no search). Returns main text + status + date metadata for each URL.\n\n"
    #         "⚠️ TOOL SELECTION RULES:\n"
    #         "- Use ONLY when you already have concrete HTTP/HTTPS URLs.\n"
    #         "- Never performs search or discovery.\n"
    #         "- If you need to FIND pages, use web_search / web_search_links.\n"
    #         "- Do not call web_search and fetch_url_contents for the same discovery task.\n\n"
    #         "Objective-aware refinement is optional and best-effort: URLs are never dropped; "
    #         "pages without reliable spans keep full content (recall-first).\n"
    #         "Refinement modes:\n"
    #         "- 'none': full pages\n"
    #         "- 'balanced': target + context (50-70%)\n"
    #         "- 'recall': most body/min chrome (80-95%)\n"
    #         "- 'precision': direct answers (20-50%, requires objective)\n"
    #         "Without objective, refinement is ignored and full content is returned."
    #     )
    # )
    @kernel_function(
        name="web_fetch",
        description=(
                "Fetch-only URL dereferencer (no search). Returns main text + status + date metadata for each URL.\n\n"
                "⚠️ TOOL SELECTION RULES:\n"
                "- Use ONLY when you already have concrete HTTP/HTTPS URLs.\n"
                "- Never performs search or discovery.\n"
                "- If you need to FIND pages, use web_search / web_search_links.\n"
                "- Do not call web_search and web_fetch for the same discovery task.\n\n"
                "Objective-aware refinement is optional and best-effort: URLs are never dropped; "
                "pages without reliable spans keep full content (recall-first).\n"
                "Refinement modes:\n"
                "- 'none': full pages\n"
                "- 'balanced': target + context (50-70%)\n"
                "- 'recall': most body/min chrome (80-95%)\n"
                "- 'precision': direct answers (20-50%, requires objective)\n"
                "Without objective, refinement is ignored and full content is returned.\n"
                "Returns an envelope: {ok, error, ret}.\n"
                "Returned items are added to sources_pool with their sid. "
                "You can use them immediately; only read from sources_pool later if a snippet is no longer visible."
        )
    )
    async def web_fetch(
        self,
        urls: Annotated[str | list[str], "Array of absolute HTTP/HTTPS URLs you already know, or a single URL string."],
        objective: Annotated[Optional[str], "Optional objective (goal / task / question). Enables refinement. Without it, content stays full."] = None,
        refinement: Annotated[str, (
            "Post-fetch content refinement (requires objective):\n"
            "- 'none': Return full page content (default, fast)\n"
            "- 'balanced': Extract target + supporting context (50-70% coverage)\n"
            "- 'recall': Extract full content bodies, remove chrome (80-95% coverage)\n"
            "- 'precision': Extract only directly relevant sections (20-50% coverage)\n"
            "Never drops URLs; no/invalid spans => keep full content."
        )] = "none",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}. ret is an array of results (same shape as web_search)."]:
        try:
            if isinstance(urls, str):
                try:
                    parsed = json.loads(urls)
                except Exception:
                    parsed = urls
                urls = parsed
            ret = await fetch_backends.fetch_url_contents(
                _SERVICE=_SERVICE,
                urls=urls,
                max_content_length=-1,
                use_archive_fallback=True,
                extraction_mode="custom",
                refinement=refinement,
                objective=objective,
            )
            items: list[dict] = []
            if isinstance(ret, dict):
                for url, row in ret.items():
                    if not isinstance(row, dict):
                        continue
                    item: dict[str, Any] = {"url": url}
                    title = (row.get("title") or row.get("name") or "").strip()
                    if title:
                        item["title"] = title
                    text = (row.get("content") or row.get("text") or "").strip()
                    if text:
                        item["text"] = text
                        # Keep full content for downstream if needed.
                        item["content"] = text
                    if row.get("mime"):
                        item["mime"] = row.get("mime")
                    if row.get("base64"):
                        item["base64"] = row.get("base64")
                    if row.get("size_bytes") is not None:
                        item["size_bytes"] = row.get("size_bytes")
                    if row.get("content_length") is not None:
                        item["content_length"] = row.get("content_length")
                    if row.get("status"):
                        item["status"] = row.get("status")
                    if row.get("error"):
                        item["error"] = row.get("error")
                    if row.get("source_type"):
                        item["source_type"] = row.get("source_type")
                    if row.get("fetched_time_iso"):
                        item["fetched_time_iso"] = row.get("fetched_time_iso")
                    if row.get("published_time_iso"):
                        item["published_time_iso"] = row.get("published_time_iso")
                    if row.get("modified_time_iso"):
                        item["modified_time_iso"] = row.get("modified_time_iso")
                    if row.get("date_method"):
                        item["date_method"] = row.get("date_method")
                    if row.get("date_confidence") is not None:
                        item["date_confidence"] = row.get("date_confidence")
                    if row.get("archive_snapshot_date"):
                        item["archive_snapshot_date"] = row.get("archive_snapshot_date")
                    if row.get("archive_snapshot_url"):
                        item["archive_snapshot_url"] = row.get("archive_snapshot_url")
                    if row.get("favicon"):
                        item["favicon"] = row.get("favicon")
                    if row.get("favicon_status"):
                        item["favicon_status"] = row.get("favicon_status")
                    items.append(_strip_none_fields(item))
            return _ok_ret_result(items)
        except Exception as e:
            msg = str(e).strip() or "web_fetch failed"
            return _error_result(
                code=type(e).__name__,
                message=msg,
                where="web_tools.web_fetch",
                managed=False,
                ret=[],
            )


kernel = sk.Kernel()
tools = WebTools()
kernel.add_plugin(tools, "web_tools")
