"""Read a caller-scoped staged/conversation import source, with bounded bytes."""
from __future__ import annotations

import base64
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.integrations.slides.lifecycle import MAX_IMPORT_BYTES, import_bytes
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError


async def import_file_payload(ctx, payload, staging_root_factory):
    result = dict(payload)
    ref = result.pop("file_ref", None)
    if not ref:
        return result
    if any(result.get(key) for key in ("source", "source_file", "content_base64")):
        raise SlidesValidationError("slides_import_source_ambiguous", "Pass exactly one Drive source or file_ref.")
    from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import STAGED_REF_PREFIX, load_staged
    from kdcube_ai_app.apps.chat.sdk.integrations.inline_files import InlineFileError, materialized_conversation_file_ref
    try:
        if str(ref).startswith(STAGED_REF_PREFIX):
            if not staging_root_factory:
                raise SlidesValidationError("slides_import_file_missing", "The deployment's staging area is unavailable.")
            filename, data = load_staged(staging_root_factory(), ref)
        else:
            async with materialized_conversation_file_ref(ref, tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id or "", conversation_id=ctx.conversation_id or "") as materialized:
                path = Path(materialized["path"])
                if path.stat().st_size > MAX_IMPORT_BYTES:
                    raise SlidesValidationError("slides_import_too_large", "Import PPTX/ODP files up to 5 MB.")
                filename, data = path.name, path.read_bytes()
    except (FileNotFoundError, ValueError, InlineFileError) as exc:
        if isinstance(exc, SlidesValidationError):
            raise
        raise SlidesValidationError("slides_import_file_missing", "The import file is unavailable for this user and conversation.") from exc
    if len(data) > MAX_IMPORT_BYTES:
        raise SlidesValidationError("slides_import_too_large", "Import PPTX/ODP files up to 5 MB.")
    result.setdefault("source_format", Path(filename).suffix.lower().lstrip("."))
    result.setdefault("title", Path(filename).stem)
    result["content_base64"] = base64.b64encode(data).decode()
    import_bytes(result)  # Refuse a mismatched format before any provider call.
    return result
