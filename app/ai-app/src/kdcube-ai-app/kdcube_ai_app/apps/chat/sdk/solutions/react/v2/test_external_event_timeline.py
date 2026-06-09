# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

import pytest

from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline import extract_user_attachments_from_blocks


class _FakeRedis:
    def __init__(self):
        self._kv = {}
        self._streams = {}
        self._stream_seq = {}

    async def incr(self, key):
        self._kv[key] = int(self._kv.get(key, 0)) + 1
        return self._kv[key]

    async def xadd(self, key, fields):
        seq = int(self._stream_seq.get(key, 0)) + 1
        self._stream_seq[key] = seq
        stream_id = f"{seq}-0"
        self._streams.setdefault(key, []).append((stream_id, dict(fields or {})))
        return stream_id

    async def xrange(self, key, min="-", max="+", count=None):
        items = list(self._streams.get(key, []))
        out = []
        for stream_id, fields in items:
            if min not in ("-", None, ""):
                exclusive = str(min).startswith("(")
                floor = str(min)[1:] if exclusive else str(min)
                if exclusive:
                    if stream_id <= floor:
                        continue
                elif stream_id < floor:
                    continue
            if max not in ("+", None, "") and stream_id > str(max):
                continue
            out.append((stream_id, dict(fields)))
            if count is not None and len(out) >= int(count):
                break
        return out

    async def setex(self, key, ttl, value):
        del ttl
        self._kv[key] = value

    async def set(self, key, value, ex=None, nx=False):
        del ex
        if nx and key in self._kv:
            return False
        self._kv[key] = value
        return True

    async def get(self, key):
        return self._kv.get(key)

    async def delete(self, key):
        self._kv.pop(key, None)


class _FakeCtxClient:
    class _Store:
        async def get_blob_bytes(self, uri_or_path):
            if uri_or_path == "s3://bucket/brief.txt":
                return b"attachment text from followup"
            if uri_or_path == "s3://bucket/prompt.txt":
                return b"attachment text from prompt"
            raise FileNotFoundError(uri_or_path)

    def __init__(self):
        self.store = self._Store()

    async def recent(self, *args, **kwargs):
        return {"items": []}

    async def fetch_latest_feedback_reactions(self, *args, **kwargs):
        return {"items": []}


@pytest.mark.asyncio
async def test_browser_folds_external_events_into_history_and_current_turn(tmp_path):
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant",
        project="project",
        conversation_id="conv_1",
    )
    await source.publish(
        kind="followup",
        explicit=True,
        target_turn_id="turn_old",
        active_turn_id_at_ingress="turn_old",
        owner_turn_id="turn_old",
        source="ingress.sse",
        text="history event",
        payload={"message": "history event"},
        task_payload={
            "request": {
                "payload": {
                    "attachments": [
                        {
                            "filename": "brief.txt",
                            "mime": "text/plain",
                            "hosted_uri": "s3://bucket/brief.txt",
                        }
                    ]
                }
            }
        },
    )
    await source.publish(
        kind="message",
        explicit=True,
        target_turn_id="turn_current",
        active_turn_id_at_ingress="turn_current",
        owner_turn_id="turn_current",
        source="ingress.sse",
        text="stream-originated prompt",
        payload={"message": "stream-originated prompt"},
        task_payload={
            "request": {
                "payload": {
                    "attachments": [
                        {
                            "filename": "prompt.txt",
                            "mime": "text/plain",
                            "hosted_uri": "s3://bucket/prompt.txt",
                        }
                    ]
                }
            }
        },
    )
    await source.publish(
        kind="steer",
        explicit=True,
        target_turn_id="turn_current",
        active_turn_id_at_ingress="turn_current",
        owner_turn_id="turn_current",
        source="ingress.sse",
        text="current event",
        payload={"message": "current event"},
    )

    runtime = RuntimeCtx(
        tenant="tenant",
        project="project",
        user_id="user_1",
        user_type="privileged",
        conversation_id="conv_1",
        turn_id="turn_current",
        bundle_id="bundle@1",
        started_at="2026-04-11T10:00:00Z",
        outdir=str(tmp_path / "out"),
        workdir=str(tmp_path / "work"),
        external_event_source=source,
    )
    browser = ContextBrowser(
        ctx_client=_FakeCtxClient(),
        runtime_ctx=runtime,
    )
    hook_saw_current_prompt = []

    async def _hook(*, type, event, blocks):
        del event, blocks
        if type == "message":
            hook_saw_current_prompt.append(
                any(
                    b.get("type") == "user.prompt"
                    and b.get("turn_id") == "turn_current"
                    and b.get("text") == "stream-originated prompt"
                    for b in browser.timeline.get_turn_blocks()
                )
            )

    browser.add_external_event_hook(_hook, start_listener=False)

    await browser.load_timeline()
    try:
        history_blocks = browser.timeline.get_history_blocks()
        current_blocks = browser.timeline.get_turn_blocks()
        history_attachments = extract_user_attachments_from_blocks(history_blocks)

        assert any(b.get("type") == "user.followup" and b.get("turn_id") == "turn_old" for b in history_blocks)
        assert any(
            b.get("type") == "user.attachment.meta"
            and b.get("turn_id") == "turn_old"
            and ".external.followup.attachments/" in str(b.get("path") or "")
            for b in history_blocks
        )
        assert any(
            b.get("type") == "user.attachment.text"
            and b.get("turn_id") == "turn_old"
            and b.get("text") == "attachment text from followup"
            for b in history_blocks
        )
        assert any(att.get("filename") == "brief.txt" for att in history_attachments)
        assert any(
            b.get("type") == "user.prompt"
            and b.get("turn_id") == "turn_current"
            and b.get("text") == "stream-originated prompt"
            and ".user.prompt." in str(b.get("path") or "")
            for b in current_blocks
        )
        assert any(
            b.get("type") == "user.attachment.meta"
            and b.get("turn_id") == "turn_current"
            and ".user.attachments/" in str(b.get("path") or "")
            for b in current_blocks
        )
        assert any(
            b.get("type") == "user.attachment.text"
            and b.get("turn_id") == "turn_current"
            and b.get("text") == "attachment text from prompt"
            for b in current_blocks
        )
        assert any(b.get("type") == "user.steer" and b.get("turn_id") == "turn_current" for b in current_blocks)
        assert hook_saw_current_prompt == [True]
        assert browser.timeline.last_external_event_id == "3-0"
        assert int(browser.timeline.last_external_event_seq or 0) == 3
    finally:
        await browser.stop_external_event_listener()


