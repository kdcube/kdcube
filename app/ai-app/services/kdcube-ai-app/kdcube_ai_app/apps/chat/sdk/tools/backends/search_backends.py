# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/tools/backends/search_backends.py

"""
Unified search backend interface with DDG + Brave.

Goals
-----
1) Single normalized API:
   - inputs: query, max_results, freshness, country, safesearch
   - outputs: [{"title","url","text","vendor"}]

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
import logging
import os, json, copy, pathlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
import json, itertools, urllib.parse
from typing import Any, Dict, List, Optional, Sequence, Annotated, Iterable

import aiohttp

from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
from kdcube_ai_app.apps.chat.sdk.tools.web.with_llm import sources_reconciler, \
    filter_search_results_by_content, filter_fetch_results
from kdcube_ai_app.infra.accounting import track_web_search
from kdcube_ai_app.infra.accounting.usage import ws_provider_extractor, ws_model_extractor, ws_usage_extractor, \
    ws_meta_extractor
from .fetch_backends import fetch_search_results_content

logger = logging.getLogger(__name__)

# ---- DDG ----
try:
    from ddgs import DDGS  # newer package name
except Exception as e:
    DDGS = None
    logger.warning("DDG library missing: %s", e)

def _claim_sid_block(n: int) -> int:
    """Reserve a contiguous block of n SIDs, return the base (first) SID."""
    n = max(int(n or 0), 0)
    st = SOURCE_ID_CV.get() or {}
    try:
        base = int(st.get("next", 1))
    except (TypeError, ValueError):
        base = 1
    st["next"] = base + n
    SOURCE_ID_CV.set(st)
    return base

def _normalize_url(u: str) -> str:
    # best-effort normalization for dedup
    try:
        p = urllib.parse.urlsplit(u.strip())
        p = p._replace(fragment="", query=p.query)  # keep query, drop fragment
        # normalize scheme/host only if present
        scheme = (p.scheme or "http").lower()
        netloc = p.netloc.lower()
        # remove default ports
        if netloc.endswith(":80") and scheme == "http":
            netloc = netloc[:-3]
        if netloc.endswith(":443") and scheme == "https":
            netloc = netloc[:-4]
        return urllib.parse.urlunsplit((scheme, netloc, p.path, p.query, ""))
    except Exception:
        return (u or "").strip()

def _iter_hits(q: str, per_query_max: int) -> Iterable[Dict[str, Any]]:
    for hit in DDGS().text(q, max_results=per_query_max):
        yield {
            "title": hit.get("title", "") or "",
            "url":  hit.get("href", hit.get("url", "")) or "",
            "body": hit.get("body", "") or "",
        }

# ----------------------------- Errors -----------------------------
class SearchBackendError(RuntimeError):
    pass

# ----------------------------- Canonical enums (string-based) -----------------------------

CANON_FRESHNESS = {None, "day", "week", "month", "year"}
CANON_SAFESEARCH = {"off", "moderate", "strict"}


def normalize_freshness(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    # allow a couple of friendly aliases
    aliases = {
        "d": "day",
        "w": "week",
        "m": "month",
        "y": "year",
        "today": "day",
        "24h": "day",
        "7d": "week",
        "30d": "month",
        "365d": "year",
        "any": None,
        "all": None,
    }
    v = aliases.get(v, v)
    if v not in CANON_FRESHNESS:
        return None
    return v


def normalize_country(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    # Canonical policy: prefer ISO-3166 alpha-2 uppercase.
    # If someone passes "de-DE" or "us-en" we degrade to the first 2 letters.
    v = v.replace("_", "-")
    if len(v) >= 2:
        cc = v[:2].upper()
        if cc.isalpha():
            return cc
    return None


def normalize_safesearch(value: Optional[str]) -> str:
    if value is None:
        return "moderate"
    v = str(value).strip().lower()
    if not v:
        return "moderate"
    aliases = {
        "safe": "strict",
        "on": "strict",
        "true": "strict",
        "false": "off",
        "none": "off",
        "medium": "moderate",
        "default": "moderate",
    }
    v = aliases.get(v, v)
    if v not in CANON_SAFESEARCH:
        return "moderate"
    return v


def clamp_max_results(n: int, *, lo: int = 1, hi: int = 100) -> int:
    try:
        x = int(n)
    except Exception:
        x = 10
    return max(lo, min(hi, x))


# ----------------------------- Data shapes -----------------------------

@dataclass(frozen=True)
class SearchRequest:
    query: str
    max_results: int = 10
    freshness: Optional[str] = None
    country: Optional[str] = None
    safesearch: str = "moderate"

    def normalized(self) -> "SearchRequest":
        q = (self.query or "").strip()
        if not q:
            raise ValueError("Query cannot be empty")
        return SearchRequest(
            query=q,
            max_results=clamp_max_results(self.max_results, lo=1, hi=100),
            freshness=normalize_freshness(self.freshness),
            country=normalize_country(self.country),
            safesearch=normalize_safesearch(self.safesearch),
        )


# Normalized hit notation for your pipeline
def make_hit(title: str, url: str, text: str, vendor: str) -> Dict[str, str]:
    return {
        "title": (title or "").strip(),
        "url": (url or "").strip(),
        "text": (text or "").strip(),
        "vendor": vendor,
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

    # These control YOUR external pipeline defaults:
    # - reconciler = your sources_reconciler
    # - refinement = your content filter/segmenter
    default_use_external_reconciler: bool = True
    default_use_external_refinement: bool = True

    # Provider hard limits (override if needed)
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
        Returns list of normalized hits:
          [{"title","url","text","vendor"}]
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
        Helper for your multi-query round-robin approach.
        Each query is searched independently.
        """
        qs = [str(q).strip() for q in (queries or []) if str(q).strip()]
        if not qs:
            return []

        per_query_max = clamp_max_results(per_query_max, lo=1, hi=50)
        sem = asyncio.Semaphore(max(1, int(concurrency)))
        errors: List[tuple[str, Exception]] = []

        async def _one(q: str) -> List[Dict[str, Any]]:
            async with sem:
                try:
                    return await self.search(
                        q,
                        max_results=per_query_max,
                        freshness=freshness,
                        country=country,
                        safesearch=safesearch,
                    )
                except Exception as e:
                    errors.append((q, e))
                    raise
        try:
            res = await asyncio.gather(*[_one(q) for q in qs])
        except Exception as e:
            # Surface the first error with query context
            q, err = errors[0] if errors else ("<unknown>", e)
            raise SearchBackendError(f"{self.name}.search_many failed for {q!r}: {err}") from err

        return res

    @abstractmethod
    async def _search_impl(self, req: SearchRequest) -> List[Dict[str, Any]]:
        """Provider-specific implementation. Must return normalized-ish dicts."""
        raise NotImplementedError


