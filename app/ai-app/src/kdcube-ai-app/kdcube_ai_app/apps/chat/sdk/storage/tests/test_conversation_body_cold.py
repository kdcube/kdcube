# SPDX-License-Identifier: MIT
"""W536 body cold tier on a temp-dir file backend: key scoping, pointer scoping, concurrent rewrite."""

from __future__ import annotations

import hashlib
import json

import pytest

from kdcube_ai_app.apps.chat.sdk.storage import conversation_body_cold as cold
from kdcube_ai_app.apps.chat.sdk.storage import conversation_store
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

TS = "2026-03-04T05-06-07"
MSG = f"user-{TS}-0000abcd"


@pytest.fixture
def store(tmp_path, monkeypatch):
    # The same message file name for every put on the same second.
    monkeypatch.setattr(conversation_store, "_mid", lambda role, ts=None: f"{role}-{ts}-0000abcd")
    return ConversationStore(storage_uri=tmp_path.as_uri())


async def _put(store, user, conv, turn="t1", text=None):
    _uri, mid, _rn = await store.put_message(
        tenant="t", project="p", user=user, fingerprint=None, conversation_id=conv, turn_id=turn,
        role="user", text=text or f"body of {user}/{conv}/{turn}", msg_ts=TS,
    )
    assert mid == MSG
    return f"cb/tenants/t/projects/p/conversation/{user}/{conv}/{turn}/{MSG}.json"


@pytest.mark.parametrize("other", [("u2", "c1"), ("u1", "c2")])
async def test_cold_keys_are_scoped_by_user_conversation_and_turn(store, other):
    a = await _put(store, "u1", "c1")
    b = await _put(store, *other)
    assert cold.cold_key_for(a) == f"cb/tenants/t/projects/p/conversation-cold-bodies/2026/03/04/u1/c1/t1/{MSG}.json"
    assert cold.cold_key_for(b) == (
        f"cb/tenants/t/projects/p/conversation-cold-bodies/2026/03/04/{other[0]}/{other[1]}/t1/{MSG}.json"
    )

    assert await store.archive_message_body(a) == cold.MOVED
    assert await store.archive_message_body(b) == cold.MOVED

    assert await store.backend.exists_a(cold.cold_key_for(a))
    assert await store.backend.exists_a(cold.cold_key_for(b))
    got_a, got_b = await store.get_message(a), await store.get_message(b)
    assert (got_a["storage"], got_a["text"]) == ("cold", "body of u1/c1/t1")
    assert (got_b["storage"], got_b["text"]) == ("cold", f"body of {other[0]}/{other[1]}/t1")


@pytest.mark.parametrize("other", [("u2", "c1"), ("u1", "c2")])
async def test_a_pointer_naming_another_owners_cold_body_is_not_followed(store, other):
    a = await _put(store, "u1", "c1")
    b = await _put(store, *other, text="SECRET of the other owner")
    assert await store.archive_message_body(b) == cold.MOVED
    foreign_key = cold.cold_key_for(b)
    foreign = await store.backend.read_bytes_a(foreign_key)

    # A's hot object becomes a well-formed pointer (valid hash) at B's cold body.
    forged = {
        "schema": cold.POINTER_SCHEMA, "cold_key": foreign_key,
        "sha256": hashlib.sha256(foreign).hexdigest(), "bytes": len(foreign),
        "user_id": "u1", "conversation_id": "c1", "turn_id": "t1", "role": "user", "message_id": MSG,
    }
    raw = json.dumps(forged).encode()
    await store.backend.write_bytes_a(a, raw)

    for got in (await store.get_message(a), cold.read_sync(store.backend, a, raw.decode())):
        assert got["storage"] in ("hot", "unavailable")
        assert got.get("text") != "SECRET of the other owner"
        assert "SECRET" not in json.dumps(got)


async def test_a_hot_body_rewritten_during_the_cold_copy_refuses_the_move(store, monkeypatch):
    a = await _put(store, "u1", "c1", text="old")
    backend = store.backend
    cold_key = cold.cold_key_for(a)
    real_read = backend.read_bytes_a
    rewritten = []

    async def read_then_rewrite(key, *args, **kwargs):
        data = await real_read(key, *args, **kwargs)
        if key == cold_key and not rewritten:  # the cold readback, before the hot re-read
            record = json.loads(await real_read(a))
            record["text"] = "new"
            await backend.write_bytes_a(a, json.dumps(record).encode())
            rewritten.append(key)
        return data

    monkeypatch.setattr(backend, "read_bytes_a", read_then_rewrite)
    with pytest.raises(ValueError, match="changed during cold copy"):
        await store.archive_message_body(a)
    assert rewritten == [cold_key]
    monkeypatch.undo()

    got = await store.get_message(a)
    assert (got["storage"], got["text"]) == ("hot", "new")
    assert json.loads(await store.backend.read_bytes_a(a)).get("schema") != cold.POINTER_SCHEMA
