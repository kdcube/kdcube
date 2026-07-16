# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The conv.artifacts.stream write path (``record.py``): canvas/tool/subsystem
delta aggregates persist so a RELOADED conversation replays them — the
code-exec panel being the flagship case. The surfaced bug: a framework-neutral
(run-to-completion) turn streamed the exec widget live, persisted nothing, and
the panel vanished on reload."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.record import (
    build_stream_artifact_payload,
    persist_stream_artifacts,
    reset_turn_log_recorded,
    mark_turn_log_recorded,
)


def _exec_aggregate(**over):
    """A delta aggregate the communicator produces for the exec widget stream
    (marker=subsystem, extra carries sub_type/execution_id — what the client
    keys the panel on when the reload replays the row)."""
    base = {
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "agent": "assistant",
        "marker": "subsystem",
        "format": "json",
        "artifact_name": "code_exec",
        "title": "Execution",
        "extra": {"sub_type": "code_exec.status", "execution_id": "exec-1"},
        "ts_first": 1000,
        "ts_last": 1002,
        "text": '{"status":"done"}',
        "chunks": [{"ts": 1000, "idx": 0, "text": '{"status":"done"}'}],
    }
    base.update(over)
    return base


# ── the payload builder (shared by React and the fallback) ──────────────────

def test_payload_keeps_subsystem_rows_with_extra_and_drops_chunks():
    payload = build_stream_artifact_payload([
        _exec_aggregate(),
        _exec_aggregate(marker="answer"),          # the answer stream is NOT persisted here
        _exec_aggregate(marker="canvas", text="", chunks=[]),  # empty -> dropped
    ])

    items = payload["content"]["items"]
    assert len(items) == 1
    row = items[0]
    assert row["marker"] == "subsystem"
    assert row["extra"] == {"sub_type": "code_exec.status", "execution_id": "exec-1"}
    assert row["text"] == '{"status":"done"}'
    assert "chunks" not in row and row["chunks_num"] == 1
    # the index keeps metadata but not the text body
    import json
    idx = json.loads(payload["content_str"])
    assert idx[0]["text_size"] == len('{"status":"done"}')
    assert "text" not in idx[0]


def test_payload_is_none_when_the_turn_streamed_nothing_persistable():
    assert build_stream_artifact_payload([]) is None
    assert build_stream_artifact_payload([_exec_aggregate(marker="answer")]) is None


# ── the reusable persist (any framework door) ────────────────────────────────

class _FakeComm:
    def __init__(self, aggregates):
        self._aggregates = aggregates
        self.cleared = None

    def get_delta_aggregates(self, *, conversation_id, turn_id, merge_text=True):
        return list(self._aggregates)

    def clear_delta_aggregates(self, *, conversation_id, turn_id):
        self.cleared = (conversation_id, turn_id)


class _FakeCtxClient:
    def __init__(self):
        self.saved = []

    async def save_artifact(self, **kwargs):
        self.saved.append(kwargs)


def _identity():
    return dict(
        tenant="t", project="p", user_id="u", user_type="registered",
        conversation_id="conv-1", turn_id="turn-1", bundle_id="b@1", agent_id="lg-react",
    )


def test_persist_saves_the_artifact_and_clears_the_aggregates():
    comm = _FakeComm([_exec_aggregate()])
    ctx = _FakeCtxClient()

    wrote = asyncio.run(persist_stream_artifacts(comm=comm, ctx_client=ctx, **_identity()))

    assert wrote is True
    assert len(ctx.saved) == 1
    saved = ctx.saved[0]
    assert saved["kind"] == "conv.artifacts.stream"
    assert saved["extra_tags"] == ["conversation", "stream", "canvas"]
    assert saved["content"]["items"][0]["extra"]["execution_id"] == "exec-1"
    assert comm.cleared == ("conv-1", "turn-1")


def test_persist_writes_nothing_for_a_quiet_turn():
    comm = _FakeComm([_exec_aggregate(marker="answer")])
    ctx = _FakeCtxClient()

    wrote = asyncio.run(persist_stream_artifacts(comm=comm, ctx_client=ctx, **_identity()))

    assert wrote is False
    assert ctx.saved == []
    assert comm.cleared is None  # aggregates untouched when nothing was written


# ── the framework-neutral fallback gate (the chatbot door method) ────────────

def _entrypoint_stub(comm, ctx):
    async def get_ctx_client():
        return ctx

    return SimpleNamespace(
        comm=comm,
        get_ctx_client=get_ctx_client,
        config=SimpleNamespace(tenant="t", project="p", ai_bundle_spec=SimpleNamespace(id="b@1")),
        settings=SimpleNamespace(TENANT="t", PROJECT="p"),
        runtime_ctx=SimpleNamespace(agent_id="lg-react"),
        _turn_id="turn-1",
        logger=SimpleNamespace(log=lambda *a, **k: None),
    )


def _run_fallback(stub, state):
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint

    return asyncio.run(
        BaseEntrypoint._persist_stream_artifacts_fallback(stub, state=state)
    )


def test_fallback_persists_for_a_framework_neutral_turn():
    reset_turn_log_recorded()
    comm = _FakeComm([_exec_aggregate()])
    ctx = _FakeCtxClient()
    state = {"conversation_id": "conv-1", "turn_id": "turn-1", "user": "u", "agent_id": "lg-react"}

    _run_fallback(_entrypoint_stub(comm, ctx), state)

    assert len(ctx.saved) == 1
    assert ctx.saved[0]["kind"] == "conv.artifacts.stream"


def test_fallback_is_inert_when_a_rich_turn_log_was_recorded():
    """The React path: its workflow already persisted (and cleared) the
    aggregates itself; the recorded signal makes the fallback a no-op."""
    reset_turn_log_recorded()
    mark_turn_log_recorded()
    comm = _FakeComm([_exec_aggregate()])
    ctx = _FakeCtxClient()
    state = {"conversation_id": "conv-1", "turn_id": "turn-1", "user": "u"}

    _run_fallback(_entrypoint_stub(comm, ctx), state)

    assert ctx.saved == []
    assert comm.cleared is None
