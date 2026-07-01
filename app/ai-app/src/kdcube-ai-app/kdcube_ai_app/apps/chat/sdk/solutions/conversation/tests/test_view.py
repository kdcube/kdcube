# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""build_conversation_timeline: rich artifacts -> lightweight interleaved view."""

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.view import build_conversation_timeline


def test_interleaves_by_ts_and_surfaces_files_sources_thinking():
    fetched = {
        "conversation_id": "c1", "user_id": "u", "conversation_title": "AI News",
        "turns": [{
            "turn_id": "t1",
            "artifacts": [
                # deliberately out of order; the transform sorts by ts
                {"type": "chat:assistant", "ts": "2026-07-01T22:05:00Z", "data": {"text": "done"}},
                {"type": "chat:user", "ts": "2026-07-01T22:00:00Z",
                 "data": {"text": "make a chart", "attachments": [
                     {"filename": "input.csv", "mime": "text/csv", "artifact_path": "fi:turn_t1.user.attachments/input.csv"}]}},
                {"type": "artifact:conv.thinking.stream", "ts": "2026-07-01T22:01:00Z",
                 "data": {"payload": {"items": [{"agent": "solver", "text": "I will plot it"}, {"agent": "solver", "text": ""}]}}},
                {"type": "artifact:solver.program.citables", "ts": "2026-07-01T22:02:00Z",
                 "data": {"payload": {"items": [{"sid": 1, "title": "Src", "url": "http://x", "text": "big body dropped"}]}}},
                {"type": "artifact:assistant.file", "ts": "2026-07-01T22:03:00Z",
                 "data": {"payload": {"filename": "chart.png", "mime": "image/png",
                                       "artifact_path": "fi:turn_t1.outputs/chart.png"}}},
                {"type": "artifact:conv.artifacts.stream", "ts": "2026-07-01T22:04:00Z",
                 "data": {"payload": {"items": [{"artifact_name": "ai.md", "title": "Summary", "format": "markdown",
                                                  "text": "huge body dropped"}]}}},
                # ignored kinds
                {"type": "artifact:conv.timeline_text.stream", "ts": "2026-07-01T22:04:30Z", "data": {"payload": {"items": []}}},
                {"type": "artifact:conv.user_shortcuts", "ts": "2026-07-01T22:04:40Z", "data": {"payload": {"items": []}}},
            ],
        }],
    }
    view = build_conversation_timeline(fetched)
    assert view["conversation_id"] == "c1"
    assert view["title"] == "AI News"
    assert view["turn_count"] == 1

    assert [t["turn_id"] for t in view["turns"]] == ["t1"]
    events = view["turns"][0]["events"]
    # turn_id is on the turn, not repeated on events.
    assert all("turn_id" not in e for e in events)
    types = [e["type"] for e in events]
    # Sorted by ts within the turn: user.message + its attachment (22:00) lead, then
    # thinking (22:01), sources (22:02), file (22:03), artifacts (22:04), reply (22:05).
    assert types == [
        "user.message", "user.attachment", "assistant.thinking",
        "sources", "assistant.file", "artifacts", "assistant.message",
    ]

    by_type = {e["type"]: e for e in events}
    # Refs are scoped to the conversation (fi:conv_<id>.…) so external clients resolve them.
    assert by_type["user.attachment"]["ref"] == "conv:fi:conv_c1.turn_t1.user.attachments/input.csv"
    assert by_type["assistant.file"]["ref"] == "conv:fi:conv_c1.turn_t1.outputs/chart.png"
    # thinking drops the empty item
    assert by_type["assistant.thinking"]["items"] == [{"agent": "solver", "text": "I will plot it"}]
    # sources keep only sid/title/url (heavy text dropped)
    assert by_type["sources"]["items"] == [{"sid": 1, "title": "Src", "url": "http://x"}]
    # artifacts keep a light catalog (no body)
    assert by_type["artifacts"]["items"] == [{"name": "ai.md", "title": "Summary", "format": "markdown"}]


def test_empty_fetch_yields_no_turns():
    view = build_conversation_timeline({})
    assert view["turns"] == []
    assert view["turn_count"] == 0
