import json
import time
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot import base_workflow as workflow_mod
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx


class _TimelineStub:
    def block(self, **kwargs):
        return dict(kwargs)


class _CtxBrowserStub:
    def __init__(self, runtime_ctx):
        self.runtime_ctx = runtime_ctx
        self.timeline = _TimelineStub()
        self.contributed = []

    def contribute(self, blocks):
        self.contributed.extend(list(blocks or []))


def _payload(*, tenant: str, project: str, user_id: str = "u1", turn_id: str = "turn-1"):
    return SimpleNamespace(
        actor=SimpleNamespace(tenant_id=tenant, project_id=project),
        user=SimpleNamespace(user_id=user_id, user_type="registered", timezone="UTC"),
        routing=SimpleNamespace(conversation_id="conv-1", turn_id=turn_id),
    )


def test_react_agent_version_selector_defaults_and_normalizes(monkeypatch):
    monkeypatch.delenv("AI_REACT_AGENT_VERSION", raising=False)
    workflow_mod.get_settings.cache_clear()
    try:
        assert workflow_mod._react_agent_version() == "v2"
        monkeypatch.setenv("AI_REACT_AGENT_VERSION", "V3")
        workflow_mod.get_settings.cache_clear()
        assert workflow_mod._react_agent_version() == "v3"
        monkeypatch.setenv("AI_REACT_AGENT_VERSION", "unknown")
        workflow_mod.get_settings.cache_clear()
        assert workflow_mod._react_agent_version() == "v2"
    finally:
        workflow_mod.get_settings.cache_clear()


def test_rebind_request_context_refreshes_runtime_ctx_bundle_storage(monkeypatch, tmp_path):
    resolved_storage = tmp_path / "bundle-storage" / "tenant-b" / "project-b" / "kdcube.copilot__main"

    def _fake_storage_for_spec(*, spec, tenant=None, project=None, ensure=True):
        assert getattr(spec, "id", None) == "kdcube.copilot@2026-04-03-19-05"
        assert tenant == "tenant-b"
        assert project == "project-b"
        if ensure:
            resolved_storage.mkdir(parents=True, exist_ok=True)
        return resolved_storage

    monkeypatch.setattr(
        "kdcube_ai_app.infra.plugin.bundle_storage.storage_for_spec",
        _fake_storage_for_spec,
    )
    monkeypatch.setattr(workflow_mod, "build_comm_from_comm_context", lambda *args, **kwargs: SimpleNamespace(delta=None))
    monkeypatch.setattr(workflow_mod, "build_relay_from_env", lambda: None)

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="kdcube.copilot@2026-04-03-19-05"))
    wf.bundle_props = {}
    wf.comm_context = _payload(tenant="tenant-a", project="project-a")
    wf._continuation_source = None
    wf.hosting_service = None
    wf.turn_status = None
    wf.runtime_ctx = RuntimeCtx(bundle_storage=None)

    wf.rebind_request_context(comm_context=_payload(tenant="tenant-b", project="project-b", turn_id="turn-2"))

    assert wf.runtime_ctx.tenant == "tenant-b"
    assert wf.runtime_ctx.project == "project-b"
    assert wf.runtime_ctx.turn_id == "turn-2"
    assert wf.runtime_ctx.bundle_storage == str(resolved_storage)


def test_rebind_request_context_refreshes_external_event_source_after_redis_bind(monkeypatch):
    monkeypatch.setattr(workflow_mod, "build_comm_from_comm_context", lambda *args, **kwargs: SimpleNamespace(delta=None))
    monkeypatch.setattr(workflow_mod, "build_relay_from_env", lambda: None)

    expected_source = object()

    def _fake_external_event_source(self):
        return expected_source if getattr(self, "redis", None) == "redis-client" else None

    monkeypatch.setattr(BaseWorkflow, "_external_event_source_for_runtime", _fake_external_event_source, raising=False)

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.bundle_props = {}
    wf.comm_context = _payload(tenant="tenant-a", project="project-a")
    wf._continuation_source = None
    wf.hosting_service = None
    wf.turn_status = None
    wf.runtime_ctx = RuntimeCtx(external_event_source=None)
    wf._sync_runtime_ctx_bundle_props = lambda: None

    wf.rebind_request_context(
        comm_context=_payload(tenant="tenant-b", project="project-b", turn_id="turn-2"),
        redis="redis-client",
    )

    assert wf.runtime_ctx.external_event_source is expected_source


