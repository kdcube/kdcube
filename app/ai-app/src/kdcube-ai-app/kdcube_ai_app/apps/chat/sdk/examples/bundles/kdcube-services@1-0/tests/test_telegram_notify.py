# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Telegram notify: contract tests for the send-to-connected-account lane.

The regression surface: the text operation must refuse file-shaped keys (the
Slack post_message rule mirrored), images must arrive through the staged /
url / capped-inline lanes only, staged refs must be consumed exactly on a
successful send, and the recipient is always the authenticated caller."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import (
    new_staged_ref,
    save_staged,
)
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import (
    load_dynamic_module_for_path,
)

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _notify_module():
    _name, module = load_dynamic_module_for_path(
        BUNDLE_ROOT / "services" / "telegram" / "notify.py"
    )
    return module


def test_identity_family_unwrap_shapes():
    notify = _notify_module()
    family = {
        "identities": [
            {"provider": "google", "provider_subject": "g-1"},
            {"provider": "telegram", "provider_subject": "424242", "label": "elena"},
        ]
    }
    for envelope in (family, {"result": family}, {"identity_family_resolve": family}):
        got = notify.telegram_identity_from_family(envelope)
        assert got == {"telegram_user_id": "424242", "username": "elena"}
    assert notify.telegram_identity_from_family({"identities": []}) is None
    assert notify.telegram_identity_from_family(None) is None


def test_bot_deeplink():
    notify = _notify_module()
    assert notify.bot_deeplink({"bot_username": "@kdcube_doc_bot"}) == "https://t.me/kdcube_doc_bot"
    assert notify.bot_deeplink({"bot_username": ""}) == ""
    assert notify.bot_deeplink(None) == ""


def test_send_text_refuses_file_shaped_keys():
    notify = _notify_module()
    result = asyncio.run(
        notify.send_text(
            object(),
            identity={"user_id": "u1", "tenant": "t", "project": "p"},
            payload={"text": "hi", "images": [{"url": "https://x/y.png"}]},
        )
    )
    assert result["ok"] is False
    assert result["error"] == "telegram_send_carries_no_files"
    assert "telegram_send_images" in result["message"]


def test_send_text_requires_text():
    notify = _notify_module()
    result = asyncio.run(
        notify.send_text(object(), identity={"user_id": "u1"}, payload={})
    )
    assert result == {
        "ok": False,
        "error": "invalid_request",
        "message": "body.data.text is required",
    }


def test_send_text_happy_path_sends_to_caller(monkeypatch):
    notify = _notify_module()

    async def fake_bot_token(_entrypoint):
        return "tok-1", {"bot_username": "kdcube_doc_bot"}

    async def fake_resolve(_entrypoint, *, user_id, tenant, project):
        assert (user_id, tenant, project) == ("u1", "t", "p")
        return {"telegram_user_id": "424242", "username": "elena"}

    captured = {}

    async def fake_send(*, bot_token, chat_id, messages):
        captured.update(bot_token=bot_token, chat_id=chat_id, messages=messages)
        return {"ok": True, "sent": len(messages)}

    monkeypatch.setattr(notify, "_bot_token", fake_bot_token)
    monkeypatch.setattr(notify, "resolve_user_telegram", fake_resolve)
    monkeypatch.setattr(notify, "send_telegram_messages", fake_send)

    result = asyncio.run(
        notify.send_text(
            object(),
            identity={"user_id": "u1", "tenant": "t", "project": "p"},
            payload={"text": "harness says hello"},
        )
    )
    assert result == {"ok": True, "sent": 1, "error": ""}
    assert captured["bot_token"] == "tok-1"
    assert captured["chat_id"] == "424242"
    assert captured["messages"][0].kind == "text"
    assert captured["messages"][0].text == "harness says hello"


