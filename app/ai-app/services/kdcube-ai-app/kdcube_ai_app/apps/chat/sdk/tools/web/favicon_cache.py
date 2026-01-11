# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/web/favicon_cache.py

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_UTM_PARAMS = {"utm_source", "utm_medium", "utm_campaign","utm_term","utm_content","utm_id","gclid","fbclid"}


def normalize_url(u: str) -> str:
    try:
        if not u:
            return ""
        s = urlsplit(u.strip())
        scheme = (s.scheme or "https").lower()
        netloc = s.netloc.lower().rstrip(":80").rstrip(":443")
        path = s.path or "/"
        fragment = ""
        q = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True) if k.lower() not in _UTM_PARAMS]
        query = urlencode(q, doseq=True)
        if path != "/" and path.endswith("/"):
            path = path[:-1]
        return urlunsplit((scheme, netloc, path, query, fragment))
    except Exception:
        return (u or "").strip()


def _favicon_cache_key(url: str) -> str:
    url_norm = normalize_url(url) if url else ""
    if not url_norm:
        return ""
    digest = hashlib.sha256(url_norm.encode("utf-8")).hexdigest()
    return f"favicon:{digest}"


async def enrich_sources_pool_with_favicons(
        sources_pool: List[Dict[str, Any]],
        log,
        *,
        cache: Any = None,
        cache_ttl_seconds: Optional[int] = None,
) -> int:
    """
    Enrich sources_pool with favicons in-place (FAST batch operation).

    Uses the shared module-level AsyncLinkPreview instance automatically.
    No need to pass or manage instances - it's handled transparently.

    - Single HTTP session for all requests (5-10x faster than individual)
    - Only processes sources without existing 'favicon' key (idempotent)
    - Updates sources_pool list in-place
    - Returns count of newly enriched sources
    """
    if not sources_pool:
        return 0

    # Find sources that need enrichment
    to_enrich = []
    url_to_sources: Dict[str, List[Dict[str, Any]]] = {}

    for src in sources_pool:
        if not isinstance(src, dict):
            continue
        if "favicon" in src:  # Already enriched
            continue
        url = (src.get("url") or "").strip()
        if url and (url.startswith("http://") or url.startswith("https://")):
            to_enrich.append(url)
            url_to_sources.setdefault(url, []).append(src)

    if not to_enrich:
        log.debug("enrich_favicons: all sources already enriched")
        return 0

    cached_results: Dict[str, Dict[str, Any]] = {}
    if cache is not None:
        cache_keys = []
        url_for_key = []
        for url in to_enrich:
            cache_key = _favicon_cache_key(url)
            if not cache_key:
                continue
            cache_keys.append(cache_key)
            url_for_key.append(url)
        if cache_keys:
            cached_list = await cache.mget_json(cache_keys)
            for url, cached in zip(url_for_key, cached_list):
                if isinstance(cached, dict):
                    cached_results[url] = cached

    if cached_results:
        for url, cached in cached_results.items():
            for src in url_to_sources.get(url, []):
                if cached.get("success"):
                    src["favicon"] = cached.get("favicon")
                    src["favicon_status"] = "success"
                    if not src.get("title") and cached.get("title"):
                        src["title"] = cached["title"]
                else:
                    src["favicon"] = None
                    src["favicon_status"] = cached.get("error", "failed")

        to_enrich = [u for u in to_enrich if u not in cached_results]
        if not to_enrich:
            log.debug("enrich_favicons: cache hit for all sources")
            return len(cached_results)

    log.info(f"enrich_favicons: batch enriching {len(to_enrich)}/{len(sources_pool)} sources")

    # Import and get shared instance
    try:
        from kdcube_ai_app.infra.rendering.link_preview import get_shared_link_preview
    except ImportError:
        log.warning("enrich_favicons: link_preview module not available, skipping")
        return 0

    try:
        # Get the shared instance (lazy-initialized on first call)
        preview = await get_shared_link_preview()

        # BATCH FETCH - single HTTP session for all URLs (FAST!)
        results_map = await preview.generate_preview_batch(
            urls=to_enrich,
            mode="minimal"
        )

        # Update sources in-place
        enriched_count = 0
        cache_payload: Dict[str, Dict[str, Any]] = {}
        for url, result in results_map.items():
            sources = url_to_sources.get(url) or []
            if not sources:
                continue

            success = bool(result.get("success"))
            for src in sources:
                if success:
                    src["favicon"] = result.get("favicon")
                    src["favicon_status"] = "success"
                    # Optionally improve title
                    if not src.get("title") and result.get("title"):
                        src["title"] = result["title"]
                    enriched_count += 1
                else:
                    src["favicon"] = None
                    src["favicon_status"] = result.get("error", "failed")

            cache_key = _favicon_cache_key(url)
            if cache_key:
                cache_payload[cache_key] = {
                    "success": success,
                    "favicon": result.get("favicon"),
                    "title": result.get("title"),
                    "error": result.get("error"),
                }

        if cache is not None and cache_payload:
            await cache.set_many_json(cache_payload, ttl_seconds=cache_ttl_seconds)

        log.info(f"enrich_favicons: completed {enriched_count}/{len(to_enrich)} successful")
        return enriched_count

    except Exception:
        log.exception("enrich_favicons: failed")
        return 0
