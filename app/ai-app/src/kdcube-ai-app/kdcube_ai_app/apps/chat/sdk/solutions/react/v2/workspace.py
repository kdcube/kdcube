# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Dict, List


WORKSPACE_IMPLEMENTATION_CUSTOM = "custom"
WORKSPACE_IMPLEMENTATION_GIT = "git"

_TEXT_MIMES = {
    "text/plain", "text/markdown", "text/x-markdown", "text/html", "text/css",
    "text/csv", "text/tab-separated-values", "text/xml",
    "application/json", "application/xml",
    "application/yaml", "application/x-yaml",
    "application/javascript", "application/x-javascript",
    "application/x-python", "text/x-python",
    "application/sql", "text/x-sql",
}

_CODE_PATH_RE = re.compile(r"(turn_[A-Za-z0-9_]+/(files|attachments)/[^\s'\"\)\];,]+)")
_REL_FILES_RE = re.compile(r"(?<![A-Za-z0-9_])files/[^\s'\"\)\];,]+")
_REL_ATTACHMENTS_RE = re.compile(r"(?<![A-Za-z0-9_])attachments/[^\s'\"\)\];,]+")
_FETCH_CTX_PATH_RE = re.compile(r"([a-z]{2}:[A-Za-z0-9_./\\-]+)")
_TURN_ROOT_RE = re.compile(r"\b(turn_[A-Za-z0-9_]+)\b")


def normalize_workspace_implementation(value: Any) -> str:
    raw = str(value or WORKSPACE_IMPLEMENTATION_CUSTOM).strip().lower().replace("-", "_")
    if raw in {WORKSPACE_IMPLEMENTATION_GIT, "workspace_git"}:
        return WORKSPACE_IMPLEMENTATION_GIT
    return WORKSPACE_IMPLEMENTATION_CUSTOM


def get_workspace_implementation(runtime_ctx: Any | None) -> str:
    return normalize_workspace_implementation(getattr(runtime_ctx, "workspace_implementation", None))


def extract_code_file_paths(code: str, *, turn_id: str = "") -> tuple[List[str], List[str]]:
    """
    Return (paths, rewritten_paths). Paths are physical (turn_id/files/rel).
    Relative "files/<rel>" are rewritten to current turn_id.
    """
    if not isinstance(code, str) or not code.strip():
        return [], []
    found = [m.group(1) for m in _CODE_PATH_RE.finditer(code)]
    rewritten: List[str] = []

    def _has_turn_prefix(start_idx: int) -> bool:
        if start_idx <= 0:
            return False
        prefix = code[max(0, start_idx - 64):start_idx]
        return bool(re.search(r"turn_[A-Za-z0-9_]+/$", prefix))

    for m in _REL_FILES_RE.finditer(code):
        raw = m.group(0)
        if _has_turn_prefix(m.start()):
            continue
        rewritten.append(f"{turn_id}/{raw}" if turn_id else raw)
    for m in _REL_ATTACHMENTS_RE.finditer(code):
        raw = m.group(0)
        if _has_turn_prefix(m.start()):
            continue
        rewritten.append(f"{turn_id}/{raw}" if turn_id else raw)

    cleaned: List[str] = []
    for p in found + rewritten:
        cleaned.append(p.rstrip(")];,"))

    seen = set()
    out: List[str] = []
    current_files_prefix = f"{turn_id}/files/" if turn_id else ""
    current_att_prefix = f"{turn_id}/attachments/" if turn_id else ""
    for p in cleaned:
        if p in seen:
            continue
        seen.add(p)
        if (current_files_prefix and p.startswith(current_files_prefix)) or (
            current_att_prefix and p.startswith(current_att_prefix)
        ):
            continue
        out.append(p)
    return out, rewritten


def extract_fetch_ctx_paths(code: str) -> List[str]:
    if not isinstance(code, str) or not code.strip():
        return []
    found = [m.group(1) for m in _FETCH_CTX_PATH_RE.finditer(code)]
    out: List[str] = []
    seen = set()
    for p in found:
        if not p or ":" not in p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def extract_workspace_turn_roots(code: str) -> List[str]:
    if not isinstance(code, str) or not code.strip():
        return []
    out: List[str] = []
    seen = set()
    for m in _TURN_ROOT_RE.finditer(code):
        turn_id = m.group(1)
        if not turn_id or turn_id in seen:
            continue
        seen.add(turn_id)
        out.append(turn_id)
    return out


def _safe_relpath(path_value: str) -> bool:
    try:
        p = pathlib.PurePosixPath(path_value)
        if path_value.startswith(("/", "\\")):
            return False
        if any(part == ".." for part in p.parts):
            return False
        return True
    except Exception:
        return False


