# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/tools/backends/fetch_backends.py

from typing import List, Optional, Dict, Any, Annotated, Callable, Awaitable
import logging, json, os, uuid, base64, asyncio
from datetime import datetime, timezone

from kdcube_ai_app.apps.chat.sdk.tools.web.web_extractors import WebContentFetcher
from kdcube_ai_app.apps.chat.sdk.tools.web.with_llm import filter_fetch_results
from kdcube_ai_app.apps.chat.sdk.runtime.solution.widgets.fetch_url_contents import (
    FetchWebResourceWidget,
)
from kdcube_ai_app.apps.chat.sdk.tools.backends.web.inventory import _normalize_url
from kdcube_ai_app.apps.chat.sdk.tools.citations import enrich_sources_pool_with_favicons
from kdcube_ai_app.tools.content_type import fetch_url_with_content_type, guess_mime_from_url
from kdcube_ai_app.tools.scrap_utils import build_content_blocks_from_html
from kdcube_ai_app.infra.service_hub.multimodality import (
    MODALITY_IMAGE_MIME,
    MODALITY_DOC_MIME,
    MODALITY_MAX_IMAGE_BYTES,
    MODALITY_MAX_DOC_BYTES,
)

logger = logging.getLogger(__name__)

async def _materialize_binary_attachment(url: str, *, include_base64: bool = True) -> Dict[str, Any]:
    if not url:
        return {}
    try:
        loop = asyncio.get_running_loop()
        content_bytes, content_type, filename = await loop.run_in_executor(
            None, fetch_url_with_content_type, url
        )
    except Exception as exc:
        return {"error": str(exc)}

    mime = (content_type or "").split(";")[0].strip().lower()
    if not mime:
        mime = (guess_mime_from_url(url) or "").strip().lower()
    size_bytes = len(content_bytes or b"")
    base64_data = None
    if include_base64 and (mime in MODALITY_IMAGE_MIME or mime in MODALITY_DOC_MIME):
        limit = MODALITY_MAX_IMAGE_BYTES if mime in MODALITY_IMAGE_MIME else MODALITY_MAX_DOC_BYTES
        if size_bytes and size_bytes <= limit:
            base64_data = base64.b64encode(content_bytes).decode("ascii")
    return {
        "mime": mime or None,
        "base64": base64_data,
        "size_bytes": size_bytes or None,
        "filename": filename or None,
    }

def _normalize_widget_status(status: Optional[str]) -> str:
    raw = (status or "").lower()
    if raw in ("success", "archive", "binary"):
        return "success"
    if "timeout" in raw:
        return "timeout"
    if "paywall" in raw:
        return "paywall"
    return "error"

