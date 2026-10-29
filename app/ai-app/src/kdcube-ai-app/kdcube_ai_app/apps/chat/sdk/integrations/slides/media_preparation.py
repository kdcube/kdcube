"""Request-owned image preparation across standalone, grid and reference actions."""
from __future__ import annotations

import copy

from kdcube_ai_app.apps.chat.sdk.integrations.slides.images import image_payload
from kdcube_ai_app.apps.chat.sdk.integrations.slides.elements import image_source
from kdcube_ai_app.apps.chat.sdk.integrations.slides.media import image_sources


async def prepare_action_images(ctx, action, payload, stack, *, staging_root_factory, provider_fetch_url_factory):
    result = copy.deepcopy(payload)
    preview = bool(result.get("preview"))
    image_sources(action, result)  # Validate bounded traversal before staging.

    async def prepare(source):
        return await stack.enter_async_context(image_payload(ctx, {**source, "preview": preview}, staging_root_factory=staging_root_factory, provider_fetch_url_factory=provider_fetch_url_factory))

    async def grid(spec):
        for cell in spec.get("cells", []):
            if "grid" in cell:
                await grid(cell["grid"])
                continue
            element = cell.get("element", {})
            if any(key in element for key in ("image", "image_uri", "file_ref")):
                source = image_source(element)
                prepared = await prepare(source)
                element.pop("file_ref", None)
                element.pop("image_uri", None)
                element["image"] = prepared

    if action == "add_image":
        result = await prepare(result)
    elif action == "compose":
        await grid(result)
    elif action == "duplicate_slide_like":
        replacements = result.get("replacements", {})
        replacements["images"] = [await prepare(image) for image in replacements.get("images", [])]
        result["replacements"] = replacements
    return result
