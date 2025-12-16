# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/solution_workspace.py

import traceback, pathlib, logging

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