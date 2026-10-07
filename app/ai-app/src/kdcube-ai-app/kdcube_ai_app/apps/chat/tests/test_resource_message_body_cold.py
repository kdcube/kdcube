# SPDX-License-Identifier: MIT
"""W536/W619: /by-rn reads message bodies cold-aware (message and citable stages)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kdcube_ai_app.apps.chat.ingress.resources import resources
from kdcube_ai_app.apps.chat.sdk.storage import conversation_body_cold as cold
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.auth.sessions import UserSession, UserType

TS = "2026-03-04T05-06-07"


@pytest.fixture
def env(tmp_path, monkeypatch):
    storage_uri = tmp_path.as_uri()
    monkeypatch.setattr(resources, "get_settings", lambda: SimpleNamespace(STORAGE_PATH=storage_uri))
    return ConversationStore(storage_uri=storage_uri)


async def _put(store):
    _uri, mid, rn = await store.put_message(
        tenant="t", project="p", user="u1", fingerprint=None, conversation_id="c1", turn_id="t1",
        role="user", text="the original body", payload={"k": [1, 2]}, msg_ts=TS,
    )
    rel = f"cb/tenants/t/projects/p/conversation/u1/c1/t1/{mid}.json"
    original = json.loads(await store.backend.read_bytes_a(rel))
    return rel, rn, original


def _rns(rn):
    parts = rn.split(":")
    assert parts[4] == "message"
    return {stage: ":".join(parts[:4] + [stage] + parts[5:]) for stage in ("message", "citable")}


async def _by_rn(rn):
    return await resources.chatbot_content_by_rn(
        resources.RNContentRequest(rn=rn),
        request=SimpleNamespace(scope={"router": resources.router}),
        session=UserSession(session_id="s", user_type=UserType.REGISTERED, user_id="u1"),
    )


@pytest.mark.parametrize("stage", ["message", "citable"])
async def test_hot_body_is_served_with_storage_hot(env, stage):
    rel, rn, original = await _put(env)
    got = await _by_rn(_rns(rn)[stage])
    assert got.content_type == stage
    assert got.content == original
    assert got.metadata["storage"] == "hot"


@pytest.mark.parametrize("stage", ["message", "citable"])
async def test_cold_body_is_served_not_the_pointer(env, stage):
    rel, rn, original = await _put(env)
    assert await env.archive_message_body(rel) == cold.MOVED
    assert json.loads(env.backend.read_text(rel))["schema"] == cold.POINTER_SCHEMA

    got = await _by_rn(_rns(rn)[stage])
    assert got.content == original
    assert got.metadata["storage"] == "cold"
    assert "schema" not in got.content and "cold_key" not in got.content


@pytest.mark.parametrize("stage", ["message", "citable"])
@pytest.mark.parametrize("damage", ["corrupt", "missing"])
async def test_unavailable_cold_body_is_a_503_not_the_pointer(env, stage, damage):
    rel, rn, _original = await _put(env)
    assert await env.archive_message_body(rel) == cold.MOVED
    cold_key = cold.cold_key_for(rel)
    if damage == "corrupt":
        await env.backend.write_bytes_a(cold_key, b'{"text": "tampered"}')
    else:
        await env.backend.delete_a(cold_key)

    with pytest.raises(HTTPException) as exc:
        await _by_rn(_rns(rn)[stage])
    assert exc.value.status_code == 503
    assert exc.value.detail == "message_body_unavailable"
