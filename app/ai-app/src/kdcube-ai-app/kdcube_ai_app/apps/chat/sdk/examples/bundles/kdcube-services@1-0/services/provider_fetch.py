"""Serve one staged file to a provider that fetches it anonymously.

A provider such as Google Docs fetches a file this deployment staged for one
call, from the public ``provider_fetch_download`` route, with no identity of its
own. The signed token is the whole authorization: it binds the staged ref and
the media type the staging action measured the bytes as. The route serves
exactly that type and never derives one from the staged name, always with
``nosniff``, and serves anything outside a display-only set as an attachment,
so the platform's origin never renders active content from it.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import load_staged
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.download_links import (
    verify_file_download_token,
)

# Types a browser only displays; every other type is served as an attachment.
INLINE_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}
)


@dataclass(frozen=True)
class ProviderFetchAnswer:
    """What the route answers: file bytes with headers, or a JSON error."""

    status: int
    body: bytes = b""
    media_type: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    error: Dict[str, Any] | None = None


def provider_fetch_headers(media_type: str, size: int) -> tuple[str, Dict[str, str]]:
    """The served media type and headers for one fetch of ``size`` bytes.

    A token without a measured type serves opaque bytes.
    """

    served = str(media_type or "").strip().lower() or "application/octet-stream"
    headers = {
        "Content-Length": str(size),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if served not in INLINE_MEDIA_TYPES:
        headers["Content-Disposition"] = "attachment"
    return served, headers


def serve_provider_fetch(
    *, secret: str, root: pathlib.Path, ref: str, token: str
) -> ProviderFetchAnswer:
    """Verify the one-fetch token for ``ref`` and serve the staged bytes."""

    try:
        claims = verify_file_download_token(
            secret, token, fi_ref=ref, require_user_scope=False
        )
    except ValueError as exc:
        return ProviderFetchAnswer(
            status=403, error={"error": "fetch_token_rejected", "message": str(exc)}
        )
    try:
        _filename, data = load_staged(root, ref)
    except (FileNotFoundError, ValueError) as exc:
        # The window is one provider call; afterwards this is the answer.
        return ProviderFetchAnswer(
            status=404, error={"error": "fetch_file_gone", "message": str(exc)}
        )
    media_type, headers = provider_fetch_headers(
        str(claims.get("media_type") or ""), len(data)
    )
    return ProviderFetchAnswer(
        status=200, body=data, media_type=media_type, headers=headers
    )
