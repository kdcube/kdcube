# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import shutil
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    ARTIFACT_NAMESPACE_ATTACHMENTS,
    ARTIFACT_NAMESPACE_FILES,
    ARTIFACT_NAMESPACE_OUTPUTS,
    build_physical_artifact_path,
    physical_path_to_logical_path,
    split_logical_artifact_path,
)
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for, resolve_artifact_path

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

_CODE_PATH_RE = re.compile(r"(turn_[A-Za-z0-9_]+/(files|outputs|attachments)/[^\s'\"\)\];,]+)")
_REL_FILES_RE = re.compile(r"(?<![A-Za-z0-9_])files/[^\s'\"\)\];,]+")
_REL_OUTPUTS_RE = re.compile(r"(?<![A-Za-z0-9_])outputs/[^\s'\"\)\];,]+")
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
    for m in _REL_OUTPUTS_RE.finditer(code):
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
    current_outputs_prefix = f"{turn_id}/outputs/" if turn_id else ""
    current_att_prefix = f"{turn_id}/attachments/" if turn_id else ""
    for p in cleaned:
        if p in seen:
            continue
        seen.add(p)
        if (current_files_prefix and p.startswith(current_files_prefix)) or (
            current_outputs_prefix and p.startswith(current_outputs_prefix)
        ) or (
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
    tid, namespace, rel = split_logical_artifact_path(p)
    if tid and rel:
        physical = build_physical_artifact_path(turn_id=tid, namespace=namespace, relpath=rel)
        if physical:
            return physical
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


def workspace_turn_root(*, runtime_ctx: Any) -> pathlib.Path:
    outdir_raw = str(getattr(runtime_ctx, "outdir", "") or "").strip()
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not outdir_raw or not turn_id:
        return pathlib.Path("")
    outdir = artifact_outdir_for(pathlib.Path(outdir_raw))
    return outdir / turn_id


def current_turn_files_root(*, runtime_ctx: Any) -> pathlib.Path:
    outdir_raw = str(getattr(runtime_ctx, "outdir", "") or "").strip()
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not outdir_raw or not turn_id:
        return pathlib.Path("")
    return artifact_outdir_for(pathlib.Path(outdir_raw)) / turn_id / "files"


def current_turn_files_nonempty(*, runtime_ctx: Any) -> bool:
    files_root = current_turn_files_root(runtime_ctx=runtime_ctx)
    if not files_root.exists():
        return False
    try:
        next(files_root.iterdir())
        return True
    except StopIteration:
        return False
    except Exception:
        return True


def list_materialized_turn_roots(*, runtime_ctx: Any) -> List[str]:
    outdir_raw = str(getattr(runtime_ctx, "outdir", "") or "").strip()
    if not outdir_raw:
        return []
    outdir = artifact_outdir_for(pathlib.Path(outdir_raw), create=False)
    if not outdir.exists():
        return []
    names: List[str] = []
    for child in outdir.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if not name.startswith("turn_"):
            continue
        names.append(name)
    names.sort()
    return names


def summarize_current_turn_scopes(*, runtime_ctx: Any) -> List[Dict[str, Any]]:
    turn_root = workspace_turn_root(runtime_ctx=runtime_ctx)
    files_root = turn_root / "files"
    if not files_root.exists():
        return []
    out: List[Dict[str, Any]] = []
    for child in sorted(files_root.iterdir(), key=lambda p: p.name.lower()):
        if child.is_file():
            out.append({"scope": child.name, "files": 1, "kind": "file"})
            continue
        if not child.is_dir():
            continue
        file_count = 0
        for nested in child.rglob("*"):
            if nested.is_file():
                file_count += 1
        out.append({"scope": f"{child.name}/", "files": file_count, "kind": "dir"})
    return out


def latest_workspace_publish_event(
    blocks: List[Dict[str, Any]],
    *,
    turn_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    for blk in reversed(blocks or []):
        if not isinstance(blk, dict):
            continue
        if (blk.get("type") or "").strip() != "react.workspace.publish":
            continue
        blk_turn = str(blk.get("turn_id") or blk.get("turn") or "").strip()
        if turn_id and blk_turn != turn_id:
            continue
        payload: Dict[str, Any] = {}
        text = blk.get("text")
        if isinstance(text, str) and text.strip():
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    payload.update(parsed)
            except Exception:
                pass
        meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
        if "status" not in payload and meta.get("status"):
            payload["status"] = meta.get("status")
        if "turn_id" not in payload and blk_turn:
            payload["turn_id"] = blk_turn
        if payload:
            return payload
        return {"turn_id": blk_turn, "status": str(meta.get("status") or "").strip()}
    return None


def latest_workspace_checkout_event(
    blocks: List[Dict[str, Any]],
    *,
    turn_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    for blk in reversed(blocks or []):
        if not isinstance(blk, dict):
            continue
        if (blk.get("type") or "").strip() != "react.workspace.checkout":
            continue
        blk_turn = str(blk.get("turn_id") or blk.get("turn") or "").strip()
        if turn_id and blk_turn != turn_id:
            continue
        payload: Dict[str, Any] = {}
        text = blk.get("text")
        if isinstance(text, str) and text.strip():
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    payload.update(parsed)
            except Exception:
                pass
        if "turn_id" not in payload and blk_turn:
            payload["turn_id"] = blk_turn
        return payload or {"turn_id": blk_turn}
    return None


def physical_to_logical_artifact_path(path: str) -> str:
    return physical_path_to_logical_path(path)


async def hydrate_workspace_paths(
    *,
    ctx_browser: Any,
    paths: List[str],
    outdir: pathlib.Path,
) -> Dict[str, Any]:
    """
    Materialize requested physical workspace paths using the configured implementation.
    Files under <turn>/files may come from custom timeline rehost or git-backed snapshots.
    Outputs and attachments always use the custom artifact/hosting path.
    """
    normalized = [str(p).strip() for p in (paths or []) if isinstance(p, str) and str(p).strip()]
    if not normalized:
        return {"rehosted": [], "missing": [], "errors": []}
    outdir = artifact_outdir_for(outdir)

    files_paths: List[str] = []
    other_paths: List[str] = []
    for path in normalized:
        if "/files/" in path or path.endswith("/files"):
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
            from kdcube_ai_app.apps.chat.sdk.solutions.react.solution_workspace import (
                resolve_logical_artifact,
                rehost_files_from_timeline,
            )
            from kdcube_ai_app.apps.chat.sdk.solutions.react.git_workspace import hydrate_files_from_git_workspace

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
            from kdcube_ai_app.apps.chat.sdk.solutions.react.solution_workspace import rehost_files_from_timeline

            await _merge(await rehost_files_from_timeline(
                ctx_browser=ctx_browser,
                paths=files_paths,
                outdir=outdir,
            ))

    if other_paths:
        from kdcube_ai_app.apps.chat.sdk.solutions.react.solution_workspace import rehost_files_from_timeline

        await _merge(await rehost_files_from_timeline(
            ctx_browser=ctx_browser,
            paths=other_paths,
            outdir=outdir,
        ))

    result["rehosted"] = list(dict.fromkeys(result["rehosted"]))
    result["missing"] = list(dict.fromkeys(result["missing"]))
    result["errors"] = list(dict.fromkeys(result["errors"]))
    return result


def _parse_checkout_file_ref(path: str) -> Optional[Dict[str, str]]:
    raw = str(path or "").strip()
    if not raw.startswith("fi:"):
        return None
    turn_id, namespace, rel = split_logical_artifact_path(raw)
    if turn_id and namespace == ARTIFACT_NAMESPACE_FILES and rel:
        return {
            "logical_path": raw,
            "turn_id": turn_id,
            "rel": rel.strip("/"),
            "physical_path": f"{turn_id}/files/{rel.strip('/')}",
        }
    logical = raw[len("fi:"):].strip()
    if logical.endswith(".files"):
        turn_id = logical[: -len(".files")].strip()
        if turn_id:
            return {
                "logical_path": raw,
                "turn_id": turn_id,
                "rel": "",
                "physical_path": f"{turn_id}/files",
            }
    if logical.endswith(".files/"):
        turn_id = logical[: -len(".files/")].strip()
        if turn_id:
            return {
                "logical_path": raw,
                "turn_id": turn_id,
                "rel": "",
                "physical_path": f"{turn_id}/files",
            }
    return None


def normalize_checkout_requests(
    *,
    raw_paths: List[Any] | None,
    legacy_version: str = "",
    current_turn_id: str = "",
) -> tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    accepted: List[Dict[str, str]] = []
    invalid: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _accept(entry: Dict[str, str]) -> None:
        key = (entry["turn_id"], entry["rel"])
        if key in seen:
            return
        seen.add(key)
        accepted.append(entry)

    requested = [str(p).strip() for p in (raw_paths or []) if str(p).strip()]
    if requested:
        for raw in requested:
            parsed = _parse_checkout_file_ref(raw)
            if not parsed:
                invalid.append({
                    "path": raw,
                    "reason": "react.checkout accepts fi:<turn_id>.files/<scope-or-path> refs only",
                })
                continue
            if current_turn_id and parsed["turn_id"] == current_turn_id:
                invalid.append({
                    "path": raw,
                    "reason": "react.checkout cannot use current-turn fi: refs as checkout sources",
                })
                continue
            _accept(parsed)
        return accepted, invalid

    version = str(legacy_version or "").strip()
    if version:
        if current_turn_id and version == current_turn_id:
            invalid.append({
                "path": version,
                "reason": "react.checkout cannot use current-turn fi: refs as checkout sources",
            })
        else:
            _accept({
                "logical_path": f"fi:{version}.files/",
                "turn_id": version,
                "rel": "",
                "physical_path": f"{version}/files",
            })
    return accepted, invalid


def normalize_checkout_mode(value: Any) -> str:
    raw = str(value or "replace").strip().lower()
    if raw == "overlay":
        return "overlay"
    return "replace"


async def checkout_workspace_paths(
    *,
    ctx_browser: Any,
    requests: List[Dict[str, str]],
    outdir: pathlib.Path,
    mode: str = "replace",
) -> Dict[str, Any]:
    runtime_ctx = getattr(ctx_browser, "runtime_ctx", None)
    if runtime_ctx is None:
        return {"mode": mode, "checked_out_from": [], "materialized": [], "missing": [], "errors": ["missing_runtime_ctx"]}
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not turn_id:
        return {"mode": mode, "checked_out_from": [], "materialized": [], "missing": [], "errors": ["missing_turn_id"]}
    mode = normalize_checkout_mode(mode)

    impl = get_workspace_implementation(runtime_ctx)
    if impl == WORKSPACE_IMPLEMENTATION_GIT:
        from kdcube_ai_app.apps.chat.sdk.solutions.react.git_workspace import ensure_current_turn_git_workspace

        await asyncio.to_thread(
            ensure_current_turn_git_workspace,
            runtime_ctx=runtime_ctx,
            outdir=outdir,
        )

    artifact_outdir = artifact_outdir_for(outdir)
    files_root = artifact_outdir / turn_id / "files"
    if mode == "replace" and current_turn_files_nonempty(runtime_ctx=runtime_ctx):
        return {
            "mode": mode,
            "checked_out_from": [str(item.get("logical_path") or "").strip() for item in requests],
            "materialized": [],
            "missing": [],
            "errors": ["workspace_checkout_nonempty"],
        }

    staged_sources: List[Dict[str, str]] = []
    missing: List[Dict[str, str]] = []
    errors: List[str] = []

    for req in requests:
        source_physical = str(req.get("physical_path") or "").strip()
        logical_path = str(req.get("logical_path") or "").strip()
        rel = str(req.get("rel") or "").strip().strip("/")
        if not source_physical or not logical_path:
            continue
        payload = await hydrate_workspace_paths(
            ctx_browser=ctx_browser,
            paths=[source_physical],
            outdir=artifact_outdir,
        )
        errors.extend(list(payload.get("errors") or []))
        source_prefix = source_physical.rstrip("/")
        matched = []
        for item in payload.get("rehosted") or []:
            cleaned = str(item or "").strip().rstrip("/")
            if cleaned == source_prefix or cleaned.startswith(source_prefix + "/"):
                matched.append(cleaned)
        if not matched:
            missing.append({
                "logical_path": logical_path,
                "physical_path": source_physical,
                "kind": "files",
            })
            continue
        for matched_physical in matched:
            rel_after = matched_physical.split("/files/", 1)[1] if "/files/" in matched_physical else rel
            staged_sources.append({
                "source_physical": matched_physical,
                "target_physical": f"{turn_id}/files/{rel_after}",
                "logical_path": logical_path,
            })

    if missing or errors:
        return {
            "mode": mode,
            "checked_out_from": [str(item.get("logical_path") or "").strip() for item in requests],
            "materialized": [],
            "missing": missing,
            "errors": list(dict.fromkeys(errors)),
        }

    if mode == "replace" and files_root.exists():
        shutil.rmtree(files_root)
    files_root.mkdir(parents=True, exist_ok=True)

    materialized: List[Dict[str, str]] = []
    for item in staged_sources:
        source_physical = item["source_physical"]
        target_physical = item["target_physical"]
        src = resolve_artifact_path(outdir, source_physical)
        dst = resolve_artifact_path(outdir, target_physical, prefer_existing=False)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        materialized.append({
            "logical_path": physical_to_logical_artifact_path(target_physical),
            "physical_path": target_physical,
            "source_logical_path": item["logical_path"],
            "source_physical_path": source_physical,
            "kind": "files",
        })

    return {
        "mode": mode,
        "checked_out_from": [str(item.get("logical_path") or "").strip() for item in requests],
        "materialized": materialized,
        "missing": [],
        "errors": [],
    }
