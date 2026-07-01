# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation object presentation: refs + object shaping."""

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.presentation import (
    CONVERSATION_OBJECT_KIND,
    TURN_OBJECT_KIND,
    conversation_id_from_ref,
    conversation_ref,
    conversation_summary_to_object,
    conversation_to_object,
    turn_hit_to_object,
)


def test_conversation_ref_roundtrip():
    assert conversation_ref("c1") == "conv:conversation:c1"
    assert conversation_ref("") == ""
    assert conversation_id_from_ref("conv:conversation:c1") == "c1"
    assert conversation_id_from_ref("conv:c1") == "c1"  # bare form
    assert conversation_id_from_ref("c1") == "c1"  # plain id
    assert conversation_id_from_ref("conv:turn:t1") == ""  # a typed non-conversation ref


def test_summary_object_is_compact_and_kinded():
    obj = conversation_summary_to_object({"conversation_id": "c1", "title": "T", "turn_count": 2, "user_id": ""})
    assert obj["object_kind"] == CONVERSATION_OBJECT_KIND
    assert obj["ref"] == "conv:conversation:c1"
    assert obj["body"] == {"conversation_id": "c1", "title": ""} or "user_id" not in obj["body"]
    # Empty fields are dropped.
    assert "user_id" not in obj["body"]
    assert obj["body"]["turn_count"] == 2


def test_full_conversation_object_carries_record_body():
    record = {"conversation_id": "c1", "user_id": "u", "turns": [{"turn_id": "t1"}]}
    obj = conversation_to_object(record)
    assert obj["object_kind"] == CONVERSATION_OBJECT_KIND
    assert obj["body"] == record


def test_turn_hit_object():
    hit = {"turn_id": "t1", "conversation_id": "c1", "snippets": [{"role": "assistant", "text": "hello"}], "score": 0.5}
    obj = turn_hit_to_object(hit)
    assert obj["object_kind"] == TURN_OBJECT_KIND
    assert obj["ref"] == "conv:turn:t1"
    assert obj["summary"] == "hello"
    assert obj["score"] == 0.5
    assert obj["body"]["conversation_id"] == "c1"
