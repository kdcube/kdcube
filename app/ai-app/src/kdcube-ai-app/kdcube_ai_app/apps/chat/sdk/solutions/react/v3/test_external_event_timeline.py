# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.apps.chat.sdk.context.memory import tools as memory_tools
from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem
from kdcube_ai_app.apps.chat.sdk.events.event_bus.orchestrator import ConversationEventBusOrchestrator
from kdcube_ai_app.apps.chat.sdk.runtime.user_inputs import iter_turn_user_input_entries
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx


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
async def test_browser_folds_accepted_external_events_into_current_turn(tmp_path):
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
        current_blocks = browser.timeline.get_turn_blocks()

        assert any(
            b.get("type") == "user.followup"
            and b.get("turn_id") == "turn_current"
            and b.get("text") == "history event"
            and b.get("meta", {}).get("origin_turn_id") == "turn_old"
            for b in current_blocks
        )
        assert any(
            b.get("type") == "user.attachment.text"
            and b.get("turn_id") == "turn_current"
            and b.get("text") == "attachment text from followup"
            for b in current_blocks
        )
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
        user_entries = iter_turn_user_input_entries(current_blocks, turn_id="turn_current")
        assert any(entry.get("plain_text") == "history event" for entry in user_entries)
        assert any(entry.get("plain_text") == "stream-originated prompt" for entry in user_entries)
        assert hook_saw_current_prompt == [True]
        assert browser.timeline.last_external_event_id == "3-0"
        assert int(browser.timeline.last_external_event_seq or 0) == 3
    finally:
        await browser.stop_external_event_listener()


@pytest.mark.asyncio
async def test_browser_initial_fold_skips_events_already_processed_by_lane_state(tmp_path):
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant",
        project="project",
        conversation_id="conv_1",
    )
    old_event = await source.publish(
        kind="followup",
        explicit=True,
        target_turn_id="turn_old",
        active_turn_id_at_ingress="turn_old",
        owner_turn_id="turn_old",
        source="ingress.sse",
        text="already handled followup",
        payload={"message": "already handled followup"},
    )
    await source.publish(
        kind="followup",
        explicit=True,
        target_turn_id="turn_current",
        active_turn_id_at_ingress="turn_current",
        owner_turn_id="turn_current",
        source="ingress.sse",
        text="new followup",
        payload={"message": "new followup"},
    )

    orchestrator = ConversationEventBusOrchestrator.for_source(source)
    await orchestrator.open_handler(turn_id="turn_current")
    await orchestrator.record_processed_events([old_event], turn_id="turn_current")

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
        assert not any(
            b.get("type") == "user.followup"
            and b.get("text") == "already handled followup"
            for b in current_blocks
        )
        assert any(
            b.get("type") == "user.followup"
            and b.get("turn_id") == "turn_current"
            and b.get("text") == "new followup"
            for b in current_blocks
        )
        user_entries = iter_turn_user_input_entries(current_blocks, turn_id="turn_current")
        assert [entry.get("plain_text") for entry in user_entries] == ["new followup"]
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
        assert "object_ref: mem:record:mem_1" in text
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


def _grant_runtime_and_browser(tmp_path, source):
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
    return runtime, browser


_GRANT_TEXT = (
    "The user approved Slack access (slack:history). The tools that needed it "
    "(read_slack_channel_history) are usable now — retry what was blocked if it "
    "is still wanted."
)


def _grant_payload() -> dict:
    facts = {
        "provider_id": "slack",
        "connector_app_id": "demo",
        "claims": ["slack:history"],
        "account_id": "acct-1",
        "tools": ["slack.read_slack_channel_history"],
    }
    return {
        "text": _GRANT_TEXT,
        "event": {
            "type": "connections.consent.granted",
            "event_source_id": "connection_hub.consent",
            "reactive": False,
            "timestamp": "2026-07-08T11:07:54.514617Z",
            "payload": {
                "mime": "text/markdown",
                "event": {"text": _GRANT_TEXT, **facts},
            },
        },
        **facts,
    }


@pytest.mark.asyncio
async def test_consent_granted_event_folds_into_live_turn_and_close_gate_settles(tmp_path):
    """A passive (task_payload=None) consent grant authored into a LIVE turn:
    the lane fold renders it as a visible timeline block, applies it exactly
    once, marks it consumed, and the very next `complete` passes the event-bus
    close gate — the turn terminates instead of demoting the answer to a
    provisional attempt every round."""
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant",
        project="project",
        conversation_id="conv_1",
    )
    _, browser = _grant_runtime_and_browser(tmp_path, source)
    await browser.load_timeline()
    try:
        orchestrator = browser._ensure_event_bus_orchestrator()
        await orchestrator.open_handler(turn_id="turn_current")

        event = await source.publish(
            kind="external_event",
            source="connection_hub",
            event_source_id="connection_hub.consent",
            text=_GRANT_TEXT,
            payload=_grant_payload(),
            # Passive by construction: the promoter acks it; only the live
            # turn's lane watcher (or the next turn's fold) consumes it.
            task_payload=None,
        )

        changed = await browser._fold_external_events(call_hooks=False)
        assert changed >= 1

        blocks = browser.timeline.get_turn_blocks()
        assert any("approved Slack access" in str(b.get("text") or "") for b in blocks), (
            "the grant must be a visible timeline block for the model"
        )

        # Applied once: a second fold pass adds nothing.
        assert await browser._fold_external_events(call_hooks=False) == 0

        # Consumption acked on application.
        stored = await source.get_event(event.message_id)
        assert stored is not None
        assert stored.consumed_by_turn_id == "turn_current"

        # After the round that rendered the event, finalize settles: the close
        # gate accepts instead of deferring with new_events_after_handler_snapshot.
        await browser.timeline.render()
        closed = await browser.try_close_external_event_handler()
        assert closed is True
    finally:
        await browser.stop_external_event_listener()


@pytest.mark.asyncio
async def test_no_block_lane_event_never_wedges_the_close_gate(tmp_path):
    """Regression for the completion livelock (turn_2026-07-08-11-07-27-129):
    an event the fold consumes WITHOUT producing blocks (here the legacy grant
    shape whose transport kind is the semantic type) must still advance the
    handler-processed cursor and be marked consumed. Before the fix the lane
    state cursor moved past the rendered cursor forever and EVERY subsequent
    `complete` was deferred (`new_events_after_handler_snapshot`) until
    iteration exhaustion."""
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant",
        project="project",
        conversation_id="conv_1",
    )
    _, browser = _grant_runtime_and_browser(tmp_path, source)
    await browser.load_timeline()
    try:
        orchestrator = browser._ensure_event_bus_orchestrator()
        await orchestrator.open_handler(turn_id="turn_current")

        event = await source.publish(
            kind="connections.consent.granted",
            source="connection_hub",
            event_source_id="connection_hub.consent",
            text=_GRANT_TEXT,
            payload={
                "provider_id": "slack",
                "claims": ["slack:history"],
                "tools": ["slack.read_slack_channel_history"],
            },
            task_payload=None,
        )

        changed = await browser._fold_external_events(call_hooks=False)
        assert changed == 0  # unknown transport kind renders nothing

        # Handler-processed cursor advanced at application time...
        cursor = browser.timeline.last_rendered_event_cursor
        assert cursor.event_id == event.message_id

        # ...consumption acked on application...
        stored = await source.get_event(event.message_id)
        assert stored is not None
        assert stored.consumed_by_turn_id == "turn_current"

        # ...so the close gate settles: `complete` terminates the turn.
        closed = await browser.try_close_external_event_handler()
        assert closed is True
    finally:
        await browser.stop_external_event_listener()
