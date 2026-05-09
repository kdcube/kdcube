# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/service_hub/multimodality.py

import base64
import io
import logging
import math
import re
from typing import Dict, Any

from PIL import Image

MODALITY_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MODALITY_DOC_MIME = {"application/pdf"}

MODALITY_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
MODALITY_MAX_DOC_BYTES = 10 * 1024 * 1024   # 10 MB
MODALITY_MAX_IMAGE_DIMENSION_PX = 8000

MESSAGE_MAX_BYTES = 25 * 1024 * 1024  # total message size cap (text + attachments); keep margin

logger = logging.getLogger(__name__)
_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def _image_format_for_mime(media_type: str) -> str:
    mime = (media_type or "").strip().lower()
    if mime == "image/jpeg":
        return "JPEG"
    if mime == "image/webp":
        return "WEBP"
    if mime == "image/gif":
        return "GIF"
    return "PNG"


def _prepare_image_for_save(image: Image.Image, fmt: str) -> Image.Image:
    if fmt == "JPEG":
        if image.mode not in ("RGB", "L"):
            return image.convert("RGB")
        return image
    if fmt in {"PNG", "WEBP"}:
        if image.mode in ("RGBA", "LA", "RGB", "L"):
            return image
        if "transparency" in image.info:
            return image.convert("RGBA")
        return image.convert("RGB")
    if fmt == "GIF":
        if image.mode == "P":
            return image
        return image.convert("P", palette=Image.ADAPTIVE)
    return image


def _serialize_image(image: Image.Image, *, media_type: str) -> bytes:
    fmt = _image_format_for_mime(media_type)
    prepared = _prepare_image_for_save(image, fmt)
    out = io.BytesIO()
    save_kwargs: Dict[str, Any] = {}
    if fmt == "JPEG":
        save_kwargs.update(optimize=True, quality=95)
    elif fmt == "PNG":
        save_kwargs.update(optimize=True)
    elif fmt == "WEBP":
        save_kwargs.update(quality=95, method=6)
    elif fmt == "GIF":
        save_kwargs.update(optimize=True)
    prepared.save(out, format=fmt, **save_kwargs)
    return out.getvalue()


def normalize_image_base64_for_model(
    base64_data: str,
    *,
    media_type: str = "image/png",
    max_dimension_px: int = MODALITY_MAX_IMAGE_DIMENSION_PX,
) -> Dict[str, Any]:
    """
    Downscale oversized raster images before they are sent to multimodal models.

    This closes a gap where an image can be small in bytes (highly compressible PNG)
    but still exceed a provider's maximum edge length.
    """
    result: Dict[str, Any] = {
        "base64": base64_data,
        "changed": False,
        "original_width": None,
        "original_height": None,
        "width": None,
        "height": None,
    }
    if not base64_data:
        return result

    try:
        raw = base64.b64decode(base64_data, validate=True)
    except Exception:
        return result

    try:
        with Image.open(io.BytesIO(raw)) as image:
            orig_width, orig_height = image.size
            result["original_width"] = orig_width
            result["original_height"] = orig_height
            result["width"] = orig_width
            result["height"] = orig_height

            max_edge = max(orig_width, orig_height)
            if max_edge <= max_dimension_px:
                return result

            scale = float(max_dimension_px) / float(max_edge)
            new_width = max(1, int(round(orig_width * scale)))
            new_height = max(1, int(round(orig_height * scale)))
            resized = image.copy().resize((new_width, new_height), _LANCZOS)
            new_raw = _serialize_image(resized, media_type=media_type)
    except Exception as exc:
        logger.warning(
            "Failed to inspect/normalize multimodal image; leaving original payload untouched: %s",
            exc,
        )
        return result

    result.update(
        {
            "base64": base64.b64encode(new_raw).decode("ascii"),
            "changed": True,
            "width": resized.width,
            "height": resized.height,
        }
    )
    logger.info(
        "Normalized multimodal image for model input: %sx%s -> %sx%s (%s)",
        orig_width,
        orig_height,
        resized.width,
        resized.height,
        media_type,
    )
    return result

def estimate_image_tokens_from_base64(base64_data: str) -> int:
    """
    Estimate Claude image tokens from dimensions.

    Args:
        base64_data: Base64-encoded image data

    Returns:
        Estimated token cost. Most current Claude models process images up to
        roughly 1.6k native image tokens; oversized images are downscaled by the
        provider before vision tokenization.
    """
    if not base64_data:
        return 0

    try:
        raw = base64.b64decode(base64_data, validate=False)
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
        if width > 0 and height > 0:
            return max(1, min(1600, int(math.ceil((width * height) / 750.0))))
    except Exception:
        pass

    size_bytes = len(base64_data) * 3 / 4  # base64 -> bytes
    kb = size_bytes / 1024

    if kb < 200:
        return 150
    elif kb < 500:
        return 400
    else:
        return 1600

def estimate_tokens(text: str, *, divisor: int = 4) -> int:
    if not text:
        return 0
    return max(1, len(text) // max(1, divisor))

def estimate_pdf_tokens_from_base64(base64_data: str) -> int:
    """
    Estimate Claude PDF tokens from page count.

    Claude processes PDFs as extracted page text plus page images. The exact
    count is provider-side, but page count gives a better local estimate than
    counting base64 bytes.

    Args:
        base64_data: Base64-encoded PDF data

    Returns:
        Estimated token cost.
    """
    if not base64_data:
        return 0

    estimated_pages = 0
    try:
        raw = base64.b64decode(base64_data, validate=False)
        estimated_pages = len(re.findall(rb"/Type\s*/Page(?!s)\b", raw))
    except Exception:
        raw = b""

    if estimated_pages <= 0:
        size_bytes = len(base64_data) * 3 / 4
        # Fallback: 50-100KB per page is common for generated PDFs.
        estimated_pages = max(1, int(math.ceil(size_bytes / 75_000.0)))

    return max(1, estimated_pages) * 4100