async def _fetch_urls_core(
    *,
    urls: List[str],
    max_content_length: int,
    use_archive_fallback: bool,
    extraction_mode: Optional[str],
    max_concurrent: int,
    include_binary_base64: bool,
    include_content_blocks: bool,
    objective: Optional[str] = None,
    content_block_titles: Optional[Dict[str, str]] = None,
    emit_delta_fn: Optional[Callable[..., Awaitable[None]]] = None,
    comm: Optional[object] = None,
    widget_agent: Optional[str] = None,
    widget_artifact_name: Optional[str] = None,
    widget_title: Optional[str] = None,
    namespaced_kv_cache: Any | None = None,
) -> Dict[str, Dict[str, Any]]:
    if not urls:
        return {}

    if emit_delta_fn is None or comm is None:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import delta as emit_delta_fn, get_comm
            comm = comm or get_comm()
        except Exception:
            emit_delta_fn = None
            comm = None

    widget = None
    widget_payload = None
    favicon_by_url: Dict[str, Dict[str, Any]] = {}
    url_items: Dict[str, Dict[str, Any]] = {}
    if emit_delta_fn and comm and widget_agent:
        widget = FetchWebResourceWidget(
            emit_delta=emit_delta_fn,
            agent=widget_agent,
            artifact_name=widget_artifact_name or "web_fetch.results",
            title=widget_title or "Fetch Results",
        )
        widget_payload = {}
        if objective:
            widget_payload["objective"] = objective
        widget_payload["urls"] = [{"url": u} for u in urls]
        url_items = {item["url"]: item for item in widget_payload["urls"]}
        favicon_rows = [
            {
                "url": u,
                "title": (content_block_titles or {}).get(u) or "",
            }
            for u in urls
        ]
        await enrich_sources_pool_with_favicons(
            favicon_rows,
            log=logger,
            cache=namespaced_kv_cache,
        )
        for row in favicon_rows:
            url = row.get("url") or ""
            key = _normalize_url(url)
            if not key:
                continue
            if "favicon" in row or "favicon_status" in row:
                favicon_by_url[key] = {
                    "favicon": row.get("favicon"),
                    "favicon_status": row.get("favicon_status"),
                    "title": row.get("title"),
                }
        if favicon_by_url:
            for url, item in url_items.items():
                key = _normalize_url(url)
                cached = favicon_by_url.get(key)
                if cached:
                    item["favicon"] = cached.get("favicon")
                    item["favicon_status"] = cached.get("favicon_status")
        await widget.send(widget_payload)

    async with WebContentFetcher(
            timeout=15,
            max_concurrent=max_concurrent,
            enable_archive=use_archive_fallback,
            extraction_mode=extraction_mode,
    ) as fetcher:
        fetch_list = await fetcher.fetch_multiple(
            urls=urls,
            max_length=max_content_length,
            include_raw_html=include_content_blocks,
        )

    results: Dict[str, Dict[str, Any]] = {}

    for url, fetch_result in zip(urls, fetch_list):
        fetched_time_iso = datetime.now(timezone.utc).isoformat()
        if isinstance(fetch_result, Exception):
            results[url] = {
                "status": "error",
                "content": "",
                "content_length": 0,
                "source_type": "web",
                "fetched_time_iso": fetched_time_iso,
                "published_time_iso": None,
                "modified_time_iso": None,
                "date_method": None,
                "date_confidence": 0.0,
                "error": str(fetch_result),
            }
        else:
            status = fetch_result.get("status", "unknown")
            content = (fetch_result.get("content") or "").strip()

            entry: Dict[str, Any] = {
                "status": status,
                "content": content,
                "content_length": len(content),
                "source_type": "web",
                "fetched_time_iso": fetched_time_iso,
                "published_time_iso": fetch_result.get("published_time_iso"),
                "published_time_raw": fetch_result.get("published_time_raw"),
                "modified_time_iso": fetch_result.get("modified_time_iso"),
                "modified_time_raw": fetch_result.get("modified_time_raw"),
                "archive_snapshot_date": fetch_result.get("archive_snapshot_date"),
                "archive_snapshot_url": fetch_result.get("archive_snapshot_url"),
                "date_method": fetch_result.get("date_method"),
                "date_confidence": fetch_result.get("date_confidence", 0.0),
            }
            if include_content_blocks:
                entry["content_blocks"] = []
            if fetch_result.get("mime"):
                entry["mime"] = fetch_result.get("mime")

            if "error" in fetch_result and fetch_result.get("error"):
                entry["error"] = fetch_result.get("error")

            if status in ("non_html", "pdf_redirect"):
                attach = await _materialize_binary_attachment(
                    url,
                    include_base64=include_binary_base64,
                )
                if attach.get("mime"):
                    entry["mime"] = attach.get("mime")
                if attach.get("size_bytes") is not None:
                    entry["size_bytes"] = attach.get("size_bytes")
                if attach.get("filename"):
                    entry["filename"] = attach.get("filename")
                if attach.get("base64"):
                    entry["base64"] = attach.get("base64")
                    entry["status"] = "binary"
                if attach.get("error"):
                    entry["error"] = attach.get("error")
            elif status in ("success", "archive"):
                entry["mime"] = entry.get("mime") or "text/html"
                if include_content_blocks:
                    raw_html = fetch_result.get("raw_html") or ""
                    blocks = build_content_blocks_from_html(
                        post_url=url,
                        raw_html=raw_html,
                        title=(content_block_titles or {}).get(url) or "",
                    )
                    entry["content_blocks"] = blocks

            if favicon_by_url:
                key = _normalize_url(url)
                cached = favicon_by_url.get(key)
                if cached:
                    entry["favicon"] = cached.get("favicon")
                    entry["favicon_status"] = cached.get("favicon_status")

            results[url] = entry

        if favicon_by_url:
            key = _normalize_url(url)
            cached = favicon_by_url.get(key)
            if cached:
                result_entry = results.get(url)
                if isinstance(result_entry, dict):
                    result_entry.setdefault("favicon", cached.get("favicon"))
                    result_entry.setdefault("favicon_status", cached.get("favicon_status"))

        if widget and widget_payload:
            item = url_items.get(url)
            if item is not None:
                entry = results.get(url) or {}
                if favicon_by_url:
                    key = _normalize_url(url)
                    cached = favicon_by_url.get(key)
                    if cached and "favicon" not in item and "favicon_status" not in item:
                        item["favicon"] = cached.get("favicon")
                        item["favicon_status"] = cached.get("favicon_status")
                if entry.get("status"):
                    item["status_orig"] = entry.get("status")
                item["status"] = _normalize_widget_status(entry.get("status"))
                if entry.get("mime"):
                    item["mime"] = entry.get("mime")
                content_length = entry.get("content_length")
                if content_length is None:
                    content_length = entry.get("size_bytes")
                if content_length is not None:
                    item["content_length"] = content_length
                if entry.get("published_time_iso"):
                    item["published_time_iso"] = entry.get("published_time_iso")
                if entry.get("modified_time_iso"):
                    item["modified_time_iso"] = entry.get("modified_time_iso")
                await widget.send(widget_payload)

    return results

