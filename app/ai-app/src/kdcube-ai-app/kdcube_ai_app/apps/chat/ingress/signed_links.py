# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class SignedLinkTokenError(ValueError):
    """Base error for signed short-lived link tokens."""


class SignedLinkTokenExpired(SignedLinkTokenError):
    """The token was well-formed but its expiry is in the past."""


class SignedLinkTokenInvalid(SignedLinkTokenError):
    """The token is malformed, has invalid claims, or has a bad signature."""


@dataclass(frozen=True)
class SignedLinkToken:
    token: str
    expires_at: int
    payload: Dict[str, Any]


@dataclass(frozen=True)
class SignedLink:
    url: str
    token: str
    expires_at: int
    payload: Dict[str, Any]


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _secret_bytes(secret: Union[str, bytes]) -> bytes:
    if isinstance(secret, bytes):
        value = secret
    else:
        value = str(secret or "").encode("utf-8")
    if not value:
        raise SignedLinkTokenInvalid("signed link secret is not configured")
    return value


def _compact_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def _bounded_ttl(ttl_seconds: int) -> int:
    try:
        ttl = int(ttl_seconds)
    except Exception as exc:
        raise SignedLinkTokenInvalid("signed link ttl must be an integer") from exc
    if ttl <= 0:
        raise SignedLinkTokenInvalid("signed link ttl must be positive")
    return ttl


def make_signed_link_token(
    secret: Union[str, bytes],
    *,
    subject: str,
    claims: Optional[Mapping[str, Any]] = None,
    ttl_seconds: int = 900,
    now: Optional[int] = None,
) -> SignedLinkToken:
    """
    Build a stateless HMAC token for a short-lived link.

    ``subject`` should be the exact resource or action being authorized, such
    as an artifact ref or canonical download path. Verification should pass
    the same subject, so a token minted for one resource cannot be replayed for
    another resource.
    """
    subject_value = str(subject or "").strip()
    if not subject_value:
        raise SignedLinkTokenInvalid("signed link subject is required")

    issued_at = int(time.time() if now is None else now)
    expires_at = issued_at + _bounded_ttl(ttl_seconds)
    payload: Dict[str, Any] = {
        "v": 1,
        "sub": subject_value,
        "iat": issued_at,
        "exp": expires_at,
        "claims": dict(claims or {}),
    }
    body = _b64url_encode(_compact_json(payload).encode("utf-8"))
    sig = _b64url_encode(
        hmac.new(_secret_bytes(secret), body.encode("ascii"), hashlib.sha256).digest()
    )
    return SignedLinkToken(token=f"{body}.{sig}", expires_at=expires_at, payload=payload)


def verify_signed_link_token(
    secret: Union[str, bytes],
    token: str,
    *,
    subject: Optional[str] = None,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Verify a signed short-lived token and return its payload.

    If ``subject`` is provided, the token must have been minted for exactly
    that subject.
    """
    try:
        body, sig = str(token or "").strip().split(".", 1)
    except ValueError as exc:
        raise SignedLinkTokenInvalid("signed link token is malformed") from exc
    if not body or not sig:
        raise SignedLinkTokenInvalid("signed link token is malformed")

    expected = _b64url_encode(
        hmac.new(_secret_bytes(secret), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected):
        raise SignedLinkTokenInvalid("signed link token signature is invalid")

    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise SignedLinkTokenInvalid("signed link token payload is invalid") from exc
    if not isinstance(payload, dict):
        raise SignedLinkTokenInvalid("signed link token payload is invalid")
    if payload.get("v") != 1:
        raise SignedLinkTokenInvalid("signed link token version is unsupported")

    token_subject = str(payload.get("sub") or "").strip()
    if subject is not None and token_subject != str(subject or "").strip():
        raise SignedLinkTokenInvalid("signed link token subject does not match")
    if not token_subject:
        raise SignedLinkTokenInvalid("signed link token subject is missing")

    try:
        expires_at = int(payload.get("exp") or 0)
    except Exception as exc:
        raise SignedLinkTokenInvalid("signed link token expiry is invalid") from exc
    if expires_at < int(time.time() if now is None else now):
        raise SignedLinkTokenExpired("signed link token is expired")

    claims = payload.get("claims")
    if claims is None:
        payload["claims"] = {}
    elif not isinstance(claims, dict):
        raise SignedLinkTokenInvalid("signed link token claims are invalid")
    return payload


def append_signed_link_token(url: str, token: str, *, param: str = "download_token") -> str:
    """
    Append or replace the signed token query parameter on a relative or absolute URL.
    """
    param_name = str(param or "").strip()
    if not param_name:
        raise SignedLinkTokenInvalid("signed link token query parameter is required")
    parts = urlsplit(str(url or ""))
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k != param_name
    ]
    query.append((param_name, str(token or "")))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _url_without_token_param(url: str, *, param: str) -> str:
    parts = urlsplit(str(url or ""))
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k != param
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def make_signed_link(
    url: str,
    secret: Union[str, bytes],
    *,
    subject: Optional[str] = None,
    claims: Optional[Mapping[str, Any]] = None,
    ttl_seconds: int = 900,
    token_param: str = "download_token",
    now: Optional[int] = None,
) -> SignedLink:
    """
    Mint a token and append it to ``url``.

    If ``subject`` is omitted, the URL without the token parameter is used as
    the subject. Pass an explicit stable subject when the URL may contain
    volatile query parameters.
    """
    subject_value = (
        str(subject).strip()
        if subject is not None
        else _url_without_token_param(url, param=token_param)
    )
    signed = make_signed_link_token(
        secret,
        subject=subject_value,
        claims=claims,
        ttl_seconds=ttl_seconds,
        now=now,
    )
    return SignedLink(
        url=append_signed_link_token(url, signed.token, param=token_param),
        token=signed.token,
        expires_at=signed.expires_at,
        payload=signed.payload,
    )


__all__ = [
    "SignedLink",
    "SignedLinkToken",
    "SignedLinkTokenError",
    "SignedLinkTokenExpired",
    "SignedLinkTokenInvalid",
    "append_signed_link_token",
    "make_signed_link",
    "make_signed_link_token",
    "verify_signed_link_token",
]
