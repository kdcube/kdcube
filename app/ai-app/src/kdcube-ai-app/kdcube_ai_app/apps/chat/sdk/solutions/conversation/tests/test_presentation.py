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
    assert obj["ref"] == "conv:turn:t1"
    # Title is derived from the first snippet's text (not the turn_id).
    assert obj["title"] == "hello"
    assert obj["score"] == 0.5
    assert obj["body"]["conversation_id"] == "c1"
    # Snippet content (with text) is preserved in the body.
    assert obj["body"]["snippets"] == [{"role": "assistant", "text": "hello"}]
    # Turn search hits carry only actionable fields — the single-object envelope
    # (schema/mime/namespace/object_kind/identity) and verbose duplicates are gone.
    for dropped in ("schema", "mime", "identity", "namespace", "object_kind", "label", "summary", "rank_score"):
        assert dropped not in obj
    # Compacted body drops null catalog fields.
    assert "ordinal" not in obj["body"]
    assert "total_turns" not in obj["body"]


def test_turn_hit_object_title_falls_back_to_turn_id_when_no_snippet_text():
    # If snippets carry no text, title falls back to the turn id (never blank).
    hit = {"turn_id": "t1", "conversation_id": "c1", "snippets": [], "score": 0.1}
    obj = turn_hit_to_object(hit)
    assert obj["title"] == "t1"