# ----------------------------- DDG backend -----------------------------

class DDGSearchBackend(SearchBackend):
    name = "duckduckgo"
    provider = "duckduckgo"

    # You want to keep external reconciler for DDG
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
        """
        Best-effort mapping to DDG region.
        Canonical input is country ISO2.
        Many DDG libs accept 'region' like 'us-en', 'de-de', 'wt-wt'.

        We map:
          DE -> 'de-de'
          US -> 'us-en' (common default)
        For unknowns: '{cc.lower()}-{cc.lower()}'.
        """
        if not country:
            return None
        cc = country.upper()
        if cc == "US":
            return "us-en"
        if cc == "GB":
            return "uk-en"
        # simple default
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



# ----------------------------- Brave backend -----------------------------

class BraveSearchBackend(SearchBackend):
    name = "brave"
    provider = "brave"

    # You want to be able to skip external reconciler for Brave
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

        # We try to page if max_results > 20.
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


def compose_search_results_html(
        *,
        objective: str,
        queries: list[str],
        initial_rows: list[dict],
        reconciled_rows: list[dict] | None = None,
        filtered_rows: list[dict] | None = None,
        title: str = "Web Search Results",
) -> str:
    """
    Build a self-contained HTML page showing:
      - Filtered survivors (top, with ✓ tick)
      - Reconciled (kept) results, with relevance badges
      - All initially found items (deduped), at the bottom

    Expected row shape (dict keys):
      - sid: int
      - title: str
      - url: str
      - body: str (snippet)
      - objective_relevance: float   # optional (reconciled)
      - query_relevance: float       # optional (reconciled)
      - content: str                 # optional (fetched content)
    """
    import html

    def esc(s: str) -> str:
        return html.escape(s or "")

    def badge(label: str) -> str:
        return f'<span class="badge">{esc(label)}</span>'

    def score_badge(v: float | None, label: str) -> str:
        if v is None:
            return ""
        pct = max(0, min(100, round(float(v)*100))) if v <= 1.0 else max(0, min(100, round(float(v))))
        # Color by band
        cls = "score-low"
        if pct >= 90:
            cls = "score-hi"
        elif pct >= 70:
            cls = "score-med"
        return f'<span class="score {cls}" title="{esc(label)}">{esc(label)} {pct}%</span>'

    def row_html(r: dict, *, tick=False, show_scores=False) -> str:
        sid = r.get("sid")
        title = r.get("title") or r.get("url") or f"Result {sid}"
        url = r.get("url") or ""
        body = (r.get("body") or r.get("text") or "").strip()
        # badges
        b = []
        if tick:
            b.append('<span class="tick" title="Survived content filter">✓</span>')
        if show_scores:
            b.append(score_badge(r.get("objective_relevance"), "Objective"))
            b.append(score_badge(r.get("query_relevance"), "Query"))
        badges = "".join(b)

        return f"""
        <article class="card" data-sid="{sid}">
          <header class="card-hd">
            <a class="title" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(title)}</a>
            <div class="badges">{badges}</div>
          </header>
          <div class="meta">
            <code class="sid">SID: {sid}</code>
            <a class="link" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(url)}</a>
          </div>
          <p class="snippet">{esc(body)}</p>
        </article>
        """

    def section(title: str, rows: list[dict], *, tick=False, show_scores=False) -> str:
        if not rows:
            return ""
        items = "\n".join(row_html(r, tick=tick, show_scores=show_scores) for r in rows)
        return f"""
        <section class="section">
          <h2>{esc(title)}</h2>
          {items}
        </section>
        """

    # To avoid duplicate repetition across sections, keep each list as provided
    filtered_rows = filtered_rows or []
    reconciled_rows = reconciled_rows or []
    initial_rows = initial_rows or []

    # Inline CSS (printable & dark-mode friendly enough)
    css = """
    :root { --bg:#fff; --fg:#111; --muted:#666; --line:#e5e7eb; --brand:#2563eb; --ok:#16a34a; --lo:#f59e0b; --med:#06b6d4; --hi:#10b981; }
    @media (prefers-color-scheme: dark) {
      :root { --bg:#0b0b0c; --fg:#e9e9ea; --muted:#9aa0a6; --line:#26282c; --brand:#60a5fa; --ok:#22c55e; --lo:#fbbf24; --med:#22d3ee; --hi:#34d399; }
    }
    *{box-sizing:border-box} html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,Roboto,Helvetica,Arial}
    main{max-width:980px;margin:0 auto;padding:24px}
    header.page h1{font-size:1.4rem;margin:0 0 6px}
    header.page .sub{color:var(--muted);font-size:.95rem}
    .pill{display:inline-block;padding:2px 8px;border:1px solid var(--line);border-radius:999px;margin-right:6px;color:var(--muted);font-size:.85rem}
    .section{margin-top:28px}
    .section>h2{font-size:1.1rem;border-bottom:1px solid var(--line);padding-bottom:6px;margin:0 0 12px}
    .card{border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:12px 0;background:rgba(0,0,0,.02)}
    .card-hd{display:flex;gap:10px;align-items:baseline;justify-content:space-between}
    .title{font-weight:600;text-decoration:none;color:var(--fg)}
    .title:hover{color:var(--brand)}
    .badges{display:flex;gap:6px;align-items:center}
    .tick{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:999px;background:var(--ok);color:#fff;font-weight:700}
    .badge{display:inline-block;padding:2px 8px;border-radius:999px;background:transparent;border:1px solid var(--line);color:var(--muted);font-size:.8rem}
    .score{display:inline-block;padding:2px 8px;border-radius:6px;font-weight:600;font-size:.8rem;border:1px solid var(--line)}
    .score-low{background:rgba(245,158,11,.15)}
    .score-med{background:rgba(6,182,212,.15)}
    .score-hi{background:rgba(16,185,129,.15)}
    .meta{display:flex;gap:10px;align-items:center;margin:6px 0 8px}
    .sid{background:transparent;color:var(--muted);border:1px dashed var(--line);padding:2px 6px;border-radius:6px}
    .link{color:var(--brand);text-decoration:none;font-size:.92rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%}
    .link:hover{text-decoration:underline}
    .snippet{margin:.2rem 0 0;color:var(--fg)}
    .topline{margin:10px 0 0;display:flex;flex-wrap:wrap;gap:6px}
    """

    q_pills = "".join(f'<span class="pill">Q: {esc(q)}</span>' for q in (queries or []))
    header_html = f"""
    <header class="page">
      <h1>{esc(title)}</h1>
      <div class="sub">Objective: {esc(objective or '')}</div>
      <div class="topline">{q_pills}</div>
    </header>
    """

    html_doc = f"""<!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>{esc(title)}</title>
        <style>{css}</style>
      </head>
      <body>
        <main>
          {header_html}
          {section("Filtered (Survivors ✓)", filtered_rows, tick=True, show_scores=True)}
          {section("Reconciled (Kept)", reconciled_rows, tick=False, show_scores=True)}
          {section("Initially Found", initial_rows, tick=False, show_scores=False)}
        </main>
      </body>
    </html>
    """
    return html_doc.strip()


