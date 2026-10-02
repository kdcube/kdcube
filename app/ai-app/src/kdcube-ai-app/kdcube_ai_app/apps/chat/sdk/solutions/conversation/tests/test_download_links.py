# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Signed conversation file download tokens: mint/verify round-trip + tamper checks."""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.download_links import (
    mint_file_download_token,
    verify_file_download_token,
)


def test_round_trip_returns_bound_payload():
    token, expires_at = mint_file_download_token(
        "secret", fi_ref="conv:fi:conv_c1.turn_t1.files/chart.png",
        user_id="u1", conversation_id="c1", tenant="t", project="p",
        ttl_seconds=900, now=1000,
    )
    assert expires_at == 1900
    payload = verify_file_download_token("secret", token, fi_ref="conv:fi:conv_c1.turn_t1.files/chart.png", now=1001)
    assert payload["user_id"] == "u1"
    assert payload["conversation_id"] == "c1"
    assert payload["tenant"] == "t"
    assert payload["project"] == "p"


def test_verify_rejects_wrong_fi_ref():
    token, _ = mint_file_download_token("secret", fi_ref="conv:fi:a", user_id="u1", now=1000)
    with pytest.raises(ValueError):
        verify_file_download_token("secret", token, fi_ref="conv:fi:b", now=1001)


def test_verify_rejects_wrong_secret_and_tamper():
    token, _ = mint_file_download_token("secret", fi_ref="conv:fi:a", user_id="u1", now=1000)
    with pytest.raises(ValueError):
        verify_file_download_token("other-secret", token, fi_ref="conv:fi:a", now=1001)
    body, sig = token.split(".", 1)
    tampered = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + sig
    with pytest.raises(ValueError):
        verify_file_download_token("secret", tampered, fi_ref="conv:fi:a", now=1001)


def test_verify_rejects_expired():
    token, expires_at = mint_file_download_token("secret", fi_ref="conv:fi:a", user_id="u1", ttl_seconds=60, now=1000)
    assert expires_at == 1060
    with pytest.raises(ValueError):
        verify_file_download_token("secret", token, fi_ref="conv:fi:a", now=1061)


def test_mint_requires_secret():
    with pytest.raises(ValueError):
        mint_file_download_token("", fi_ref="conv:fi:a", user_id="u1", now=1000)


def test_ttl_is_clamped():
    # Below the floor and above the ceiling both clamp into range.
    _, low = mint_file_download_token("s", fi_ref="conv:fi:a", user_id="u", ttl_seconds=1, now=0)
    _, high = mint_file_download_token("s", fi_ref="conv:fi:a", user_id="u", ttl_seconds=10**9, now=0)
    assert low == 60
    assert high == 86400


def test_a_token_for_a_third_party_carries_no_identity() -> None:
    import base64
    import json as _json

    from kdcube_ai_app.apps.chat.sdk.solutions.conversation.download_links import (
        mint_file_download_token,
        verify_file_download_token,
    )

    token, _expires = mint_file_download_token(
        "secret",
        fi_ref="staged:abc:chart.png",
        user_id="user-1",
        tenant="demo",
        project="project",
        conversation_id="c-1",
        include_identity=False,
    )

    body = token.split(".", 1)[0]
    payload = _json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    # A provider records the whole URL inside the file it inserts, so anyone
    # who opens that file can read this payload.
    assert set(payload) == {"v", "fi_ref", "exp"}

    verified = verify_file_download_token(
        "secret", token, fi_ref="staged:abc:chart.png", require_user_scope=False
    )
    assert verified["fi_ref"] == "staged:abc:chart.png"

    # The surfaces that do serve a person's own file still demand the scope.
    try:
        verify_file_download_token("secret", token, fi_ref="staged:abc:chart.png")
    except ValueError as exc:
        assert "user scope" in str(exc)
    else:  # pragma: no cover - the guard must hold
        raise AssertionError("a person-facing download accepted an unscoped token")
