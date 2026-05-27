# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.storage.rn import parse_file_path


@pytest.mark.asyncio
async def test_file_attachment_uri_is_absolute_file_uri(tmp_path):
    store = ConversationStore(storage_uri=tmp_path.as_uri())

    uri, key, _rn = await store.put_attachment(
        tenant="tenant",
        project="project",
        user="user",
        fingerprint=None,
        conversation_id="conversation",
        turn_id="turn_1",
        filename="report.xlsx",
        data=b"xlsx-bytes",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert uri.startswith("file:///")
    assert "file://tmp" not in uri
    assert key == "cb/tenants/tenant/projects/project/attachments/user/conversation/turn_1/report.xlsx"
    assert await store.get_blob_bytes(uri) == b"xlsx-bytes"


@pytest.mark.asyncio
async def test_artifact_file_preserves_workspace_relative_path(tmp_path):
    store = ConversationStore(storage_uri=tmp_path.as_uri())
    conversation_id = "9274b8b0-2edd-4398-aa91-ecec94991db7"

    uri, key, _rn = await store.put_artifact_file(
        tenant="tenant",
        project="project",
        user="user",
        fingerprint=None,
        conversation_id=conversation_id,
        turn_id="turn_1",
        relpath="turn_1/outputs/analysis/report.json",
        data=b'{"ok": true}\n',
        mime="application/json",
    )

    assert uri.startswith("file:///")
    assert key == (
        f"cb/tenants/tenant/projects/project/attachments/user/{conversation_id}/"
        "turn_1/turn_1/outputs/analysis/report.json"
    )
    assert _rn.endswith(":artifact:turn_1%2Foutputs%2Fanalysis%2Freport.json")
    assert "/" not in _rn.rsplit(":", 1)[-1]
    assert parse_file_path(key)["filename"] == "turn_1/outputs/analysis/report.json"
    assert await store.get_blob_bytes(key) == b'{"ok": true}\n'
    assert await store.get_blob_bytes(uri) == b'{"ok": true}\n'
