# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""LinkedInTools: claim gating, author resolution, and request shapes."""

from __future__ import annotations

from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.connected_accounts import (
    ConnectedAccountCredential,
)
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import rest_api
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import tools as linkedin_tools
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    ConnectedAccount,
)

ACCOUNT = ConnectedAccount(
    account_id="acc_1",
    provider_id="linkedin",
    external_subject="dE5aOhH-ap",
    display_name="Jane Smith",
    claims=("linkedin:post",),
    credential_id="cred_1",
)


class _Response:
    def __init__(self, status: int = 201, body: Any = None, headers: dict | None = None) -> None:
        self.status_code = status
        self._body = {} if body is None else body
        self.headers = headers or {}

    def json(self):
        return self._body


class _Client:
    """httpx.AsyncClient stand-in recording every call."""

    def __init__(self, sent: list) -> None:
        self._sent = sent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self._sent.append(("POST", url, kwargs))
        if "/rest/images" in url:
            return _Response(200, {"value": {"uploadUrl": "https://up", "image": "urn:li:image:IMG"}})
        if "/socialActions/" in url:
            return _Response(201, {"commentUrn": "urn:li:comment:(urn:li:share:7,99)"}, {"x-restli-id": "99"})
        return _Response(201, {}, {"x-restli-id": "urn:li:share:7123456789"})

    async def put(self, url, **kwargs):
        self._sent.append(("PUT", url, kwargs))
        return _Response(201, {})

    async def get(self, url, **kwargs):
        self._sent.append(("GET", url, kwargs))
        return _Response(200, {"sub": "dE5aOhH-ap", "name": "Jane Smith", "email": "jane@example.com"})


@pytest.fixture()
def sent():
    return []


@pytest.fixture()
def granted(monkeypatch, sent):
    """A connected account holding every claim."""
    credential = ConnectedAccountCredential(
        ok=True,
        access_token="TOKEN",
        raw_credential={"access_token": "TOKEN"},
        account_id="acc_1",
        provider_id="linkedin",
        tenant="t",
        project="p",
    )

    async def _resolve(*_args, **_kwargs):
        return credential

    async def _accounts(**_kwargs):
        return [ACCOUNT]

    monkeypatch.setattr(linkedin_tools, "resolve_connected_account_claim", _resolve)
    monkeypatch.setattr(linkedin_tools, "connected_linkedin_accounts", _accounts)
    monkeypatch.setattr(linkedin_tools.httpx, "AsyncClient", lambda **kw: _Client(sent))
    return credential


@pytest.fixture()
def denied(monkeypatch):
    """No usable account: the resolver answers with the consent envelope."""
    credential = ConnectedAccountCredential(
        ok=False,
        provider_id="linkedin",
        claim="linkedin:post",
        tool_name="linkedin.post_linkedin_update",
        error_payload={
            "error": {"code": "needs_connected_account_consent", "message": "Connect LinkedIn."},
            "consent": {"provider_id": "linkedin"},
        },
    )

    async def _resolve(*_args, **_kwargs):
        return credential

    monkeypatch.setattr(linkedin_tools, "resolve_connected_account_claim", _resolve)
    return credential


def _bodies(sent, needle: str) -> list[dict]:
    return [kwargs.get("json") for method, url, kwargs in sent if needle in url]


@pytest.mark.asyncio
async def test_text_post_sends_the_versioned_posts_request(granted, sent):
    result = await linkedin_tools.LinkedInTools().post_linkedin_update(text="hello")
    assert result["ok"] is True
    assert result["ret"]["post_urn"] == "urn:li:share:7123456789"
    assert result["ret"]["permalink"].endswith("urn:li:share:7123456789")

    method, url, kwargs = next(item for item in sent if "/rest/posts" in item[1])
    assert method == "POST"
    assert kwargs["headers"]["LinkedIn-Version"] == rest_api.DEFAULT_LINKEDIN_API_VERSION
    assert kwargs["headers"]["X-Restli-Protocol-Version"] == "2.0.0"
    assert kwargs["json"]["author"] == "urn:li:person:dE5aOhH-ap"
    assert "content" not in kwargs["json"]