def test_resolve_mcp_services_config_prefers_bundle_props_over_env(monkeypatch):
    monkeypatch.setenv("MCP_SERVICES", '{"mcpServers":{"env_only":{"transport":"stdio","command":"python"}}}')

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.bundle_props = {
        "mcp": {
            "services": {
                "mcpServers": {
                    "docs": {
                        "transport": "http",
                        "url": "https://mcp.example.com",
                        "auth": {"type": "bearer", "secret": "bundles.react.mcp@2026-03-09.secrets.docs.token"},
                    }
                }
            }
        }
    }

    resolved = wf._resolve_mcp_services_config()

    assert resolved == {
        "mcpServers": {
            "docs": {
                "transport": "http",
                "url": "https://mcp.example.com",
                "auth": {"type": "bearer", "secret": "bundles.react.mcp@2026-03-09.secrets.docs.token"},
            }
        }
    }


def test_runtime_ctx_carries_workspace_git_repo(monkeypatch, tmp_path):
    resolved_storage = tmp_path / "bundle-storage" / "tenant-a" / "project-a" / "kdcube.copilot__main"

    def _fake_storage_for_spec(*, spec, tenant=None, project=None, ensure=True):
        if ensure:
            resolved_storage.mkdir(parents=True, exist_ok=True)
        return resolved_storage

    monkeypatch.setenv("REACT_WORKSPACE_GIT_REPO", "git@github.com:org/agentic-workspace.git")
    monkeypatch.setattr(
        "kdcube_ai_app.infra.plugin.bundle_storage.storage_for_spec",
        _fake_storage_for_spec,
    )
    monkeypatch.setattr(workflow_mod, "build_comm_from_comm_context", lambda *args, **kwargs: SimpleNamespace(delta=lambda *a, **k: None))
    monkeypatch.setattr(workflow_mod, "build_relay_from_env", lambda: None)
    workflow_mod.get_settings.cache_clear()
    try:
        wf = BaseWorkflow(
            conv_idx=SimpleNamespace(),
            kb=SimpleNamespace(),
            store=SimpleNamespace(),
            comm=SimpleNamespace(delta=lambda *a, **k: None),
            model_service=SimpleNamespace(),
            conv_ticket_store=SimpleNamespace(),
            config=SimpleNamespace(
                ai_bundle_spec=SimpleNamespace(id="kdcube.copilot@2026-04-03-19-05"),
                max_tokens=512,
            ),
            comm_context=_payload(tenant="tenant-a", project="project-a", turn_id="turn-3"),
            ctx_client=SimpleNamespace(),
        )

        assert wf.runtime_ctx.workspace_git_repo == "git@github.com:org/agentic-workspace.git"
        assert wf.runtime_ctx.bundle_storage == str(resolved_storage)
    finally:
        workflow_mod.get_settings.cache_clear()


