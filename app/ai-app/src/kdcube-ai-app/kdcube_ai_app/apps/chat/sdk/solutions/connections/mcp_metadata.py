from __future__ import annotations

from urllib.parse import urlsplit
from typing import Any


KDCUBE_ICON_PATH = "/img/favicon.svg"


def _request_public_base_url(request: Any = None) -> str:
    headers = getattr(request, "headers", None)
    if headers is not None:
        proto = str(headers.get("x-forwarded-proto") or headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip()
        host = str(
            headers.get("x-forwarded-host")
            or headers.get("X-Forwarded-Host")
            or headers.get("host")
            or headers.get("Host")
            or ""
        ).split(",", 1)[0].strip()
        if host:
            return f"{proto or 'https'}://{host}".rstrip("/")

    base_url = str(getattr(request, "base_url", "") or "").strip().rstrip("/")
    if not base_url:
        return ""
    parsed = urlsplit(base_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return ""


def _origin_url(value: str | None) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return text


def kdcube_icon_url(*, request: Any = None, public_base_url: str | None = None) -> str:
    base_url = _origin_url(public_base_url) or _request_public_base_url(request)
    return f"{base_url}{KDCUBE_ICON_PATH}" if base_url else ""


def kdcube_website_url(*, request: Any = None, public_base_url: str | None = None) -> str | None:
    base_url = _origin_url(public_base_url) or _request_public_base_url(request)
    return base_url or None


def kdcube_icon_descriptor(*, request: Any = None, public_base_url: str | None = None) -> dict[str, Any]:
    src = kdcube_icon_url(request=request, public_base_url=public_base_url)
    if not src:
        return {}
    return {
        "src": src,
        "mimeType": "image/svg+xml",
        "sizes": ["64x64"],
    }


def kdcube_mcp_icons(
    icon_cls: type[Any],
    *,
    request: Any = None,
    public_base_url: str | None = None,
) -> list[Any]:
    """KDCube icon metadata for MCP clients that render server/tool icons."""

    src = kdcube_icon_url(request=request, public_base_url=public_base_url)
    if not src:
        return []
    return [
        icon_cls(
            src=src,
            mimeType="image/svg+xml",
            sizes=["64x64"],
        )
    ]


def read_only_annotations(annotation_cls: type[Any], *, title: str | None = None) -> Any:
    return annotation_cls(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def write_annotations(annotation_cls: type[Any], *, title: str | None = None) -> Any:
    return annotation_cls(
        title=title,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )


def action_annotations(annotation_cls: type[Any], *, title: str | None = None) -> Any:
    return annotation_cls(
        title=title,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )


def destructive_annotations(annotation_cls: type[Any], *, title: str | None = None) -> Any:
    return annotation_cls(
        title=title,
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )


__all__ = [
    "KDCUBE_ICON_PATH",
    "action_annotations",
    "destructive_annotations",
    "kdcube_icon_descriptor",
    "kdcube_icon_url",
    "kdcube_mcp_icons",
    "kdcube_website_url",
    "read_only_annotations",
    "write_annotations",
]