def _infer_physical_from_fi(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        return ""
    p = path.strip()
    if p.startswith("fi:"):
        p = p[len("fi:"):]
    if ".files/" in p:
        tid, rel = p.split(".files/", 1)
        if tid and rel:
            return f"{tid}/files/{rel}"
    if ".user.attachments/" in p:
        tid, rel = p.split(".user.attachments/", 1)
        if tid and rel:
            return f"{tid}/attachments/{rel}"
    if ".attachments/" in p:
        tid, rel = p.split(".attachments/", 1)
        if tid and rel:
            return f"{tid}/attachments/{rel}"
    if p and _safe_relpath(p):
        return p
    return ""


def _guess_mime_from_path(path: str) -> str:
    try:
        import mimetypes
        guess, _ = mimetypes.guess_type(path)
        if guess:
            return guess.strip()
        ext = pathlib.Path(path).suffix.lower().lstrip(".")
        text_exts = {
            "md": "text/markdown",
            "markdown": "text/markdown",
            "txt": "text/plain",
            "rst": "text/plain",
            "yaml": "text/plain",
            "yml": "text/plain",
            "json": "application/json",
            "toml": "text/plain",
            "ini": "text/plain",
            "cfg": "text/plain",
            "py": "text/x-python",
            "js": "text/javascript",
            "ts": "text/plain",
            "tsx": "text/plain",
            "jsx": "text/plain",
            "html": "text/html",
            "css": "text/css",
            "sh": "text/x-shellscript",
            "mmd": "text/plain",
            "mermaid": "text/plain",
        }
        return text_exts.get(ext, "")
    except Exception:
        return ""


def _is_text_mime(m: str | None) -> bool:
    m = (m or "").lower().strip()
    if m in _TEXT_MIMES:
        return True
    return m.startswith("text/")


def workspace_lineage_segments(runtime_ctx: Any) -> Dict[str, str]:
    def _seg(value: Any, fallback: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raw = fallback
        raw = raw.replace("/", "_")
        raw = re.sub(r"[^A-Za-z0-9._@-]+", "_", raw)
        return raw.strip("._-") or fallback

    return {
        "tenant": _seg(getattr(runtime_ctx, "tenant", None), "tenant"),
        "project": _seg(getattr(runtime_ctx, "project", None), "project"),
        "user_id": _seg(getattr(runtime_ctx, "user_id", None), "user"),
        "conversation_id": _seg(getattr(runtime_ctx, "conversation_id", None), "conversation"),
    }


def workspace_version_ref(runtime_ctx: Any, version_id: str) -> str:
    segs = workspace_lineage_segments(runtime_ctx)
    version = str(version_id or "").strip()
    if not version:
        return ""
    return (
        f"refs/kdcube/{segs['tenant']}/{segs['project']}/"
        f"{segs['user_id']}/{segs['conversation_id']}/versions/{version}"
    )


def workspace_lineage_branch_ref(runtime_ctx: Any) -> str:
    segs = workspace_lineage_segments(runtime_ctx)
    return (
        f"refs/heads/kdcube/{segs['tenant']}/{segs['project']}/"
        f"{segs['user_id']}/{segs['conversation_id']}"
    )


def physical_to_logical_artifact_path(path: str) -> str:
    raw = str(path or "").strip().strip("/")
    if not raw:
        return ""
    if "/files/" in raw:
        turn_id, rel = raw.split("/files/", 1)
        if turn_id and rel:
            return f"fi:{turn_id}.files/{rel}"
    if "/attachments/" in raw:
        turn_id, rel = raw.split("/attachments/", 1)
        if turn_id and rel:
            return f"fi:{turn_id}.user.attachments/{rel}"
    return ""


async def hydrate_workspace_paths(
    *,
    ctx_browser: Any,
    paths: List[str],
    outdir: pathlib.Path,
) -> Dict[str, Any]:
    """
    Materialize requested physical workspace paths using the configured implementation.
    Files under <turn>/files may come from custom timeline rehost or git-backed snapshots.
    Attachments always use the custom artifact/hosting path.
    """
    normalized = [str(p).strip() for p in (paths or []) if isinstance(p, str) and str(p).strip()]
    if not normalized:
        return {"rehosted": [], "missing": [], "errors": []}

    files_paths: List[str] = []
    other_paths: List[str] = []
    for path in normalized:
        if "/files/" in path:
            files_paths.append(path)
        else:
            other_paths.append(path)

    result = {"rehosted": [], "missing": [], "errors": []}
    impl = get_workspace_implementation(getattr(ctx_browser, "runtime_ctx", None))

    async def _merge(payload: Dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            return
        result["rehosted"].extend(list(payload.get("rehosted") or []))
        result["missing"].extend(list(payload.get("missing") or []))
        result["errors"].extend(list(payload.get("errors") or []))

    if files_paths:
        if impl == WORKSPACE_IMPLEMENTATION_GIT:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import (
                resolve_logical_artifact,
                rehost_files_from_timeline,
            )
            from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace import hydrate_files_from_git_workspace

            git_candidate_paths: List[str] = []
            custom_candidate_paths: List[str] = []
            for physical in files_paths:
                logical = physical_to_logical_artifact_path(physical)
                artifact = await resolve_logical_artifact(ctx_browser=ctx_browser, path=logical) if logical else None
                mime = (
                    (artifact.get("mime") or "").strip()
                    if isinstance(artifact, dict)
                    else _guess_mime_from_path(physical)
                )
                if mime and not _is_text_mime(mime):
                    custom_candidate_paths.append(physical)
                    continue
                git_candidate_paths.append(physical)

            if git_candidate_paths:
                await _merge(await hydrate_files_from_git_workspace(
                    ctx_browser=ctx_browser,
                    paths=git_candidate_paths,
                    outdir=outdir,
                ))
            if custom_candidate_paths:
                await _merge(await rehost_files_from_timeline(
                    ctx_browser=ctx_browser,
                    paths=custom_candidate_paths,
                    outdir=outdir,
                ))
        else:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import rehost_files_from_timeline

            await _merge(await rehost_files_from_timeline(
                ctx_browser=ctx_browser,
                paths=files_paths,
                outdir=outdir,
            ))

    if other_paths:
        from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import rehost_files_from_timeline

        await _merge(await rehost_files_from_timeline(
            ctx_browser=ctx_browser,
            paths=other_paths,
            outdir=outdir,
        ))

    result["rehosted"] = list(dict.fromkeys(result["rehosted"]))
    result["missing"] = list(dict.fromkeys(result["missing"]))
    result["errors"] = list(dict.fromkeys(result["errors"]))
    return result
