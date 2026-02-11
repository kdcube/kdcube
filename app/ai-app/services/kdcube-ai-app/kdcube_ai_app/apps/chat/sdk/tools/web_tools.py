# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/web_tools.py

from __future__ import annotations

import json
from typing import Annotated, Optional

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
                "Use when you need to FIND pages. For known URLs only, use fetch_url_contents.\n"
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
        country: Annotated[Optional[str], "Canonical country ISO2, e.g. 'DE', 'US'."] = None,
        safesearch: Annotated[str, "Canonical safesearch: 'off'|'moderate'|'strict'."] = "moderate",
    ) -> Annotated[list[dict], (
            "Array of results: [{sid,title,url,text,objective_relevance?,query_relevance?,content?,mime?,base64?,size_bytes?,...date/meta...}]. "
            "Scores (0..1) from snippet reconciliation when enabled by backend. "
            "Content present only if fetched; may be refined per mode. Non-HTML supported files return mime/base64 instead of content."
    )]:
        refinement: Annotated[str, "Post-fetch content refinement: 'none'|'balanced'|'recall'|'precision'"] = "balanced"
        fetch_content: Annotated[bool, "If true, fetch full page content according to 'refinement' option. Increase tokens as stated in refinement modes. Use False if you need to decide the fetch on your own. If false, return ranked snippets/URLs only (no content attr)."] = True
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
        return rows

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
    ):
        return await self.web_search(
            queries=queries,
            objective=objective,
            refinement="none",
            n=n,
            freshness=freshness,
            country=country,
            safesearch=safesearch,
            fetch_content=False,
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
        name="fetch_url_contents",
        description=(
                "Fetch-only URL dereferencer (no search). Returns main text + status + date metadata for each URL.\n\n"
                "⚠️ TOOL SELECTION RULES:\n"
                "- Use ONLY when you already have concrete HTTP/HTTPS URLs.\n"
                "- Never performs search or discovery.\n"
                "- If you need to FIND pages, use web_search / web_search_links.\n"
                "- Do not call web_search and fetch_url_contents for the same discovery task.\n\n"
                "Objective-aware refinement is optional and best-effort: URLs are never dropped; "
                "pages without reliable spans keep full content (recall-first).\n"
                "Refinement modes:\n"
                "- 'none': full pages\n"
                "- 'balanced': target + context (50-70%)\n"
                "- 'recall': most body/min chrome (80-95%)\n"
                "- 'precision': direct answers (20-50%, requires objective)\n"
                "Without objective, refinement is ignored and full content is returned."
        )
    )
    async def fetch_url_contents(
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
    ):
        if isinstance(urls, str):
            try:
                parsed = json.loads(urls)
            except Exception:
                parsed = urls
            urls = parsed
        return await fetch_backends.fetch_url_contents(
            _SERVICE=_SERVICE,
            urls=urls,
            max_content_length=-1,
            use_archive_fallback=True,
            extraction_mode="custom",
            refinement=refinement,
            objective=objective,
        )


kernel = sk.Kernel()
tools = WebTools()
kernel.add_plugin(tools, "web_tools")
