# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/service_hub/multimodality.py

import base64
import io
import logging
import math
import re
from typing import Dict, Any, Optional

from PIL import Image

MODALITY_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MODALITY_DOC_MIME = {"application/pdf"}

# Default attachment byte caps. The effective caps are descriptor-owned
# (assembly.yaml ``platform.attachments``); read them through
# ``max_image_bytes()``, ``max_doc_bytes()`` and ``max_message_bytes()``.
MODALITY_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
MODALITY_MAX_DOC_BYTES = 10 * 1024 * 1024   # 10 MB
MODALITY_MAX_IMAGE_DIMENSION_PX = 8000

MESSAGE_MAX_BYTES = 25 * 1024 * 1024  # total message size cap (text + attachments); keep margin

ATTACHMENT_LIMITS_PATH = "platform.attachments"

logger = logging.getLogger(__name__)


def _descriptor_byte_cap(field: str, default: int) -> int:
    """One positive byte cap from ``platform.attachments.<field>``.

    Absent -> ``default``. A value that is not a positive integer is logged
    and ignored, so a malformed descriptor keeps the default cap.
    """
    path = f"{ATTACHMENT_LIMITS_PATH}.{field}"
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_plain

        raw = get_plain(f"a:{path}", None)
    except Exception:
        # A process without platform settings (an isolated executor, a bare
        # script) has no assembly descriptor to read: the default applies.
        logger.debug("assembly %s unavailable; using %d", path, default, exc_info=True)
        return default
    if raw is None or raw == "":
        return default
    try:
        if isinstance(raw, bool):
            raise ValueError("boolean")
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("assembly %s=%r is not an integer byte count; using %d", path, raw, default)
        return default
    if value <= 0:
        logger.warning("assembly %s=%r must be positive; using %d", path, raw, default)
        return default
    return value


def max_image_bytes() -> int:
    """Per-image attachment cap (``platform.attachments.max_image_bytes``)."""
    return _descriptor_byte_cap("max_image_bytes", MODALITY_MAX_IMAGE_BYTES)


def max_doc_bytes() -> int:
    """Per-PDF attachment cap (``platform.attachments.max_doc_bytes``)."""
    return _descriptor_byte_cap("max_doc_bytes", MODALITY_MAX_DOC_BYTES)


def max_message_bytes() -> int:
    """Total message cap, text plus attachments (``platform.attachments.max_message_bytes``)."""
    return _descriptor_byte_cap("max_message_bytes", MESSAGE_MAX_BYTES)

_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")

_IMAGE_FORMAT_BY_MIME = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
}


def _image_format_for_mime(media_type: str) -> str:
    mime = (media_type or "").strip().lower()
    return _IMAGE_FORMAT_BY_MIME.get(mime, "PNG")


