# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional, Callable

from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    dedupe_sources_by_url,
    extract_citation_sids_any,
    extract_local_paths_any,
)
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import rehost_files_from_timeline


def _bump_sources_pool_next_sid(pool: List[Dict[str, Any]]) -> None:
    try:
        mx = max(int(s.get("sid") or 0) for s in pool if isinstance(s, dict))
        SOURCE_ID_CV.set({"next": int(mx) + 1})
    except Exception:
        pass


def merge_sources_pool_for_attachment_rows(
    *,
    ctx_browser: Any,
    rows: List[Dict[str, Any]],
) -> None:
    if not rows:
        return
    existing = list(ctx_browser.sources_pool or [])
    merged = dedupe_sources_by_url(existing, rows)
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
        raw = r.get("raw") or {}
        url_val = (raw.get("hosted_uri") or raw.get("rn") or raw.get("key") or physical_path).strip()
        filename = (r.get("filename") or pathlib.Path(physical_path).name).strip()
        new = {
            "url": url_val or physical_path,
            "title": filename,
            "text": "",
            "source_type": "file",
            "mime": (r.get("mime") or "").strip(),
            "size_bytes": r.get("size_bytes"),
            "local_path": physical_path,
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
                local_path = (row.get("local_path") or "").strip()
                if not local_path:
                    ap = (row.get("artifact_path") or "").strip()
                    if ap.startswith("fi:") and ".files/" in ap:
                        tid, rel = ap.split(".files/", 1)
                        local_path = f"{tid[3:]}/files/{rel}" if tid.startswith("fi:") else ""
                    elif ap.startswith("fi:") and ".user.attachments/" in ap:
                        tid, rel = ap.split(".user.attachments/", 1)
                        local_path = f"{tid[3:]}/attachments/{rel}" if tid.startswith("fi:") else ""
                if local_path and local_path.startswith("turn_") and ("/files/" in local_path or "/attachments/" in local_path):
                    sid_rehost.append(local_path)
            if sid_rehost:
                rehost = await rehost_files_from_timeline(
                    ctx_browser=ctx_browser,
                    paths=sid_rehost,
                    outdir=outdir,
                )
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
        if p.startswith("turn_") and ("/files/" in p or "/attachments/" in p):
            rehost_paths.append(p)
    if rehost_paths:
        rehost = await rehost_files_from_timeline(
            ctx_browser=ctx_browser,
            paths=rehost_paths,
            outdir=outdir,
        )
        missing = rehost.get("missing") or []
        if missing and notice_fn:
            notice_fn(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="tool_call_error.missing_assets",
                message="Rendering content references local assets that were not found.",
                extra={"missing": missing, "tool_id": tool_id},
            )