async def fetch_search_results_content(
        search_results: List[dict],
        max_content_length: int = 15000,
        use_archive_fallback: bool = False,
        extraction_mode: Optional[str]="custom",
        MAX_CONCURRENT_WEB_FETCHES: int = 5,
        include_binary_base64: bool = True,
        include_content_blocks: bool = True,
        namespaced_kv_cache: Any | None = None,
        widget_agent: Optional[str] = None,
        widget_artifact_name: Optional[str] = None,
        widget_title: Optional[str] = None,
) -> list[dict]:
    """
    Best-effort content materialization for search results.

    - Runs fetches concurrently with a cap (MAX_CONCURRENT_WEB_FETCHES).
    - DOES NOT drop items on failure: rows stay in the list.
      On failure, row["fetch_status"] and optional row["fetch_error"] are set,
      and 'content' is omitted / empty.
    """
    if not search_results:
        return search_results

    logger.info(f"Fetching content for {len(search_results)} sources")

    urls = [row.get("url", "") for row in search_results]

    fetch_results = await _fetch_urls_core(
        urls=urls,
        max_content_length=max_content_length,
        use_archive_fallback=use_archive_fallback,
        extraction_mode=extraction_mode,
        max_concurrent=MAX_CONCURRENT_WEB_FETCHES,
        include_binary_base64=include_binary_base64,
        include_content_blocks=include_content_blocks,
        objective=None,
        content_block_titles={row.get("url", ""): (row.get("title") or "") for row in search_results},
        namespaced_kv_cache=namespaced_kv_cache,
        widget_agent=widget_agent,
        widget_artifact_name=widget_artifact_name,
        widget_title=widget_title,
    )

    success_count = 0

    for row, url in zip(search_results, urls):
        fetch_result = fetch_results.get(url)
        # Default metadata in case of failure
        row.setdefault("fetch_status", "error")

        if not fetch_result:
            # Transport-level failure
            row["fetch_error"] = "fetch_failed"
            logger.debug(f"Fetch exception for {row.get('url')}: no result")
            continue

        fetch_status = fetch_result.get("status", "unknown")
        row["fetch_status"] = fetch_status
        if fetch_result.get("mime"):
            row["mime"] = fetch_result.get("mime")

        if fetch_status in ("non_html", "pdf_redirect", "binary"):
            if fetch_result.get("mime"):
                row["mime"] = fetch_result.get("mime")
            if fetch_result.get("size_bytes") is not None:
                row["size_bytes"] = fetch_result.get("size_bytes")
            if fetch_result.get("filename"):
                row["filename"] = fetch_result.get("filename")
            if fetch_result.get("base64"):
                row["base64"] = fetch_result.get("base64")
                row["fetch_status"] = "binary"
            if fetch_result.get("error"):
                row["fetch_error"] = fetch_result.get("error")
            continue

        if fetch_status != "success" and fetch_status != "archive":
            # Keep the row, just record the failure status + optional error
            if fetch_result.get("error"):
                row["fetch_error"] = fetch_result["error"]
            logger.debug(
                f"Fetch failed for {row.get('url')}: status={fetch_status}, "
                f"error={fetch_result.get('error')}"
            )
            continue

        # Successful fetch: attach content + metadata
        row["content"] = fetch_result.get("content", "")
        row["content_length"] = fetch_result.get("content_length", 0)
        if fetch_result.get("mime"):
            row["mime"] = fetch_result.get("mime")
        if not row.get("mime"):
            row["mime"] = "text/html"
        if include_content_blocks:
            row["content_blocks"] = fetch_result.get("content_blocks", [])

        for k in [
            "published_time_raw",
            "published_time_iso",
            "modified_time_raw",
            "modified_time_iso",
            "archive_snapshot_date",
            "archive_snapshot_url",
            "date_method",
            "date_confidence",
        ]:
            if k in fetch_result:
                row[k] = fetch_result.get(k)

        success_count += 1

    logger.info(
        f"Content fetch complete: {success_count}/{len(search_results)} successful "
        f"(others kept without 'content')"
    )

    return search_results

