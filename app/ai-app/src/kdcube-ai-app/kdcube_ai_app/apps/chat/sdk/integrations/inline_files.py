# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Inline payload files for turn-less transports.

Integration tools load outbound files (mail attachments, Slack uploads) from
the current turn's artifact workspace. External MCP clients have no turn — but
they DO hold the bytes, so named services accept them inline (base64) in the
action payload and stage them into a workspace the tools can read:

- inside chat, the CURRENT turn workspace is used (files land next to other
  turn artifacts);
- on turn-less transports, a disposable workspace + synthetic turn id are
  bound around the single tool call and deleted afterwards.

The tools stay untouched: they keep resolving ``attachment_paths`` /
``file_path`` exactly as in chat.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import mimetypes
import pathlib
import shutil
import tempfile
import uuid
from typing import Any, AsyncIterator, Iterator, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime import run_ctx
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    bind_current_request_context,
    get_current_request_context,
)
from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace import artifact_outdir_for
from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace.references import (
    CONVERSATION_FILE_REF_PREFIX,
)

MAX_INLINE_FILE_BYTES = 10 * 1024 * 1024
MAX_INLINE_TOTAL_BYTES = 25 * 1024 * 1024
_INLINE_DIRNAME = "inline-payload-files"


class InlineFileError(ValueError):
    """One inline payload file is unusable (name, encoding, or size)."""