def test_send_text_reports_not_connected(monkeypatch):
    notify = _notify_module()

    async def fake_bot_token(_entrypoint):
        return "tok-1", {}

    async def fake_resolve(_entrypoint, **_kwargs):
        return None

    monkeypatch.setattr(notify, "_bot_token", fake_bot_token)
    monkeypatch.setattr(notify, "resolve_user_telegram", fake_resolve)
    result = asyncio.run(
        notify.send_text(object(), identity={"user_id": "u1"}, payload={"text": "x"})
    )
    assert result["ok"] is False
    assert result["error"] == "not_connected"


def test_send_images_staged_lane_consumes_on_success(tmp_path, monkeypatch):
    notify = _notify_module()
    staged_ref = new_staged_ref("shot.png")
    save_staged(tmp_path, staged_ref, b"png-bytes", owner={"user_id": "u1", "tenant": "t", "project": "p"})

    async def fake_bot_token(_entrypoint):
        return "tok-1", {}

    async def fake_resolve(_entrypoint, **_kwargs):
        return {"telegram_user_id": "424242", "username": ""}

    captured = {}

    async def fake_send(*, bot_token, chat_id, messages):
        captured["messages"] = messages
        return {"ok": True, "sent": len(messages)}

    monkeypatch.setattr(notify, "_bot_token", fake_bot_token)
    monkeypatch.setattr(notify, "resolve_user_telegram", fake_resolve)
    monkeypatch.setattr(notify, "send_telegram_messages", fake_send)
    monkeypatch.setattr(notify, "staging_root", lambda _p: tmp_path)

    result = asyncio.run(
        notify.send_images(
            object(),
            identity={"user_id": "u1", "tenant": "t", "project": "p"},
            payload={"images": [{"staged_ref": staged_ref}], "caption": "the chart"},
            storage_path=str(tmp_path),
        )
    )
    assert result == {"ok": True, "sent": 1, "total": 1, "error": ""}
    message = captured["messages"][0]
    assert message.kind == "photo"
    assert message.text == "the chart"
    file_item = message.files[0]
    assert file_item["filename"] == "shot.png"
    assert base64.b64decode(file_item["base64"]) == b"png-bytes"
    # single-use: the staged file is consumed after the successful send
    assert not any(tmp_path.rglob("shot.png"))


def test_send_images_inline_cap_and_document_kind(monkeypatch, tmp_path):
    notify = _notify_module()

    async def fake_bot_token(_entrypoint):
        return "tok-1", {}

    async def fake_resolve(_entrypoint, **_kwargs):
        return {"telegram_user_id": "424242", "username": ""}

    sent_messages = {}

    async def fake_send(*, bot_token, chat_id, messages):
        sent_messages["messages"] = messages
        return {"ok": True, "sent": len(messages)}

    monkeypatch.setattr(notify, "_bot_token", fake_bot_token)
    monkeypatch.setattr(notify, "resolve_user_telegram", fake_resolve)
    monkeypatch.setattr(notify, "send_telegram_messages", fake_send)

    oversized = base64.b64encode(b"x" * (notify.INLINE_IMAGE_MAX_BYTES + 1)).decode()
    result = asyncio.run(
        notify.send_images(
            object(),
            identity={"user_id": "u1"},
            payload={"images": [{"content_base64": oversized, "filename": "big.png"}]},
            storage_path=str(tmp_path),
        )
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_request"
    assert "staged_ref" in result["message"]

    result = asyncio.run(
        notify.send_images(
            object(),
            identity={"user_id": "u1"},
            payload={
                "images": [
                    {
                        "content_base64": base64.b64encode(b"%PDF").decode(),
                        "filename": "report.pdf",
                    }
                ]
            },
            storage_path=str(tmp_path),
        )
    )
    assert result["ok"] is True
    assert sent_messages["messages"][0].kind == "document"


def test_send_images_requires_images(tmp_path):
    notify = _notify_module()
    result = asyncio.run(
        notify.send_images(
            object(), identity={"user_id": "u1"}, payload={}, storage_path=str(tmp_path)
        )
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_request"
