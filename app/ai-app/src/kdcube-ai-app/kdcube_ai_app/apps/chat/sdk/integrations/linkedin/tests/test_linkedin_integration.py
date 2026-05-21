from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import accounts as li_accounts
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import delivery as li_delivery
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import settings as li_settings


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_entrypoint(props: dict | None = None) -> SimpleNamespace:
    prop_map = props or {}

    def bundle_prop(key, default=None):
        return prop_map.get(key, default)

    ns = SimpleNamespace(
        bundle_prop=bundle_prop,
        bundle_id="test-bundle@1",
        config=SimpleNamespace(
            ai_bundle_spec=SimpleNamespace(id="test-bundle@1"),
        ),
        comm_context=SimpleNamespace(
            actor=SimpleNamespace(tenant_id="tenant1", project_id="proj1"),
        ),
        settings=SimpleNamespace(TENANT="tenant1", PROJECT="proj1"),
    )
    return ns


def _make_store(tmp_path, user_id="user-1"):
    return li_accounts.LinkedInAccountStore(tmp_path, user_id=user_id, bundle_id="test-bundle@1")


# ── OAuth state: create and consume ───────────────────────────────────────────

def test_oauth_state_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    secret = "test-secret-abc"

    result = store.create_oauth_state(secret=secret, source="settings", return_hint="back")
    state = result["state"]
    payload = result["payload"]

    assert "." in state
    assert payload["user_id"] == "user-1"
    assert payload["source"] == "settings"
    assert payload["return_hint"] == "back"

    consumed = store.consume_oauth_state(state=state, secret=secret)
    assert consumed["user_id"] == "user-1"
    assert consumed["provider"] == "linkedin"


def test_oauth_state_wrong_secret_rejected(tmp_path):
    store = _make_store(tmp_path)
    result = store.create_oauth_state(secret="correct", source="settings")
    with pytest.raises(ValueError, match="signature"):
        store.consume_oauth_state(state=result["state"], secret="wrong")


def test_oauth_state_tampered_payload_rejected(tmp_path):
    store = _make_store(tmp_path)
    result = store.create_oauth_state(secret="s3cr3t", source="settings")
    state = result["state"]
    encoded, sig = state.rsplit(".", 1)
    tampered = encoded[:-2] + "AA" + "." + sig
    with pytest.raises(ValueError):
        store.consume_oauth_state(state=tampered, secret="s3cr3t")