@pytest.mark.asyncio
async def test_browser_uses_default_policy_for_unregistered_snapshot_event(tmp_path):
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant",
        project="project",
        conversation_id="conv_1",
    )
    logical_path = "ev:turn_current.events/task-tracker/snapshots/draft-123/canvas/latest"
    await source.publish(
        kind="external_event",
        event_id="evt_snapshot_1",
        explicit=True,
        target_turn_id="turn_current",
        active_turn_id_at_ingress="turn_current",
        owner_turn_id="turn_current",
        source="ingress.sse",
        event_source_id="task_tracker.canvas.snapshot",
        payload={
            "event": {
                "event_id": "evt_snapshot_1",
                "type": "event.snapshot",
                "event_source_id": "task_tracker.canvas.snapshot",
                "logical_path": logical_path,
                "reactive": False,
                "story_id": "draft:123",
                "payload": {
                    "mime": "application/json",
                    "event": {
                        "title": "Canvas snapshot",
                        "summary": "Canvas has two notes and one attachment.",
                    },
                },
            }
        },
    )

    runtime = RuntimeCtx(
        tenant="tenant",
        project="project",
        user_id="user_1",
        user_type="privileged",
        conversation_id="conv_1",
        turn_id="turn_current",
        bundle_id="bundle@1",
        started_at="2026-04-11T10:00:00Z",
        outdir=str(tmp_path / "out"),
        workdir=str(tmp_path / "work"),
        external_event_source=source,
    )
    browser = ContextBrowser(
        ctx_client=_FakeCtxClient(),
        runtime_ctx=runtime,
    )
    await browser.load_timeline()
    try:
        current_blocks = browser.timeline.get_turn_blocks()
        snapshot = next((b for b in current_blocks if b.get("type") == "event.snapshot"), None)
        assert snapshot is not None
        meta = snapshot.get("meta") if isinstance(snapshot.get("meta"), dict) else {}
        assert snapshot.get("path") == logical_path
        assert meta.get("event_source_id") == "task_tracker.canvas.snapshot"
        assert meta.get("event_id") == "evt_snapshot_1"
        assert meta.get("event_type") == "event.snapshot"
        payload = json.loads(snapshot.get("text") or "{}")
        assert payload["ok"] is True
        assert payload["ret"]["summary"] == "Canvas has two notes and one attachment."
    finally:
        await browser.stop_external_event_listener()


