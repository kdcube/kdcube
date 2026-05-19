# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import pathlib
import logging
from typing import Any, Dict, List, Optional, Callable

from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    dedupe_sources_by_url,
    extract_citation_sids_any,
    extract_local_paths_any,
    normalize_sources_any,
    normalize_url,
    _get_physical_path,
)
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_physical_artifact_path,
    split_logical_artifact_path,
    split_physical_artifact_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import hydrate_workspace_paths

logger = logging.getLogger(__name__)

SOURCE_TEXT_FIELDS = ("content", "text", "snippet", "summary", "preview")


def _source_text_len(row: Dict[str, Any], key: str) -> int:
    val = row.get(key)
    return len(val) if isinstance(val, str) else 0


def _brief(value: Any, *, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def build_sources_pool_items_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compact, structured stats for source-row lists.

    This is intentionally metadata only: it helps the model decide whether it
    has full source content without replacing the source rows themselves.
    """
    pool = [r for r in (rows or []) if isinstance(r, dict)]
    items: List[Dict[str, Any]] = []
    total_content_symbols = 0
    total_text_symbols = 0
    total_size_bytes = 0
    content_rows = 0

    for row in pool:
        content_symbols = _source_text_len(row, "content")
        text_symbols = _source_text_len(row, "text")
        total_content_symbols += content_symbols
        total_text_symbols += text_symbols
        if content_symbols:
            content_rows += 1

        size_bytes = row.get("size_bytes")
        if isinstance(size_bytes, (int, float)) and size_bytes > 0:
            total_size_bytes += int(size_bytes)

        item: Dict[str, Any] = {
            "sid": row.get("sid"),
            "source_type": row.get("source_type") or "",
            "mime": row.get("mime") or "",
            "title": _brief(row.get("title") or row.get("name") or ""),
            "url": _brief(row.get("url") or row.get("artifact_path") or row.get("physical_path") or "", limit=260),
            "has_content": bool(content_symbols),
            "content_symbols": content_symbols,
            "text_symbols": text_symbols,
            "fields": sorted([str(k) for k in row.keys()]),
        }
        content_length = row.get("content_length")
        if isinstance(content_length, (int, float)):
            item["content_length"] = int(content_length)
        if isinstance(size_bytes, (int, float)):
            item["size_bytes"] = int(size_bytes)
        items.append(item)

    return {
        "kind": "sources_pool",
        "items_count": len(pool),
        "sids": [r.get("sid") for r in pool],
        "content_rows": content_rows,
        "total_content_symbols": total_content_symbols,
        "total_text_symbols": total_text_symbols,
        "total_size_bytes": total_size_bytes,
        "items": items,
    }


def _log_render_assets(ctx_browser: Any, message: str, *, level: str = "INFO") -> None:
    try:
        log = getattr(ctx_browser, "log", None)
        if log and hasattr(log, "log"):
            log.log(message, level=level)
            return
    except Exception:
        pass
    try:
        getattr(logger, level.lower())(message)
    except Exception:
        pass


def _bump_sources_pool_next_sid(pool: List[Dict[str, Any]]) -> None:
    try:
        mx = max(int(s.get("sid") or 0) for s in pool if isinstance(s, dict))
        SOURCE_ID_CV.set({"next": int(mx) + 1})
    except Exception:
        pass


def _sources_pool_key(row: Dict[str, Any]) -> str:
    url = normalize_url(row.get("url", ""))
    physical_path = _get_physical_path(row)
    if physical_path:
        return f"local:{physical_path}"
    return url


def merge_sources_pool_with_map(
    *,
    prior: List[Dict[str, Any]],
    new: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Dict[int, int]]:
    """
    Merge sources with dedupe and return a sid mapping for the new sources.
    """
    prior_rows = normalize_sources_any(prior)
    new_rows = normalize_sources_any(new)
    merged = dedupe_sources_by_url(prior_rows, new_rows)
    if not new_rows:
        return merged, {}

    by_key: Dict[str, int] = {}
    for row in merged:
        if not isinstance(row, dict):
            continue
        key = _sources_pool_key(row)
        if not key:
            continue
        try:
            by_key[key] = int(row.get("sid") or 0)
        except Exception:
            continue

    sid_map: Dict[int, int] = {}
    for row in new_rows:
        if not isinstance(row, dict):
            continue
        try:
            old_sid = int(row.get("sid") or 0)
        except Exception:
            continue
        if not old_sid:
            continue
        key = _sources_pool_key(row)
        if not key:
            continue
        new_sid = by_key.get(key)
        if new_sid:
            sid_map[old_sid] = int(new_sid)
    return merged, sid_map


def _is_sources_pool_allowed_mime(mime: str) -> bool:
    if not mime:
        return False
    mime = mime.strip().lower()
    return mime.startswith("image/")


def merge_sources_pool_for_attachment_rows(
    *,
    ctx_browser: Any,
    rows: List[Dict[str, Any]],
) -> None:
    if not rows:
        return
    allowed = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        mime = (r.get("mime") or "").strip()
        if not _is_sources_pool_allowed_mime(mime):
            continue
        allowed.append(r)
    if not allowed:
        return
    existing = list(ctx_browser.sources_pool or [])
    merged = dedupe_sources_by_url(existing, allowed)
    ctx_browser.set_sources_pool(sources_pool=merged)
    _bump_sources_pool_next_sid(merged)


def merge_sources_pool_for_file_rows(
    *,
    ctx_browser: Any,
    rows: List[Dict[str, Any]],
) -> None:
    if not rows:
        return
    new_rows = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        physical_path = (r.get("physical_path") or "").strip()
        if not physical_path:
            continue
        mime = (r.get("mime") or "").strip()
        if not _is_sources_pool_allowed_mime(mime):
            continue
        raw = r.get("raw") or {}
        url_val = (raw.get("hosted_uri") or raw.get("rn") or raw.get("key") or physical_path).strip()
        filename = (r.get("filename") or pathlib.Path(physical_path).name).strip()
        new = {
            "url": url_val or physical_path,
            "title": filename,
            "text": "",
            "source_type": "file",
            "mime": mime,
            "size_bytes": r.get("size_bytes"),
            "physical_path": physical_path,
            "artifact_path": (r.get("artifact_path") or "").strip(),
            "turn_id": (r.get("turn_id") or "").strip(),
        }
        if raw.get("rn"):
            new["rn"] = raw.get("rn")
        if raw.get("hosted_uri"):
            new["hosted_uri"] = raw.get("hosted_uri")
        if raw.get("key"):
            new["key"] = raw.get("key")
        new_rows.append(new)
    if not new_rows:
        return
    existing = list(ctx_browser.sources_pool or [])
    merged = dedupe_sources_by_url(existing, new_rows)
    ctx_browser.set_sources_pool(sources_pool=merged)
    _bump_sources_pool_next_sid(merged)


async def ensure_rendering_assets(
    *,
    ctx_browser: Any,
    tool_call_id: str,
    tool_id: str,
    content: Any,
    outdir: pathlib.Path,
    notice_fn: Optional[Callable[..., Any]] = None,
) -> None:
    if not isinstance(content, str) or not content.strip():
        return

    # Warn on missing SIDs and rehost SID-backed files if possible
    try:
        sids = extract_citation_sids_any(content)
    except Exception:
        sids = []
    if sids:
        try:
            pool = list(ctx_browser.sources_pool or [])
            pool_sids = {int(r.get("sid") or 0) for r in pool if isinstance(r, dict)}
            missing_sids = [sid for sid in sids if sid not in pool_sids]
            if missing_sids and notice_fn:
                notice_fn(
                    ctx_browser=ctx_browser,
                    tool_call_id=tool_call_id,
                    code="tool_call_warning.missing_sources",
                    message="Rendering content cites SIDs that are not in sources_pool.",
                    extra={"missing_sids": missing_sids, "tool_id": tool_id},
                )

            # If SIDs map to file/attachment sources, rehost them into OUT_DIR
            sid_rehost: List[str] = []
            by_sid = {int(r.get("sid") or 0): r for r in pool if isinstance(r, dict)}
            for sid in sids:
                row = by_sid.get(int(sid) if sid is not None else 0)
                if not isinstance(row, dict):
                    continue
                physical_path = (row.get("physical_path") or row.get("local_path") or "").strip()
                if not physical_path:
                    ap = (row.get("artifact_path") or "").strip()
                    if ap.startswith("fi:"):
                        tid, namespace, rel = split_logical_artifact_path(ap)
                        if tid and namespace and rel:
                            physical_path = build_physical_artifact_path(
                                turn_id=tid,
                                namespace=namespace,
                                relpath=rel,
                            )
                        else:
                            physical_path = ap[len("fi:"):].lstrip("/")
                tid, namespace, rel = split_physical_artifact_path(physical_path)
                if tid and namespace and rel:
                    sid_rehost.append(physical_path)
            if sid_rehost:
                rehost = await hydrate_workspace_paths(
                    ctx_browser=ctx_browser,
                    paths=sid_rehost,
                    outdir=outdir,
                )
                try:
                    rehosted = rehost.get("rehosted") or []
                    missing = rehost.get("missing") or []
                    if rehosted:
                        _log_render_assets(
                            ctx_browser,
                            f"[rendering.assets] rehosted (by SID): {rehosted}",
                            level="INFO",
                        )
                    if missing:
                        _log_render_assets(
                            ctx_browser,
                            f"[rendering.assets] missing (by SID): {missing}",
                            level="WARNING",
                        )
                except Exception:
                    pass
                missing = rehost.get("missing") or []
                if missing and notice_fn:
                    notice_fn(
                        ctx_browser=ctx_browser,
                        tool_call_id=tool_call_id,
                        code="tool_call_warning.missing_sid_assets",
                        message="Sources referenced by SID are missing local files.",
                        extra={"missing": missing, "tool_id": tool_id},
                    )
        except Exception:
            pass

    # Ensure local asset paths are available under OUT_DIR
    try:
        raw_paths = extract_local_paths_any(content)
    except Exception:
        raw_paths = []
    if not raw_paths:
        return
    rehost_paths: List[str] = []
    for p in raw_paths:
        if not p or not isinstance(p, str):
            continue
        if p.startswith(("/", "\\")):
            continue
        tid, namespace, rel = split_physical_artifact_path(p)
        if tid and namespace and rel:
            rehost_paths.append(p)
    if rehost_paths:
        rehost = await hydrate_workspace_paths(
            ctx_browser=ctx_browser,
            paths=rehost_paths,
            outdir=outdir,
        )
        try:
            rehosted = rehost.get("rehosted") or []
            missing = rehost.get("missing") or []
            if rehosted:
                _log_render_assets(
                    ctx_browser,
                    f"[rendering.assets] rehosted (by path): {rehosted}",
                    level="INFO",
                )
            if missing:
                _log_render_assets(
                    ctx_browser,
                    f"[rendering.assets] missing (by path): {missing}",
                    level="WARNING",
                )
        except Exception:
            pass
        missing = rehost.get("missing") or []
        if missing and notice_fn:
            notice_fn(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="tool_call_error.missing_assets",
                message="Rendering content references local assets that were not found.",
                extra={"missing": missing, "tool_id": tool_id},
            )
