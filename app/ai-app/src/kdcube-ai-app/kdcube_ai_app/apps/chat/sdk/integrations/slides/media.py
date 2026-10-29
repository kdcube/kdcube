"""Bounded image-source traversal for composition and reference replacements."""
from __future__ import annotations

from collections.abc import Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.slides.elements import image_source
from kdcube_ai_app.apps.chat.sdk.integrations.slides.grid import invalid


def composition_images(spec, *, depth=0):
    if not isinstance(spec, Mapping) or depth > 3:
        invalid("Use a bounded composition map with at most three nested grids.")
    cells = spec.get("cells", [])
    if not isinstance(cells, list) or len(cells) > 100:
        invalid("Use at most 100 cells in each grid.")
    result = []
    for cell in cells:
        if not isinstance(cell, Mapping):
            invalid("Pass grid cells as maps.")
        if "grid" in cell:
            result.extend(composition_images(cell["grid"], depth=depth+1))
        else:
            element = cell.get("element", {})
            if not isinstance(element, Mapping):
                invalid("Pass each grid element as a map.")
            if any(key in element for key in ("image", "image_uri", "file_ref")):
                result.append(image_source(element))
        if len(result) > 100:
            invalid("Keep the complete composition within 100 images.")
    return result


def image_sources(operation, payload):
    if operation == "add_image":
        return [image_source(payload)]
    if operation == "compose":
        return composition_images(payload)
    if operation == "duplicate_slide_like":
        replacements = payload.get("replacements", {})
        if not isinstance(replacements, Mapping):
            invalid("Pass replacements as a map.")
        images = replacements.get("images", [])
        if not isinstance(images, list) or len(images) > 100:
            invalid("Pass at most 100 image replacements.")
        return [image_source(image) for image in images]
    return []