@pytest.mark.asyncio
async def test_markdown_is_stripped_before_publishing(granted, sent):
    await linkedin_tools.LinkedInTools().post_linkedin_update(text="## Title\n\n**bold**")
    body = _bodies(sent, "/rest/posts")[0]
    assert "#" not in body["commentary"] and "*" not in body["commentary"]
    assert body["commentary"] == "Title\n\nbold"


@pytest.mark.asyncio
async def test_author_comes_from_the_account_not_the_credential(granted, sent):
    await linkedin_tools.LinkedInTools().post_linkedin_update(text="hi")
    assert _bodies(sent, "/rest/posts")[0]["author"] == "urn:li:person:dE5aOhH-ap"


@pytest.mark.asyncio
async def test_image_post_uploads_then_references_the_image_urn(granted, sent, monkeypatch):
    monkeypatch.setattr(
        linkedin_tools,
        "load_image_artifact",
        lambda path: ({"filename": "c.png", "mime_type": "image/png", "data": b"\x89PNG"}, None),
    )
    result = await linkedin_tools.LinkedInTools().post_linkedin_image_update(
        text="chart", image_path="conv:fi:x", alt_text="quarterly"
    )
    assert result["ok"] is True and result["ret"]["image_count"] == 1

    urls = [url for _method, url, _kwargs in sent]
    assert any("/rest/images" in url for url in urls)
    assert any(url == "https://up" for url in urls)
    body = _bodies(sent, "/rest/posts")[0]
    assert body["content"] == {"media": {"id": "urn:li:image:IMG", "altText": "quarterly"}}


@pytest.mark.asyncio
async def test_comment_targets_the_encoded_post_urn(granted, sent):
    result = await linkedin_tools.LinkedInTools().comment_on_linkedin_post(
        post_urn="urn:li:share:7123456789", text="nice"
    )
    assert result["ok"] is True
    assert result["ret"]["comment_id"] == "99"

    _method, url, kwargs = next(item for item in sent if "/socialActions/" in item[1])
    # Comments live on the unversioned endpoint; /rest/socialActions is
    # partner-gated and refuses w_member_social.
    assert url == (
        "https://api.linkedin.com/v2/socialActions/"
        "urn%3Ali%3Ashare%3A7123456789/comments"
    )
    assert "LinkedIn-Version" not in kwargs["headers"]
    assert kwargs["json"]["actor"] == "urn:li:person:dE5aOhH-ap"
    assert kwargs["json"]["object"] == "urn:li:share:7123456789"


@pytest.mark.asyncio
async def test_publish_without_a_claim_returns_the_consent_envelope(denied, sent):
    result = await linkedin_tools.LinkedInTools().post_linkedin_update(text="hello")
    assert result["ok"] is False
    assert result["error"]["code"] == "needs_connected_account_consent"
    assert sent == []


