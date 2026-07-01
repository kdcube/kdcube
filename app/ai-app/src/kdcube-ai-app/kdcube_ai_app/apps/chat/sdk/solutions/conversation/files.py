# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Materialize `fi:` file artifacts referenced by conversation turns.

Turns reference file artifacts (uploaded attachments, produced outputs, snapshots,
and pulled external attachments) as `fi:` logical paths — both as search-result
handles and inside turn text (e.g. working summaries). External clients reach them
through the `conv` namespace as `conv:fi:<path>` object refs.

This module owns the byte materialization: it reuses the proven react workspace
hydration (`rehost_files_from_timeline`) to pull one artifact into a temp dir and
read its bytes, then the caller shapes it into a JSON object (text inline / bounded
base64) or a streamed result. The hydration is user-scoped through the browser's
runtime_ctx, so a caller only materializes their own conversations' files.
"""

from __future__ import annotations

import logging
import mimetypes
import pathlib
import tempfile
from typing import Any, Dict

LOGGER = logging.getLogger("kdcube.sdk.conversation.files")

# Max bytes read into memory for a single object.get file materialization. Larger
# files are reported with metadata only (the byte stream stays for react.pull).
MAX_INLINE_FILE_BYTES = 5 * 1024 * 1024


def _guess_mime(name: str) -> str:
    guess, _ = mimetypes.guess_type(name)
    return (guess or "").strip() or "application/octet-stream"


async def materialize_fi_artifact(
    *,
    browser: Any,
    fi_ref: str,
    conversation_id: str = "",
    max_bytes: int = MAX_INLINE_FILE_BYTES,
) -> Dict[str, Any]:
    """Materialize a single `fi:` artifact to bytes.

    `browser` is a react `ContextBrowser` bound to the caller's identity (its
    runtime_ctx scopes the read). `fi_ref` is a `fi:<...>` logical path.

    Returns one of:
      {"ok": True, "filename", "mime", "size", "data": bytes, "physical_path"}
      {"ok": False, "reason": "unresolvable_ref" | "not_found" | "too_large" | "error",
       "detail": {...}}
    """
    from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import _infer_physical_from_fi
    from kdcube_ai_app.apps.chat.sdk.solutions.react.solution_workspace import rehost_files_from_timeline

    ref = str(fi_ref or "").strip()
    if not ref:
        return {"ok": False, "reason": "unresolvable_ref"}
    physical = _infer_physical_from_fi(ref)
    conv = str(conversation_id or "").strip()
    browser_conv = str(getattr(getattr(browser, "runtime_ctx", None), "conversation_id", "") or "").strip()
    LOGGER.info(
        "[conversation.files] materialize ref=%s physical=%s conversation_id=%s browser_conversation_id=%s",
        ref, physical, conv or "<none>", browser_conv or "<none>",
    )
    if not physical:
        LOGGER.warning("[conversation.files] unresolvable_ref ref=%s", ref)
        return {"ok": False, "reason": "unresolvable_ref", "detail": {"ref": ref}}

    with tempfile.TemporaryDirectory(prefix="conv_fi_") as td:
        outdir = pathlib.Path(td)
        try:
            result = await rehost_files_from_timeline(
                ctx_browser=browser,
                paths=[physical],
                outdir=outdir,
                conversation_id=conv or None,
            )
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("[conversation.files] rehost raised ref=%s", ref)
            return {"ok": False, "reason": "error", "detail": {"error": str(exc)}}

        rehosted = list(result.get("rehosted") or []) if isinstance(result, dict) else []
        missing = list(result.get("missing") or []) if isinstance(result, dict) else []
        errors = list(result.get("errors") or []) if isinstance(result, dict) else []

        # Collect concrete files written under the temp dir. rehost reports physical
        # paths; a directory-shaped ref expands to several files, so scan to be safe.
        files = [p for p in outdir.rglob("*") if p.is_file()]
        LOGGER.info(
            "[conversation.files] rehost ref=%s physical=%s rehosted=%s files_found=%s missing=%s errors=%s",
            ref, physical, len(rehosted), len(files), missing, errors,
        )
        if not files:
            LOGGER.warning(
                "[conversation.files] not_found ref=%s physical=%s conversation_id=%s missing=%s errors=%s",
                ref, physical, conv or "<none>", missing, errors,
            )
            return {"ok": False, "reason": "not_found",
                    "detail": {"physical_path": physical, "conversation_id": conv, "missing": missing, "errors": errors}}

        # Single-file materialization: pick the file whose name matches the ref's
        # leaf when possible, else the first (smallest surprise for exact-file refs).
        leaf = pathlib.PurePosixPath(physical).name
        target = next((p for p in files if p.name == leaf), files[0])

        size = target.stat().st_size
        if size > max_bytes:
            return {
                "ok": False,
                "reason": "too_large",
                "detail": {"filename": target.name, "mime": _guess_mime(target.name), "size": size},
            }
        data = target.read_bytes()
        return {
            "ok": True,
            "filename": target.name,
            "mime": _guess_mime(target.name),
            "size": len(data),
            "data": data,
            "physical_path": str(target.relative_to(outdir)),
        }


def is_text_mime(mime: str) -> bool:
    m = str(mime or "").strip().lower()
    if m.startswith("text/"):
        return True
    return m in {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "application/javascript",
        "application/x-ndjson",
        "image/svg+xml",
    } or m.endswith("+json") or m.endswith("+xml") or m.endswith("+yaml")


__all__ = [
    "MAX_INLINE_FILE_BYTES",
    "materialize_fi_artifact",
    "is_text_mime",
]
