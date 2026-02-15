# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
import tempfile

import json
import os
import asyncio
import re
# kdcube_ai_app/apps/chat/sdk/runtime/solution/solution_workspace.py

import traceback, pathlib, logging
from typing import Any, Optional, List, Dict, Union

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.timeline import resolve_artifact_from_timeline

logger = logging.getLogger(__name__)

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
        if turn_id:
            rewritten.append(f"{turn_id}/{raw}")
        else:
            rewritten.append(raw)
    for m in _REL_ATTACHMENTS_RE.finditer(code):
        raw = m.group(0)
        if _has_turn_prefix(m.start()):
            continue
        if turn_id:
            rewritten.append(f"{turn_id}/{raw}")
        else:
            rewritten.append(raw)
    # Strip common trailing punctuation
    cleaned: List[str] = []
    for p in found + rewritten:
        cleaned.append(p.rstrip(")];,"))
    # de-dup preserve order; exclude current-turn outputs from rehost
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
    """
    Best-effort extraction of logical artifact paths referenced in code for fetch_ctx.
    Looks for strings like fi:<...>, ar:<...>, so:<...>, tc:<...>.
    """
    if not isinstance(code, str) or not code.strip():
        return []
    found = [m.group(1) for m in _FETCH_CTX_PATH_RE.finditer(code)]
    out: List[str] = []
    seen = set()
    for p in found:
        if not p or ":" not in p:
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
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


async def rehost_files_from_timeline(
        *,
        ctx_browser: Any,
        paths: List[str],
        outdir: pathlib.Path,
) -> Dict[str, Any]:
    """
    Rehost referenced files by logical path "turn_id/files/<relpath>" into OUT_DIR.
    Uses turn log blocks to resolve hosted_uri/rn/key from timeline blocks.
    """
    if not paths:
        return {"rehosted": [], "missing": [], "errors": []}
    try:
        from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
    except Exception:
        return {"rehosted": [], "missing": [], "errors": ["missing_store"]}

    store = ConversationStore(get_settings().STORAGE_PATH)
    rehosted: List[str] = []
    missing: List[str] = []
    errors: List[str] = []

    by_turn: Dict[str, List[str]] = {}
    by_turn_attachments: Dict[str, List[str]] = {}
    for p in paths:
        if not isinstance(p, str):
            continue
        if "/files/" in p:
            if not _safe_relpath(p):
                errors.append(f"unsafe_path:{p}")
                continue
            tid, rel = p.split("/files/", 1)
            if not tid or not rel:
                continue
            by_turn.setdefault(tid, []).append(rel)
        elif "/attachments/" in p:
            if not _safe_relpath(p):
                errors.append(f"unsafe_path:{p}")
                continue
            tid, rel = p.split("/attachments/", 1)
            if not tid or not rel:
                continue
            by_turn_attachments.setdefault(tid, []).append(rel)

    for turn_id, rels in by_turn.items():
        try:
            turn_log = await ctx_browser.get_turn_log(turn_id=turn_id)
        except Exception:
            turn_log = {}
        contrib_log = (turn_log.get("blocks") or []) if isinstance(turn_log, dict) else []
        for rel in rels:
            artifact_path = f"fi:{turn_id}.files/{rel}"
            artifact = resolve_artifact_from_timeline({"blocks": contrib_log, "sources_pool": []}, artifact_path)
            if not isinstance(artifact, dict):
                missing.append(f"{turn_id}/files/{rel}")
                continue
            src = (artifact.get("hosted_uri") or artifact.get("rn") or artifact.get("key") or "").strip()
            if not src and (artifact.get("base64") or artifact.get("text")):
                src = ""
            target = outdir / turn_id / "files" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if not target.exists():
                    if artifact.get("base64"):
                        import base64 as _b64
                        target.write_bytes(_b64.b64decode(artifact.get("base64")))
                    elif isinstance(artifact.get("text"), str):
                        target.write_text(artifact.get("text"), encoding="utf-8")
                    elif src:
                        data = await store.get_blob_bytes(src)
                        target.write_bytes(data)
                    else:
                        missing.append(f"{turn_id}/files/{rel}")
                        continue
                rehosted.append(f"{turn_id}/files/{rel}")
            except Exception as e:
                errors.append(f"rehost_failed:{turn_id}/files/{rel}:{e}")

    for turn_id, rels in by_turn_attachments.items():
        try:
            turn_log = await ctx_browser.get_turn_log(turn_id=turn_id)
        except Exception:
            turn_log = {}
        contrib_log = (turn_log.get("blocks") or []) if isinstance(turn_log, dict) else []
        for rel in rels:
            artifact_path = f"fi:{turn_id}.user.attachments/{rel}"
            artifact = resolve_artifact_from_timeline({"blocks": contrib_log, "sources_pool": []}, artifact_path)
            if not isinstance(artifact, dict):
                missing.append(f"{turn_id}/attachments/{rel}")
                continue
            src = (artifact.get("hosted_uri") or artifact.get("rn") or artifact.get("key") or "").strip()
            if not src and (artifact.get("base64") or artifact.get("text")):
                src = ""
            target = outdir / turn_id / "attachments" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if not target.exists():
                    if artifact.get("base64"):
                        import base64 as _b64
                        target.write_bytes(_b64.b64decode(artifact.get("base64")))
                    elif isinstance(artifact.get("text"), str):
                        target.write_text(artifact.get("text"), encoding="utf-8")
                    elif src:
                        data = await store.get_blob_bytes(src)
                        target.write_bytes(data)
                    else:
                        missing.append(f"{turn_id}/attachments/{rel}")
                        continue
                rehosted.append(f"{turn_id}/attachments/{rel}")
            except Exception as e:
                errors.append(f"rehost_failed:{turn_id}/attachments/{rel}:{e}")

    return {"rehosted": rehosted, "missing": missing, "errors": errors}


