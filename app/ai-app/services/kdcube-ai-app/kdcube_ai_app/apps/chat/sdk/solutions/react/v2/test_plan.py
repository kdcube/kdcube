# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_announce_text
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.plan import (
    apply_plan_updates,
    build_plan_block,
    create_plan_snapshot,
    latest_active_plan_snapshot,
    plan_snapshot_ref,
    latest_plan_snapshot,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline import resolve_artifact_from_timeline
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.plan import (
    handle_react_plan,
    handle_react_plan_ack,
)


class _CtxBrowserStub:
    def __init__(self, *, turn_id: str, started_at: str, blocks: list[dict] | None = None) -> None:
        self.runtime_ctx = SimpleNamespace(turn_id=turn_id, started_at=started_at)
        self.timeline = SimpleNamespace(blocks=list(blocks or []))
        self.contributed: list[dict] = []
        self.notices: list[dict] = []

    def contribute(self, *, blocks: list[dict]) -> None:
        self.contributed.extend(blocks)
        self.timeline.blocks.extend(blocks)

    def contribute_notice(self, *, code: str, message: str, extra=None, call_id=None, meta=None) -> None:
        self.notices.append(
            {
                "code": code,
                "message": message,
                "extra": extra or {},
                "call_id": call_id,
                "meta": meta or {},
            }
        )


def test_apply_plan_updates_keeps_existing_plan_id_and_status() -> None:
    snap = create_plan_snapshot(
        plan={"steps": ["gather sources", "draft report"]},
        turn_id="turn_1",
        created_ts="2026-03-28T10:00:00Z",
    )
    snap.update_status({"1": "done"}, ts="2026-03-28T10:01:00Z", turn_id="turn_1")
    blocks = [build_plan_block(snap=snap, turn_id="turn_1", ts="2026-03-28T10:01:00Z")]

    status_map, out_blocks = apply_plan_updates(
        notes="✓ [2] draft report",
        plan_steps=list(snap.steps),
        status_map={},
        timeline_blocks=blocks,
        turn_id="turn_2",
        iteration=2,
        ts="2026-03-28T10:02:00Z",
    )

    assert status_map == {"2": "done"}
    plan_blocks = [b for b in out_blocks if b.get("type") == "react.plan"]
    assert len(plan_blocks) == 1
    payload = json.loads(plan_blocks[0]["text"])
    assert payload["plan_id"] == snap.plan_id
    assert payload["status"] == {"1": "done", "2": "done"}
    assert payload["last_ack_turn_id"] == "turn_2"


@pytest.mark.asyncio
async def test_react_plan_close_persists_closed_snapshot() -> None:
    snap = create_plan_snapshot(
        plan={"steps": ["gather sources", "draft report"]},
        turn_id="turn_1",
        created_ts="2026-03-28T10:00:00Z",
    )
    blocks = [build_plan_block(snap=snap, turn_id="turn_1", ts="2026-03-28T10:00:00Z")]
    ctx_browser = _CtxBrowserStub(
        turn_id="turn_2",
        started_at="2026-03-28T10:05:00Z",
        blocks=blocks,
    )
    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "mode": "close",
                    "plan_id": snap.plan_id,
                }
            }
        },
        "plan_steps": list(snap.steps),
        "plan_status": {"1": "done"},
    }

    out = await handle_react_plan(
        react=None,
        ctx_browser=ctx_browser,
        state=state,
        tool_call_id="plan_close_1",
    )

    assert out["plan_steps"] == []
    assert out["plan_status"] == {}

    closed_blocks = [b for b in ctx_browser.contributed if b.get("type") == "react.plan"]
    assert len(closed_blocks) == 1
    closed_payload = json.loads(closed_blocks[0]["text"])
    assert closed_payload["plan_id"] == snap.plan_id
    assert closed_payload["closed_ts"] == "2026-03-28T10:05:00Z"
    assert closed_payload["closed_turn_id"] == "turn_2"
    result_blocks = [b for b in ctx_browser.contributed if b.get("type") == "react.tool.result"]
    assert result_blocks
    assert any(f"latest_snapshot_ref: {plan_snapshot_ref(snap.plan_id)}" in (b.get("text") or "") for b in result_blocks)

    announce_text = build_announce_text(
        iteration=1,
        max_iterations=6,
        started_at="2026-03-28T10:05:00Z",
        timezone="UTC",
        timeline_blocks=ctx_browser.timeline.blocks,
        constraints=None,
        feedback_updates=None,
        feedback_incorporated=False,
        mode="full",
    )
    assert "  - plans: none" in announce_text


@pytest.mark.asyncio
async def test_react_plan_ack_updates_current_plan_without_new_plan_id() -> None:
    snap = create_plan_snapshot(
        plan={"steps": ["gather sources", "draft report"]},
        turn_id="turn_1",
        created_ts="2026-03-28T10:00:00Z",
    )
    blocks = [build_plan_block(snap=snap, turn_id="turn_1", ts="2026-03-28T10:00:00Z")]
    ctx_browser = _CtxBrowserStub(
        turn_id="turn_2",
        started_at="2026-03-28T10:05:00Z",
        blocks=blocks,
    )
    state = {
        "iteration": 3,
        "last_decision": {
            "tool_call": {
                "params": {
                    "updates": [
                        {"step": 1, "status": "done"},
                        {"step": 2, "status": "in_progress"},
                    ]
                }
            }
        },
        "plan_steps": list(snap.steps),
        "plan_status": {},
    }

    out = await handle_react_plan_ack(
        react=None,
        ctx_browser=ctx_browser,
        state=state,
        tool_call_id="plan_ack_1",
    )

    assert out["plan_status"] == {"1": "done", "2": "in_progress"}
    latest = latest_plan_snapshot(ctx_browser.timeline.blocks)
    assert latest is not None
    assert latest.plan_id == snap.plan_id
    assert latest.status == {"1": "done", "2": "in_progress"}


