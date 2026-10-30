"""Request-scoped provider-fetch staging for Slides image replacements."""
from __future__ import annotations

from contextlib import asynccontextmanager
import io
import logging
import uuid
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError

LOGGER=logging.getLogger("kdcube.sdk.integrations.slides.images")


@asynccontextmanager
async def image_payload(ctx, payload, *, staging_root_factory, provider_fetch_url_factory):
    """Produce a private URL only for this call; remove only the copy we own."""
    from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import MAX_STAGED_FILE_BYTES, STAGED_REF_PREFIX, load_staged, new_staged_ref, save_staged, delete_staged
    from kdcube_ai_app.apps.chat.sdk.integrations.inline_files import materialized_conversation_file_ref, InlineFileError
    from kdcube_ai_app.infra.service_hub.multimodality import validate_image_bytes
    from PIL import Image

    result = dict(payload)
    result.pop("_preview_image_size", None)
    file_ref = result.pop("file_ref", None)
    if not file_ref:
        yield result
        return
    if result.get("image_uri"):
        raise SlidesValidationError("slides_image_source_ambiguous", "Pass either file_ref or image_uri.")
    if not staging_root_factory or not provider_fetch_url_factory:
        raise SlidesValidationError("slides_image_hosting_unavailable", "This deployment needs a public HTTPS provider-fetch origin to serve a staged image.")
    try:
        root = staging_root_factory()
    except Exception as exc:
        raise SlidesValidationError("slides_image_hosting_unavailable", "The deployment's image staging area is unavailable.") from exc
    if root is None:
        raise SlidesValidationError("slides_image_hosting_unavailable", "Configure the deployment's image staging area.")
    try:
        if str(file_ref).startswith(STAGED_REF_PREFIX):
            filename, data = load_staged(root, file_ref, owner=ctx)
        else:
            async with materialized_conversation_file_ref(file_ref, tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id or "", conversation_id=ctx.conversation_id or "") as materialized:
                path = Path(materialized["path"])
                if path.stat().st_size > MAX_STAGED_FILE_BYTES:
                    raise SlidesValidationError("slides_image_too_large", "Deployment file_ref images are limited to 25 MB by the shared staging store.")
                filename, data = path.name, path.read_bytes()
    except SlidesValidationError:
        raise
    except (FileNotFoundError, ValueError, InlineFileError) as exc:
        raise SlidesValidationError("slides_image_file_missing", "The image ref is unavailable for this user and conversation. Stage the image and use its staged ref.") from exc
    if len(data) > MAX_STAGED_FILE_BYTES:
        raise SlidesValidationError("slides_image_too_large", "Deployment file_ref images are limited to 25 MB by the shared staging store.")
    if len(data) >= 50*1024*1024:
        raise SlidesValidationError("slides_image_too_large", "Images must be smaller than 50 MB.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width*image.height > 25_000_000:
                raise SlidesValidationError("slides_image_too_large", "Images must contain at most 25 megapixels.")
    except SlidesValidationError:
        raise
    except Exception as exc:
        raise SlidesValidationError("slides_image_unexpected_type", "Use a valid PNG, JPEG or GIF image.") from exc
    measured = validate_image_bytes(data)
    mime = measured.get("media_type") or {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}.get(measured.get("format"), "")
    extensions = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}
    if not measured.get("valid") or mime not in extensions:
        raise SlidesValidationError("slides_image_unexpected_type", "Use a PNG, JPEG or GIF image.")
    if len(data) >= 50*1024*1024 or (measured.get("width", 0) * measured.get("height", 0)) > 25_000_000:
        raise SlidesValidationError("slides_image_too_large", "Images must be smaller than 50 MB and at most 25 megapixels.")
    if result.get("preview"):
        # Compile only; the placeholder URL is never returned or fetched.
        result["image_uri"] = "https://preview.invalid/" + uuid.uuid4().hex + "/image" + extensions[mime]
        result["_preview_image_size"] = {"width": measured["width"], "height": measured["height"]}
        yield result
        return
    staged = new_staged_ref("image" + extensions[mime])
    try:
        try:
            save_staged(root, staged, data, owner=ctx)
        except (OSError, ValueError) as exc:
            raise SlidesValidationError("slides_image_hosting_unavailable", "The deployment could not stage its owned image copy.") from exc
        try:
            minted = provider_fetch_url_factory(ctx, {"staged_ref": staged, "filename": filename, "media_type": mime})
            if hasattr(minted, "__await__"):
                minted = await minted
        except Exception as exc:
            raise SlidesValidationError("slides_image_hosting_unavailable", "The deployment could not issue a public image URL.") from exc
        url = str((minted or {}).get("url") or "")
        if not url.startswith("https://"):
            raise SlidesValidationError("slides_image_hosting_unavailable", "Configure the deployment's public HTTPS provider-fetch origin.")
        result["image_uri"] = url
        yield result
    finally:
        try:
            delete_staged(root, staged, owner=ctx)
        except Exception as exc:
            # Preserve the write outcome; the staging store's expiry is the
            # recovery path if filesystem cleanup is temporarily unavailable.
            LOGGER.warning("Slides owned-image cleanup failed: %s",type(exc).__name__)
