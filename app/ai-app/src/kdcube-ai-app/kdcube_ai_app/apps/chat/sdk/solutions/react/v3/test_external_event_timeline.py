# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.timeline import extract_user_attachments_from_blocks


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
        assert any(b.get("type") == "user.steer" and b.get("turn_id") == "turn_current" for b in current_blocks)
        assert browser.timeline.last_external_event_id == "2-0"
        assert int(browser.timeline.last_external_event_seq or 0) == 2
    finally:
        await browser.stop_external_event_listener()


@pytest.mark.asyncio
async def test_browser_marks_applied_external_events_consumed(tmp_path):
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant",
        project="project",
        conversation_id="conv_1",
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
    event = await source.publish(
        kind="followup",
        explicit=True,
        target_turn_id="turn_current",
        active_turn_id_at_ingress="turn_current",
        owner_turn_id="turn_current",
        source="ingress.sse",
        text="current followup",
        payload={"message": "current followup"},
    )
    try:
        changed = await browser.apply_external_events([event], call_hooks=False)

        assert changed > 0
        stored = await source.get_event(event.message_id)
        assert stored is not None
        assert stored.consumed_by_turn_id == "turn_current"
    finally:
        await browser.stop_external_event_listener()