async def fetch_url_contents(
        _SERVICE,
        urls: Annotated[str, (
                "JSON array of absolute HTTP/HTTPS URLs you ALREADY KNOW, e.g. "
                '["https://example.com/article", "https://docs.vendor.com/page"]. '
                "This tool NEVER runs a search engine; it ONLY dereferences these URLs."
        )],
        max_content_length: Annotated[int, (
                "Maximum number of characters of cleaned article-like content to keep per URL "
                "(500–20000). Longer pages are truncated at a sentence boundary. Number <0 means 'no limit'"
        ), {"min": 500, "max": 20000}] = -1,
        use_archive_fallback: Annotated[bool, (
                "If true, then for pages that are blocked, paywalled, or error on direct fetch, "
                "also try an archive mirror (e.g. web.archive.org).",
        )] = False,
        include_binary_base64: Annotated[bool, (
                "If true, attach base64 for binary/image/PDF fetches when size limits allow."
        )] = True,
        include_content_blocks: Annotated[bool, (
                "If true, include ordered content blocks (text/image) derived from the page HTML."
        )] = False,
        extraction_mode: str = "custom",
        refinement="none",
        objective: Optional[str] = None,
        namespaced_kv_cache: Any | None = None,
) -> Annotated[dict, (
        "JSON object mapping each input URL to a result object, for example:\n"
        "{\n"
        '  "https://example.com/article": {\n'
        '    "status": "success|timeout|paywall|error|non_html|insufficient_content|archive|blocked_403|http_XXX|pdf_redirect",\n'
        '    "content": "<main text in Markdown-style or plain text>",\n'
        '    "content_length": 1234,\n'
        '    "published_time_iso": "2025-09-19T10:23:00+00:00" | null,\n'
        '    "modified_time_iso": "2025-09-20T11:00:00+00:00" | null,\n'
        '    "date_method": "<how the date was inferred>" | null,\n'
        '    "date_confidence": 0.0–1.0,\n'
        '    "content_blocks": [\n'
        '      {"type": "text", "text": "..."},\n'
        '      {"type": "image", "url": "https://...", "alt": "...", "caption": "..."},\n'
        '      {"type": "image", "mime": "image/png", "base64": "...", "alt": "...", "caption": "..."}\n'
        '    ],\n'
        '    "error": "<error message if any>"\n'
        "  },\n"
        "  \"...\": { ... }\n"
        "}\n"
        "The `content` field is suitable to treat as Markdown for summarization / RAG."
)]:
    # SAFEGUARD: Handle if urls is accidentally passed as a list instead of string
    if isinstance(urls, (list, tuple)):
        logger.warning(
            f"web_fetch: received {type(urls).__name__} instead of string, converting to list"
        )
        url_list = [str(u).strip() for u in urls if str(u).strip()]
    else:
        # Original logic: expect a string (either JSON array or single URL)
        try:
            urls_str = str(urls or "").strip()
            if urls_str.startswith("["):
                # Parse as JSON array
                raw = json.loads(urls_str)
                url_list = [str(u).strip() for u in (raw or []) if str(u).strip()]
            else:
                # Single URL string
                url_list = [urls_str] if urls_str else []
        except json.JSONDecodeError as e:
            logger.warning(f"web_fetch: JSON parse failed: {e}, treating as single URL")
            url_list = [str(urls or "").strip()]
        except Exception as e:
            logger.warning(f"web_fetch: unexpected error: {e}, treating as single URL")
            url_list = [str(urls or "").strip()]

    # Deduplicate & keep only http/https
    seen: set[str] = set()
    normalized_urls: list[str] = []
    for u in url_list:
        if not u:
            continue
        if not (u.startswith("http://") or u.startswith("https://")):
            logger.debug(f"_web_fetch: skipping non-http(s) URL: {u}")
            continue
        if u in seen:
            continue
        seen.add(u)
        normalized_urls.append(u)

    if not normalized_urls:
        return {}

    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import delta as emit_delta_fn, get_comm
    except Exception:
        emit_delta_fn = None
        def get_comm():
            return None

    comm = get_comm()

    # Human-readable agent name
    if objective and objective.strip():
        agent_name = f"fetch url contents objective={objective.strip()}"
    else:
        joined = ", ".join(normalized_urls[:3])
        if len(normalized_urls) > 3:
            joined += ", ..."
        agent_name = f"fetch url contents urls={joined}" if joined else "fetch url contents"
    agent_label = agent_name[:120]
    agent_suffix = uuid.uuid4().hex[:8]
    max_label_len = max(1, 120 - (len(agent_suffix) + 3))
    agent_name = f"{agent_label[:max_label_len]} [{agent_suffix}]"

    results = await _fetch_urls_core(
        urls=normalized_urls,
        max_content_length=max_content_length,
        use_archive_fallback=use_archive_fallback,
        extraction_mode=extraction_mode,
        max_concurrent=5,
        include_binary_base64=include_binary_base64,
        include_content_blocks=include_content_blocks,
        objective=objective,
        emit_delta_fn=emit_delta_fn,
        comm=comm,
        widget_agent=agent_name,
        widget_artifact_name=f"Fetch URL Contents [{agent_suffix}]",
        widget_title=agent_label,
        namespaced_kv_cache=namespaced_kv_cache,
    )

    # ---------- Optional: objective-aware segmentation on fetched pages ----------
    # Only run if objective is non-empty and we have a service for the LLM backend.
    if objective and objective.strip() and refinement != "none" and _SERVICE:
        # Validate mode
        mode = refinement.lower()
        if mode not in ("balanced", "recall", "precision"):
            mode = "balanced"
        # Side effect function, changes `results` in place.
        await filter_fetch_results(
            _SERVICE=_SERVICE,
            results=results,
            objective=objective,
            mode=mode,
        )
    # ---------- Done ----------
    return results