def validate_image_bytes(raw: bytes, *, media_type: str = "") -> Dict[str, Any]:
    """Verify that bytes decode as the declared provider-supported image type."""
    result: Dict[str, Any] = {
        "valid": False,
        "error": "invalid_image_data",
        "media_type": (media_type or "").strip().lower(),
        "format": None,
        "width": None,
        "height": None,
        "size_bytes": len(raw or b""),
    }
    if not raw:
        result["error"] = "empty_image_data"
        return result
    if result["media_type"] and result["media_type"] not in _IMAGE_FORMAT_BY_MIME:
        result["error"] = "unsupported_image_media_type"
        return result

    try:
        with Image.open(io.BytesIO(raw)) as image:
            detected_format = str(image.format or "").strip().upper()
            width, height = image.size
            image.verify()
        # ``verify`` checks the container without decoding pixels. Reopen and
        # load so provider-bound validation proves the payload is decodable.
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
    except Exception as exc:
        result["detail"] = type(exc).__name__
        return result

    result.update({
        "format": detected_format,
        "width": width,
        "height": height,
    })
    expected_format = _IMAGE_FORMAT_BY_MIME.get(result["media_type"])
    if expected_format and detected_format != expected_format:
        result["error"] = "image_mime_mismatch"
        result["expected_format"] = expected_format
        return result
    if width <= 0 or height <= 0:
        result["error"] = "invalid_image_dimensions"
        return result

    result["valid"] = True
    result["error"] = None
    return result


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
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Downscale oversized raster images before they are sent to multimodal models.

    This closes a gap where an image can be small in bytes (highly compressible PNG)
    but still exceed a provider's maximum edge length.
    """
    result: Dict[str, Any] = {
        "base64": base64_data,
        "valid": False,
        "error": "empty_image_data",
        "changed": False,
        "original_width": None,
        "original_height": None,
        "width": None,
        "height": None,
        "original_size_bytes": None,
        "size_bytes": None,
        "max_bytes": max_bytes,
        "byte_limited": False,
    }
    if not base64_data:
        return result

    try:
        raw = base64.b64decode(base64_data, validate=True)
        result["original_size_bytes"] = len(raw)
        result["size_bytes"] = len(raw)
    except Exception as exc:
        result["error"] = "invalid_image_base64"
        result["detail"] = type(exc).__name__
        return result

    validation = validate_image_bytes(raw, media_type=media_type)
    result.update({
        "valid": bool(validation.get("valid")),
        "error": validation.get("error"),
        "detail": validation.get("detail"),
        "format": validation.get("format"),
        "original_width": validation.get("width"),
        "original_height": validation.get("height"),
        "width": validation.get("width"),
        "height": validation.get("height"),
    })
    if not result["valid"]:
        logger.warning(
            "Rejected invalid multimodal image before model input: media_type=%s error=%s detail=%s size_bytes=%s",
            media_type,
            result.get("error"),
            result.get("detail"),
            result.get("size_bytes"),
        )
        return result

    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            orig_width, orig_height = image.size
            result["original_width"] = orig_width
            result["original_height"] = orig_height
            result["width"] = orig_width
            result["height"] = orig_height

            max_edge = max(orig_width, orig_height)
            byte_cap = int(max_bytes or 0)
            if max_edge <= max_dimension_px and (byte_cap <= 0 or len(raw) <= byte_cap):
                return result

            scale = 1.0
            if max_edge > max_dimension_px:
                scale = min(scale, float(max_dimension_px) / float(max_edge))
            new_width = max(1, int(round(orig_width * scale)))
            new_height = max(1, int(round(orig_height * scale)))
            resized = image.copy()
            if (new_width, new_height) != image.size:
                resized = resized.resize((new_width, new_height), _LANCZOS)
            new_raw = _serialize_image(resized, media_type=media_type)

            if byte_cap > 0 and len(new_raw) > byte_cap:
                result["byte_limited"] = True
                # Resize geometrically until the serialized payload fits the
                # visible-context byte budget. This is intentionally lossy only
                # for derived model previews; the original artifact is untouched.
                for _ in range(14):
                    current_edge = max(resized.width, resized.height)
                    if current_edge <= 64:
                        break
                    shrink = max(0.25, min(0.85, math.sqrt(byte_cap / max(1, len(new_raw))) * 0.92))
                    next_width = max(1, int(round(resized.width * shrink)))
                    next_height = max(1, int(round(resized.height * shrink)))
                    if (next_width, next_height) == resized.size:
                        next_width = max(1, resized.width - 1)
                        next_height = max(1, resized.height - 1)
                    resized = resized.resize((next_width, next_height), _LANCZOS)
                    new_raw = _serialize_image(resized, media_type=media_type)
                    if len(new_raw) <= byte_cap:
                        break

                if len(new_raw) > byte_cap:
                    result["valid"] = False
                    result["error"] = "image_exceeds_model_byte_limit"
                    return result
    except Exception as exc:
        logger.warning(
            "Failed to normalize multimodal image; omitting it from model input: %s",
            exc,
        )
        result["valid"] = False
        result["error"] = "image_normalization_failed"
        result["detail"] = type(exc).__name__
        return result

    result.update(
        {
            "base64": base64.b64encode(new_raw).decode("ascii"),
            "changed": True,
            "width": resized.width,
            "height": resized.height,
            "size_bytes": len(new_raw),
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