@pytest.mark.asyncio
async def test_comment_without_a_claim_returns_the_consent_envelope(denied, sent):
    result = await linkedin_tools.LinkedInTools().comment_on_linkedin_post(
        post_urn="urn:li:share:7", text="nice"
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "needs_connected_account_consent"
    assert sent == []


@pytest.mark.asyncio
async def test_empty_text_is_refused_before_any_provider_call(granted, sent):
    result = await linkedin_tools.LinkedInTools().post_linkedin_update(text="   ")
    assert result["ok"] is False
    assert result["error"]["code"] == "text_required"
    assert sent == []


@pytest.mark.asyncio
async def test_unsupported_image_type_is_refused(granted, sent):
    result = await linkedin_tools.LinkedInTools().publish(
        text="hi",
        files=[{"filename": "doc.pdf", "mime_type": "application/pdf", "data": b"%PDF"}],
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "unsupported_image_type"
    assert sent == []


@pytest.mark.asyncio
async def test_oversized_image_is_refused(granted, sent):
    result = await linkedin_tools.LinkedInTools().publish(
        text="hi",
        files=[
            {
                "filename": "big.png",
                "mime_type": "image/png",
                "data": b"x" * (rest_api.MAX_IMAGE_BYTES + 1),
            }
        ],
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "file_too_large"
    assert sent == []


def test_api_version_is_read_from_the_descriptor(monkeypatch):
    class _Entrypoint:
        def bundle_prop(self, path, default=None):
            return "202612" if path == linkedin_tools.API_VERSION_PROP else default

    linkedin_tools.bind_service(_Entrypoint())
    try:
        assert linkedin_tools.linkedin_api_version() == "202612"
    finally:
        linkedin_tools.bind_service(None)
    assert linkedin_tools.linkedin_api_version() == rest_api.DEFAULT_LINKEDIN_API_VERSION


@pytest.mark.asyncio
async def test_publish_staged_reads_bytes_and_releases_the_slot(granted, sent, monkeypatch, tmp_path):
    from kdcube_ai_app.apps.chat.sdk.integrations import file_staging

    root = tmp_path / "staging"
    root.mkdir()
    ref = file_staging.new_staged_ref("chart.png")
    file_staging.save_staged(root, ref, b"\x89PNG-staged")
    monkeypatch.setattr(linkedin_tools, "staging_root_for_service", lambda: root)

    result = await linkedin_tools.LinkedInTools().publish_staged(
        text="from a staged image", staged_refs=[ref], alt_texts=["chart"]
    )
    assert result["ok"] is True and result["ret"]["image_count"] == 1

    body = _bodies(sent, "/rest/posts")[0]
    assert body["content"] == {"media": {"id": "urn:li:image:IMG", "altText": "chart"}}
    # Staged bytes are single-use.
    with pytest.raises((FileNotFoundError, ValueError)):
        file_staging.load_staged(root, ref)


@pytest.mark.asyncio
async def test_publish_staged_reports_a_missing_ref(granted, sent, monkeypatch, tmp_path):
    root = tmp_path / "staging"
    root.mkdir()
    monkeypatch.setattr(linkedin_tools, "staging_root_for_service", lambda: root)

    result = await linkedin_tools.LinkedInTools().publish_staged(
        text="x", staged_refs=["staged:deadbeef:gone.png"]
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "staged_file_missing"
    assert sent == []


@pytest.mark.asyncio
async def test_publish_staged_refuses_a_non_image(granted, sent, monkeypatch, tmp_path):
    from kdcube_ai_app.apps.chat.sdk.integrations import file_staging

    root = tmp_path / "staging"
    root.mkdir()
    ref = file_staging.new_staged_ref("notes.pdf")
    file_staging.save_staged(root, ref, b"%PDF-1.4")
    monkeypatch.setattr(linkedin_tools, "staging_root_for_service", lambda: root)

    result = await linkedin_tools.LinkedInTools().publish_staged(text="x", staged_refs=[ref])
    assert result["ok"] is False
    assert result["error"]["code"] == "unsupported_image_type"
    assert sent == []


class _RejectingClient:
    """httpx.AsyncClient stand-in whose posts always fail with one status."""

    def __init__(self, sent: list, *, status: int, body: Any, headers: dict | None = None) -> None:
        self._sent = sent
        self._status = status
        self._body = body
        self._headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self._sent.append(("POST", url, kwargs))
        return _Response(self._status, self._body, self._headers)

    async def get(self, url, **kwargs):
        self._sent.append(("GET", url, kwargs))
        return _Response(200, {"sub": "dE5aOhH-ap", "name": "Jane Smith"})


class _UploadRejectingClient(_RejectingClient):
    """Initialize succeeds, but the binary upload rejects the credential."""

    async def post(self, url, **kwargs):
        self._sent.append(("POST", url, kwargs))
        if "/rest/images" in url:
            return _Response(
                200,
                {"value": {"uploadUrl": "https://up", "image": "urn:li:image:IMG"}},
            )
        return _Response(201, {}, {"x-restli-id": "urn:li:share:7"})

    async def put(self, url, **kwargs):
        self._sent.append(("PUT", url, kwargs))
        return _Response(
            self._status,
            self._body,
            self._headers,
        )


class _MissingIdClient(_Client):
    """Mutation succeeds at HTTP level but returns no created identifier."""

    async def post(self, url, **kwargs):
        self._sent.append(("POST", url, kwargs))
        return _Response(201, {})


class _BodyUrnOnlyClient(_Client):
    """Unversioned /v2 shape: the created URN is in the body, no restli header."""

    async def post(self, url, **kwargs):
        self._sent.append(("POST", url, kwargs))
        return _Response(201, {"commentUrn": "urn:li:comment:(urn:li:activity:7,99)"})


def _envelope_text(result: Any) -> str:
    import json

    return json.dumps(result, default=str)


@pytest.mark.asyncio
async def test_a_provider_429_is_reported_as_rate_limited_with_retry_after(granted, sent, monkeypatch):
    """A throttled post carries the wait hint and stays retryable."""
    monkeypatch.setattr(
        linkedin_tools.httpx,
        "AsyncClient",
        lambda **kw: _RejectingClient(
            sent,
            status=429,
            body={"message": "Too many requests", "status": 429, "serviceErrorCode": 100},
            headers={"Retry-After": "30"},
        ),
    )

    result = await linkedin_tools.LinkedInTools().post_linkedin_update(text="Hello")

    assert result["ok"] is False
    assert result["error"]["code"] == "linkedin_rate_limited"
    ret = result["ret"]
    assert ret["provider_status"] == 429
    assert ret["category"] == "rate_limited"
    assert ret["retryable"] is True
    assert ret["retry_after"] == "30"
    # Sanitized: the bearer never reaches the caller.
    assert "TOKEN" not in _envelope_text(result)


@pytest.mark.asyncio
async def test_a_provider_401_refreshes_once_then_asks_for_a_reconnect(granted, sent, monkeypatch):
    """A rejected token is refreshed once; a still-rejecting account reconnects."""
    from kdcube_ai_app.apps.chat.sdk.integrations import connected_accounts

    refreshed = ConnectedAccountCredential(
        ok=False,
        provider_id="linkedin",
        claim="linkedin:post",
        tool_name="linkedin.post_linkedin_update",
        error_payload={
            "error": {"code": "needs_connected_account_consent", "message": "Reconnect LinkedIn."},
            "consent": {"provider_id": "linkedin", "reason": "reconnect_required"},
        },
    )
    refresh_calls: list = []

    async def _refresh(_source, *, credential):
        refresh_calls.append(credential)
        return refreshed

    monkeypatch.setattr(connected_accounts, "refresh_connected_account_claim", _refresh)
    monkeypatch.setattr(
        linkedin_tools.httpx,
        "AsyncClient",
        lambda **kw: _RejectingClient(
            sent,
            status=401,
            body={"message": "Invalid access token", "status": 401},
        ),
    )

    result = await linkedin_tools.LinkedInTools().post_linkedin_update(text="Hello")

    assert result["ok"] is False
    # The 401 is a credential failure, so recovery runs before anything is
    # reported; a still-unusable account becomes an actionable reconnect demand
    # rather than a raw provider category.
    assert len(refresh_calls) == 1
    assert result["error"]["code"] == "needs_connected_account_consent"
    assert result["error"]["consent"]["reason"] == "reconnect_required"
    assert "TOKEN" not in _envelope_text(result)


@pytest.mark.asyncio
async def test_staged_image_upload_consumes_auth_marker_before_returning(
    granted, sent, monkeypatch, tmp_path
):
    """The signed-upload MCP path must never serialize the credential marker."""
    from kdcube_ai_app.apps.chat.sdk.integrations import connected_accounts, file_staging

    reconnect = ConnectedAccountCredential(
        ok=False,
        provider_id="linkedin",
        claim="linkedin:post",
        error_payload={
            "error": {"code": "needs_connected_account_consent"},
            "consent": {"reason": "reconnect_required"},
        },
    )

    async def _refresh(_source, *, credential):
        assert credential.access_token == "TOKEN"
        return reconnect

    root = tmp_path / "staging"
    root.mkdir()
    ref = file_staging.new_staged_ref("chart.png")
    file_staging.save_staged(root, ref, b"\x89PNG-staged")
    monkeypatch.setattr(linkedin_tools, "staging_root_for_service", lambda: root)
    monkeypatch.setattr(connected_accounts, "refresh_connected_account_claim", _refresh)
    monkeypatch.setattr(
        linkedin_tools.httpx,
        "AsyncClient",
        lambda **kw: _UploadRejectingClient(
            sent,
            status=401,
            body={"message": "Invalid access token", "status": 401},
        ),
    )

    result = await linkedin_tools.LinkedInTools().publish_staged(
        text="chart", staged_refs=[ref]
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "needs_connected_account_consent"
    assert result["error"]["consent"]["reason"] == "reconnect_required"
    assert "__connected_account_auth_failure__" not in _envelope_text(result)
    assert "TOKEN" not in _envelope_text(result)


@pytest.mark.asyncio
async def test_linkedin_permission_phrase_uses_credential_recovery(granted, sent, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.integrations import connected_accounts

    reconnect = ConnectedAccountCredential(
        ok=False,
        provider_id="linkedin",
        claim="linkedin:post",
        error_payload={
            "error": {"code": "needs_connected_account_consent"},
            "consent": {"reason": "reconnect_required"},
        },
    )
    refresh_calls: list[ConnectedAccountCredential] = []

    async def _refresh(_source, *, credential):
        refresh_calls.append(credential)
        return reconnect

    monkeypatch.setattr(connected_accounts, "refresh_connected_account_claim", _refresh)
    monkeypatch.setattr(
        linkedin_tools.httpx,
        "AsyncClient",
        lambda **kw: _RejectingClient(
            sent,
            status=403,
            body={"message": "Not enough permissions to access this resource", "status": 403},
        ),
    )

    result = await linkedin_tools.LinkedInTools().post_linkedin_update(text="Hello")

    assert len(refresh_calls) == 1
    assert result["error"]["consent"]["reason"] == "reconnect_required"
    # One recovery decision, and neither the retry protocol nor the bearer
    # crosses the boundary - the properties the live 403 run would have checked.
    assert "__connected_account_auth_failure__" not in _envelope_text(result)
    assert "TOKEN" not in _envelope_text(result)


@pytest.mark.asyncio
async def test_successful_post_without_created_id_reports_unknown_outcome(
    granted, sent, monkeypatch
):
    monkeypatch.setattr(
        linkedin_tools.httpx, "AsyncClient", lambda **kw: _MissingIdClient(sent)
    )

    result = await linkedin_tools.LinkedInTools().post_linkedin_update(text="Hello")

    assert result["ok"] is False
    assert result["error"]["code"] == "linkedin_response_incomplete"
    assert result["ret"]["outcome_unknown"] is True


@pytest.mark.asyncio
async def test_successful_comment_without_created_id_reports_unknown_outcome(
    granted, sent, monkeypatch
):
    monkeypatch.setattr(
        linkedin_tools.httpx, "AsyncClient", lambda **kw: _MissingIdClient(sent)
    )

    result = await linkedin_tools.LinkedInTools().comment_on_linkedin_post(
        post_urn="urn:li:share:7", text="Hello"
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "linkedin_response_incomplete"
    assert result["ret"]["outcome_unknown"] is True


@pytest.mark.asyncio
async def test_comment_urn_in_the_body_alone_is_a_complete_outcome(
    granted, sent, monkeypatch
):
    """The URN identifies the comment; comment_id is the weaker of the two."""
    monkeypatch.setattr(
        linkedin_tools.httpx, "AsyncClient", lambda **kw: _BodyUrnOnlyClient(sent)
    )

    result = await linkedin_tools.LinkedInTools().comment_on_linkedin_post(
        post_urn="urn:li:share:7", text="Hello"
    )

    assert result["ok"] is True
    assert result["ret"]["comment_urn"] == "urn:li:comment:(urn:li:activity:7,99)"
    assert result["ret"]["comment_id"] == ""
