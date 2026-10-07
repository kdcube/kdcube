# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Recover consent form fields from the authorization server's own origin."""
from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qs, urlsplit

from fastapi import Request

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http.discovery import resolve_issuer


def _origin(url: str) -> tuple[str, str, int] | None:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    port = parsed.port
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    return scheme, parsed.hostname.lower(), effective_port


def authorize_referrer_params(
    request: Request, *, form_keys: Iterable[str],
) -> dict[str, str]:
    """Use the existing issuer policy, including its local/dev fallback.

    A configured request-local or app-level issuer remains authoritative behind
    a proxy. Caller-supplied Host and forwarded headers cannot replace it.
    Without a configured issuer, resolve_issuer supplies the ASGI request origin;
    this helper never interprets forwarded headers as a trust decision.
    """
    referrer = str(request.headers.get("referer") or request.headers.get("referrer") or "").strip()
    if not referrer:
        return {}
    try:
        expected = _origin(resolve_issuer(request))
        got = urlsplit(referrer)
        if expected is None or _origin(referrer) != expected:
            return {}
        if not got.path.rstrip("/").endswith("/oauth/authorize"):
            return {}
        parsed = parse_qs(got.query, keep_blank_values=True)
    except (TypeError, ValueError):
        return {}
    return {
        key: str(parsed[key][-1] or "")
        for key in form_keys
        if parsed.get(key)
    }
