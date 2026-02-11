# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/tools/backends/web/search_backends.py

"""
Unified search backend interface with DDG + Brave.

Goals
-----
1) Single normalized API:
   - inputs: query, max_results, freshness, country, safesearch
   - outputs: [{"title","url","text","provider"}]

2) Canonical modifiers:
   - freshness: None | "day" | "week" | "month" | "year"
   - country: None | ISO-3166 alpha-2 (e.g. "DE","US")
   - safesearch: "off" | "moderate" | "strict"

3) Backend capabilities:
   - default_use_external_reconciler
   - default_use_external_refinement

   We set:
     * DDG: reconciler ON (external), refinement ON (external)
     * Brave: reconciler OFF (external), refinement ON (external)

4) Boilerplate-free:
   - common validation, normalization, logging, batching in base class
   - provider impls only implement _search_impl / _blocking_search
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import os, copy, uuid
from abc import ABC, abstractmethod

import json, itertools
from typing import Any, Dict, List, Optional, Sequence, Annotated, Iterable

import aiohttp

from kdcube_ai_app.apps.chat.sdk.tools.backends.web.inventory import compose_search_results_html, SearchRequest, \
    make_hit, clamp_max_results, SearchBackendError, _claim_sid_block, _normalize_url, dedup_round_robin_ranked, \
    PROVIDERS_AUTHORITY_RANK
from kdcube_ai_app.apps.chat.sdk.tools.web.with_llm import sources_reconciler, \
    filter_search_results_by_content
from kdcube_ai_app.apps.chat.sdk.tools.citations import enrich_sources_pool_with_favicons
from kdcube_ai_app.infra.accounting import track_web_search, with_accounting
from kdcube_ai_app.infra.accounting.usage import ws_provider_extractor, ws_model_extractor, ws_usage_extractor, \
    ws_meta_extractor
from kdcube_ai_app.apps.chat.sdk.tools.backends.web.fetch_backends import fetch_search_results_content
from kdcube_ai_app.apps.chat.sdk.tools.backends.web.ranking import apply_weighted_rank, \
    max_relevance_score, provider_rank

logger = logging.getLogger(__name__)

# ---- DDG ----
try:
    from ddgs import DDGS  # newer package name
except Exception as e:
    DDGS = None
    logger.warning("DDG library missing: %s", e)

def _iter_hits(q: str, per_query_max: int) -> Iterable[Dict[str, Any]]:
    for hit in DDGS().text(q, max_results=per_query_max):
        yield {
            "title": hit.get("title", "") or "",
            "url":  hit.get("href", hit.get("url", "")) or "",
            "body": hit.get("body", "") or "",
        }

# ----------------------------- Base backend -----------------------------

class SearchBackend(ABC):
    """
    Base class with:
      - unified/normalized public search API
      - batching helper for multiple query variants
      - capability flags for external pipeline steps
    """

    name: str = "base"
    provider: str = "base"

    # These control external pipeline defaults
    default_use_external_reconciler: bool = True
    default_use_external_refinement: bool = True

    # Provider hard limits
    max_results_hard_cap: int = 100

    async def search(
            self,
            query: str,
            max_results: int = 10,
            freshness: Optional[str] = None,
            country: Optional[str] = None,
            safesearch: str = "moderate",
    ) -> List[Dict[str, Any]]:
        """
        Public normalized search API.
        Returns list of normalized hits: [{"title","url","text","provider"}]
        """
        req = SearchRequest(
            query=query,
            max_results=min(max_results, self.max_results_hard_cap),
            freshness=freshness,
            country=country,
            safesearch=safesearch,
        ).normalized()

        logger.debug(
            "%s.search query=%r max=%s freshness=%s country=%s safesearch=%s",
            self.name, req.query, req.max_results, req.freshness, req.country, req.safesearch
        )

        hits = await self._search_impl(req)

        # Final safety normalization + trim
        out: List[Dict[str, Any]] = []
        for h in hits or []:
            if not isinstance(h, dict):
                continue
            title = h.get("title") or ""
            url = h.get("url") or ""
            text = h.get("text") or ""
            if not url:
                continue
            out.append(make_hit(title, url, text, self.name))
            if len(out) >= req.max_results:
                break

        logger.info("%s returned %d results", self.name, len(out))
        return out

    @abstractmethod
    async def _search_impl(self, req: SearchRequest) -> List[Dict[str, Any]]:
        """Provider-specific implementation. Must return normalized-ish dicts."""
        raise NotImplementedError


# ----------------------------- DDG backend -----------------------------

class DDGSearchBackend(SearchBackend):
    name = "duckduckgo"
    provider = "duckduckgo"

    default_use_external_reconciler = True
    default_use_external_refinement = True

    max_results_hard_cap = 100

    _FRESHNESS_MAP = {
        "day": "d",
        "week": "w",
        "month": "m",
        "year": "y",
    }

    _SAFESEARCH_MAP = {
        "off": "off",
        "moderate": "moderate",
        "strict": "on",
    }

    def _country_to_region(self, country: Optional[str]) -> Optional[str]:
        """Best-effort mapping to DDG region."""
        if not country:
            return None
        cc = country.upper()
        if cc == "US":
            return "us-en"
        if cc == "GB":
            return "uk-en"
        return f"{cc.lower()}-{cc.lower()}"

    async def _search_impl(self, req: SearchRequest) -> List[Dict[str, Any]]:
        if DDGS is None:
            raise SearchBackendError("DDGS library not available")

        return await asyncio.to_thread(self._blocking_search, req)

    def _blocking_search(self, req: SearchRequest) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {"max_results": req.max_results}

        if req.freshness:
            tl = self._FRESHNESS_MAP.get(req.freshness)
            if tl:
                kwargs["timelimit"] = tl

        region = self._country_to_region(req.country)
        if region:
            kwargs["region"] = region

        kwargs["safesearch"] = self._SAFESEARCH_MAP.get(req.safesearch, "moderate")

        results: List[Dict[str, Any]] = []
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(req.query, **kwargs):
                    url = (r.get("href") or r.get("url") or "").strip()
                    if not url:
                        continue
                    results.append(
                        make_hit(
                            r.get("title", ""),
                            url,
                            r.get("body", "") or r.get("text", "") or r.get("description", ""),
                            self.name,
                        )
                    )
        except Exception as e:
            raise SearchBackendError(f"DDG search failed: {e}") from e

        return results


    @track_web_search(
        provider_extractor=ws_provider_extractor,
        model_extractor=ws_model_extractor,
        usage_extractor=ws_usage_extractor,
        metadata_extractor=ws_meta_extractor,
    )
    async def search_many(
            self,
            queries: Sequence[str],
            *,
            per_query_max: int,
            freshness: Optional[str] = None,
            country: Optional[str] = None,
            safesearch: str = "moderate",
            concurrency: int = 8,
    ) -> List[List[Dict[str, Any]]]:
        """
        Execute multiple queries with success tracking.

        Decorator reports only successful queries (non-error).
        Tracks successes in self._last_successful_queries for extractor.
        """
        qs = [str(q).strip() for q in (queries or []) if str(q).strip()]
        if not qs:
            self._last_successful_queries = []
            return []

        per_query_max = clamp_max_results(per_query_max, lo=1, hi=50)
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        # Track which queries succeeded
        successful_queries: List[str] = []

        async def _one(q: str) -> List[Dict[str, Any]]:
            async with sem:
                try:
                    hits = await self.search(
                        q,
                        max_results=per_query_max,
                        freshness=freshness,
                        country=country,
                        safesearch=safesearch,
                    )

                    # Track success (non-empty results = successful query)
                    if hits:
                        successful_queries.append(q)

                    return hits

                except Exception as e:
                    logger.warning(f"{self.name} failed for '{q[:50]}...': {e}")
                    return []

        results = await asyncio.gather(*[_one(q) for q in qs])

        # Store for extractor
        self._last_successful_queries = successful_queries

        logger.info(
            f"{self.name}.search_many: {len(successful_queries)}/{len(qs)} successful"
        )

        return results


# ----------------------------- Brave backend -----------------------------

class BraveSearchBackend(SearchBackend):
    name = "brave"
    provider = "brave"

    default_use_external_reconciler = False
    default_use_external_refinement = True

    API_URL = "https://api.search.brave.com/res/v1/web/search"
    TIMEOUT_S = 20
    MAX_COUNT_PER_REQ = 20
    max_results_hard_cap = 100

    _FRESHNESS_MAP = {
        "day": "pd",
        "week": "pw",
        "month": "pm",
        "year": "py",
    }

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or os.environ.get("BRAVE_API_KEY") or "").strip()
        if not self.api_key:
            raise SearchBackendError("BRAVE_API_KEY not set")

    async def _search_impl(self, req: SearchRequest) -> List[Dict[str, Any]]:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }

        remaining = req.max_results
        offset = 0
        out: List[Dict[str, Any]] = []

        timeout = aiohttp.ClientTimeout(total=self.TIMEOUT_S)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            while remaining > 0:
                count = min(remaining, self.MAX_COUNT_PER_REQ)

                params: Dict[str, Any] = {
                    "q": req.query,
                    "count": count,
                    "safesearch": req.safesearch,
                }

                # Brave supports freshness codes
                if req.freshness:
                    fr = self._FRESHNESS_MAP.get(req.freshness)
                    if fr:
                        params["freshness"] = fr

                # Brave supports country (ISO2)
                if req.country:
                    params["country"] = req.country

                # Offset is supported by Brave in many versions of the API.
                # If your account tier/API version doesn't accept it, Brave
                # will typically ignore unknown params. Adjust if needed.
                if offset:
                    params["offset"] = offset

                try:
                    async with session.get(self.API_URL, headers=headers, params=params) as resp:
                        if resp.status == 401:
                            raise SearchBackendError("Brave auth failed (401)")
                        if resp.status == 429:
                            ra = resp.headers.get("Retry-After")
                            raise SearchBackendError(f"Brave rate limited (429){' retry-after=' + ra if ra else ''}")
                        if resp.status != 200:
                            txt = await resp.text()
                            raise SearchBackendError(f"Brave API error {resp.status}: {txt[:300]}")

                        data = await resp.json()

                except asyncio.TimeoutError as e:
                    raise SearchBackendError(f"Brave timeout after {self.TIMEOUT_S}s") from e
                except aiohttp.ClientError as e:
                    raise SearchBackendError(f"Brave HTTP error: {e}") from e

                web = (data or {}).get("web") or {}
                results = web.get("results") or []
                if not results:
                    break

                for r in results:
                    url = (r.get("url") or "").strip()
                    if not url:
                        continue
                    text = (r.get("description") or r.get("meta_description") or "").strip()
                    out.append(make_hit(r.get("title", ""), url, text, self.name))
                    if len(out) >= req.max_results:
                        break

                # Update loop counters
                got = len(results)
                remaining = req.max_results - len(out)
                offset += got

                # If API returns fewer than requested, stop paging
                if got < count:
                    break

        return out

    @track_web_search(
        provider_extractor=ws_provider_extractor,
        model_extractor=ws_model_extractor,
        usage_extractor=ws_usage_extractor,
        metadata_extractor=ws_meta_extractor,
    )
    async def search_many(
            self,
            queries: Sequence[str],
            *,
            per_query_max: int,
            freshness: Optional[str] = None,
            country: Optional[str] = None,
            safesearch: str = "moderate",
            concurrency: int = 8,
    ) -> List[List[Dict[str, Any]]]:
        """
        Execute multiple queries with success tracking.

        Decorator reports only successful queries.
        Rate-limited queries (429) are NOT counted in usage.
        Tracks successes in self._last_successful_queries for extractor.
        """
        qs = [str(q).strip() for q in (queries or []) if str(q).strip()]
        if not qs:
            self._last_successful_queries = []
            return []

        per_query_max = clamp_max_results(per_query_max, lo=1, hi=50)
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        # Track which queries succeeded (exclude 429s and errors)
        successful_queries: List[str] = []

        async def _one(q: str) -> List[Dict[str, Any]]:
            async with sem:
                try:
                    hits = await self.search(
                        q,
                        max_results=per_query_max,
                        freshness=freshness,
                        country=country,
                        safesearch=safesearch,
                    )

                    # Track success (non-empty results = successful query)
                    if hits:
                        successful_queries.append(q)

                    return hits

                except SearchBackendError as e:
                    # Rate limit or API error - don't count toward usage
                    if "429" in str(e) or "rate" in str(e).lower():
                        logger.warning(f"{self.name} rate limited for '{q[:50]}...'")
                    else:
                        logger.warning(f"{self.name} failed for '{q[:50]}...': {e}")
                    return []
                except Exception as e:
                    logger.error(f"{self.name} unexpected error for '{q[:50]}...': {e}")
                    return []

        results = await asyncio.gather(*[_one(q) for q in qs])

        # Store for extractor
        self._last_successful_queries = successful_queries

        logger.info(
            f"{self.name}.search_many: {len(successful_queries)}/{len(qs)} successful"
        )

        return results


# ----------------------------- Factory -----------------------------

def get_search_backend(name: Optional[str] = None) -> SearchBackend:
    """
    Factory.
    Uses env WEB_SEARCH_BACKEND if name not provided.
    Supported: duckduckgo|ddg, brave
    """
    n = (name or os.environ.get("WEB_SEARCH_BACKEND") or "duckduckgo").strip().lower()

    if n in ("duckduckgo", "ddg"):
        return DDGSearchBackend()
    if n == "brave":
        return BraveSearchBackend()

    raise SearchBackendError(f"Unknown backend '{n}'. Use 'duckduckgo' or 'brave'.")


def get_search_backend_or_hybrid(
        backend_name: Optional[str] = None,
        *,
        enable_hybrid: bool = True,
        hybrid_mode: str = "sequential",
        spare_backend: str = "duckduckgo"
):
    """
    Get search backend with optional hybrid wrapper.

    Args:
        backend_name: Primary backend name or None for env default
        enable_hybrid: If True and primary != DDG, wrap in HybridSearchBackend
        hybrid_mode: "sequential" (spare only for failures) or "parallel" (both run)
        spare_backend: Fallback backend (default: "duckduckgo")

    Returns:
        SearchBackend or HybridSearchBackend instance
    """
    if not backend_name:
        backend_name = os.environ.get("WEB_SEARCH_BACKEND", "duckduckgo").strip().lower()
    else:
        backend_name = backend_name.strip().lower()

    # Explicit hybrid mode via backend name
    if backend_name == "hybrid":
        from kdcube_ai_app.apps.chat.sdk.tools.backends.web.hybrid_search_backend import get_hybrid_search_backend, HybridMode
        primary_name = os.environ.get("WEB_SEARCH_PRIMARY_BACKEND", "brave")
        mode = HybridMode(hybrid_mode) if hybrid_mode in ("sequential", "parallel") else HybridMode.SEQUENTIAL
        return get_hybrid_search_backend(primary_name, spare_backend, mode)

    # Get requested backend
    try:
        backend = get_search_backend(backend_name)
    except SearchBackendError:
        logger.exception(f"Failed to init backend '{backend_name}'; falling back to DDG")
        backend = get_search_backend("duckduckgo")
        backend_name = "duckduckgo"

    # Wrap in hybrid if enabled and not already DDG
    if enable_hybrid and backend_name not in ("duckduckgo", "ddg"):
        logger.info(
            f"Wrapping {backend_name} in HybridSearchBackend (mode={hybrid_mode}) "
            f"with {spare_backend} fallback"
        )
        from kdcube_ai_app.apps.chat.sdk.tools.backends.web.hybrid_search_backend import HybridSearchBackend, HybridMode
        spare = get_search_backend(spare_backend)
        mode = HybridMode(hybrid_mode) if hybrid_mode in ("sequential", "parallel") else HybridMode.SEQUENTIAL
        backend = HybridSearchBackend(primary=backend, spare=spare, mode=mode)

    return backend


# ----------------------------- Outer web_search (NO decorator) -----------------------------

async def web_search(
        _SERVICE,
        queries: Annotated[str|List[str], (
                "JSON array of rephrases/synonyms or single query string."
        )],
        objective: Annotated[Optional[str], "Search objective for relevance scoring and refinement."],
        refinement: Annotated[str, "Content refinement: 'none'|'balanced'|'recall'|'precision'"] = "balanced",
        n: Annotated[int, "Max results (1-20)", {"min": 1, "max": 20}] = 8,
        fetch_content: Annotated[bool, "If true, fetch page content according to 'refinement' option."] = True,
        include_binary_base64: Annotated[bool, (
                "If true, attach base64 for binary/image/PDF fetches when size limits allow."
        )] = True,
        freshness: Annotated[Optional[str], "Canonical freshness: 'day'|'week'|'month'|'year' or null."] = None,
        country: Annotated[Optional[str], "Canonical country ISO2, e.g. 'DE', 'US'."] = None,
        safesearch: Annotated[str, "Canonical safesearch: 'off'|'moderate'|'strict'."] = "moderate",
        artifact_id: str = None,
        enable_hybrid: bool = True,
        hybrid_mode: str = "sequential",
        namespaced_kv_cache: Any | None = None,
) -> Annotated[List[dict]|None, (
        "JSON array: [{sid, title, url, text, objective_relevance?, query_relevance?, content?}, ...]. "
        "Relevance fields present only when reconciliation runs. Content present if fetched."
)]:
    """
    Main web search orchestrator.

    NO decorator here - accounting handled by backend.search_many() decorators.
    Results are CLEAN - no provider metadata returned to LLM.
    """

    WEB_SEARCH_AGENTIC_THINKING_BUDGET = int(os.getenv("WEB_SEARCH_AGENTIC_THINKING_BUDGET") or 0)
    refinement = (refinement or "balanced").lower()
    if refinement not in ("none", "balanced", "recall", "precision"):
        refinement = "balanced"
    reconciling: bool = True
    reconciler_reasoning: bool = False

    # --- Normalize queries input ---
    if isinstance(queries, (list, tuple)):
        logger.warning(f"web_search: received {type(queries).__name__} instead of string, converting")
        q_list: List[str] = [str(q).strip() for q in queries if str(q).strip()]
    else:
        # Original logic: expect a string (either JSON array or single query)
        try:
            queries_str = str(queries or "").strip()
            if queries_str.startswith("["):
                # Parse as JSON array
                raw = json.loads(queries_str)
                q_list = [str(x).strip() for x in raw if str(x).strip()]
            else:
                # Single query string
                q_list = [queries_str] if queries_str else []
        except json.JSONDecodeError as e:
            logger.warning(f"web_search: JSON parse failed: {e}, treating as single query")
            q_list = [str(queries or "").strip()]
        except Exception as e:
            logger.warning(f"web_search: unexpected error: {e}, treating as single query")
            q_list = [str(queries or "").strip()]

    # Filter out empty queries
    q_list = [q for q in q_list if q]

    if not q_list:
        logger.warning("web_search: no valid queries after normalization")
        return []

    # --- Comm / thinking channel setup ---
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import delta as emit_delta_fn, get_comm
    except Exception:
        emit_delta_fn = None
        def get_comm():
            return None

    comm = get_comm()
    think_idx = 0
    finish_thinking_is_sent = False

    q_list = [" ".join(q_list)]

    if not artifact_id:
        if objective and objective.strip():
            artifact_id = f"Web search for {objective.strip()}"
        else:
            joined = ", ".join(q_list)
            artifact_id = f"Web search for {joined}" if joined else "web search"
        artifact_id = artifact_id[:120]

    agent_label = artifact_id
    agent_suffix = uuid.uuid4().hex[:8]
    max_label_len = max(1, 120 - (len(agent_suffix) + 3))
    agent_id = f"{agent_label[:max_label_len]} [{agent_suffix}]"
    search_id = f"Web Search Results [{agent_suffix}]-{uuid.uuid4().hex[:6]}"

    marker = "timeline_text" # "thinking"
    disable_display_progress = True
    async def emit_progress(text: str, completed: bool = False, **kwargs):
        """Wrapper to emit thinking/timeline_text deltas."""
        nonlocal think_idx
        nonlocal finish_thinking_is_sent
        if disable_display_progress:
            return

        if not (emit_delta_fn and comm):
            return
        if not text and not completed:
            return

        await emit_delta_fn(
            text=text,
            index=think_idx,
            marker=marker,
            agent=agent_id,
            title=agent_label,
            format="markdown",
            artifact_name=agent_id,
            completed=completed,
            **kwargs
        )
        if text or completed:  # Only increment if we actually emitted something
            think_idx += 1
        if completed:
            finish_thinking_is_sent = True

    async def finish_thinking():
        """Signal thinking completion."""
        nonlocal finish_thinking_is_sent
        if disable_display_progress:
            return
        if think_idx:
            await emit_progress("", completed=True)
        finish_thinking_is_sent = True

    # --- Get backend ---
    n = max(1, min(int(n), 20))
    per_query_max = n

    # search_backend = get_search_backend_or_hybrid(
    #     enable_hybrid=enable_hybrid,
    #     hybrid_mode=hybrid_mode,
    #     spare_backend="duckduckgo"
    # )
    enable_hybrid = False
    search_backend = get_search_backend_or_hybrid(
        backend_name="brave",
        enable_hybrid=enable_hybrid,
        hybrid_mode=hybrid_mode,
        # spare_backend="duckduckgo"
    )

    #queries

    backend_name = (
            getattr(search_backend, "provider", None)
            or getattr(search_backend, "name", None)
            or "unknown"
    )

    # Decide external pipeline defaults based on backend capability flags
    reconciling = bool(getattr(search_backend, "default_use_external_reconciler", True))
    # You said: keep refinement in both backends (external refinement stays available)
    # We treat this as "do_segment path still allowed".
    use_external_refinement = bool(getattr(search_backend, "default_use_external_refinement", True))

    from kdcube_ai_app.infra.accounting import _get_context

    context = _get_context()
    context_snapshot = context.to_dict()
    logger.warning(f"[Context snapshot]:\n{context_snapshot}")

    bundle_id = context_snapshot.get("app_bundle_id")

    # --- Execute search (backend.search_many() is decorated, will emit accounting event) ---
    async with with_accounting(
            bundle_id,
            artifact_id=artifact_id,
            backend=backend_name,
            metadata={
                "backend": backend_name,
                "artifact_id": artifact_id,
            }
    ):
        per_query_results: List[List[Dict[str, Any]]] = await search_backend.search_many(
            q_list,
            per_query_max=per_query_max,
            freshness=freshness,
            country=country,
            safesearch=safesearch,
            concurrency=8,
        )

    if not per_query_results:
        base = _claim_sid_block(0)
        if not finish_thinking_is_sent:
            await finish_thinking()
        return []

    rows = dedup_round_robin_ranked(per_query_results=per_query_results,
                                    n=999,)
    # --- Reconcile ---
    if not reconciling:
        # No snippet-based LLM reconciliation.
        # We still want content fetch + content-based filtering/segmentation.
        reconciled_rows = rows[:n]  # keep ephemeral for now
        for row in reconciled_rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("provider") or "").lower() != "brave":
                continue
            if row.get("objective_relevance") is None:
                row["objective_relevance"] = 1.0
            if row.get("query_relevance") is None:
                row["query_relevance"] = 1.0
    else:
        # ---- Reconcile (keeps/annotates by ephemeral sids) ----
        try:
            recon_json = await sources_reconciler(
                _SERVICE=_SERVICE,
                objective=objective,
                queries=q_list,
                sources_list=rows,
                max_items=len(rows),
                reasoning=reconciler_reasoning
            )
        except Exception:
            logger.exception("web_search: reconciler failed; returning raw rows")
            # Still claim only the rows we return
            base = _claim_sid_block(len(rows))
            for i, r in enumerate(rows):
                r["sid"] = base + i
            await finish_thinking()
            return rows

        # Map sid -> relevance from reconciler output (sid are ephemeral here)
        kept = recon_json if isinstance(recon_json, list) else []
        if not kept:
            logger.exception("web_search: reconciler returned empty/invalid list; returning raw rows")
            base = _claim_sid_block(len(rows))
            for i, r in enumerate(rows):
                r["sid"] = base + i
            await finish_thinking()
            return rows

        logger.info(f"web_search: reconciler response: {kept}")
        rel_by_sid: Dict[int, Dict[str, float]] = {}
        for it in kept if isinstance(kept, list) else []:
            try:
                sid = int(it.get("sid"))
            except Exception:
                continue
            orel = float(it.get("o_relevance") or 0.0)
            qmax = 0.0
            for qr in (it.get("q_relevance") or []):
                try:
                    qmax = max(qmax, float(qr.get("score")))
                except Exception:
                    pass
            rel_by_sid[sid] = {"objective_relevance": orel, "query_relevance": qmax}

        # Keep only reconciled ephemeral sids, then sort
        by_sid = {r["sid"]: r for r in rows}
        reconciled_rows: list[dict] = []
        for sid, rel in rel_by_sid.items():
            if sid in by_sid:
                enriched = dict(by_sid[sid])  # copy
                enriched.update(rel)
                reconciled_rows.append(enriched)

        reconciled_rows.sort(
            key=lambda r: (float(r.get("objective_relevance", 0.0)),
                           float(r.get("query_relevance", 0.0))),
            reverse=True
        )
        reconciled_rows = reconciled_rows[:n]

    favicon_by_url: Dict[str, Dict[str, Any]] = {}
    if emit_delta_fn and comm:
        await enrich_sources_pool_with_favicons(
            reconciled_rows,
            log=logger,
            cache=namespaced_kv_cache,
        )
        for row in reconciled_rows:
            if not isinstance(row, dict):
                continue
            url = row.get("url") or ""
            if not url:
                continue
            key = _normalize_url(url)
            if not key:
                continue
            if "favicon" in row or "favicon_status" in row:
                favicon_by_url[key] = {
                    "favicon": row.get("favicon"),
                    "favicon_status": row.get("favicon_status"),
                    "title": row.get("title"),
                }

    widget = None
    if emit_delta_fn and comm:
        from kdcube_ai_app.apps.chat.sdk.runtime.solution.widgets.web_search import WebSearchWidget

        rel_weight = 0.60
        prov_weight = 0.25
        denom = rel_weight + prov_weight
        max_rank = max(PROVIDERS_AUTHORITY_RANK.values() or [0])
        filtered_payload = []
        for row in reconciled_rows:
            rel_score = max_relevance_score(row)
            rank = provider_rank(row.get("provider"))
            prov_score = (rank / max_rank) if max_rank > 0 else 0.0
            weighted_score = (
                (rel_weight * rel_score + prov_weight * prov_score) / denom
                if denom > 0
                else 0.0
            )
            if weighted_score < 0.0:
                weighted_score = 0.0
            elif weighted_score > 1.0:
                weighted_score = 1.0
            payload_row = dict(row)
            payload_row["weighted_score"] = weighted_score
            filtered_payload.append(payload_row)

        widget = WebSearchWidget(
            emit_delta=emit_delta_fn,
            agent=agent_id,
            title=agent_label,
            artifact_name=f"Web Search [{agent_suffix}]",
            search_id=search_id,
        )
        await widget.send_search_results(
            filtered_payload=filtered_payload,
            objective=objective,
            queries=q_list,
        )

    # --- Fetch content ---
    filtered_rows = reconciled_rows
    new_rows = filtered_rows
    if refinement != "none" and fetch_content and reconciled_rows and use_external_refinement:
        fetch_agent_label = (
            f"Reading through web search results for {objective.strip()}"
            if objective and objective.strip()
            else "Reading through web search results"
        )
        fetch_agent_id = f"Web fetch for {objective.strip()}" if objective and objective.strip() else "Web fetch"
        fetch_agent_id = f"{fetch_agent_id} [{agent_suffix}]"
        fetched_rows = await fetch_search_results_content(
            search_results=reconciled_rows,
            max_content_length=-1,
            extraction_mode="custom",
            include_binary_base64=include_binary_base64,
            widget_agent=fetch_agent_id,
            widget_artifact_name=f"Web Fetch [{agent_suffix}]",
            widget_title=fetch_agent_label,
            namespaced_kv_cache=namespaced_kv_cache,
        )
        new_rows = fetched_rows
        if fetched_rows and len(fetched_rows) > 1:
            new_rows = copy.deepcopy(fetched_rows)
            new_rows = await filter_search_results_by_content(
                _SERVICE=_SERVICE,
                objective=objective,
                queries=q_list,
                search_results=new_rows,
                do_segment=True,
                mode=refinement,
                on_progress_fn=emit_progress,
                thinking_budget=WEB_SEARCH_AGENTIC_THINKING_BUDGET
            )

    # --- Finalize SIDs ---
    final_rows = new_rows or []
    if final_rows:
        fetched_time_iso = datetime.now(timezone.utc).isoformat()
        for row in final_rows:
            if isinstance(row, dict) and "fetched_time_iso" not in row:
                row["fetched_time_iso"] = fetched_time_iso
    if favicon_by_url:
        for row in final_rows:
            if not isinstance(row, dict):
                continue
            if row.get("favicon") is not None or "favicon_status" in row:
                continue
            url = row.get("url") or ""
            if not url:
                continue
            key = _normalize_url(url)
            if not key:
                continue
            cached = favicon_by_url.get(key)
            if not cached:
                continue
            row["favicon"] = cached.get("favicon")
            row["favicon_status"] = cached.get("favicon_status")
            if not row.get("title") and cached.get("title"):
                row["title"] = cached.get("title")
    apply_weighted_rank(final_rows, force=True)
    base = _claim_sid_block(len(final_rows))

    sid_map: Dict[int, int] = {}
    for i, r in enumerate(final_rows):
        old = r.get("sid")
        new = base + i
        r["sid"] = new
        sid_map[old] = new

    if not finish_thinking_is_sent:
        await finish_thinking()

    # ---- HTML artifact emission (tool channel) ----
    should_emit_html = True
    if should_emit_html and emit_delta_fn and comm and widget:

        html_view = compose_search_results_html(
            objective=objective or "",
            queries=q_list,
            initial_rows=rows,          # raw initial results (ephemeral sids)
            reconciled_rows=final_rows, # global SIDs
            filtered_rows=final_rows,
            title="Web Search Results",
        )
        await widget.send_search_report(html_view=html_view)
        # html_idx += 1
        # await emit_delta_fn(
        #     "",
        #     completed=True,
        #     index=html_idx,
        #     marker="subsystem",
        #     agent=agent_id,
        #     title=agent_label,
        #     format="html",
        #     artifact_name=artifact_html,
        #     sub_type="web_search",
        #     search_id=search_id,
        # )

    # --- CLEAN OUTPUT: remove accounting metadata ---
    # Provider are for accounting only, not for LLM
    for r in final_rows:
        r.pop("provider", None)
        r["authority"] = "web"  # Tag for downstream

    return final_rows