def test_oauth_state_expired(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    result = store.create_oauth_state(secret="s3cr3t", source="settings", ttl_seconds=1)
    state = result["state"]
    # Wind the clock forward past the TTL — capture real time.time before patching
    real_now = time.time()
    monkeypatch.setattr(li_accounts.time, "time", lambda: real_now + 10)
    with pytest.raises(ValueError, match="expired"):
        store.consume_oauth_state(state=state, secret="s3cr3t")


def test_oauth_state_replay_rejected(tmp_path):
    store = _make_store(tmp_path)
    result = store.create_oauth_state(secret="s3cr3t", source="settings")
    state = result["state"]
    store.consume_oauth_state(state=state, secret="s3cr3t")  # first use ok
    with pytest.raises(ValueError, match="not found"):
        store.consume_oauth_state(state=state, secret="s3cr3t")  # replay rejected


def test_oauth_state_requires_non_empty_secret(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="not configured"):
        store.create_oauth_state(secret="", source="settings")


# ── Account store: upsert + tokens ───────────────────────────────────────────

def test_upsert_creates_account(tmp_path):
    store = _make_store(tmp_path)
    row = store.upsert_account({"person_id": "ABC123", "email": "a@example.com", "display_name": "Alice"})

    assert row["person_id"] == "ABC123"
    assert row["provider"] == "linkedin"
    assert "access_token" not in row
    assert "token" not in row


def test_upsert_idempotent_by_person_id(tmp_path):
    store = _make_store(tmp_path)
    row1 = store.upsert_account({"person_id": "P1", "display_name": "Alice"})
    row2 = store.upsert_account({"person_id": "P1", "display_name": "Alice Updated"})

    accounts = store.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["account_id"] == row1["account_id"]
    assert accounts[0]["display_name"] == "Alice Updated"


def test_upsert_does_not_leak_tokens_into_metadata(tmp_path):
    store = _make_store(tmp_path)
    row = store.upsert_account({
        "person_id": "P1",
        "display_name": "Alice",
        "access_token": "should-not-appear",  # caller mistake — must be ignored
    })
    raw = json.loads((store.accounts_path).read_text())
    account_row = raw["accounts"][0]
    assert "access_token" not in account_row
    assert row.get("access_token") is None


def test_tokens_stored_out_of_band_not_in_accounts_file(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    store.upsert_account({"person_id": "P1", "display_name": "Alice"})
    account_id = store.list_accounts()[0]["account_id"]

    token_data = {"access_token": "tok123", "expires_in": 5183944}
    stored: dict = {}

    monkeypatch.setattr(li_accounts, "set_user_secret", lambda key, value, **kw: stored.update({key: value}))
    monkeypatch.setattr(li_accounts, "get_user_secret", lambda key, **kw: stored.get(key))

    store.set_tokens(account_id, token_data)

    # Token must NOT appear in the accounts JSON file
    raw = json.loads(store.accounts_path.read_text())
    assert "access_token" not in json.dumps(raw)

    # Token must be retrievable from the separate secret store
    retrieved = store.get_tokens(account_id)
    assert retrieved["access_token"] == "tok123"


def test_list_accounts_reflects_has_token(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    store.upsert_account({"person_id": "P1", "display_name": "Alice"})
    account_id = store.list_accounts()[0]["account_id"]

    monkeypatch.setattr(li_accounts, "get_user_secret", lambda key, **kw: None)
    assert store.list_accounts()[0]["has_token"] is False

    monkeypatch.setattr(li_accounts, "get_user_secret", lambda key, **kw: '{"access_token":"tok"}')
    assert store.list_accounts()[0]["has_token"] is True


def test_delete_account_removes_entry(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    store.upsert_account({"person_id": "P1", "display_name": "Alice"})
    account_id = store.list_accounts()[0]["account_id"]

    deleted_keys: list[str] = []
    monkeypatch.setattr(li_accounts, "delete_user_secret", lambda key, **kw: deleted_keys.append(key))

    deleted = store.delete_account(account_id)
    assert deleted is True
    assert store.list_accounts() == []
    assert any(account_id in k for k in deleted_keys)


# ── create_linkedin_post ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_linkedin_post_sends_correct_ugc_payload():
    captured: dict = {}

    async def _mock_post(url, *, json, headers, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = ""
        mock_resp.headers = {"x-restli-id": "urn:li:ugcPost:99999"}
        return mock_resp

    with patch("kdcube_ai_app.apps.chat.sdk.integrations.linkedin.accounts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _mock_post
        mock_client_cls.return_value = mock_client

        result = await li_accounts.create_linkedin_post(
            access_token="tok-abc",
            person_id="PERSON1",
            text="Hello LinkedIn!",
        )

    assert captured["url"] == li_accounts.LINKEDIN_UGC_POSTS_URL
    payload = captured["payload"]
    assert payload["author"] == "urn:li:person:PERSON1"
    assert payload["lifecycleState"] == "PUBLISHED"
    content = payload["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert content["shareCommentary"]["text"] == "Hello LinkedIn!"
    assert content["shareMediaCategory"] == "NONE"
    assert payload["visibility"]["com.linkedin.ugc.MemberNetworkVisibility"] == "PUBLIC"
    assert result["post_id"] == "urn:li:ugcPost:99999"


@pytest.mark.asyncio
async def test_create_linkedin_post_raises_on_403():
    async def _mock_post(url, *, json, headers, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.reason_phrase = "Forbidden"
        mock_resp.text = '{"message":"Insufficient permissions","status":403}'
        mock_resp.headers = {}
        return mock_resp

    with patch("kdcube_ai_app.apps.chat.sdk.integrations.linkedin.accounts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _mock_post
        mock_client_cls.return_value = mock_client

        with pytest.raises(li_accounts.ProviderHttpError) as exc_info:
            await li_accounts.create_linkedin_post(
                access_token="tok-abc",
                person_id="PERSON1",
                text="Hello",
            )

    err = exc_info.value
    assert err.status == 403
    assert "Insufficient permissions" in err.message


# ── settings.status ───────────────────────────────────────────────────────────

def test_settings_status_reflects_enabled_flag(tmp_path):
    li_settings.configure_linkedin_settings(
        storage_root_or_error=lambda ep: tmp_path,
        target_user_id=lambda ep, **kw: "user-1",
    )

    ep_enabled = _make_entrypoint({
        "integrations.linkedin.enabled": True,
        "integrations.linkedin.client_id": "real_client_id",
    })
    ep_disabled = _make_entrypoint({
        "integrations.linkedin.enabled": False,
    })

    result_on = li_settings.status(ep_enabled)
    result_off = li_settings.status(ep_disabled)

    assert result_on["enabled"] is True
    assert result_off["enabled"] is False
    assert "integrations.linkedin.enabled" not in result_on["configuration_missing"]
    assert "integrations.linkedin.enabled" in result_off["configuration_missing"]


def test_settings_status_missing_client_id(tmp_path):
    li_settings.configure_linkedin_settings(
        storage_root_or_error=lambda ep: tmp_path,
        target_user_id=lambda ep, **kw: "user-1",
    )
    ep = _make_entrypoint({
        "integrations.linkedin.enabled": True,
        "integrations.linkedin.client_id": "",
    })
    result = li_settings.status(ep)
    assert "integrations.linkedin.client_id" in result["configuration_missing"]
    assert result["linkedin_configured"] is False


def test_settings_status_fully_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(li_accounts, "get_secret", lambda key: "state-secret" if "oauth_state_secret" in key else "")
    li_settings.configure_linkedin_settings(
        storage_root_or_error=lambda ep: tmp_path,
        target_user_id=lambda ep, **kw: "user-1",
    )
    ep = _make_entrypoint({
        "integrations.linkedin.enabled": True,
        "integrations.linkedin.client_id": "real_client_id",
    })
    result = li_settings.status(ep)
    assert result["ok"] is True
    assert result["linkedin_configured"] is True
    assert result["configuration_missing"] == []


def test_settings_status_returns_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(li_accounts, "get_secret", lambda key: "state-secret" if "oauth_state_secret" in key else "")
    monkeypatch.setattr(li_accounts, "get_user_secret", lambda key, **kw: '{"access_token":"tok"}')
    li_settings.configure_linkedin_settings(
        storage_root_or_error=lambda ep: tmp_path,
        target_user_id=lambda ep, **kw: "user-1",
    )
    store = _make_store(tmp_path)
    store.upsert_account({"person_id": "P1", "display_name": "Alice"})

    ep = _make_entrypoint({
        "integrations.linkedin.enabled": True,
        "integrations.linkedin.client_id": "real_client_id",
    })
    result = li_settings.status(ep)
    assert len(result["accounts"]) == 1
    assert result["accounts"][0]["display_name"] == "Alice"
    assert result["accounts"][0]["has_token"] is True


# ── delivery: strip_markdown ──────────────────────────────────────────────────

@pytest.mark.parametrize("input_text,expected_fragment", [
    ("# Heading", "Heading"),
    ("## Sub heading", "Sub heading"),
    ("**bold text**", "bold text"),
    ("*italic*", "italic"),
    ("__also bold__", "also bold"),
    ("_also italic_", "also italic"),
    ("`inline code`", "inline code"),
    ("~~strikethrough~~", "strikethrough"),
    ("- bullet item", "bullet item"),
    ("1. ordered item", "ordered item"),
    ("> blockquote text", "blockquote text"),
    ("[label](https://example.com)", "label (https://example.com)"),
    ("![alt text](https://img.example.com/pic.png)", "alt text"),
])
def test_strip_markdown_removes_syntax(input_text, expected_fragment):
    result = li_delivery.strip_markdown(input_text)
    assert expected_fragment in result
    # Markdown syntax characters that wrapped the content should be gone
    for char in ("**", "__", "~~", "```"):
        assert char not in result


def test_strip_markdown_fenced_code_block():
    text = "Before\n```python\nprint('hello')\n```\nAfter"
    result = li_delivery.strip_markdown(text)
    assert "print('hello')" in result
    assert "```" not in result


def test_strip_markdown_preserves_newlines():
    text = "Line one\n\nLine two\n\nLine three"
    result = li_delivery.strip_markdown(text)
    assert "Line one" in result
    assert "Line two" in result
    assert "\n\n" in result


def test_strip_markdown_collapses_excess_blank_lines():
    text = "A\n\n\n\n\nB"
    result = li_delivery.strip_markdown(text)
    assert "\n\n\n" not in result


# ── delivery: truncate_post_text ──────────────────────────────────────────────

def test_truncate_short_text_unchanged():
    text = "Short post"
    assert li_delivery.truncate_post_text(text) == text


def test_truncate_long_text_cuts_on_word_boundary():
    words = ["word"] * 700  # ~3500 chars with spaces
    text = " ".join(words)
    result = li_delivery.truncate_post_text(text, max_chars=3000)
    assert len(result) <= 3000
    assert result.endswith("…")
    # Should not cut mid-word
    body = result[:-1]
    assert not body[-1].isalpha() or body.endswith("word")


def test_truncate_respects_custom_max():
    text = "Hello world this is a long sentence"
    result = li_delivery.truncate_post_text(text, max_chars=10, suffix="…")
    assert len(result) <= 10


# ── delivery: format_post_text ────────────────────────────────────────────────

def test_format_post_text_strips_and_truncates():
    long_md = "# Title\n\n" + ("**word** " * 400)
    result = li_delivery.format_post_text(long_md)
    assert len(result) <= li_delivery.LINKEDIN_POST_MAX_CHARS
    assert "**" not in result
    assert "#" not in result or result[0] != "#"


def test_format_post_text_passthrough_for_plain_short_text():
    text = "Just a plain post."
    assert li_delivery.format_post_text(text) == text


# ── register_image_upload ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_image_upload_returns_url_and_urn():
    fake_response = {
        "value": {
            "uploadMechanism": {
                "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest": {
                    "uploadUrl": "https://cdn.linkedin.com/upload/abc",
                    "headers": {"media-type-family": "STILLIMAGE"},
                }
            },
            "asset": "urn:li:digitalmediaAsset:C5600AQG123",
        }
    }

    async def _mock_post(url, *, json, headers, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = __import__("json").dumps(fake_response)
        mock_resp.headers = {}
        return mock_resp

    with patch("kdcube_ai_app.apps.chat.sdk.integrations.linkedin.accounts.httpx.AsyncClient") as cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _mock_post
        cls.return_value = mock_client

        result = await li_accounts.register_image_upload(access_token="tok", person_id="P1")

    assert result["upload_url"] == "https://cdn.linkedin.com/upload/abc"
    assert result["asset_urn"] == "urn:li:digitalmediaAsset:C5600AQG123"
    assert result["upload_headers"] == {"media-type-family": "STILLIMAGE"}


@pytest.mark.asyncio
async def test_register_image_upload_raises_on_403():
    async def _mock_post(url, *, json, headers, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.reason_phrase = "Forbidden"
        mock_resp.text = '{"message":"Forbidden"}'
        mock_resp.headers = {}
        return mock_resp

    with patch("kdcube_ai_app.apps.chat.sdk.integrations.linkedin.accounts.httpx.AsyncClient") as cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _mock_post
        cls.return_value = mock_client

        with pytest.raises(li_accounts.ProviderHttpError) as exc_info:
            await li_accounts.register_image_upload(access_token="tok", person_id="P1")

    assert exc_info.value.status == 403


# ── register_document_upload ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_document_upload_returns_url_and_urn():
    fake_response = {
        "value": {
            "uploadUrl": "https://cdn.linkedin.com/doc-upload/xyz",
            "document": "urn:li:document:D4E10AQF456",
        }
    }

    async def _mock_post(url, *, json, headers, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = __import__("json").dumps(fake_response)
        mock_resp.headers = {}
        return mock_resp

    with patch("kdcube_ai_app.apps.chat.sdk.integrations.linkedin.accounts.httpx.AsyncClient") as cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _mock_post
        cls.return_value = mock_client

        result = await li_accounts.register_document_upload(access_token="tok", person_id="P1")

    assert result["upload_url"] == "https://cdn.linkedin.com/doc-upload/xyz"
    assert result["document_urn"] == "urn:li:document:D4E10AQF456"


# ── upload_media_binary ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_media_binary_puts_data():
    captured: dict = {}

    async def _mock_put(url, *, content, headers, **kwargs):
        captured["url"] = url
        captured["data"] = content
        captured["content_type"] = headers.get("Content-Type")
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = ""
        return mock_resp

    with patch("kdcube_ai_app.apps.chat.sdk.integrations.linkedin.accounts.httpx.AsyncClient") as cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put = _mock_put
        cls.return_value = mock_client

        await li_accounts.upload_media_binary(
            upload_url="https://cdn.linkedin.com/upload/abc",
            data=b"\xff\xd8\xff",
            content_type="image/jpeg",
        )

    assert captured["url"] == "https://cdn.linkedin.com/upload/abc"
    assert captured["data"] == b"\xff\xd8\xff"
    assert captured["content_type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_upload_media_binary_raises_on_error():
    async def _mock_put(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.reason_phrase = "Bad Request"
        mock_resp.text = ""
        return mock_resp

    with patch("kdcube_ai_app.apps.chat.sdk.integrations.linkedin.accounts.httpx.AsyncClient") as cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put = _mock_put
        cls.return_value = mock_client

        with pytest.raises(li_accounts.ProviderHttpError):
            await li_accounts.upload_media_binary(
                upload_url="https://cdn.linkedin.com/upload/abc",
                data=b"data",
            )


# ── create_linkedin_media_post ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_linkedin_media_post_image_payload():
    captured: dict = {}

    async def _mock_post(url, *, json, headers, **kwargs):
        captured["payload"] = json
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = ""
        mock_resp.headers = {"x-restli-id": "urn:li:ugcPost:77777"}
        return mock_resp

    with patch("kdcube_ai_app.apps.chat.sdk.integrations.linkedin.accounts.httpx.AsyncClient") as cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _mock_post
        cls.return_value = mock_client

        result = await li_accounts.create_linkedin_media_post(
            access_token="tok",
            person_id="P1",
            text="Check this out",
            asset_urns=["urn:li:digitalmediaAsset:ABC", "urn:li:digitalmediaAsset:DEF"],
            media_category="IMAGE",
        )

    payload = captured["payload"]
    content = payload["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert content["shareMediaCategory"] == "IMAGE"
    assert len(content["media"]) == 2
    assert content["media"][0]["media"] == "urn:li:digitalmediaAsset:ABC"
    assert content["media"][0]["status"] == "READY"
    assert result["post_id"] == "urn:li:ugcPost:77777"


@pytest.mark.asyncio
async def test_create_linkedin_media_post_document_includes_title():
    captured: dict = {}

    async def _mock_post(url, *, json, headers, **kwargs):
        captured["payload"] = json
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = ""
        mock_resp.headers = {}
        return mock_resp

    with patch("kdcube_ai_app.apps.chat.sdk.integrations.linkedin.accounts.httpx.AsyncClient") as cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _mock_post
        cls.return_value = mock_client

        await li_accounts.create_linkedin_media_post(
            access_token="tok",
            person_id="P1",
            text="Here is my report",
            asset_urns=["urn:li:document:XYZ"],
            media_category="DOCUMENT",
            media_titles=["Q1 Report"],
        )

    content = captured["payload"]["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert content["shareMediaCategory"] == "DOCUMENT"
    assert content["media"][0]["title"] == {"text": "Q1 Report"}


@pytest.mark.asyncio
async def test_create_linkedin_media_post_raises_on_empty_urns():
    with pytest.raises(ValueError, match="asset_urns"):
        await li_accounts.create_linkedin_media_post(
            access_token="tok", person_id="P1", text="Hi", asset_urns=[],
        )
