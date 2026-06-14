# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.user_inputs import (
    ingest_user_attachments,
    iter_turn_user_input_entries,
)


@pytest.mark.asyncio
async def test_ingest_user_attachments_extracts_base64_text_attachment():
    result = await ingest_user_attachments(
        attachments=[
            {
                "filename": "note.txt",
                "mime": "text/plain",
                "base64": base64.b64encode(b"hello from telegram").decode("ascii"),
            }
        ],
        store=None,
    )

    assert len(result) == 1
    assert result[0]["filename"] == "note.txt"
    assert result[0]["mime"] == "text/plain"
    assert result[0]["text"] == "hello from telegram"
    assert result[0]["base64"]


@pytest.mark.asyncio
async def test_ingest_user_attachments_reports_invalid_base64_attachment():
    result = await ingest_user_attachments(
        attachments=[
            {
                "filename": "broken.txt",
                "mime": "text/plain",
                "base64": "not-valid-base64",
            }
        ],
        store=None,
    )

    assert len(result) == 1
    assert result[0]["filename"] == "broken.txt"
    assert result[0]["error"].startswith("base64_decode_failed:")


def test_iter_turn_user_input_entries_groups_context_only_batch():
    blocks = [
        {
            "turn_id": "turn_1",
            "type": "event.external",
            "path": "ar:turn_1.context.memory",
            "text": "{}",
            "meta": {
                "batch_id": "batch_1",
                "event_type": "memory.context",
                "event_source_id": "memories",
                "event": {
                    "id": "evt_context",
                    "type": "memory.context",
                    "payload": {
                        "event": {
                            "label": "Judo cancellation memory",
                            "ref": "mem:123",
                            "summary": "Signed cancellation document was attached.",
                        }
                    },
                },
            },
        },
        {
            "turn_id": "turn_1",
            "type": "user.attachment.file",
            "path": "fi:turn_1.attachments/cancel.docx",
            "text": '{"filename":"cancel.docx","mime":"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}',
            "meta": {"batch_id": "batch_1"},
        },
        {
            "turn_id": "turn_1",
            "type": "user.prompt",
            "path": "ar:turn_1.user.prompt",
            "text": "",
            "ts": "2026-06-14T00:00:00Z",
            "meta": {
                "batch_id": "batch_1",
                "event_type": "event.user.prompt",
                "event": {"id": "evt_prompt", "type": "event.user.prompt", "payload": {"event": {"text": ""}}},
            },
        },
    ]

    entries = iter_turn_user_input_entries(blocks, turn_id="turn_1")

    assert len(entries) == 1
    entry = entries[0]
    assert entry["plain_text"] == ""
    assert entry["batch_id"] == "batch_1"
    assert entry["user_event_type"] == "event.user.prompt"
    assert entry["contexts"][0]["label"] == "Judo cancellation memory"
    assert entry["contexts"][0]["ref"] == "mem:123"
    assert entry["attachments"][0]["label"] == "cancel.docx"
    assert '"context"' in entry["text"]
    assert "Judo cancellation memory" in entry["text"]
    assert "[user.message]\n(no typed message)" in entry["index_text"]
    assert "[context.1] Judo cancellation memory" in entry["index_text"]
    assert "[attachment.1] cancel.docx" in entry["index_text"]
