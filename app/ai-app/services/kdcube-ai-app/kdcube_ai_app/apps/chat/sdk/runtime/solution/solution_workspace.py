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

async def rehost_previous_files(prev_files: list[dict], workdir: pathlib.Path) -> list[dict]:
    """
    Copy readable (text) files from conversation storage into workdir/files/,
    update each file dict's 'path' to the new on-disk location,
    and annotate with {rehosted: bool, source_path: str}.
    Non-text files are passed through unchanged (rehosted: False).
    """
    from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
    from kdcube_ai_app.apps.chat.sdk.config import get_settings

    out: list[dict] = []
    if not prev_files:
        return out

    # files_dir = workdir / "files"
    files_dir = workdir
    files_dir.mkdir(parents=True, exist_ok=True)

    store = ConversationStore(get_settings().STORAGE_PATH)

    for file in prev_files:
        try:
            artifact = file.get("value") or {}
            # output = artifact.get("output") or {}
            mime = artifact.get("mime") or ""
            # src_path = output.get("path") or ""
            src_path = artifact.get("path") or ""
            if not src_path:
                # Nothing to rehost; pass through
                out.append({**artifact, "rehosted": False})
                continue
            if _is_text_mime(mime):
                # Read from conversation storage and write into workdir/files
                # src_path stored is RELATIVE within conversation store
                try:
                    content = await store.backend.read_text_a(src_path)
                except Exception:
                    # If we can't read it, pass through
                    out.append({**artifact, "rehosted": False, "rehost_error": "read_failed"})
                    continue

                target = src_path
                target.write_text(content, encoding="utf-8")
                artifact["source_path"] = src_path
                artifact["path"] = str(target)
                artifact["rehosted"] = True
                out.append(artifact)
            else:
                try:
                    content = await store.backend.read_bytes_a(src_path)
                except Exception:
                    # If we can't read it, pass through
                    out.append({**artifact, "rehosted": False, "rehost_error": "read_failed"})
                    continue

                basename = pathlib.Path(src_path).name
                target = _unique_target(files_dir, basename)
                target.write_bytes(content)
                artifact["source_path"] = src_path
                artifact["path"] = str(target)
                artifact["rehosted"] = True
                out.append(artifact)

        except Exception as e:
            logger.error(traceback.format_exc())

    return out