def test_base_workflow_constructor_binds_external_event_source_when_redis_present(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(
        workflow_mod,
        "build_conversation_external_event_source",
        lambda **kwargs: sentinel,
    )

    wf = BaseWorkflow(
        conv_idx=SimpleNamespace(),
        kb=SimpleNamespace(),
        store=SimpleNamespace(),
        comm=SimpleNamespace(delta=lambda *a, **k: None),
        model_service=SimpleNamespace(),
        conv_ticket_store=SimpleNamespace(),
        config=SimpleNamespace(
            ai_bundle_spec=SimpleNamespace(id="bundle.test"),
            max_tokens=256,
        ),
        comm_context=_payload(tenant="tenant-a", project="project-a", turn_id="turn-ctor"),
        ctx_client=SimpleNamespace(),
        redis="redis-client",
    )

    assert wf.redis == "redis-client"
    assert wf.runtime_ctx.external_event_source is sentinel


@pytest.mark.asyncio
async def test_publish_git_workspace_if_needed_calls_publisher_in_git_mode(monkeypatch, tmp_path):
    calls = {}

    def _fake_publish_current_turn_git_workspace(*, runtime_ctx, outdir, logger=None):
        calls["turn_id"] = runtime_ctx.turn_id
        calls["outdir"] = str(outdir)
        return {"commit_sha": "abc123"}

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace.publish_current_turn_git_workspace",
        _fake_publish_current_turn_git_workspace,
    )

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.logger = SimpleNamespace(log=lambda *args, **kwargs: None)
    wf.runtime_ctx = RuntimeCtx(
        turn_id="turn-42",
        outdir=str(tmp_path / "out"),
        workspace_implementation="git",
    )
    wf.ctx_browser = _CtxBrowserStub(wf.runtime_ctx)

    result = await wf._publish_git_workspace_if_needed()

    assert result == {"commit_sha": "abc123"}
    assert calls == {"turn_id": "turn-42", "outdir": str(tmp_path / "out")}
    assert wf.ctx_browser.contributed
    payload = json.loads(wf.ctx_browser.contributed[-1]["text"])
    assert payload["status"] == "succeeded"
    assert payload["commit_sha"] == "abc123"


@pytest.mark.asyncio
async def test_publish_git_workspace_if_needed_skips_custom_mode(monkeypatch, tmp_path):
    def _unexpected_publish_current_turn_git_workspace(**kwargs):
        raise AssertionError("publisher should not be called")

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace.publish_current_turn_git_workspace",
        _unexpected_publish_current_turn_git_workspace,
    )

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.logger = SimpleNamespace(log=lambda *args, **kwargs: None)
    wf.runtime_ctx = RuntimeCtx(
        turn_id="turn-42",
        outdir=str(tmp_path / "out"),
        workspace_implementation="custom",
    )

    result = await wf._publish_git_workspace_if_needed()

    assert result is None


@pytest.mark.asyncio
async def test_publish_git_workspace_if_needed_raises_turn_phase_error_on_publish_failure(monkeypatch, tmp_path):
    def _failing_publish_current_turn_git_workspace(**kwargs):
        raise RuntimeError("push failed")

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace.publish_current_turn_git_workspace",
        _failing_publish_current_turn_git_workspace,
    )

    wf = BaseWorkflow.__new__(BaseWorkflow)
    logs = []
    wf.logger = SimpleNamespace(log=lambda *args, **kwargs: logs.append((args, kwargs)))
    wf.runtime_ctx = RuntimeCtx(
        turn_id="turn-42",
        outdir=str(tmp_path / "out"),
        workspace_implementation="git",
    )
    wf.ctx_browser = _CtxBrowserStub(wf.runtime_ctx)

    with pytest.raises(workflow_mod.TurnPhaseError) as exc_info:
        await wf._publish_git_workspace_if_needed()

    assert exc_info.value.code == "workspace_publish_failed"
    assert exc_info.value.data == {
        "workspace_implementation": "git",
        "turn_id": "turn-42",
        "error": "RuntimeError",
        "cause": "push failed",
    }
    assert "push failed" in str(exc_info.value)
    assert logs
    assert wf.ctx_browser.contributed
    payload = json.loads(wf.ctx_browser.contributed[-1]["text"])
    assert payload["status"] == "failed"
    assert payload["error"] == "RuntimeError"


def test_stage_current_turn_text_workspace_skips_when_repo_has_no_tracked_files(monkeypatch, tmp_path):
    turn_root = tmp_path / "turn"
    turn_root.mkdir(parents=True, exist_ok=True)

    calls = []

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace._git_has_tracked_files",
        lambda *, repo_root: False,
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace._run_git_checked",
        lambda repo_root, args, op, env=None: calls.append((repo_root, list(args), op, env)),
    )

    from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace import _stage_current_turn_text_workspace

    _stage_current_turn_text_workspace(turn_root=turn_root)

    assert calls == []


