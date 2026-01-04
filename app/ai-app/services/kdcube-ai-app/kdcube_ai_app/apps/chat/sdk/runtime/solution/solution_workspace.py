# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
import time
# kdcube_ai_app/apps/chat/sdk/runtime/solution/solution_workspace.py

import traceback, pathlib, logging
from typing import Any, Optional, List, Dict

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

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

async def rehost_previous_files(
        prev_files: list[dict],
        workdir: pathlib.Path,
        turn_id: str  # ← turn_id for this deliverable's source turn
) -> list[dict]:
    """
    Copy files from conversation storage into workdir/<turn_id>/,
    organizing historical files by their source turn.

    Structure:
      workdir/
        turn_1765841825124_s1lw9s/
          report.pdf
          data.xlsx
        turn_1765841834567_a2bc3d/
          chart.png

    Updated paths in artifacts reflect this structure: "turn_<id>/filename"

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
    turn_dir = workdir / turn_id
    turn_dir.mkdir(parents=True, exist_ok=True)

    store = ConversationStore(get_settings().STORAGE_PATH)

    for file in prev_files:
        artifact = {}
        try:
            artifact = file.get("value") or {}
            mime = artifact.get("mime") or ""
            src_path = (artifact.get("path") or "").strip()  # ← Normalize to stripped string
            is_draft = bool(artifact.get("draft"))

            # Handle missing/empty path (draft file slots)
            if not src_path:
                # Draft file slot with no actual file - skip rehosting
                # The surrogate text in artifact["text"] is still accessible
                logger.info(
                    f"[rehost] Skipping turn {turn_id} - "
                    f"{'draft ' if is_draft else ''}file slot with no path"
                )
                out.append({
                    **artifact,
                    "rehosted": False,
                    "file_exists": False,  # ← NEW: explicit flag
                    "rehost_reason": "no_source_path"  # ← Why rehosting was skipped
                })
                continue

            # Extract just the filename (no parent dirs from storage)
            filename = pathlib.Path(src_path).name
            if not filename:
                logger.warning(f"[rehost] Invalid path (no filename): {src_path}")
                out.append({
                    **artifact,
                    "rehosted": False,
                    "file_exists": False,
                    "rehost_reason": "invalid_path"
                })
                continue

            # Target: workdir/<turn_id>/filename
            target = turn_dir / filename

            # Attempt to read and write file
            try:
                if _is_text_mime(mime):
                    content = await store.backend.read_text_a(src_path)
                    target.write_text(content, encoding="utf-8")
                else:
                    content = await store.backend.read_bytes_a(src_path)
                    target.write_bytes(content)

                # Success - update artifact with new path
                out.append({
                    **artifact,
                    "source_path": src_path,  # Original storage path
                    "path": f"{turn_id}/{filename}",  # ← Turn-namespaced path
                    "rehosted": True,
                    "file_exists": True,  # ← File successfully copied
                })
                logger.info(f"[rehost] ✓ {turn_id}/{filename} from {src_path}")

            except FileNotFoundError:
                logger.warning(
                    f"[rehost] File not found in storage: {src_path} "
                    f"(turn {turn_id}, file: {filename})"
                )
                out.append({
                    **artifact,
                    "rehosted": False,
                    "file_exists": False,
                    "rehost_reason": "file_not_found",
                    "source_path": src_path
                })

            except Exception as read_err:
                logger.error(
                    f"[rehost] Failed to read {src_path}: {read_err}"
                )
                out.append({
                    **artifact,
                    "rehosted": False,
                    "file_exists": False,
                    "rehost_reason": "read_error",
                    "rehost_error": str(read_err)[:200],
                    "source_path": src_path
                })

        except Exception as e:
            logger.error(
                f"[rehost] Failed to process file artifact: {e}\n"
                f"{traceback.format_exc()}"
            )
            # Append original artifact unchanged with error flag
            out.append({
                **artifact,
                "rehosted": False,
                "file_exists": False,
                "rehost_reason": "processing_error",
                "rehost_error": str(e)[:200]
            })

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

    for att in prev_attachments:
        base = {}
        try:
            base = dict(att) if isinstance(att, dict) else {}
            if not base:
                out.append(base)
                continue

            filename = (base.get("filename") or "attachment.bin").strip() or "attachment.bin"
            src = base.get("hosted_uri") or base.get("source_path") or base.get("path") or base.get("key")
            if not src:
                out.append({
                    **base,
                    "rehosted": False,
                    "file_exists": False,
                    "rehost_reason": "no_source_path",
                })
                continue

            target = _unique_target(turn_dir, filename)
            try:
                content = await store.get_blob_bytes(src)
                target.write_bytes(content)
                out.append({
                    **base,
                    "source_path": src,
                    "path": f"{turn_id}/attachments/{target.name}",
                    "rehosted": True,
                    "file_exists": True,
                })
                logger.info(f"[rehost] ✓ {turn_id}/attachments/{target.name} from {src}")
            except FileNotFoundError:
                logger.warning(f"[rehost] Attachment not found in storage: {src}")
                out.append({
                    **base,
                    "rehosted": False,
                    "file_exists": False,
                    "rehost_reason": "file_not_found",
                    "source_path": src,
                })
            except Exception as read_err:
                logger.error(f"[rehost] Failed to read attachment {src}: {read_err}")
                out.append({
                    **base,
                    "rehosted": False,
                    "file_exists": False,
                    "rehost_reason": "read_error",
                    "rehost_error": str(read_err)[:200],
                    "source_path": src,
                })
        except Exception as e:
            logger.error(
                f"[rehost] Failed to process attachment: {e}\n"
                f"{traceback.format_exc()}"
            )
            out.append({
                **base,
                "rehosted": False,
                "file_exists": False,
                "rehost_reason": "processing_error",
                "rehost_error": str(e)[:200],
            })

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
        logger: Optional[logging.Logger] = None,
    ):
        self.store = store
        self.comm = comm
        self.log = logger or logging.getLogger(__name__)

    def _is_hosted_path(self, path: str) -> bool:
        if not isinstance(path, str) or not path.strip():
            return False
        p = path.strip()
        return p.startswith("cb/") or "://" in p

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
        track_id: str,
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
            if self._is_hosted_path(rel_or_abs):
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
                track_id=track_id,
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

    async def emit_solver_artifacts(self, *, files: List[Dict[str, Any]], citations: List[Dict[str, Any]]) -> None:
        """
        Emits chat events for batch files + citations.
        """
        if not self.comm:
            return
        if files:
            await self.comm.event(
                agent="tooling",
                type="chat.files",
                title=f"Files Ready ({len(files)})",
                step="files",
                status="completed",
                data={"count": len(files), "items": files},
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
