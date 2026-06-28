# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Anti-phishing / anti-spoofing properties of the consent screen.

Because dynamic client registration is open, the consent screen must show the
admin exactly WHICH client is asking and WHERE the authorization code will be
sent (the redirect_uri), and must not present an arbitrary/unknown client with a
hardcoded trusted brand. Otherwise a phishing link to /oauth/authorize with an
attacker's client_id + redirect_uri yields a familiar-looking screen and steals
a feedback-reader grant.
"""
from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.consent import render_consent_html
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.flow import AuthorizeRequest

ISSUER = "https://yey.boats"


def _req(client_id="claude", redirect_uri="http://127.0.0.1:9000/callback"):
    return AuthorizeRequest(
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type="code",
        scopes=["conversations:read"],
        state="s1",
        code_challenge="c" * 43,
        code_challenge_method="S256",
    )


def test_consent_shows_the_redirect_destination():
    html = render_consent_html(_req(redirect_uri="https://evil.example/cb"), ISSUER, csrf_token="t", trusted=False)
    assert "https://evil.example/cb" in html


def test_consent_shows_the_actual_client_id():
    html = render_consent_html(_req(client_id="dcr-abc123"), ISSUER, csrf_token="t", trusted=False)
    assert "dcr-abc123" in html


def test_consent_does_not_brand_an_arbitrary_client_as_claude_code():
    # A DCR client must NOT be presented as the trusted "Claude Code".
    html = render_consent_html(_req(client_id="dcr-evil"), ISSUER, csrf_token="t", trusted=False)
    assert "claude code" not in html.lower()


def test_consent_warns_for_dynamically_registered_client():
    html = render_consent_html(_req(client_id="dcr-evil"), ISSUER, csrf_token="t", trusted=False).lower()
    # An explicit "newly/dynamically registered — verify you started this" caution.
    assert "registered" in html
    assert "verify" in html or "only approve" in html


def test_consent_marks_preregistered_client_distinctly():
    untrusted = render_consent_html(_req(client_id="dcr-x"), ISSUER, csrf_token="t", trusted=False)
    trusted = render_consent_html(_req(client_id="claude"), ISSUER, csrf_token="t", trusted=True)
    # The trust state must be visibly different between a known and an unknown client.
    assert trusted != untrusted
    assert "claude" in trusted
