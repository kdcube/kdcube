# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.apps.chat.sdk.context.memory import tools as memory_tools
from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem
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
async def test_browser_renders_followup_batch_as_one_section(tmp_path):
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant",
        project="project",
        conversation_id="conv_1",
    )
    batch_id = "batch_followup_context"
    await source.publish(
        kind="external_event",
        batch_id=batch_id,
        explicit=True,
        target_turn_id="turn_current",
        active_turn_id_at_ingress="turn_current",
        owner_turn_id="turn_current",
        source="ingress.sse",
        event_source_id="memory.context",
        payload={
            "event": {
                "event_id": "evt_memory",
                "type": "event.external",
                "event_source_id": "memory.context",
                "logical_path": "ev:turn_current.events/mem_1",
                "hosted_uri": "mem:mem_1",
                "reactive": False,
                "payload": {
                    "mime": "application/json",
                    "event_ref": "mem:mem_1",
                    "event": {
                        "context_role": "context",
                        "kind": "memory",
                        "label": "Excel with openpyxl charts",
                        "summary": "Never use openpyxl native chart objects.",
                        "ref": "mem:mem_1",
                    },
                },
            }
        },
    )
    await source.publish(
        kind="followup",
        batch_id=batch_id,
        explicit=True,
        target_turn_id="turn_current",
        active_turn_id_at_ingress="turn_current",
        owner_turn_id="turn_current",
        source="ingress.sse",
        text="and this?",
        payload={"message": "and this?"},
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
        event_sources=EventSourceSubsystem(modules=[{"mod": memory_tools, "alias": "memory"}]),
        event_source_pipeline_enabled=True,
    )
    browser = ContextBrowser(
        ctx_client=_FakeCtxClient(),
        runtime_ctx=runtime,
    )

    await browser.load_timeline()
    try:
        current_blocks = browser.timeline.get_turn_blocks()
        memory_block = next(
            block for block in current_blocks
            if (block.get("meta") or {}).get("event_source_id") == "memory.context"
        )
        assert (memory_block.get("meta") or {}).get("batch_id") == batch_id

        rendered = await browser.timeline.render(cache_last=False, include_sources=False, include_announce=False)
        text = "\n".join(str(block.get("text") or "") for block in rendered if isinstance(block, dict))

        followup_section_idx = text.index("[FOLLOWUP DURING TURN]")
        memory_idx = text.index("[MEMORY CONTEXT]")
        attachment_idx = text.index("[USER ATTACHMENT] brief.txt")
        followup_message_idx = text.index("[FOLLOWUP MESSAGE]")
        assert followup_section_idx < memory_idx < attachment_idx < followup_message_idx
        assert text.count("[FOLLOWUP DURING TURN]") == 1
        assert "object_ref: mem:mem_1" in text
        assert "and this?" in text
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
