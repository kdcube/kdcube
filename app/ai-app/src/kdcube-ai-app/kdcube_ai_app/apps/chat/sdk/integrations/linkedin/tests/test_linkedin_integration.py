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
from kdcube_ai_app.apps.chat.sdk.integrations import integration_config


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_entrypoint(props: dict | None = None, *, bundle_id: str = "test-bundle@1") -> SimpleNamespace:
    prop_map = props or {}

    def bundle_prop(key, default=None):
        return prop_map.get(key, default)

    ns = SimpleNamespace(
        bundle_prop=bundle_prop,
        bundle_id=bundle_id,
        config=SimpleNamespace(
            ai_bundle_spec=SimpleNamespace(id=bundle_id),
        ),
        comm_context=SimpleNamespace(
            actor=SimpleNamespace(tenant_id="tenant1", project_id="proj1"),
        ),
        settings=SimpleNamespace(TENANT="tenant1", PROJECT="proj1"),
    )
    return ns


def _linkedin_descriptor(
    *,
    enabled: bool = True,
    client_id: str = "real_client_id",
    secret_refs: dict | None = None,
) -> dict:
    return {
        "integrations": {
            "linkedin.default": {
                "provider": "linkedin",
                "enabled": enabled,
                "definition": {
                    "client_id": client_id,
                },
                "secret_refs": dict(secret_refs or {}),
            }
        }
    }


def _make_store(tmp_path, user_id="user-1"):
    return li_accounts.LinkedInAccountStore(tmp_path, user_id=user_id, bundle_id="test-bundle@1")


@pytest.fixture(autouse=True)
def _sdk_secret_fakes(monkeypatch):
    li_settings._CONFIGS.clear()
    li_settings.BUNDLE_ID = ""

    async def _empty_get_secret(*args, **kwargs):
        return ""

    async def _noop_set_user_secret(*args, **kwargs):
        return None

    async def _noop_delete_user_secret(*args, **kwargs):
        return None

    monkeypatch.setattr(li_accounts, "get_secret", _empty_get_secret)
    monkeypatch.setattr(integration_config, "get_secret", _empty_get_secret)
    monkeypatch.setattr(li_accounts, "set_user_secret", _noop_set_user_secret)
    monkeypatch.setattr(li_accounts, "delete_user_secret", _noop_delete_user_secret)


@pytest.mark.asyncio
async def test_oauth_state_secret_uses_linkedin_secret_only(monkeypatch):
    seen: list[str] = []

    async def _get_secret(key, **kw):
        seen.append(str(key))
        if "integrations.email.oauth_state_secret" in str(key):
            return "wrong-email-secret"
        if "integrations.telegram.webhook_secret" in str(key):
            return "wrong-telegram-secret"
        return ""

    monkeypatch.setattr(integration_config, "get_secret", _get_secret)
    ep = _make_entrypoint(
        _linkedin_descriptor(
            secret_refs={"oauth_state_secret": "identity.linkedin_oauth_state_secret"}
        )
    )

    assert await li_accounts.oauth_state_secret(ep) == ""
    assert all("integrations.email" not in key for key in seen)
    assert all("integrations.telegram" not in key for key in seen)


@pytest.mark.asyncio
async def test_oauth_state_secret_uses_descriptor_secret_ref(monkeypatch):
    async def _get_secret(key, **kwargs):
        if "linkedin_oauth_state_secret" in str(key):
            return "prop-secret"
        return ""

    monkeypatch.setattr(integration_config, "get_secret", _get_secret)
    ep = _make_entrypoint(
        _linkedin_descriptor(
            secret_refs={"oauth_state_secret": "identity.linkedin_oauth_state_secret"}
        )
    )
    assert await li_accounts.oauth_state_secret(ep) == "prop-secret"


# ── OAuth state: create and consume ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_oauth_state_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    secret = "test-secret-abc"

    result = await store.create_oauth_state_async(secret=secret, source="settings", return_hint="back")
    state = result["state"]
    payload = result["payload"]

    assert "." in state
    assert payload["user_id"] == "user-1"
    assert payload["source"] == "settings"
    assert payload["return_hint"] == "back"

    consumed = await store.consume_oauth_state_async(state=state, secret=secret)
    assert consumed["user_id"] == "user-1"
    assert consumed["provider"] == "linkedin"