# ----------------------------- Factory -----------------------------

def get_search_backend(name: Optional[str] = None) -> SearchBackend:
    """
    Factory.
    Uses env SEARCH_BACKEND if name not provided.
    Supported: duckduckgo|ddg, brave
    """
    n = (name or os.environ.get("WEB_SEARCH_BACKEND") or "duckduckgo").strip().lower()

    if n in ("duckduckgo", "ddg"):
        return DDGSearchBackend()
    if n == "brave":
        return BraveSearchBackend()

    raise SearchBackendError(f"Unknown backend '{n}'. Use 'duckduckgo' or 'brave'.")

@track_web_search(
    provider_extractor=ws_provider_extractor,
    model_extractor=ws_model_extractor,
    usage_extractor=ws_usage_extractor,
    metadata_extractor=ws_meta_extractor,
)
async def web_search(
        _SERVICE,
        queries: Annotated[str|List[str], (
                "JSON array of rephrases/synonyms, e.g. "
                '["israeli ncd supply chain directive", "national cyber directorate vendor risk"]. '
                "Single string also accepted."
        )],
        objective: Annotated[Optional[str], "Search objective for relevance scoring and refinement."],
        refinement: Annotated[str, "Content refinement: 'none'|'balanced'|'recall'|'precision'"] = "balanced",
        n: Annotated[int, "Max results (1-20)", {"min": 1, "max": 20}] = 8,
        fetch_content: Annotated[bool, "If true, fetch page content according to 'refinement' option. Increase tokens as stated in refinement modes. Use False if you need to decide the fetch on your own."] = True,
        freshness: Annotated[Optional[str], "Canonical freshness: 'day'|'week'|'month'|'year' or null."] = None,
        country: Annotated[Optional[str], "Canonical country ISO2, e.g. 'DE', 'US'."] = None,
        safesearch: Annotated[str, "Canonical safesearch: 'off'|'moderate'|'strict'."] = "moderate",
) -> Annotated[List[dict]|None, (
        "JSON array: [{sid, title, url, text, objective_relevance?, query_relevance?, content?}, ...]. "
        "Relevance fields present only when reconciliation runs successfully. Content field is present if fetched."
        "Relevance scored on snippets (0-1). Content refined by mode. Pre-sorted by relevance."
)]:
    """
    Behavior:
      - Interleaves results round-robin across queries to increase topical diversity.
      - Deduplicates by normalized URL (scheme/host/path/query; fragment ignored).
      - Assigns stable SIDs via a contiguous block reserved up front.
      - Stops when the total unique results reaches `n` or all query streams are exhausted.
      - If reconciling=True: runs sources_reconciler to DROP irrelevant hits and annotate kept ones.
      - If fetch_content=True: fetches content, then filters duplicates and low-quality content.

    Notes:
      - Pass richer rephrases (synonyms, entities, years) to improve coverage.
      - If you only have one query, pass a single string or a 1-element JSON array.
    """
    # --- normalize inputs ---

    # Set on if the agent(s) supporting the heuristics in this tool have the thinking channel.
    # In that case, we can emit thinking updates. The budget can be set accordingly.
    WEB_SEARCH_AGENTIC_THINKING_BUDGET = int(os.getenv("WEB_SEARCH_AGENTIC_THINKING_BUDGET") or 0)
    refinement = (refinement or "balanced").lower()
    if refinement not in ("none", "balanced", "recall", "precision"):
        refinement = "balanced"
    reconciling: Annotated[bool, "If true, run LLM reconciliation to check the search results for relevance against queries and objective, and filter/enrich them."] = True

    # Cut down spendings
    reconciler_reasoning: bool = False

    # SAFEGUARD: Handle if queries is accidentally passed as a list instead of string
    if isinstance(queries, (list, tuple)):
        logger.warning(
            f"web_search: received {type(queries).__name__} instead of string, converting to list"
        )
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

    # --- comm / thinking channel setup ---
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import delta as emit_delta_fn, get_comm
    except Exception:
        emit_delta_fn = None
        def get_comm():
            return None

    comm = get_comm()

    # Unique-ish, human-readable agent name
    if objective and objective.strip():
        agent_name = f"web search for {objective.strip()}"
    else:
        joined = ", ".join(q_list)
        agent_name = f"web search for {joined}" if joined else "web search"
    agent_name = agent_name[:120]  # keep it bounded

    artifact_thinking = "Web Search Trace"
    think_idx = 0
    finish_thinking_is_sent = False

    async def emit_thinking(text: str, completed: bool = False, **kwargs):
        """Wrapper to emit thinking deltas."""
        nonlocal think_idx
        nonlocal finish_thinking_is_sent
        if not (emit_delta_fn and comm):
            return
        if not text and not completed:
            return

        await emit_delta_fn(
            text=text,
            index=think_idx,
            marker="thinking",
            agent=agent_name,
            format="markdown",
            artifact_name=artifact_thinking,
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
        if think_idx:
            await emit_thinking("", completed=True)
        finish_thinking_is_sent = True

    # ---- DDGS search (non-blocking via to_thread) ----
    n = max(1, min(int(n), 20))
    per_query_max = n

    try:
        search_backend = get_search_backend()
    except SearchBackendError:
        logger.exception("web_search: failed to init backend; falling back to DDG")
        search_backend = get_search_backend("duckduckgo")

    provider_name = (
            getattr(search_backend, "provider", None)
            or getattr(search_backend, "name", None)
            or "unknown"
    )

    # Decide external pipeline defaults based on backend capability flags
    reconciling = bool(getattr(search_backend, "default_use_external_reconciler", True))
    # You said: keep refinement in both backends (external refinement stays available)
    # We treat this as "do_segment path still allowed".
    use_external_refinement = bool(getattr(search_backend, "default_use_external_refinement", True))

    per_query_results: List[List[Dict[str, Any]]] = await search_backend.search_many(
        q_list,
        per_query_max=per_query_max,
        freshness=freshness,
        country=country,
        safesearch=safesearch,
        concurrency=8,
    )
    if not per_query_results:
        # Nothing found: avoid LLM reconciliation + content fetch.
        base = _claim_sid_block(0)  # no-op but consistent
        if not finish_thinking_is_sent:
            await finish_thinking()
        return []

    # Wrap each result list in an iterator so we can round-robin them
    streams = [iter(res) for res in per_query_results]

    unique: Dict[str, Dict[str, Any]] = {}
    exhausted = [False] * len(streams)
    cycles = itertools.cycle(range(len(streams)))

    while len(unique) < n and not all(exhausted):
        idx = next(cycles)
        if exhausted[idx]:
            continue
        try:
            hit = next(streams[idx])
        except StopIteration:
            exhausted[idx] = True
            continue

        url = _normalize_url(hit.get("url", ""))
        if not url:
            continue
        if url in unique:
            if not unique[url].get("title") and hit.get("title"):
                unique[url]["title"] = hit["title"]
            if not unique[url].get("text") and hit.get("body"):
                unique[url]["text"] = hit["body"]
            if not unique[url].get("provider") and hit.get("vendor"):
                unique[url]["provider"] = hit.get("vendor")
            continue

        unique[url] = {
            "title": hit.get("title", ""),
            "url": url,
            "text": hit.get("body", ""),
            "provider": hit.get("vendor") or provider_name,
        }


    # ---- Build raw unique rows (no global SIDs yet) ----
    rows = []
    for (idx, (_url, row)) in enumerate(unique.items(), 1):
        # Ephemeral SIDs
        rows.append({
            "sid": idx,  # ephemeral
            "title": row["title"],
            "url": row["url"],
            "text": row["text"],
            "provider": row.get("provider") or provider_name,
        })
        if len(rows) >= n:
            break

    if not reconciling:
        # No snippet-based LLM reconciliation.
        # We still want content fetch + content-based filtering/segmentation.
        reconciled_rows = rows[:]  # keep ephemeral for now
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
        try:
            kept = json.loads(recon_json) if recon_json else []
        except Exception:
            logger.exception("web_search: invalid reconciler JSON; returning raw rows")
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

    # ---- Optional content fetch/filter, still using ephemeral sids ----
    filtered_rows = reconciled_rows
    new_rows = filtered_rows
    if refinement != "none" and fetch_content and reconciled_rows and use_external_refinement:
        fetched_rows = await fetch_search_results_content(search_results=reconciled_rows,
                                                          max_content_length=-1,
                                                          extraction_mode="custom")
        new_rows = fetched_rows
        if fetched_rows and len(fetched_rows) > 1:
            new_rows = copy.deepcopy(fetched_rows)
            new_rows = await filter_search_results_by_content(_SERVICE=_SERVICE,
                                                              objective=objective,
                                                              queries=q_list,
                                                              search_results=new_rows,
                                                              do_segment=True,
                                                              mode=refinement,
                                                              on_thinking_fn=emit_thinking,
                                                              thinking_budget=WEB_SEARCH_AGENTIC_THINKING_BUDGET)

    # ---- FINALIZE: claim only the number we actually return; remap ephemeral → global ----
    final_rows = new_rows or []
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
    if should_emit_html and emit_delta_fn and comm:
        html_view = compose_search_results_html(
            objective=objective or "",
            queries=q_list,
            initial_rows=rows,          # raw initial results (ephemeral sids)
            reconciled_rows=final_rows, # global SIDs
            filtered_rows=final_rows,
            title="Web Search Results",
        )
        artifact_html = "Web Search Results"
        html_idx = 0
        await emit_delta_fn(
            html_view,
            index=html_idx,
            marker="tool",
            agent=agent_name,
            format="html",
            artifact_name=artifact_html,
        )
        html_idx += 1
        await emit_delta_fn(
            "",
            completed=True,
            index=html_idx,
            marker="tool",
            agent=agent_name,
            format="html",
            artifact_name=artifact_html,
        )

    # tag authority for downstream
    final_rows = [
        {**f, "authority": "web", "provider": f.get("provider") or provider_name}
        for f in final_rows
    ]
    return final_rows