# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for the turn-collapse logic ported from the Phase-0 extractor: a fetched
turn's `artifacts` list is collapsed into a flat {user, assistant, attachments,
citations} record. (The DB-backed ControlPlaneDataSource is integration-tested
live in Phase 2.)
"""
from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.export_adapter import collapse_turn


def test_collapses_user_and_assistant_text():
    turn = {
        "turn_id": "t1",
        "artifacts": [
            {"type": "chat:user", "data": {"payload": {"text": "where am I?"}}},
            {"type": "chat:assistant", "data": {"payload": {"text": "near Hvar"}}},
        ],
    }
    out = collapse_turn(turn)
    assert out["turn_id"] == "t1"
    assert out["user"] == "where am I?"
    assert out["assistant"] == "near Hvar"


def test_collapses_multiple_messages_joined():
    turn = {
        "turn_id": "t2",
        "artifacts": [
            {"type": "chat:user", "data": {"payload": {"text": "one"}}},
            {"type": "chat:user", "data": {"payload": {"text": "two"}}},
        ],
    }
    assert collapse_turn(turn)["user"] == "one\n\ntwo"


def test_extracts_attachments_without_raw_bytes():
    turn = {
        "turn_id": "t3",
        "artifacts": [
            {"type": "artifact:user.attachment",
             "data": {"payload": {"filename": "chart.png", "base64": "AAAA", "bytes": 123}}},
        ],
    }
    atts = collapse_turn(turn)["attachments"]
    assert atts == [{"filename": "chart.png"}]


def test_extracts_citations():
    turn = {
        "turn_id": "t4",
        "artifacts": [
            {"type": "artifact:solver.program.citables",
             "data": {"payload": {"items": [{"title": "src", "url": "http://x"}]}}},
        ],
    }
    cites = collapse_turn(turn)["citations"]
    assert cites == [{"title": "src", "url": "http://x"}]


def test_payload_accepts_flat_dict():
    # Some artifacts carry the payload at the top level rather than under 'payload'.
    turn = {"turn_id": "t5", "artifacts": [{"type": "chat:user", "data": {"text": "flat"}}]}
    assert collapse_turn(turn)["user"] == "flat"