@pytest.mark.asyncio
async def test_react_plan_activate_makes_older_open_plan_current() -> None:
    old_snap = create_plan_snapshot(
        plan={"steps": ["collect metrics", "compare trends"]},
        turn_id="turn_1",
        created_ts="2026-03-28T10:00:00Z",
    )
    newer_snap = create_plan_snapshot(
        plan={"steps": ["draft answer", "verify citations"]},
        turn_id="turn_2",
        created_ts="2026-03-28T10:05:00Z",
    )
    blocks = [
        build_plan_block(snap=old_snap, turn_id="turn_1", ts="2026-03-28T10:00:00Z"),
        build_plan_block(snap=newer_snap, turn_id="turn_2", ts="2026-03-28T10:05:00Z"),
    ]
    ctx_browser = _CtxBrowserStub(
        turn_id="turn_3",
        started_at="2026-03-28T10:10:00Z",
        blocks=blocks,
    )
    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "mode": "activate",
                    "plan_id": old_snap.plan_id,
                }
            }
        },
        "plan_steps": list(newer_snap.steps),
        "plan_status": {},
    }

    out = await handle_react_plan(
        react=None,
        ctx_browser=ctx_browser,
        state=state,
        tool_call_id="plan_activate_1",
    )

    assert out["plan_id"] == old_snap.plan_id
    assert out["plan_steps"] == old_snap.steps
    assert latest_active_plan_snapshot(ctx_browser.timeline.blocks).plan_id == old_snap.plan_id

    status_map, out_blocks = apply_plan_updates(
        notes="✓ [1] collect metrics",
        plan_steps=list(old_snap.steps),
        status_map={},
        timeline_blocks=ctx_browser.timeline.blocks,
        turn_id="turn_4",
        iteration=4,
        ts="2026-03-28T10:11:00Z",
    )
    assert status_map == {"1": "done"}
    payloads = [json.loads(b["text"]) for b in out_blocks if b.get("type") == "react.plan"]
    assert len(payloads) == 1
    assert payloads[0]["plan_id"] == old_snap.plan_id
    assert payloads[0]["status"] == {"1": "done"}


@pytest.mark.asyncio
async def test_react_plan_replace_supersedes_target_and_announce_shows_only_new_open_plan() -> None:
    old_snap = create_plan_snapshot(
        plan={"steps": ["collect metrics", "compare trends"]},
        turn_id="turn_1",
        created_ts="2026-03-28T10:00:00Z",
    )
    blocks = [build_plan_block(snap=old_snap, turn_id="turn_1", ts="2026-03-28T10:00:00Z")]
    ctx_browser = _CtxBrowserStub(
        turn_id="turn_2",
        started_at="2026-03-28T10:05:00Z",
        blocks=blocks,
    )
    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "mode": "replace",
                    "plan_id": old_snap.plan_id,
                    "steps": ["draft answer", "verify citations"],
                }
            }
        },
        "plan_steps": list(old_snap.steps),
        "plan_status": {},
    }

    out = await handle_react_plan(
        react=None,
        ctx_browser=ctx_browser,
        state=state,
        tool_call_id="plan_update_1",
    )

    assert out["plan_id"] != old_snap.plan_id

    old_payloads = [
        json.loads(b["text"])
        for b in ctx_browser.contributed
        if b.get("type") == "react.plan" and old_snap.plan_id in (b.get("text") or "")
    ]
    assert old_payloads and old_payloads[0]["superseded_by_plan_id"] == out["plan_id"]
    result_blocks = [b for b in ctx_browser.contributed if b.get("type") == "react.tool.result"]
    assert any(f"target_snapshot_ref: {plan_snapshot_ref(old_snap.plan_id)}" in (b.get("text") or "") for b in result_blocks)
    assert any(f"latest_snapshot_ref: {plan_snapshot_ref(out['plan_id'])}" in (b.get("text") or "") for b in result_blocks)

    announce_text = build_announce_text(
        iteration=1,
        max_iterations=6,
        started_at="2026-03-28T10:05:00Z",
        timezone="UTC",
        timeline_blocks=ctx_browser.timeline.blocks,
        constraints=None,
        feedback_updates=None,
        feedback_incorporated=False,
        mode="full",
    )
    assert f"plan_id={out['plan_id']}" in announce_text
    assert f"snapshot_ref={plan_snapshot_ref(out['plan_id'])}" in announce_text
    assert f"plan_id={old_snap.plan_id}" not in announce_text


def test_plan_latest_alias_resolves_live_snapshot() -> None:
    snap = create_plan_snapshot(
        plan={"steps": ["collect metrics", "compare trends"]},
        turn_id="turn_1",
        created_ts="2026-03-28T10:00:00Z",
    )
    block = build_plan_block(snap=snap, turn_id="turn_2", ts="2026-03-28T10:05:00Z")

    art = resolve_artifact_from_timeline(
        {"blocks": [block], "sources_pool": []},
        plan_snapshot_ref(snap.plan_id),
    )

    assert art is not None
    assert art["path"] == plan_snapshot_ref(snap.plan_id)
    assert art["source_path"] == block["path"]
    assert snap.plan_id in (art.get("text") or "")
