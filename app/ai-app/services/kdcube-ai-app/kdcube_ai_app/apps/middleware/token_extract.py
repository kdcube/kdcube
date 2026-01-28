# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Mapping, Optional, Tuple, Any

from kdcube_ai_app.infra.namespaces import CONFIG


def extract_auth_tokens_from_cookies(
    cookies: Mapping[str, str] | None,
) -> Tuple[Optional[str], Optional[str]]:
    if not cookies:
        return None, None
    bearer = cookies.get(CONFIG.AUTH_TOKEN_COOKIE_NAME)
    id_token = cookies.get(CONFIG.ID_TOKEN_COOKIE_NAME)
    return bearer or None, id_token or None


def extract_auth_tokens_from_cookie_header(
    cookie_header: str | None,
) -> Tuple[Optional[str], Optional[str]]:
    if not cookie_header:
        return None, None
    cookies = SimpleCookie()
    cookies.load(cookie_header)
    bearer = cookies.get(CONFIG.AUTH_TOKEN_COOKIE_NAME)
    id_token = cookies.get(CONFIG.ID_TOKEN_COOKIE_NAME)
    return (bearer.value if bearer and bearer.value else None,
            id_token.value if id_token and id_token.value else None)


def extract_auth_tokens_from_query_params(
    params: Mapping[str, Any] | None,
) -> Tuple[Optional[str], Optional[str]]:
    if not params:
        return None, None
    bearer = params.get("bearer_token")
    id_token = params.get("id_token")
    return bearer or None, id_token or None


def extract_auth_tokens_from_socket_auth(
    auth: Mapping[str, Any] | None,
) -> Tuple[Optional[str], Optional[str]]:
    if not auth:
        return None, None
    bearer = auth.get("bearer_token")
    id_token = auth.get("id_token")
    return bearer or None, id_token or None


def resolve_socket_auth_tokens(
    auth: Mapping[str, Any] | None,
    environ: Mapping[str, Any] | None,
) -> Tuple[Optional[str], Optional[str]]:
    bearer, id_token = extract_auth_tokens_from_socket_auth(auth)
    if not bearer or not id_token:
        cookie_bearer, cookie_id = extract_auth_tokens_from_cookie_header(
            (environ or {}).get("HTTP_COOKIE")
        )
        if not bearer:
            bearer = cookie_bearer
        if not id_token:
            id_token = cookie_id
    return bearer, id_token


def resolve_auth_from_headers_and_cookies(
    authorization_header: Optional[str],
    id_token_header: Optional[str],
    cookies: Mapping[str, str] | None,
) -> Tuple[Optional[str], Optional[str]]:
    auth_header = authorization_header
    id_token = id_token_header

    if not auth_header or not id_token:
        cookie_bearer, cookie_id = extract_auth_tokens_from_cookies(cookies)
        if not auth_header and cookie_bearer:
            auth_header = f"Bearer {cookie_bearer}"
        if not id_token and cookie_id:
            id_token = cookie_id

    return auth_header, id_token


def inject_auth_tokens_into_headers(
    headers: Mapping[str, str],
    bearer_token: Optional[str],
    id_token: Optional[str],
    *,
    id_header_name: str = CONFIG.ID_TOKEN_HEADER_NAME,
) -> dict:
    if not bearer_token and not id_token:
        return dict(headers)
    out = dict(headers)
    if bearer_token and "authorization" not in {k.lower(): v for k, v in out.items()}:
        out["authorization"] = f"Bearer {bearer_token}"
    if id_token:
        out[id_header_name] = id_token
    return out
