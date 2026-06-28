# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Pure /oauth/authorize request validation and redirect construction.

Validation order matters: ``client_id`` and ``redirect_uri`` are checked first
and their failures are **non-redirectable** (we must not bounce a code/error to
an unvalidated URI). Everything after a good client+redirect is redirectable per
RFC 6749 §4.1.2.1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.clients import get_client, redirect_uri_allowed
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.metadata import CONVERSATIONS_READ_SCOPE

SUPPORTED_SCOPES = {CONVERSATIONS_READ_SCOPE}


@dataclass
class AuthorizeRequest:
    client_id: str
    redirect_uri: str
    response_type: str
    scopes: List[str]
    state: Optional[str]
    code_challenge: str
    code_challenge_method: str


class AuthorizeError(Exception):
    def __init__(
        self,
        error: str,
        description: str = "",
        *,
        redirectable: bool,
        state: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ):
        super().__init__(error)
        self.error = error
        self.error_description = description
        self.redirectable = redirectable
        self.state = state
        self.redirect_uri = redirect_uri


def parse_authorize_request(
    params: Dict[str, Any],
    *,
    client_resolver=None,
    public_client_resolver=None,
) -> AuthorizeRequest:
    client_id = (params.get("client_id") or "").strip()
    redirect_uri = (params.get("redirect_uri") or "").strip()

    # Static pre-registered client first, then any dynamically-registered (DCR) one.
    resolver = public_client_resolver or get_client
    client = resolver(client_id)
    if client is None and client_resolver is not None:
        client = client_resolver(client_id)
    if client is None:
        raise AuthorizeError("invalid_client", "unknown client_id", redirectable=False)
    if not redirect_uri_allowed(client, redirect_uri):
        raise AuthorizeError("invalid_request", "redirect_uri not allowed", redirectable=False)

    # client + redirect validated -> remaining errors may be redirected back.
    state = params.get("state")

    response_type = (params.get("response_type") or "").strip()
    if response_type != "code":
        raise AuthorizeError(
            "unsupported_response_type", "only 'code' is supported",
            redirectable=True, state=state, redirect_uri=redirect_uri,
        )

    raw_scope = (params.get("scope") or CONVERSATIONS_READ_SCOPE).strip()
    scopes = [s for s in raw_scope.split() if s] or [CONVERSATIONS_READ_SCOPE]
    for s in scopes:
        if s not in SUPPORTED_SCOPES:
            raise AuthorizeError(
                "invalid_scope", f"unsupported scope: {s}",
                redirectable=True, state=state, redirect_uri=redirect_uri,
            )

    code_challenge = (params.get("code_challenge") or "").strip()
    method = (params.get("code_challenge_method") or "").strip()
    if not code_challenge:
        raise AuthorizeError(
            "invalid_request", "code_challenge is required (PKCE)",
            redirectable=True, state=state, redirect_uri=redirect_uri,
        )
    if method != "S256":
        raise AuthorizeError(
            "invalid_request", "code_challenge_method must be S256",
            redirectable=True, state=state, redirect_uri=redirect_uri,
        )

    return AuthorizeRequest(
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type=response_type,
        scopes=scopes,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=method,
    )


def build_redirect(redirect_uri: str, params: Dict[str, Optional[str]]) -> str:
    parts = urlsplit(redirect_uri)
    query = dict(parse_qsl(parts.query))
    query.update({k: v for k, v in params.items() if v is not None})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