@pytest.mark.asyncio
async def test_default_event_block_preserves_standard_tool_result_surfaces(tmp_path):
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant",
        project="project",
        conversation_id="conv_1",
    )
    logical_path = "ev:turn_current.events/task-tracker/snapshots/draft-123/canvas/latest"
    await source.publish(
        kind="external_event",
        event_id="evt_snapshot_composite",
        explicit=True,
        target_turn_id="turn_current",
        active_turn_id_at_ingress="turn_current",
        owner_turn_id="turn_current",
        source="ingress.sse",
        event_source_id="task_tracker.canvas.snapshot",
        payload={
            "event": {
                "event_id": "evt_snapshot_composite",
                "type": "event.snapshot",
                "event_source_id": "task_tracker.canvas.snapshot",
                "logical_path": logical_path,
                "hosted_uri": "cnv:snapshots/draft-123/canvas/latest",
                "reactive": False,
                "story_id": "draft:123",
                "payload": {
                    "mime": "application/json",
                    "event": {
                        "title": "Canvas snapshot",
                        "summary": "Canvas has a selected note and one attachment.",
                        "exploration_results": [
                            {
                                "url": "https://example.test/task-context",
                                "title": "Task context",
                                "content": "context row",
                            }
                        ],
                        "hosted_artifacts": [
                            {
                                "artifact_id": "diagram",
                                "filename": "diagram.png",
                                "mime": "image/png",
                                "hosted_uri": "nmsp:files/draft-123/diagram.png",
                            }
                        ],
                        "artifact_type": "files",
                        "files": [
                            {
                                "filename": "brief.md",
                                "mime": "text/markdown",
                                "hosted_uri": "nmsp:files/draft-123/brief.md",
                                "description": "Canvas brief",
                                "visibility": "external",
                            }
                        ],
                        "snapshot_ref": "cnv:snapshots/draft-123/canvas/latest",
                        "announce_entry": {
                            "title": "Canvas snapshot",
                            "text": "Canvas has a selected note and one attachment.",
                        },
                    },
                },
            }
        },
    )

    runtime = RuntimeCtx(
        tenant="tenant",
        project="project",
        user_id="user_1",
        user_type="privileged",
        conversation_id="conv_1",
        turn_id="turn_current",
        bundle_id="bundle@1",
        started_at="2026-04-11T10:00:00Z",
        outdir=str(tmp_path / "out"),
        workdir=str(tmp_path / "work"),
        external_event_source=source,
    )
    browser = ContextBrowser(
        ctx_client=_FakeCtxClient(),
        runtime_ctx=runtime,
    )
    await browser.load_timeline()
    try:
        current_blocks = browser.timeline.get_turn_blocks()
        snapshot = next((b for b in current_blocks if b.get("type") == "event.snapshot"), None)
        assert snapshot is not None
        payload = json.loads(snapshot.get("text") or "{}")
        assert payload["ret"]["summary"] == "Canvas has a selected note and one attachment."

        surfaces = payload.get("surfaces") or {}
        assert surfaces["source_rows_merge"] is True
        assert surfaces["source_rows"][0]["url"] == "https://example.test/task-context"
        assert surfaces["artifact_rows"][0]["hosted_uri"] == "nmsp:files/draft-123/diagram.png"
        assert surfaces["declared_file_items_produced"] is True
        assert surfaces["declared_file_items"][0]["output"]["filename"] == "brief.md"
        assert surfaces["snapshot_refs"] == ["cnv:snapshots/draft-123/canvas/latest"]
        assert surfaces["announce_candidates"][0]["title"] == "Canvas snapshot"
    finally:
        await browser.stop_external_event_listener()


@pytest.mark.asyncio
async def test_default_event_canvas_stores_mutable_canvas_json_occurrence(tmp_path):
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant",
        project="project",
        conversation_id="conv_1",
    )
    logical_path = "ev:turn_current.events/task-tracker/canvas/draft-123/state/rev-7"
    await source.publish(
        kind="external_event",
        event_id="evt_canvas_rev_7",
        explicit=True,
        target_turn_id="turn_current",
        active_turn_id_at_ingress="turn_current",
        owner_turn_id="turn_current",
        source="ingress.sse",
        event_source_id="task_tracker.canvas.state",
        payload={
            "event": {
                "event_id": "evt_canvas_rev_7",
                "type": "event.canvas",
                "event_source_id": "task_tracker.canvas.state",
                "logical_path": logical_path,
                "reactive": False,
                "story_id": "draft:123",
                "payload": {
                    "mime": "application/json",
                    "event": {
                        "canvas_id": "draft-123",
                        "revision": 7,
                        "items": [
                            {
                                "id": "note-1",
                                "kind": "note",
                                "text": "Browser crashes after uploading a large CSV.",
                                "x": 120,
                                "y": 96,
                            }
                        ],
                    },
                },
            }
        },
    )

    runtime = RuntimeCtx(
        tenant="tenant",
        project="project",
        user_id="user_1",
        user_type="privileged",
        conversation_id="conv_1",
        turn_id="turn_current",
        bundle_id="bundle@1",
        started_at="2026-04-11T10:00:00Z",
        outdir=str(tmp_path / "out"),
        workdir=str(tmp_path / "work"),
        external_event_source=source,
    )
    browser = ContextBrowser(
        ctx_client=_FakeCtxClient(),
        runtime_ctx=runtime,
    )
    await browser.load_timeline()
    try:
        current_blocks = browser.timeline.get_turn_blocks()
        canvas = next((b for b in current_blocks if b.get("type") == "event.canvas"), None)
        assert canvas is not None
        assert canvas.get("path") == logical_path
        meta = canvas.get("meta") if isinstance(canvas.get("meta"), dict) else {}
        assert meta.get("event_source_id") == "task_tracker.canvas.state"
        assert meta.get("event_type") == "event.canvas"
        payload = json.loads(canvas.get("text") or "{}")
        assert payload["ret"]["canvas_id"] == "draft-123"
        assert payload["ret"]["revision"] == 7
        assert payload["ret"]["items"][0]["text"] == "Browser crashes after uploading a large CSV."
    finally:
        await browser.stop_external_event_listener()