def _filter_blocks_for_paths(blocks: List[Dict[str, Any]], *, paths: List[str]) -> List[Dict[str, Any]]:
    if not paths:
        return list(blocks or [])
    wanted = set(p for p in paths if isinstance(p, str) and p.strip())
    kept: List[Dict[str, Any]] = []
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue
        bpath = (b.get("path") or "").strip()
        if bpath and bpath in wanted:
            kept.append(b)
            continue
        if (b.get("type") or "") == "react.tool.result" and (b.get("mime") or "") == "application/json":
            try:
                meta_obj = json.loads(b.get("text") or "{}")
            except Exception:
                meta_obj = {}
            if isinstance(meta_obj, dict) and meta_obj.get("artifact_path") in wanted:
                kept.append(b)
    return kept


def _copy_file(src: pathlib.Path, dest: pathlib.Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())


def _copy_tree(src: pathlib.Path, dest: pathlib.Path) -> None:
    if not src.exists():
        return
    for root, _, files in os.walk(src):
        root_path = pathlib.Path(root)
        rel = root_path.relative_to(src)
        for f in files:
            s = root_path / f
            d = dest / rel / f
            _copy_file(s, d)


def build_exec_snapshot_workspace(
    *,
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    timeline: Optional[Dict[str, Any]],
    code: str,
) -> Dict[str, pathlib.Path]:
    """
    Build a lightweight workspace for distributed exec:
    - workdir: full copy (main.py, helper files)
    - outdir: timeline.json (filtered) + only referenced files
    """
    tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="exec_ws_"))
    snap_work = tmp_root / "work"
    snap_out = tmp_root / "out"
    snap_work.mkdir(parents=True, exist_ok=True)
    snap_out.mkdir(parents=True, exist_ok=True)

    _copy_tree(workdir, snap_work)

    # Build filtered timeline
    tl_payload = timeline or {}
    tl_blocks = tl_payload.get("blocks") if isinstance(tl_payload.get("blocks"), list) else []
    tl_sources = tl_payload.get("sources_pool") if isinstance(tl_payload.get("sources_pool"), list) else []
    tl_title = tl_payload.get("conversation_title") or ""
    tl_started = tl_payload.get("conversation_started_at") or ""
    fetch_paths = extract_fetch_ctx_paths(code)
    filtered_blocks = _filter_blocks_for_paths(tl_blocks, paths=fetch_paths)
    payload = {
        "version": tl_payload.get("version") or 1,
        "ts": tl_payload.get("ts") or "",
        "blocks": filtered_blocks,
        "sources_pool": tl_sources,
        "conversation_title": tl_title,
        "conversation_started_at": tl_started,
    }
    try:
        (snap_out / "timeline.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    # Copy referenced files (physical paths in code + fi: logical refs)
    code_paths, _ = extract_code_file_paths(code, turn_id="")
    file_paths = list(code_paths)
    for p in fetch_paths:
        if p.startswith("fi:"):
            file_paths.append(p)
    for p in file_paths:
        if p.startswith("fi:"):
            # logical -> physical
            try:
                art = resolve_artifact_from_timeline({"blocks": tl_blocks, "sources_pool": tl_sources}, p)
            except Exception:
                art = None
            if isinstance(art, dict):
                phys = (art.get("filepath") or art.get("physical_path") or "").strip()
                if phys:
                    file_paths.append(phys)
            continue
    try:
        (snap_out / "exec_snapshot_manifest.json").write_text(json.dumps({
            "filtered_paths": fetch_paths,
            "included_files": file_paths,
            "timeline_blocks_total": len(tl_blocks),
            "timeline_blocks_filtered": len(filtered_blocks),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    seen = set()
    for phys in file_paths:
        if not isinstance(phys, str) or not phys.strip():
            continue
        if phys.startswith("fi:"):
            continue
        if phys in seen:
            continue
        seen.add(phys)
        src = outdir / phys
        if src.exists():
            _copy_file(src, snap_out / phys)

    return {"workdir": snap_work, "outdir": snap_out, "root": tmp_root}

def _is_hosted_path(path: str) -> bool:
    if not isinstance(path, str) or not path.strip():
        return False
    p = path.strip()
    return p.startswith("cb/") or "://" in p


def _is_text_mime(m: str | None) -> bool:
    m = (m or "").lower().strip()
    if m in _TEXT_MIMES:
        return True
    return m.startswith("text/")

def _unique_target(base_dir: pathlib.Path, basename: str) -> pathlib.Path:
    """
    Ensure we don't overwrite duplicates; add -1, -2, ... if needed.
    """
    candidate = base_dir / basename
    if not candidate.exists():
        return candidate
    stem = pathlib.Path(basename).stem
    suf  = pathlib.Path(basename).suffix
    i = 1
    while True:
        c = base_dir / f"{stem}-{i}{suf}"
        if not c.exists():
            return c
        i += 1

def _alloc_unique_name(name: str, used: Dict[str, int]) -> str:
    base = name or "file"
    count = used.get(base, 0)
    used[base] = count + 1
    if count == 0:
        return base
    stem = pathlib.Path(base).stem
    suf = pathlib.Path(base).suffix
    return f"{stem}-{count}{suf}"

async def rehost_previous_files(
        prev_files: list[dict],
        workdir: pathlib.Path,
        turn_id: str  # ← turn_id for this deliverable's source turn
) -> list[dict]:
    """
    Copy files from conversation storage into workdir/<turn_id>/files/,
    organizing historical files by their source turn.

    Structure:
      workdir/
        turn_1765841825124_s1lw9s/
          files/
            report.pdf
            data.xlsx
        turn_1765841834567_a2bc3d/
          chart.png

    Updated paths in artifacts reflect this structure: "<turn_id>/files/<relative_path>"

    **Draft file slots:** File slots with `path: ""` are draft artifacts (no actual file).
    These are skipped with `rehosted: False` and `file_exists: False` flags.
    The surrogate text in `artifact["text"]` remains accessible via context.
    """
    from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
    from kdcube_ai_app.apps.chat.sdk.config import get_settings

    out: list[dict] = []
    if not prev_files:
        return out

    # Create turn-specific subdirectory
    turn_dir = workdir / turn_id / "files"
    turn_dir.mkdir(parents=True, exist_ok=True)

    store = ConversationStore(get_settings().STORAGE_PATH)

    used_names: Dict[str, int] = {}
    items: List[Dict[str, Any]] = []
    for file in prev_files:
        artifact = (file or {}).get("value") if isinstance(file, dict) else None
        if not isinstance(artifact, dict):
            items.append({"_artifact": artifact, "_src": None, "_filename": None, "_mime": "", "_draft": False})
            continue
        mime = artifact.get("mime") or ""
        src_path = (artifact.get("path") or "").strip()
        is_draft = bool(artifact.get("draft"))
        rel_path = ""
        if src_path and not _is_hosted_path(src_path):
            try:
                p = pathlib.Path(src_path)
                rel_path = p.name if p.is_absolute() else str(p)
            except Exception:
                rel_path = ""
        if not rel_path:
            rel_path = (artifact.get("filename") or "").strip()
        rel_path = rel_path.strip().lstrip("/").replace("..", "").strip()
        if rel_path:
            dir_part = str(pathlib.Path(rel_path).parent)
            base_name = pathlib.Path(rel_path).name
            if base_name:
                unique_name = _alloc_unique_name(base_name, used_names)
                rel_path = str(pathlib.Path(dir_part) / unique_name) if dir_part and dir_part != "." else unique_name
        items.append({
            "_artifact": artifact,
            "_src": src_path,
            "_rel_path": rel_path,
            "_mime": mime,
            "_draft": is_draft
        })

    sem = asyncio.Semaphore(os.cpu_count() or 4)

    async def _process_one(idx: int, item: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        async with sem:
            artifact = item.get("_artifact")
            src_path = (item.get("_src") or "").strip()
            rel_path = item.get("_rel_path") or ""
            mime = item.get("_mime") or ""
            is_draft = bool(item.get("_draft"))
            try:
                if not isinstance(artifact, dict):
                    return idx, artifact
                if not src_path:
                    logger.info(
                        f"[rehost] Skipping turn {turn_id} - "
                        f"{'draft ' if is_draft else ''}file slot with no path"
                    )
                    return idx, {
                        **artifact,
                        "rehosted": False,
                        "file_exists": False,
                        "rehost_reason": "no_source_path"
                    }
                if not rel_path:
                    logger.warning(f"[rehost] Invalid path (no relative path): {src_path}")
                    return idx, {
                        **artifact,
                        "rehosted": False,
                        "file_exists": False,
                        "rehost_reason": "invalid_path"
                    }
                target = turn_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if _is_text_mime(mime):
                        content = await store.backend.read_text_a(src_path)
                        target.write_text(content, encoding="utf-8")
                    else:
                        content = await store.backend.read_bytes_a(src_path)
                        target.write_bytes(content)
                    logger.info(f"[rehost] ✓ {turn_id}/files/{rel_path} from {src_path}")
                    return idx, {
                        **artifact,
                        "source_path": src_path,
                        "path": f"{turn_id}/files/{rel_path}",
                        "rehosted": True,
                        "file_exists": True,
                    }
                except FileNotFoundError:
                    logger.warning(
                        f"[rehost] File not found in storage: {src_path} "
                        f"(turn {turn_id}, file: {rel_path})"
                    )
                    return idx, {
                        **artifact,
                        "rehosted": False,
                        "file_exists": False,
                        "rehost_reason": "file_not_found",
                        "source_path": src_path
                    }
                except Exception as read_err:
                    logger.error(f"[rehost] Failed to read {src_path}: {read_err}")
                    return idx, {
                        **artifact,
                        "rehosted": False,
                        "file_exists": False,
                        "rehost_reason": "read_error",
                        "rehost_error": str(read_err)[:200],
                        "source_path": src_path
                    }
            except Exception as e:
                logger.error(
                    f"[rehost] Failed to process file artifact: {e}\n"
                    f"{traceback.format_exc()}"
                )
                return idx, {
                    **(artifact or {}),
                    "rehosted": False,
                    "file_exists": False,
                    "rehost_reason": "processing_error",
                    "rehost_error": str(e)[:200]
                }

    tasks = [asyncio.create_task(_process_one(i, item)) for i, item in enumerate(items)]
    results = await asyncio.gather(*tasks)
    out = [None] * len(results)
    for idx, res in results:
        out[idx] = res

    logger.info(
        f"[rehost] Turn {turn_id}: processed {len(prev_files)} files, "
        f"rehosted {sum(1 for a in out if a.get('rehosted'))} successfully"
    )
    return out


async def rehost_previous_attachments(
        prev_attachments: list[dict],
        workdir: pathlib.Path,
        turn_id: str,
) -> list[dict]:
    """
    Copy attachments from conversation storage into workdir/<turn_id>/attachments/.

    Structure:
      workdir/
        turn_<id>/attachments/
          filename.pdf
          image.png
    """
    from kdcube_ai_app.apps.chat.sdk.config import get_settings

    out: list[dict] = []
    if not prev_attachments:
        return out

    turn_dir = workdir / turn_id / "attachments"
    turn_dir.mkdir(parents=True, exist_ok=True)

    store = ConversationStore(get_settings().STORAGE_PATH)

    used_names: Dict[str, int] = {}
    items: List[Dict[str, Any]] = []
    for att in prev_attachments:
        base = dict(att) if isinstance(att, dict) else {}
        if not base:
            items.append({"_artifact": base, "_src": None, "_filename": None})
            continue
        raw_name = (base.get("filename") or "attachment.bin").strip() or "attachment.bin"
        filename = _alloc_unique_name(raw_name, used_names)
        src = base.get("hosted_uri") or base.get("source_path") or base.get("path") or base.get("key")
        items.append({"_artifact": base, "_src": src, "_filename": filename})

    sem = asyncio.Semaphore(os.cpu_count() or 4)

    async def _process_one(idx: int, item: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        async with sem:
            base = item.get("_artifact") or {}
            src = item.get("_src")
            filename = item.get("_filename")
            try:
                if not base:
                    return idx, base
                if not src:
                    return idx, {
                        **base,
                        "rehosted": False,
                        "file_exists": False,
                        "rehost_reason": "no_source_path",
                    }
                target = turn_dir / filename
                try:
                    content = await store.get_blob_bytes(src)
                    target.write_bytes(content)
                    logger.info(f"[rehost] ✓ {turn_id}/attachments/{target.name} from {src}")
                    return idx, {
                        **base,
                        "source_path": src,
                        "path": f"{turn_id}/attachments/{target.name}",
                        "rehosted": True,
                        "file_exists": True,
                    }
                except FileNotFoundError:
                    logger.warning(f"[rehost] Attachment not found in storage: {src}")
                    return idx, {
                        **base,
                        "rehosted": False,
                        "file_exists": False,
                        "rehost_reason": "file_not_found",
                        "source_path": src,
                    }
                except Exception as read_err:
                    logger.error(f"[rehost] Failed to read attachment {src}: {read_err}")
                    return idx, {
                        **base,
                        "rehosted": False,
                        "file_exists": False,
                        "rehost_reason": "read_error",
                        "rehost_error": str(read_err)[:200],
                        "source_path": src,
                    }
            except Exception as e:
                logger.error(
                    f"[rehost] Failed to process attachment: {e}\n"
                    f"{traceback.format_exc()}"
                )
                return idx, {
                    **base,
                    "rehosted": False,
                    "file_exists": False,
                    "rehost_reason": "processing_error",
                    "rehost_error": str(e)[:200],
                }

    tasks = [asyncio.create_task(_process_one(i, item)) for i, item in enumerate(items)]
    results = await asyncio.gather(*tasks)
    out = [None] * len(results)
    for idx, res in results:
        out[idx] = res

    logger.info(
        f"[rehost] Turn {turn_id}: processed {len(prev_attachments)} attachments, "
        f"rehosted {sum(1 for a in out if a.get('rehosted'))} successfully"
    )
    return out


class ApplicationHostingService:
    """
    Host local files into ConversationStore and emit chat events for hosted artifacts.
    """

    def __init__(
        self,
        *,
        store: ConversationStore,
        comm: Optional[ChatCommunicator] = None,
        logger: Optional[Union[logging.Logger, AgentLogger]] = None,
    ):
        self.store = store
        self.comm = comm
        self.log = logger or logging.getLogger(__name__)

    def _extract_file_fields(self, a: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(a, dict):
            return None

        if a.get("type") == "file":
            output = a.get("output") or {}
            path = output.get("path") or a.get("path") or ""
            text = output.get("text") if isinstance(output, dict) else None
            return {
                "path": path,
                "mime": a.get("mime") or (output.get("mime") if isinstance(output, dict) else None),
                "tool_id": a.get("tool_id") or "",
                "description": a.get("description") or "",
                "slot": a.get("resource_id") or a.get("slot") or a.get("artifact_id") or "",
                "text": text,
            }

        val = a.get("value") if isinstance(a.get("value"), dict) else None
        if isinstance(val, dict) and val.get("type") == "file":
            return {
                "path": val.get("path") or "",
                "mime": val.get("mime"),
                "tool_id": a.get("tool_id") or "",
                "description": a.get("description") or "",
                "slot": a.get("resource_id") or a.get("slot") or a.get("artifact_id") or "",
                "text": val.get("text"),
            }

        return None

    async def host_files_to_conversation(
        self,
        *,
        rid: str,
        files: List[Dict[str, Any]],
        outdir: str | pathlib.Path | None,
        tenant: str,
        project: str,
        user: str,
        conversation_id: str,
        user_type: str,
        turn_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Copy deliverable file artifacts from local outdir → ConversationStore.
        Returns rows: [{slot, key, hosted_uri, filename, mime, size, tool_id, description, owner_id, rn, local_path}]
        """
        import pathlib as _pathlib

        files_rehosted: List[Dict[str, Any]] = []
        base = _pathlib.Path(outdir) if outdir else None
        for a in (files or []):
            info = self._extract_file_fields(a)
            if not info:
                continue
            rel_or_abs = (info.get("path") or "").strip()
            if not rel_or_abs:
                continue
            if _is_hosted_path(rel_or_abs):
                continue

            p = _pathlib.Path(rel_or_abs)
            if not p.is_absolute():
                p = (base / rel_or_abs).resolve() if base else p.resolve()
            try:
                data = p.read_bytes()
            except Exception as ex:
                self.log.log(f"[host_files] Failed to read file {p}: {ex}", level="ERROR")
                continue

            name = p.name
            uri, key, rn_f = await self.store.put_attachment(
                tenant=tenant,
                project=project,
                user=user,
                fingerprint=None,
                conversation_id=conversation_id,
                filename=name,
                data=data,
                mime=info.get("mime") or "application/octet-stream",
                user_type=user_type,
                turn_id=turn_id,
                request_id=rid,
            )
            files_rehosted.append({
                "slot": info.get("slot") or "",
                "key": key,
                "filename": name,
                "mime": info.get("mime") or "application/octet-stream",
                "size": len(data),
                "tool_id": info.get("tool_id") or "",
                "description": info.get("description") or "",
                "owner_id": user,
                "rn": rn_f,
                "hosted_uri": uri,
                "local_path": str(p),
            })
        return files_rehosted

    async def persist_workspace(
        self,
        *,
        outdir: Optional[str],
        workdir: Optional[str],
        tenant: str,
        project: str,
        user: Optional[str],
        conversation_id: str,
        user_type: str,
        turn_id: str,
        codegen_run_id: str,
        fingerprint: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Persist execution snapshot (out/work trees) into ConversationStore.
        Mirrors BaseWorkflow._snapshot_execution_tree.
        """
        if not self.store:
            return None
        if not (tenant and project and conversation_id and turn_id and codegen_run_id):
            return None
        try:
            return await self.store.put_execution_snapshot(
                tenant=tenant,
                project=project,
                user=user,
                user_type=user_type,
                fingerprint=fingerprint,
                conversation_id=conversation_id,
                turn_id=turn_id,
                codegen_run_id=codegen_run_id,
                out_dir=outdir,
                pkg_dir=workdir,
            )
        except Exception as exc:
            try:
                self.log.log(f"[persist_workspace] failed: {exc}", level="ERROR")
            except Exception:
                pass
            return None

    async def emit_solver_artifacts(self, *, files: List[Dict[str, Any]], citations: List[Dict[str, Any]]) -> None:
        """
        Emits chat events for batch files + citations.
        """
        if not self.comm:
            return
        from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.artifacts import normalize_file_payload
        cleaned_files: List[Dict[str, Any]] = []
        for item in files or []:
            if not isinstance(item, dict):
                continue
            payload = dict(item)
            data = payload.get("data")
            if isinstance(data, dict):
                meta = data.get("meta")
                if isinstance(meta, dict):
                    meta = normalize_file_payload(meta)
                    data = dict(data)
                    data["meta"] = meta
                    payload["data"] = data
            cleaned_files.append(payload)
        if files:
            await self.comm.event(
                agent="tooling",
                type="chat.files",
                title=f"Files Ready ({len(files)})",
                step="files",
                status="completed",
                data={"count": len(cleaned_files), "items": cleaned_files},
            )
        if citations:
            await self.comm.event(
                agent="tooling",
                type="chat.citations",
                title=f"Citations ({len(citations)})",
                step="citations",
                status="completed",
                data={"count": len(citations), "items": citations},
            )