def _safe_filename(raw: str) -> str:
    name = pathlib.PurePosixPath(str(raw or "").replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        raise InlineFileError("Every inline file needs a plain filename (no directories).")
    return name


def _decode_content(entry: Mapping[str, Any], *, filename: str) -> bytes:
    raw = entry.get("data")
    if isinstance(raw, (bytes, bytearray)):
        # Adapter-resolved bytes (staged uploads); size was enforced at upload.
        if not raw:
            raise InlineFileError(f"Inline file {filename!r} carries zero bytes.")
        return bytes(raw)
    encoded = str(entry.get("content_base64") or "").strip()
    if not encoded:
        raise InlineFileError(f"Inline file {filename!r} carries no content_base64.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InlineFileError(f"Inline file {filename!r} content_base64 is not valid base64.") from exc
    if not data:
        raise InlineFileError(f"Inline file {filename!r} decoded to zero bytes.")
    if len(data) > MAX_INLINE_FILE_BYTES:
        raise InlineFileError(
            f"Inline file {filename!r} is larger than the {MAX_INLINE_FILE_BYTES}-byte inline limit."
        )
    return data


def _declared_mime(entry: Mapping[str, Any], *, filename: str) -> str:
    return (
        str(entry.get("mime") or entry.get("mime_type") or "").strip().lower()
        or str(mimetypes.guess_type(filename)[0] or "").strip().lower()
    )


def _validate_common_file_integrity(data: bytes, *, filename: str, mime: str) -> None:
    """Reject obviously truncated inline media before a provider accepts garbage.

    This is intentionally small and format-specific. Inline base64 is a last
    resort transport where truncation can still decode to bytes; staged upload
    remains the preferred path for large or arbitrary files.
    """
    suffix = pathlib.PurePosixPath(filename).suffix.lower()
    if mime == "image/png" or suffix == ".png" or data.startswith(b"\x89PNG\r\n\x1a\n"):
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise InlineFileError(f"Inline file {filename!r} is not a valid PNG file.")
        if not data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"):
            raise InlineFileError(f"Inline file {filename!r} is an incomplete PNG file.")
    elif mime == "image/jpeg" or suffix in {".jpg", ".jpeg"} or data.startswith(b"\xff\xd8"):
        if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
            raise InlineFileError(f"Inline file {filename!r} is an incomplete JPEG file.")
    elif mime == "image/gif" or suffix == ".gif" or data.startswith((b"GIF87a", b"GIF89a")):
        if not data.startswith((b"GIF87a", b"GIF89a")) or not data.endswith(b";"):
            raise InlineFileError(f"Inline file {filename!r} is an incomplete GIF file.")
    elif mime == "image/webp" or suffix == ".webp" or (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        if len(data) < 12 or not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
            raise InlineFileError(f"Inline file {filename!r} is not a valid WebP file.")
        expected = int.from_bytes(data[4:8], "little") + 8
        if expected != len(data):
            raise InlineFileError(f"Inline file {filename!r} is an incomplete WebP file.")


def materialize_inline_files(
    artifact_root: pathlib.Path,
    entries: list[Any],
    *,
    validate_integrity: bool = False,
) -> list[dict[str, Any]]:
    """Write inline payload files under the artifact root.

    Returns ``[{relpath, filename, mime, size_bytes}]``; ``relpath`` is what
    the integration tools accept in ``attachment_paths`` / ``file_path``.
    Raises :class:`InlineFileError` on any bad entry — callers fail the whole
    action rather than sending a partial set.
    """
    staged: list[dict[str, Any]] = []
    total = 0
    batch = uuid.uuid4().hex[:12]
    for index, entry in enumerate(entries or []):
        if not isinstance(entry, Mapping):
            raise InlineFileError(f"Inline file #{index} must be an object with filename and content_base64.")
        filename = _safe_filename(entry.get("filename") or entry.get("name") or "")
        data = _decode_content(entry, filename=filename)
        mime = _declared_mime(entry, filename=filename)
        if validate_integrity:
            _validate_common_file_integrity(data, filename=filename, mime=mime)
        total += len(data)
        if total > MAX_INLINE_TOTAL_BYTES:
            raise InlineFileError(
                f"Inline files exceed the {MAX_INLINE_TOTAL_BYTES}-byte total inline limit."
            )
        rel = pathlib.PurePosixPath(_INLINE_DIRNAME) / batch / str(index) / filename
        target = pathlib.Path(artifact_root) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        staged.append(
            {
                "relpath": rel.as_posix(),
                "filename": filename,
                "mime": mime or "application/octet-stream",
                "size_bytes": len(data),
            }
        )
    if not staged:
        raise InlineFileError("No inline files were provided.")
    return staged


def resolve_payload_file_entries(
    entries: list[Any],
    *,
    staging_root: pathlib.Path | None,
    owner,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize action payload file entries for staging.

    Each entry carries either ``staged_ref`` (preferred: bytes were PUT to the
    signed upload URL, never through the model) or ``content_base64`` (small
    inline fallback). Returns ``(normalized_entries, consumed_staged_refs)``;
    staged entries become ``{filename, data, mime}``. Raises
    :class:`InlineFileError` on unknown refs or missing staging support.
    """
    from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import load_staged

    normalized: list[dict[str, Any]] = []
    consumed: list[str] = []
    for index, entry in enumerate(entries or []):
        if not isinstance(entry, Mapping):
            raise InlineFileError(
                f"File entry #{index} must be an object with staged_ref or filename+content_base64."
            )
        staged_ref = str(entry.get("staged_ref") or "").strip()
        if staged_ref:
            if staging_root is None:
                raise InlineFileError("This deployment has no upload staging configured.")
            try:
                filename, data = load_staged(staging_root, staged_ref, owner=owner)
            except (FileNotFoundError, ValueError) as exc:
                raise InlineFileError(str(exc)) from exc
            normalized.append(
                {
                    "filename": str(entry.get("filename") or filename),
                    "data": data,
                    "mime": str(entry.get("mime") or entry.get("mime_type") or ""),
                }
            )
            consumed.append(staged_ref)
        else:
            normalized.append(dict(entry))
    return normalized, consumed


@contextlib.asynccontextmanager
async def materialized_conversation_file_ref(
    file_ref: str,
    *,
    tenant: str,
    project: str,
    user_id: str,
    conversation_id: str = "",
    storage_path: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Materialize a current actor's durable ``conv:fi:`` ref into a tool workspace."""
    ref = str(file_ref or "").strip()
    if not ref.startswith(CONVERSATION_FILE_REF_PREFIX):
        raise InlineFileError("Conversation file refs must start with conv:fi:.")

    tenant_id = str(tenant or "").strip()
    project_id = str(project or "").strip()
    owner_id = str(user_id or "").strip()
    if not tenant_id or not project_id or not owner_id:
        raise InlineFileError("Conversation file refs require a bound tenant, project, and user.")

    from kdcube_ai_app.apps.chat.sdk.runtime.harness import workspace as sdk_workspace

    with inline_files_workspace() as artifact_root:
        reports = await sdk_workspace.pull_refs_into_dir(
            refs=[ref],
            dest_dir=artifact_root,
            tenant=tenant_id,
            project=project_id,
            user_id=owner_id,
            conversation_id=str(conversation_id or "").strip(),
            storage_path=storage_path,
        )
        report = reports[0] if reports else {}
        if not report.get("ok"):
            raise InlineFileError("Conversation file is not available to the current user or does not exist.")
        yield report


def _current_turn_id() -> str:
    ctx = get_current_request_context()
    return str(getattr(getattr(ctx, "routing", None), "turn_id", "") or "").strip()


def has_turn_workspace() -> bool:
    """True when the caller runs inside a chat turn with a bound artifact workspace.

    Providers use this to speak the right file contract per surface: in a turn,
    files travel by workspace path/ref; on turn-less transports (MCP), callers
    hold the bytes themselves and stage them via upload or inline entries.
    """
    existing_outdir = str(run_ctx.OUTDIR_CV.get("") or "").strip()
    return bool(existing_outdir and _current_turn_id())


@contextlib.contextmanager
def inline_files_workspace() -> Iterator[pathlib.Path]:
    """Yield an artifact root the workspace-coupled tools will resolve against.

    When the calling context already has a turn workspace (chat), that
    workspace is yielded untouched. Otherwise a disposable outdir plus a
    synthetic turn id are bound for the scope (contextvars are task-local, so
    the binding covers exactly the wrapped tool call) and removed afterwards.
    """
    existing_outdir = str(run_ctx.OUTDIR_CV.get("") or "").strip()
    if existing_outdir and _current_turn_id():
        yield artifact_outdir_for(pathlib.Path(existing_outdir), create=True)
        return

    request_context = get_current_request_context()
    routing = getattr(request_context, "routing", None)
    if request_context is None or routing is None:
        raise InlineFileError(
            "Inline files need a bound request identity; this transport carries none."
        )
    tmpdir = tempfile.mkdtemp(prefix="kdcube-inline-")
    outdir_token = run_ctx.OUTDIR_CV.set(tmpdir)
    try:
        if _current_turn_id():
            yield artifact_outdir_for(pathlib.Path(tmpdir), create=True)
        else:
            patched = request_context.model_copy(deep=True)
            patched.routing.turn_id = f"turn_inline_{uuid.uuid4().hex[:12]}"
            with bind_current_request_context(patched):
                yield artifact_outdir_for(pathlib.Path(tmpdir), create=True)
    finally:
        run_ctx.OUTDIR_CV.reset(outdir_token)
        shutil.rmtree(tmpdir, ignore_errors=True)


__all__ = [
    "InlineFileError",
    "MAX_INLINE_FILE_BYTES",
    "MAX_INLINE_TOTAL_BYTES",
    "has_turn_workspace",
    "inline_files_workspace",
    "materialize_inline_files",
    "materialized_conversation_file_ref",
    "resolve_payload_file_entries",
]
