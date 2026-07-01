# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation object presentation: refs + object shaping."""

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.presentation import (
    CONVERSATION_OBJECT_KIND,
    TURN_OBJECT_KIND,
    conv_file_ref,
    conversation_id_from_ref,
    conversation_ref,
    conversation_summary_to_object,
    conversation_to_object,
    fi_path_from_conv_ref,
    is_conv_file_ref,
    turn_hit_to_object,
)


def test_conv_file_ref_grammar_roundtrips():
    # fi: path -> conv:fi: handle (round-trippable), idempotent, non-fi passthrough.
    assert conv_file_ref("fi:turn_1.outputs/summary.md") == "conv:fi:turn_1.outputs/summary.md"
    assert conv_file_ref("conv:fi:turn_1.files/x") == "conv:fi:turn_1.files/x"
    assert conv_file_ref("ar:turn_1.react.turn.index") == "ar:turn_1.react.turn.index"
    # With a conversation id, the ref is scoped so an external client can resolve it.
    assert conv_file_ref("fi:turn_1.outputs/summary.md", "c9") == "conv:fi:conv_c9.turn_1.outputs/summary.md"
    assert conv_file_ref("fi:conv_c9.turn_1.outputs/summary.md", "c9") == "conv:fi:conv_c9.turn_1.outputs/summary.md"
    assert is_conv_file_ref("conv:fi:turn_1.outputs/summary.md")
    assert not is_conv_file_ref("conv:conversation:c1")
    # conv:fi: -> fi:, tolerating a bare fi: ref; anything else -> "".
    assert fi_path_from_conv_ref("conv:fi:turn_1.outputs/summary.md") == "fi:turn_1.outputs/summary.md"
    assert fi_path_from_conv_ref("fi:turn_1.files/x") == "fi:turn_1.files/x"
    assert fi_path_from_conv_ref("conv:conversation:c1") == ""
    # conversation_id_from_ref must NOT mistake a conv:fi: file ref for a conversation.
    assert conversation_id_from_ref("conv:fi:turn_1.outputs/summary.md") == ""


def test_turn_hit_snippet_path_presented_as_conv_fi():
    hit = {
        "turn_id": "t1", "conversation_id": "c1", "score": 0.3,
        "snippets": [{"role": "attachment", "path": "fi:turn_t1.user.attachments/summary.md", "text": "hello"}],
    }
    obj = turn_hit_to_object(hit)
    # Scoped to the hit's conversation so an external client can pass it back.
    assert obj["body"]["snippets"][0]["path"] == "conv:fi:conv_c1.turn_t1.user.attachments/summary.md"
    assert obj["body"]["snippets"][0]["text"] == "hello"


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
