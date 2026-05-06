# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.ingress.signed_links import (
    SignedLinkTokenExpired,
    SignedLinkTokenInvalid,
    append_signed_link_token,
    make_signed_link,
    make_signed_link_token,
    verify_signed_link_token,
)


def test_signed_link_token_round_trips_subject_and_claims():
    signed = make_signed_link_token(
        "secret",
        subject="fi:turn_1.outputs/report.pdf",
        claims={"user_id": "user-1"},
        ttl_seconds=60,
        now=1000,
    )

    payload = verify_signed_link_token(
        "secret",
        signed.token,
        subject="fi:turn_1.outputs/report.pdf",
        now=1010,
    )

    assert signed.expires_at == 1060
    assert payload["sub"] == "fi:turn_1.outputs/report.pdf"
    assert payload["claims"]["user_id"] == "user-1"


def test_signed_link_token_rejects_wrong_subject():
    signed = make_signed_link_token("secret", subject="artifact-a", ttl_seconds=60, now=1000)

    with pytest.raises(SignedLinkTokenInvalid, match="subject"):
        verify_signed_link_token("secret", signed.token, subject="artifact-b", now=1001)


def test_signed_link_token_rejects_tampered_signature():
    signed = make_signed_link_token("secret", subject="artifact-a", ttl_seconds=60, now=1000)
    tampered = signed.token[:-1] + ("a" if signed.token[-1] != "a" else "b")

    with pytest.raises(SignedLinkTokenInvalid, match="signature"):
        verify_signed_link_token("secret", tampered, subject="artifact-a", now=1001)


def test_signed_link_token_rejects_expired_token():
    signed = make_signed_link_token("secret", subject="artifact-a", ttl_seconds=60, now=1000)

    with pytest.raises(SignedLinkTokenExpired):
        verify_signed_link_token("secret", signed.token, subject="artifact-a", now=1061)


def test_append_signed_link_token_replaces_existing_param():
    url = append_signed_link_token(
        "/api/download?artifact_ref=abc&download_token=old",
        "new-token",
    )

    assert url == "/api/download?artifact_ref=abc&download_token=new-token"


def test_make_signed_link_uses_url_without_token_as_default_subject():
    signed = make_signed_link(
        "/api/download?artifact_ref=abc&download_token=old",
        "secret",
        claims={"user_id": "user-1"},
        ttl_seconds=60,
        now=1000,
    )

    payload = verify_signed_link_token(
        "secret",
        signed.token,
        subject="/api/download?artifact_ref=abc",
        now=1001,
    )
    assert signed.url.endswith(f"download_token={signed.token}")
    assert payload["claims"]["user_id"] == "user-1"
