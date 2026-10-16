# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.storage.rn import parse_file_path


def test_explicit_storage_uri_does_not_read_ambient_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.storage.conversation_store.get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("ambient settings were read")),
    )

    store = ConversationStore(storage_uri=tmp_path.as_uri())

    assert store.storage_uri == tmp_path.as_uri()


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


async def _upload(store, filename, data):
    return await store.put_attachment(
        tenant="tenant", project="project", user="user", fingerprint=None,
        conversation_id="conversation", turn_id="turn_1", filename=filename, data=data,
        mime="image/png", origin="user",
    )


@pytest.mark.asyncio
async def test_three_uploads_with_one_name_are_three_stored_objects(tmp_path):
    """W210: three files named image.png in one click keep three distinct objects and sizes."""
    store = ConversationStore(storage_uri=tmp_path.as_uri())
    payloads = [b"a" * 91, b"b" * 264, b"c" * 183]

    uploaded = [await _upload(store, "image.png", data) for data in payloads]

    keys = [key for _uri, key, _rn in uploaded]
    assert len(set(keys)) == 3
    assert [await store.get_blob_bytes(uri) for uri, _key, _rn in uploaded] == payloads


@pytest.mark.asyncio
async def test_an_upload_never_replaces_an_existing_attachment(tmp_path, monkeypatch):
    """W210: even when two uploads draw the same stored name, the first keeps its bytes."""
    import kdcube_ai_app.apps.chat.sdk.storage.conversation_store as module

    store = ConversationStore(storage_uri=tmp_path.as_uri())
    first_uri, first_key, _ = await _upload(store, "image.png", b"first")
    taken = first_key.rsplit("/", 1)[1]
    names = iter([taken, taken])  # the next upload collides twice, then draws a fresh name
    original = module.attachment_rn_and_rel_name

    async def colliding(*args, **kwargs):
        rn, rel_name = await original(*args, **kwargs)
        forced = next(names, None)
        return (rn.replace(rel_name, forced), forced) if forced else (rn, rel_name)

    monkeypatch.setattr(module, "attachment_rn_and_rel_name", colliding)
    second_uri, second_key, _ = await _upload(store, "image.png", b"second")

    assert second_key != first_key
    assert await store.get_blob_bytes(first_uri) == b"first"
    assert await store.get_blob_bytes(second_uri) == b"second"
