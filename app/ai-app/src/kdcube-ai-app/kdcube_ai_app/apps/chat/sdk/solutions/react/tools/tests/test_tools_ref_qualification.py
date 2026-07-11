# SPDX-License-Identifier: MIT

"""Conversation-qualified refs at birth + dual-dialect resolution.

Covers the ref-qualification contract for the react tool handlers:
- emitted conv:fi: refs carry the `conv_<conversation_id>.` scope segment;
- a ref qualified with the CURRENT conversation maps to the local
  `turn_<id>/...` physical layout;
- a ref qualified with ANOTHER conversation keeps its `conv_<id>/...`
  physical layout;
- legacy unqualified refs keep resolving (tolerance).
"""

from __future__ import annotations

import json

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import Timeline
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.pull import handle_react_pull
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.read import handle_react_read
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.write import handle_react_write


class FakeBrowser:
    def __init__(self, runtime_ctx: RuntimeCtx):
        self.runtime_ctx = runtime_ctx
        self.timeline = Timeline(runtime=runtime_ctx, svc=None)
        self._turn_logs = {}

    def contribute(self, blocks, persist=True):
        self.timeline.blocks.extend(blocks or [])

    def contribute_notice(self, *, code, message, extra=None, call_id=None, meta=None):
        block = {
            "type": "react.notice",
            "call_id": call_id,
            "text": f"{code}:{message}",
            "meta": extra or {},
            "turn_id": self.runtime_ctx.turn_id or "",
        }
        if meta:
            block["meta"] = {**block.get("meta", {}), **meta}
        self.contribute([block])

    def timeline_visible_paths(self):
        return self.timeline.visible_paths()

    def bind_params_with_refs(self, base_params, tool_id=None, visible_paths=None):
        return self.timeline.bind_params_with_refs(
            base_params=base_params,
            tool_id=tool_id,
            visible_paths=visible_paths,
        )

    async def get_turn_log(self, turn_id: str, conversation_id: str | None = None):
        return self._turn_logs.get((conversation_id or "", turn_id), self._turn_logs.get(turn_id, {}))


class FakeReact:
    tool_manager = type("T", (), {"tools": {}})()
    log = None

    def __init__(self, hosting_service=None, comm=None):
        self.hosting_service = hosting_service
        self.comm = comm


def _latest_json_payload(ctx: FakeBrowser) -> dict:
    blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("mime") == "application/json"
    ]
    assert blocks
    return json.loads(blocks[-1]["text"])


def _read_summary_payload(ctx: FakeBrowser) -> dict:
    for block in reversed(ctx.timeline.blocks):
        if block.get("type") != "react.tool.result" or block.get("mime") != "application/json":
            continue
        try:
            payload = json.loads(block.get("text") or "")
        except Exception:
            continue
        if isinstance(payload, dict) and "paths" in payload:
            return payload
    raise AssertionError("react.read summary block not found")


def _stub_settings(monkeypatch, tmp_path):
    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    monkeypatch.setattr(cfg, "get_settings", lambda: _Settings())


def _turn_log_with_file(*, logical: str, physical: str, text: str) -> dict:
    return {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": json.dumps({"artifact_path": logical, "physical_path": physical}),
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "text/markdown",
                "path": logical,
                "text": text,
                "turn_id": "turn_prev",
                "meta": {"physical_path": physical},
            },
        ],
        "sources_pool": [],
    }


# ---------------------------------------------------------------------------
# react.write mints conversation-qualified refs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_result_artifact_path_is_conversation_qualified(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_cur",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        conversation_id="conv_A",
    )
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {"tool_call": {"params": {
            "path": "turn_cur/files/draft.md",
            "channel": "canvas",
            "content": "hello",
            "kind": "display",
        }}},
        "outdir": str(tmp_path),
    }

    await handle_react_write(react=FakeReact(), ctx_browser=ctx, state=state, tool_call_id="w1")

    qualified = "conv:fi:conv_conv_A.turn_cur.files/draft.md"
    assert any(b.get("path") == qualified for b in ctx.timeline.blocks)
    # The file itself lives in the LOCAL physical layout (no conv_ directory).
    assert (tmp_path / "workdir" / "turn_cur" / "files" / "draft.md").read_text() == "hello"
    assert not (tmp_path / "workdir" / "conv_conv_A").exists()


@pytest.mark.asyncio
async def test_write_without_conversation_id_keeps_unscoped_ref(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_cur", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {"tool_call": {"params": {
            "path": "turn_cur/files/draft.md",
            "channel": "canvas",
            "content": "hello",
            "kind": "display",
        }}},
        "outdir": str(tmp_path),
    }

    await handle_react_write(react=FakeReact(), ctx_browser=ctx, state=state, tool_call_id="w2")

    assert any(b.get("path") == "conv:fi:turn_cur.files/draft.md" for b in ctx.timeline.blocks)


