# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/tools/backends/fetch_backends.py

from typing import List, Optional, Dict, Any, Annotated
import logging, json, os, uuid

from kdcube_ai_app.apps.chat.sdk.tools.web.web_extractors import WebContentFetcher
from kdcube_ai_app.apps.chat.sdk.tools.web.with_llm import filter_fetch_results

logger = logging.getLogger(__name__)

async def fetch_search_results_content(
        search_results: List[dict],
        max_content_length: int = 15000,
        use_archive_fallback: bool = False,
        extraction_mode: Optional[str]="custom",
        MAX_CONCURRENT_WEB_FETCHES: int = 5,
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

    async with WebContentFetcher(
            timeout=15,
            max_concurrent=MAX_CONCURRENT_WEB_FETCHES,
            enable_archive=use_archive_fallback,
            extraction_mode=extraction_mode
    ) as fetcher:
        fetch_results = await fetcher.fetch_multiple(urls=urls, max_length=max_content_length)

    success_count = 0

    for row, fetch_result in zip(search_results, fetch_results):
        # Default metadata in case of failure
        row.setdefault("fetch_status", "error")

        if isinstance(fetch_result, Exception):
            # Transport-level failure
            row["fetch_error"] = str(fetch_result)
            logger.debug(f"Fetch exception for {row.get('url')}: {fetch_result}")
            continue

        fetch_status = fetch_result.get("status", "unknown")
        row["fetch_status"] = fetch_status

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
        extraction_mode: str = "custom",
        refinement="none",
        objective: Optional[str] = None,
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
        '    "error": "<error message if any>"\n'
        "  },\n"
        "  \"...\": { ... }\n"
        "}\n"
        "The `content` field is suitable to treat as Markdown for summarization / RAG."
)]:
    # SAFEGUARD: Handle if urls is accidentally passed as a list instead of string
    if isinstance(urls, (list, tuple)):
        logger.warning(
            f"fetch_url_contents: received {type(urls).__name__} instead of string, converting to list"
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
            logger.warning(f"fetch_url_contents: JSON parse failed: {e}, treating as single URL")
            url_list = [str(urls or "").strip()]
        except Exception as e:
            logger.warning(f"fetch_url_contents: unexpected error: {e}, treating as single URL")
            url_list = [str(urls or "").strip()]

    # Deduplicate & keep only http/https
    seen: set[str] = set()
    normalized_urls: list[str] = []
    for u in url_list:
        if not u:
            continue
        if not (u.startswith("http://") or u.startswith("https://")):
            logger.debug(f"_fetch_url_contents: skipping non-http(s) URL: {u}")
            continue
        if u in seen:
            continue
        seen.add(u)
        normalized_urls.append(u)

    if not normalized_urls:
        return {}

    # ==== thinking / comm setup (parallel to web_search) ====
    # If this env var is >0, we let the LLM agent burn that budget internally
    # and avoid extra heuristic thinking text here (same pattern as web_search).
    FETCH_URL_AGENTIC_THINKING_BUDGET = int(os.getenv("FETCH_URL_AGENTIC_THINKING_BUDGET") or 0)

    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import delta as emit_delta_fn, get_comm
    except Exception:
        emit_delta_fn = None
        def get_comm():
            return None

    comm = get_comm()

    # Human-readable agent name
    if objective and objective.strip():
        agent_name = f"fetch url contents for {objective.strip()}"
    else:
        joined = ", ".join(normalized_urls[:3])
        if len(normalized_urls) > 3:
            joined += ", ..."
        agent_name = f"fetch url contents for {joined}" if joined else "fetch url contents"
    agent_label = agent_name[:120]
    agent_suffix = uuid.uuid4().hex[:8]
    max_label_len = max(1, 120 - (len(agent_suffix) + 3))
    agent_name = f"{agent_label[:max_label_len]} [{agent_suffix}]"

    artifact_thinking = "Fetch URL Contents Trace"
    think_idx = 0

    marker = "timeline_text" # "thinking"
    async def emit_progress(text: str, completed: bool = False, **kwargs):
        """Wrapper to emit thinking deltas."""
        nonlocal think_idx
        if not (emit_delta_fn and comm):
            return
        if not text and not completed:
            return

        await emit_delta_fn(
            text=text,
            index=think_idx,
            marker=marker,
            agent=agent_name,
            title=agent_label,
            format="markdown",
            artifact_name=artifact_thinking,
            completed=completed,
            **kwargs
        )
        if text or completed:  # Only increment if we actually emitted something
            think_idx += 1

    async def finish_thinking():
        """Signal thinking completion."""
        await emit_progress("", completed=True)

    # Initial trace, only when we are not delegating to an agent with its own budget
    if not FETCH_URL_AGENTIC_THINKING_BUDGET:
        initial_lines: list[str] = ["### URL content fetch"]
        if objective and objective.strip():
            initial_lines.append("")
            initial_lines.append(f"- Objective: {objective.strip()}")
        initial_lines.append("")
        initial_lines.append("- URLs to fetch:")
        for u in normalized_urls[:10]:
            initial_lines.append(f"  - `{u}`")
        await emit_progress("\n".join(initial_lines))

    results: Dict[str, Dict[str, Any]] = {}

    # ---- fetch all URLs concurrently, NO SEARCH ----
    async with WebContentFetcher(
            timeout=15,
            max_concurrent=5,
            enable_archive=use_archive_fallback,
            extraction_mode=extraction_mode,
    ) as fetcher:
        fetch_list = await fetcher.fetch_multiple(
            urls=normalized_urls,
            max_length=max_content_length,
        )

    for url, fetch_result in zip(normalized_urls, fetch_list):
        if isinstance(fetch_result, Exception):
            # Transport-level failure
            results[url] = {
                "status": "error",
                "content": "",
                "content_length": 0,
                "published_time_iso": None,
                "modified_time_iso": None,
                "date_method": None,
                "date_confidence": 0.0,
                "error": str(fetch_result),
            }
            continue

        status = fetch_result.get("status", "unknown")
        content = (fetch_result.get("content") or "").strip()

        entry: Dict[str, Any] = {
            "status": status,
            "content": content,
            "content_length": len(content),
            "published_time_iso": fetch_result.get("published_time_iso"),
            "modified_time_iso": fetch_result.get("modified_time_iso"),
            "date_method": fetch_result.get("date_method"),
            "date_confidence": fetch_result.get("date_confidence", 0.0),
        }

        if "error" in fetch_result and fetch_result.get("error"):
            entry["error"] = fetch_result.get("error")

        results[url] = entry

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
            on_progress_fn=emit_progress if (emit_delta_fn and comm) else None,
            thinking_budget=FETCH_URL_AGENTIC_THINKING_BUDGET,
        )

    if not FETCH_URL_AGENTIC_THINKING_BUDGET:
        # Short completion marker with a quick summary
        success_count = sum(
            1
            for r in results.values()
            if r.get("status") in ("success", "archive")
        )
        lines = [
            "\n",
            "### URL fetch complete",
            "",
            f"- Total URLs requested: {len(normalized_urls)}",
            f"- Successful/archived: {success_count}",
        ]
        await emit_progress("\n".join(lines))

    await finish_thinking()
    # ---------- Done ----------
    return results
