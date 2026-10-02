# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Short-lived signed download tokens for conversation file artifacts.

MCP tool results are JSON: bytes can only ride inline as base64, which lands in
the model's context. For binary `conv:fi:` artifacts (images, spreadsheets, PDFs)
we instead hand the external client a short-lived HTTP download URL it fetches
out-of-band — the bytes never enter the model's context.

The token is a stateless HMAC-SHA256 stamp (`<b64url body>.<b64url sig>`) bound to
the exact artifact and requester: the download route re-derives who and what from
the *verified* token, never from the (unauthenticated) public request. Mirrors the
automations artifact-download token so the two surfaces stay consistent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict

TOKEN_VERSION = 1
DEFAULT_TTL_SECONDS = 900
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 86400


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _as_secret(secret: Any) -> bytes:
    if isinstance(secret, (bytes, bytearray)):
        return bytes(secret)
    return str(secret or "").encode("utf-8")


def _clamp_ttl(ttl_seconds: int) -> int:
    try:
        ttl = int(ttl_seconds)
    except Exception:
        ttl = DEFAULT_TTL_SECONDS
    return max(MIN_TTL_SECONDS, min(ttl, MAX_TTL_SECONDS))


def mint_file_download_token(
    secret: Any,
    *,
    fi_ref: str,
    user_id: str,
    conversation_id: str = "",
    tenant: str = "",
    project: str = "",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
    include_identity: bool = True,
) -> tuple[str, int]:
    """Mint a signed download token bound to a single artifact + requester.

    ``include_identity=False`` leaves tenant, project, user and conversation
    out of the payload. Use it for a URL handed to a third party that records
    it: a document provider keeps the fetch URL in the file it inserts, so
    whatever the payload holds becomes readable by everyone who opens that
    file. The ref is already unguessable, and the route it addresses needs
    nothing else.

    Returns ``(token, expires_at)``. The token carries everything the download
    route needs to re-materialize the file (tenant/project/user/conversation), so
    the route trusts the *signature*, not the public request. Raises ValueError if
    no secret is available (callers must fail closed rather than emit an
    unverifiable URL).
    """
    key = _as_secret(secret)
    if not key:
        raise ValueError("download token signing secret is not configured")
    issued = int(now if now is not None else time.time())
    expires_at = issued + _clamp_ttl(ttl_seconds)
    payload = {
        "v": TOKEN_VERSION,
        "fi_ref": str(fi_ref or "").strip(),
        "exp": expires_at,
    }
    if include_identity:
        payload.update(
            {
                "user_id": str(user_id or "").strip(),
                "conversation_id": str(conversation_id or "").strip(),
                "tenant": str(tenant or "").strip(),
                "project": str(project or "").strip(),
            }
        )
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url_encode(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}", expires_at


def verify_file_download_token(
    secret: Any,
    token: str,
    *,
    fi_ref: str,
    now: int | None = None,
    require_user_scope: bool = True,
) -> Dict[str, Any]:
    """Verify a download token and return its payload dict.

    Checks signature, expiry, and that the token was minted for exactly this
    ``fi_ref``. Raises ValueError on any mismatch — the caller maps that to a 4xx.
    """
    key = _as_secret(secret)
    if not key:
        raise ValueError("download token signing secret is not configured")
    try:
        body, sig = str(token or "").strip().split(".", 1)
    except ValueError:
        raise ValueError("download token is malformed")
    expected = _b64url_encode(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise ValueError("download token signature is invalid")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise ValueError("download token payload is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("download token payload is invalid")
    if str(payload.get("fi_ref") or "") != str(fi_ref or "").strip():
        raise ValueError("download token does not match the requested file")
    try:
        expires_at = int(payload.get("exp") or 0)
    except Exception:
        expires_at = 0
    current = int(now if now is not None else time.time())
    if expires_at < current:
        raise ValueError("download token is expired")
    if require_user_scope and not str(payload.get("user_id") or "").strip():
        raise ValueError("download token does not include a user scope")
    return payload


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "mint_file_download_token",
    "verify_file_download_token",
]