# ---------------------------------------------------------------------------
# react.pull: dual-dialect resolution + qualified reporting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_qualified_current_ref_materializes_local_layout(monkeypatch, tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(
        turn_id="turn_pull",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_A",
    )
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = _turn_log_with_file(
        logical="conv:fi:turn_prev.files/report.md",
        physical="turn_prev/files/report.md",
        text="# Report\n",
    )
    _stub_settings(monkeypatch, tmp_path)

    state = {
        "last_decision": {"tool_call": {"params": {
            "paths": ["conv:fi:conv_conv_A.turn_prev.files/report.md"],
        }}},
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_current_scoped")

    payload = _latest_json_payload(ctx)
    assert payload["pulled"] == [{
        "logical_path": "conv:fi:conv_conv_A.turn_prev.files/report.md",
        "physical_path": "turn_prev/files/report.md",
        "file_count": 1,
    }]
    assert "invalid" not in payload
    assert "missing" not in payload
    # Local layout: no conv_ directory for the current conversation.
    assert (outdir / "workdir" / "turn_prev" / "files" / "report.md").read_text() == "# Report\n"
    assert not (outdir / "workdir" / "conv_conv_A").exists()


@pytest.mark.asyncio
async def test_pull_foreign_qualified_ref_keeps_conversation_scoped_layout(monkeypatch, tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(
        turn_id="turn_pull",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_A",
    )
    ctx = FakeBrowser(runtime)
    ctx._turn_logs[("conv_B", "turn_prev")] = _turn_log_with_file(
        logical="conv:fi:turn_prev.files/report.md",
        physical="turn_prev/files/report.md",
        text="# Foreign\n",
    )
    _stub_settings(monkeypatch, tmp_path)

    state = {
        "last_decision": {"tool_call": {"params": {
            "paths": ["conv:fi:conv_conv_B.turn_prev.files/report.md"],
        }}},
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_foreign_scoped")

    payload = _latest_json_payload(ctx)
    assert payload["pulled"] == [{
        "logical_path": "conv:fi:conv_conv_B.turn_prev.files/report.md",
        "physical_path": "conv_conv_B/turn_prev/files/report.md",
        "file_count": 1,
    }]
    assert "missing" not in payload
    assert (
        outdir / "workdir" / "conv_conv_B" / "turn_prev" / "files" / "report.md"
    ).read_text() == "# Foreign\n"


@pytest.mark.asyncio
async def test_pull_legacy_unqualified_ref_still_resolves(monkeypatch, tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(
        turn_id="turn_pull",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_A",
    )
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = _turn_log_with_file(
        logical="conv:fi:turn_prev.files/report.md",
        physical="turn_prev/files/report.md",
        text="# Legacy\n",
    )
    _stub_settings(monkeypatch, tmp_path)

    state = {
        "last_decision": {"tool_call": {"params": {
            "paths": ["conv:fi:turn_prev.files/report.md"],
        }}},
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_legacy")

    payload = _latest_json_payload(ctx)
    assert payload["pulled"][0]["physical_path"] == "turn_prev/files/report.md"
    # The report back to the model carries the conversation-qualified form.
    assert payload["pulled"][0]["logical_path"] == "conv:fi:conv_conv_A.turn_prev.files/report.md"
    assert "missing" not in payload
    assert (outdir / "workdir" / "turn_prev" / "files" / "report.md").read_text() == "# Legacy\n"


# ---------------------------------------------------------------------------
# react.read: dual-dialect resolution
# ---------------------------------------------------------------------------


async def _run_read(ctx: FakeBrowser, outdir, path: str, call_id: str) -> dict:
    state = {
        "last_decision": {"tool_call": {"params": {"paths": [path]}}},
        "outdir": str(outdir),
    }
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id=call_id)
    return state


@pytest.mark.asyncio
async def test_read_qualified_current_ref_reads_local_file(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(
        turn_id="turn_cur",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_A",
    )
    ctx = FakeBrowser(runtime)
    local_file = outdir / "workdir" / "turn_cur" / "files" / "note.md"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text("note body\n", encoding="utf-8")

    qualified = "conv:fi:conv_conv_A.turn_cur.files/note.md"
    await _run_read(ctx, outdir, qualified, "read_scoped")

    summary = _read_summary_payload(ctx)
    entries = {e["path"]: e for e in summary["paths"]}
    assert qualified in entries
    # Current-conversation refs resolve locally: no foreign conversation marker.
    assert "conversation_id" not in entries[qualified]
    assert "missing" not in summary
    assert any(
        b.get("path") == qualified and "note body" in (b.get("text") or "")
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_read_legacy_unqualified_ref_still_resolves(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(
        turn_id="turn_cur",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_A",
    )
    ctx = FakeBrowser(runtime)
    local_file = outdir / "workdir" / "turn_cur" / "files" / "note.md"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text("note body\n", encoding="utf-8")

    legacy = "conv:fi:turn_cur.files/note.md"
    await _run_read(ctx, outdir, legacy, "read_legacy")

    summary = _read_summary_payload(ctx)
    entries = {e["path"]: e for e in summary["paths"]}
    assert legacy in entries
    assert "conversation_id" not in entries[legacy]
    assert "missing" not in summary
    assert any(
        b.get("path") == legacy and "note body" in (b.get("text") or "")
        for b in ctx.timeline.blocks
    )