@pytest.mark.asyncio
async def test_finish_turn_persists_turn_log_and_timeline_when_git_workspace_publish_fails(tmp_path):
    saved_turn_logs = []
    timeline_persisted = {"count": 0}
    stopped_listener = {"count": 0}

    async def _noop_async(*args, **kwargs):
        del args, kwargs

    class _TimelineWithBlocks(_TimelineStub):
        def __init__(self):
            self.blocks = []

    class _CtxBrowserFinishStub:
        def __init__(self, runtime_ctx, blocks):
            self.runtime_ctx = runtime_ctx
            self.timeline = _TimelineWithBlocks()
            self.timeline.blocks = list(blocks)

        def current_turn_blocks(self):
            return list(self.timeline.blocks)

        async def persist_timeline(self):
            timeline_persisted["count"] += 1

        async def stop_external_event_listener(self):
            stopped_listener["count"] += 1

        def contribute(self, blocks):
            self.timeline.blocks.extend(list(blocks or []))

    async def _save_turn_log_as_artifact(**kwargs):
        saved_turn_logs.append(dict(kwargs))

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.logger = SimpleNamespace(
        log=lambda *args, **kwargs: None,
        finish_operation=lambda *args, **kwargs: None,
    )
    wf.ctx_client = SimpleNamespace(save_turn_log_as_artifact=_save_turn_log_as_artifact)
    wf._emit = _noop_async
    wf._persist_stream_artifacts = _noop_async
    wf.report_timings = _noop_async
    wf._ctx = {
        "service": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user": "user-a",
            "user_type": "registered",
            "request_id": "req-1",
        },
        "conversation": {
            "conversation_id": "conv-1",
            "turn_id": "turn-1",
        },
        "turn": {
            "t_turn0": time.perf_counter(),
            "ms0u": 0,
        },
    }
    wf.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="bundle.test"))
    wf.runtime_ctx = RuntimeCtx(
        turn_id="turn-1",
        conversation_id="conv-1",
        workspace_implementation="git",
    )
    contrib_blocks = [
        {
            "type": "react.decision.raw",
            "turn_id": "turn-1",
            "text": "partial generated text",
            "path": "ar:turn-1.react.decision.raw.interrupted.0",
            "meta": {"interrupted": True},
        }
    ]
    wf.ctx_browser = _CtxBrowserFinishStub(wf.runtime_ctx, contrib_blocks)

    async def _failing_publish():
        raise workflow_mod.TurnPhaseError(
            "Failed to save git workspace progress: push failed",
            code="workspace_publish_failed",
            data={"cause": "push failed"},
        )

    wf._publish_git_workspace_if_needed = _failing_publish

    scratchpad = SimpleNamespace(
        answer="",
        timings=[],
        started_at="2026-04-13T00:56:00Z",
        suggested_followups=[],
    )

    await wf.finish_turn(scratchpad, ok=True)

    assert stopped_listener["count"] == 1
    assert timeline_persisted["count"] == 1
    assert len(saved_turn_logs) == 1
    saved_blocks = saved_turn_logs[0]["payload"]["blocks"]
    assert any(isinstance(b, dict) and b.get("type") == "react.decision.raw" for b in saved_blocks)


@pytest.mark.asyncio
async def test_emit_committed_answer_once_streams_single_answer_pair():
    deltas = []

    async def _delta(**kwargs):
        deltas.append(dict(kwargs))

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.comm = SimpleNamespace(delta=_delta)
    wf._answer_delta_idx = 0

    scratchpad = SimpleNamespace(answer="Committed answer")

    await wf._emit_committed_answer_once(scratchpad)
    await wf._emit_committed_answer_once(scratchpad)

    assert scratchpad._final_answer_delta_emitted is True
    assert len(deltas) == 2
    assert deltas[0]["text"] == "Committed answer"
    assert deltas[0]["marker"] == "answer"
    assert deltas[0]["agent"] == "assistant.completion"
    assert deltas[0]["completed"] is False
    assert deltas[1]["text"] == ""
    assert deltas[1]["completed"] is True
