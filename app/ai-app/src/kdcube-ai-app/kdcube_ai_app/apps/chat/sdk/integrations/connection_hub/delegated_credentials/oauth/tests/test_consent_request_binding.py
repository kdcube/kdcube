# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Consent Referer recovery uses the host's existing issuer origin policy."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http.consent_request import authorize_referrer_params


def _request(referrer, *, issuer="https://public.example", local_issuer=None, **headers):
    app = FastAPI()
    app.state.oauth_delegated_config = {"enabled": True, "issuer": issuer}
    scope = {
        "type": "http", "method": "POST", "scheme": "http",
        "path": "/oauth/authorize/consent", "root_path": "",
        "query_string": b"", "server": ("internal", 8000), "app": app,
        "headers": [(b"host", b"internal:8000")]
        + [(key.lower().encode(), str(value).encode()) for key, value in {"referer": referrer, **headers}.items()],
    }
    request = Request(scope)
    if local_issuer is not None:
        request.state.oauth_delegated_issuer = local_issuer
    return request


def _params(request):
    return authorize_referrer_params(request, form_keys=("state", "code_challenge"))


@pytest.mark.parametrize("referrer", [
    "https://public.example/oauth/authorize?state=original",
    "https://PUBLIC.example:443/mounted/oauth/authorize/?state=original",
])
def test_configured_public_origin_works_behind_internal_proxy(referrer):
    assert _params(_request(
        referrer, **{"X-Forwarded-Host": "foreign.example", "X-Forwarded-Proto": "http"},
    )) == {"state": "original"}


@pytest.mark.parametrize("referrer", [
    "https://foreign.example/oauth/authorize?state=foreign",
    "http://public.example/oauth/authorize?state=foreign",
    "https://public.example:444/oauth/authorize?state=foreign",
    "http://internal:8000/oauth/authorize?state=foreign",
    "https://user@public.example/oauth/authorize?state=foreign",
    "https://public.example:bad/oauth/authorize?state=foreign",
    "https://[broken/oauth/authorize?state=foreign",
    "/oauth/authorize?state=foreign",
    "https://public.example/other?state=foreign",
])
def test_caller_headers_cannot_supply_a_different_consent_origin(referrer):
    assert _params(_request(
        referrer, **{"X-Forwarded-Host": "foreign.example", "X-Forwarded-Proto": "https"},
    )) == {}


def test_request_local_mount_issuer_precedes_app_issuer():
    assert _params(_request(
        "https://mounted.example/oauth/authorize?state=original",
        local_issuer="https://mounted.example/prefix",
    )) == {"state": "original"}
    assert _params(_request(
        "https://public.example/oauth/authorize?state=wrong-mount",
        local_issuer="https://mounted.example/prefix",
    )) == {}


def test_unconfigured_local_dev_uses_existing_request_origin():
    assert _params(_request(
        "http://internal:8000/oauth/authorize?state=original", issuer="",
        **{"X-Forwarded-Host": "foreign.example", "X-Forwarded-Proto": "https"},
    )) == {"state": "original"}
    assert _params(_request(
        "https://foreign.example/oauth/authorize?state=foreign", issuer="",
        **{"X-Forwarded-Host": "foreign.example", "X-Forwarded-Proto": "https"},
    )) == {}