@pytest.mark.asyncio
async def test_oauth_state_wrong_secret_rejected(tmp_path):
    store = _make_store(tmp_path)
    result = await store.create_oauth_state_async(secret="correct", source="settings")
    with pytest.raises(ValueError, match="signature"):
        await store.consume_oauth_state_async(state=result["state"], secret="wrong")


@pytest.mark.asyncio
async def test_oauth_state_tampered_payload_rejected(tmp_path):
    store = _make_store(tmp_path)
    result = await store.create_oauth_state_async(secret="s3cr3t", source="settings")
    state = result["state"]
    encoded, sig = state.rsplit(".", 1)
    tampered = encoded[:-2] + "AA" + "." + sig
    with pytest.raises(ValueError):
        await store.consume_oauth_state_async(state=tampered, secret="s3cr3t")


@pytest.mark.asyncio
async def test_oauth_state_expired(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    result = await store.create_oauth_state_async(secret="s3cr3t", source="settings", ttl_seconds=1)
    state = result["state"]
    # Wind the clock forward past the TTL — capture real time.time before patching
    real_now = time.time()
    monkeypatch.setattr(li_accounts.time, "time", lambda: real_now + 10)
    with pytest.raises(ValueError, match="expired"):
        await store.consume_oauth_state_async(state=state, secret="s3cr3t")


@pytest.mark.asyncio
async def test_oauth_state_replay_rejected(tmp_path):
    store = _make_store(tmp_path)
    result = await store.create_oauth_state_async(secret="s3cr3t", source="settings")
    state = result["state"]
    await store.consume_oauth_state_async(state=state, secret="s3cr3t")  # first use ok
    with pytest.raises(ValueError, match="not found"):
        await store.consume_oauth_state_async(state=state, secret="s3cr3t")  # replay rejected


@pytest.mark.asyncio
async def test_oauth_state_requires_non_empty_secret(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="not configured"):
        await store.create_oauth_state_async(secret="", source="settings")


# ── Account store: upsert + tokens ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_creates_account(tmp_path):
    store = _make_store(tmp_path)
    row = await store.upsert_account_async({"person_id": "ABC123", "email": "a@example.com", "display_name": "Alice"})

    assert row["person_id"] == "ABC123"
    assert row["provider"] == "linkedin"
    assert "access_token" not in row
    assert "token" not in row


@pytest.mark.asyncio
async def test_upsert_idempotent_by_person_id(tmp_path):
    store = _make_store(tmp_path)
    row1 = await store.upsert_account_async({"person_id": "P1", "display_name": "Alice"})
    await store.upsert_account_async({"person_id": "P1", "display_name": "Alice Updated"})

    accounts = await store.list_accounts_async()
    assert len(accounts) == 1
    assert accounts[0]["account_id"] == row1["account_id"]
    assert accounts[0]["display_name"] == "Alice Updated"


@pytest.mark.asyncio
async def test_upsert_does_not_leak_tokens_into_metadata(tmp_path):
    store = _make_store(tmp_path)
    row = await store.upsert_account_async({
        "person_id": "P1",
        "display_name": "Alice",
        "access_token": "should-not-appear",  # caller mistake — must be ignored
    })
    raw = json.loads((store.accounts_path).read_text())
    account_row = raw["accounts"][0]
    assert "access_token" not in account_row
    assert row.get("access_token") is None


@pytest.mark.asyncio
async def test_tokens_stored_out_of_band_not_in_accounts_file(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    account = await store.upsert_account_async({"person_id": "P1", "display_name": "Alice"})
    account_id = account["account_id"]

    token_data = {"access_token": "tok123", "expires_in": 5183944}
    stored: dict = {}

    async def _set_secret(key, value, **kw):
        stored[key] = value

    async def _get_secret(key, **kw):
        normalized = str(key or "")
        if normalized.startswith("u:"):
            normalized = normalized[2:]
        return stored.get(normalized)

    monkeypatch.setattr(li_accounts, "set_user_secret", _set_secret)
    monkeypatch.setattr(li_accounts, "get_secret", _get_secret)
    monkeypatch.setattr(integration_config, "get_secret", _get_secret)

    await store.set_tokens_async(account_id, token_data)

    # Token must NOT appear in the accounts JSON file
    raw = json.loads(store.accounts_path.read_text())
    assert "access_token" not in json.dumps(raw)

    # Token must be retrievable from the separate secret store
    retrieved = await store.get_tokens_async(account_id)
    assert retrieved["access_token"] == "tok123"


@pytest.mark.asyncio
async def test_list_accounts_reflects_has_token(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    await store.upsert_account_async({"person_id": "P1", "display_name": "Alice"})

    async def _empty_get_secret(key, **kw):
        return None

    monkeypatch.setattr(li_accounts, "get_secret", _empty_get_secret)
    assert (await store.list_accounts_async())[0]["has_token"] is False

    async def _token_get_secret(key, **kw):
        return '{"access_token":"tok"}'

    monkeypatch.setattr(li_accounts, "get_secret", _token_get_secret)
    assert (await store.list_accounts_async())[0]["has_token"] is True


@pytest.mark.asyncio
async def test_delete_account_removes_entry(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    account = await store.upsert_account_async({"person_id": "P1", "display_name": "Alice"})
    account_id = account["account_id"]

    deleted_keys: list[str] = []

    async def _delete_secret(key, **kw):
        deleted_keys.append(key)

    monkeypatch.setattr(li_accounts, "delete_user_secret", _delete_secret)

    deleted = await store.delete_account_async(account_id)
    assert deleted is True
    assert await store.list_accounts_async() == []
    assert any(account_id in k for k in deleted_keys)


def test_account_store_exposes_async_account_and_token_operations_only(tmp_path):
    store = _make_store(tmp_path)
    for name in (
        "list_accounts",
        "upsert_account",
        "delete_account",
        "set_tokens",
        "get_tokens",
        "delete_tokens",
        "create_oauth_state",
        "consume_oauth_state",
    ):
        assert not hasattr(store, name)


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

@pytest.mark.asyncio
async def test_settings_status_reflects_enabled_flag(tmp_path):
    li_settings.configure_linkedin_settings(
        storage_root_or_error=lambda ep: tmp_path,
        target_user_id=lambda ep, **kw: "user-1",
    )

    ep_enabled = _make_entrypoint(_linkedin_descriptor(enabled=True, client_id="real_client_id"))
    ep_disabled = _make_entrypoint(_linkedin_descriptor(enabled=False))

    result_on = await li_settings.status(ep_enabled)
    result_off = await li_settings.status(ep_disabled)

    assert result_on["enabled"] is True
    assert result_off["enabled"] is False
    assert "integrations[id=linkedin.*].enabled" not in result_on["configuration_missing"]
    assert "integrations[id=linkedin.*].enabled" in result_off["configuration_missing"]


@pytest.mark.asyncio
async def test_settings_status_missing_client_id(tmp_path):
    li_settings.configure_linkedin_settings(
        storage_root_or_error=lambda ep: tmp_path,
        target_user_id=lambda ep, **kw: "user-1",
    )
    ep = _make_entrypoint(_linkedin_descriptor(enabled=True, client_id=""))
    result = await li_settings.status(ep)
    assert "integrations[id=linkedin.*].definition.client_id" in result["configuration_missing"]
    assert result["linkedin_configured"] is False


@pytest.mark.asyncio
async def test_settings_status_fully_configured(tmp_path, monkeypatch):
    async def _get_secret(key, **kw):
        if "oauth_state_secret" in str(key):
            return "state-secret"
        if "client_secret" in str(key):
            return "client-secret"
        return ""

    monkeypatch.setattr(integration_config, "get_secret", _get_secret)
    li_settings.configure_linkedin_settings(
        storage_root_or_error=lambda ep: tmp_path,
        target_user_id=lambda ep, **kw: "user-1",
    )
    ep = _make_entrypoint(
        _linkedin_descriptor(
            enabled=True,
            client_id="real_client_id",
            secret_refs={
                "oauth_state_secret": "identity.linkedin_oauth_state_secret",
                "client_secret": "identity.linkedin_client_secret",
            },
        )
    )
    result = await li_settings.status(ep)
    assert result["ok"] is True
    assert result["linkedin_configured"] is True
    assert result["configuration_missing"] == []


@pytest.mark.asyncio
async def test_settings_status_returns_accounts(tmp_path, monkeypatch):
    async def _get_secret(key, **kw):
        raw_key = str(key)
        if raw_key.startswith("u:"):
            return '{"access_token":"tok"}'
        if "oauth_state_secret" in raw_key:
            return "state-secret"
        if "client_secret" in raw_key:
            return "client-secret"
        return ""

    monkeypatch.setattr(li_accounts, "get_secret", _get_secret)
    li_settings.configure_linkedin_settings(
        storage_root_or_error=lambda ep: tmp_path,
        target_user_id=lambda ep, **kw: "user-1",
    )
    store = _make_store(tmp_path)
    await store.upsert_account_async({"person_id": "P1", "display_name": "Alice"})

    ep = _make_entrypoint(
        _linkedin_descriptor(
            enabled=True,
            client_id="real_client_id",
            secret_refs={
                "oauth_state_secret": "identity.linkedin_oauth_state_secret",
                "client_secret": "identity.linkedin_client_secret",
            },
        )
    )
    result = await li_settings.status(ep)
    assert len(result["accounts"]) == 1
    assert result["accounts"][0]["display_name"] == "Alice"
    assert result["accounts"][0]["has_token"] is True


@pytest.mark.asyncio
async def test_settings_resolves_per_bundle_configuration(tmp_path, monkeypatch):
    async def _get_secret(key, **kw):
        raw_key = str(key)
        if raw_key.startswith("u:"):
            return '{"access_token":"tok"}'
        if "oauth_state_secret" in raw_key:
            return "state-secret"
        if "client_secret" in raw_key:
            return "client-secret"
        return ""

    monkeypatch.setattr(li_accounts, "get_secret", _get_secret)
    monkeypatch.setattr(integration_config, "get_secret", _get_secret)

    root_a = tmp_path / "bundle-a"
    root_b = tmp_path / "bundle-b"
    li_settings.configure_linkedin_settings(
        storage_root_or_error=lambda ep: root_a,
        target_user_id=lambda ep, **kw: "user-a",
        bundle_id="bundle-a@1",
    )
    li_settings.configure_linkedin_settings(
        storage_root_or_error=lambda ep: root_b,
        target_user_id=lambda ep, **kw: "user-b",
        bundle_id="bundle-b@1",
    )

    await li_accounts.LinkedInAccountStore(root_a, user_id="user-a", bundle_id="bundle-a@1").upsert_account_async(
        {"person_id": "A", "display_name": "Account A"}
    )
    await li_accounts.LinkedInAccountStore(root_b, user_id="user-b", bundle_id="bundle-b@1").upsert_account_async(
        {"person_id": "B", "display_name": "Account B"}
    )

    ep_a = _make_entrypoint(
        _linkedin_descriptor(
            enabled=True,
            client_id="client-a",
            secret_refs={
                "oauth_state_secret": "identity.linkedin_oauth_state_secret",
                "client_secret": "identity.linkedin_client_secret",
            },
        ),
        bundle_id="bundle-a@1",
    )
    ep_b = _make_entrypoint(
        _linkedin_descriptor(
            enabled=True,
            client_id="client-b",
            secret_refs={
                "oauth_state_secret": "identity.linkedin_oauth_state_secret",
                "client_secret": "identity.linkedin_client_secret",
            },
        ),
        bundle_id="bundle-b@1",
    )

    result_a = await li_settings.status(ep_a)
    result_b = await li_settings.status(ep_b)

    assert result_a["user_id"] == "user-a"
    assert [item["display_name"] for item in result_a["accounts"]] == ["Account A"]
    assert result_b["user_id"] == "user-b"
    assert [item["display_name"] for item in result_b["accounts"]] == ["Account B"]


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


def test_strip_markdown_image_does_not_leak_url_or_marker():
    result = li_delivery.strip_markdown("![alt text](https://img.example.com/pic.png)")
    assert result == "alt text"


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
