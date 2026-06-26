import base64
import json
import time
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot import base_workflow as workflow_mod
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.infra.service_hub.errors import ServiceError, ServiceException, ServiceKind


class _TimelineStub:
    def block(self, **kwargs):
        return dict(kwargs)


class _CtxBrowserStub:
    def __init__(self, runtime_ctx):
        self.runtime_ctx = runtime_ctx
        self.timeline = _TimelineStub()
        self.contributed = []
        self.closed_external_event_handler = False

    def contribute(self, blocks):
        self.contributed.extend(list(blocks or []))

    async def close_external_event_handler(self):
        self.closed_external_event_handler = True


def _payload(
    *,
    tenant: str,
    project: str,
    user_id: str = "u1",
    turn_id: str = "turn-1",
    agent_id: str | None = None,
):
    return SimpleNamespace(
        actor=SimpleNamespace(tenant_id=tenant, project_id=project),
        user=SimpleNamespace(user_id=user_id, user_type="registered", timezone="UTC"),
        routing=SimpleNamespace(conversation_id="conv-1", turn_id=turn_id),
        event=SimpleNamespace(agent_id=agent_id) if agent_id is not None else None,
    )


def test_cleanup_turn_workspace_removes_git_cache_and_empty_ctx_root(tmp_path):
    parent = tmp_path / "exec_20260506125243_ab12"
    workdir = parent / "work"
    outdir = parent / "out"
    cache = parent / ".react_workspace_git" / "tenant__project__user__conversation"
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (outdir / "result.txt").write_text("ok\n", encoding="utf-8")
    runtime = SimpleNamespace(workdir=str(workdir), outdir=str(outdir))
    logs = []

    workflow_mod._cleanup_turn_workspace(
        runtime,
        SimpleNamespace(log=lambda message, level="INFO": logs.append((level, message))),
    )

    assert not parent.exists()
    assert any("cleaned up turn workspace git cache" in message for _, message in logs)


def test_cleanup_turn_workspace_keeps_nonempty_ctx_root(tmp_path):
    parent = tmp_path / "ctx_v2_cleanup"
    workdir = parent / "work"
    outdir = parent / "out"
    cache = parent / ".react_workspace_git" / "tenant__project__user__conversation"
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    (parent / "keep.txt").write_text("keep\n", encoding="utf-8")
    runtime = SimpleNamespace(workdir=str(workdir), outdir=str(outdir))

    workflow_mod._cleanup_turn_workspace(
        runtime,
        SimpleNamespace(log=lambda message, level="INFO": None),
    )

    assert parent.exists()
    assert (parent / "keep.txt").exists()
    assert not workdir.exists()
    assert not outdir.exists()
    assert not (parent / ".react_workspace_git").exists()


@pytest.mark.asyncio
async def test_materialize_current_turn_user_attachments_writes_non_modal_binary(tmp_path):
    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.runtime_ctx = SimpleNamespace(outdir=str(tmp_path), turn_id="turn_abc")
    wf.ctx_browser = None
    wf.store = None
    payload = base64.b64encode(b"XLSXDATA").decode("ascii")
    scratchpad = SimpleNamespace(
        turn_id="turn_abc",
        user_attachments=[
            {
                "filename": "template.xlsx",
                "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "base64": payload,
            }
        ],
    )

    await wf._materialize_current_turn_user_attachments(scratchpad)

    target = tmp_path / "turn_abc" / "attachments" / "template.xlsx"
    assert target.read_bytes() == b"XLSXDATA"
    item = scratchpad.user_attachments[0]
    assert item["physical_path"] == "turn_abc/attachments/template.xlsx"
    assert item["local_materialized"] is True
    assert item["file_exists"] is True


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
    wf.hosting_service = None
    wf.turn_status = None
    wf.runtime_ctx = RuntimeCtx(external_event_source=None)
    wf._sync_runtime_ctx_bundle_props = lambda: None

    wf.rebind_request_context(
        comm_context=_payload(tenant="tenant-b", project="project-b", turn_id="turn-2"),
        redis="redis-client",
    )

    assert wf.runtime_ctx.external_event_source is expected_source


def test_external_event_source_uses_scoped_user_without_event_context(monkeypatch):
    captured = {}

    def _fake_source(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(lane_id="lane")

    monkeypatch.setattr(workflow_mod, "build_conversation_external_event_source", _fake_source)

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.redis = "redis-client"
    wf.runtime_ctx = RuntimeCtx(agent_id="custom.react")
    wf.comm_context = SimpleNamespace(
        actor=SimpleNamespace(tenant_id="tenant-a", project_id="project-a"),
        user=SimpleNamespace(user_id="user-1", fingerprint="fp-1"),
        routing=SimpleNamespace(conversation_id="conv-1", session_id="sess-1"),
        event=None,
    )

    source = wf._external_event_source_for_runtime()

    assert source is not None
    assert captured["tenant"] == "tenant-a"
    assert captured["project"] == "project-a"
    assert captured["conversation_id"] == "conv-1"
    assert captured["user_id"] == "user-1"
    assert captured["agent_id"] == "custom.react"


@pytest.mark.asyncio
async def test_construct_turn_uses_request_context_external_events_when_payload_omits_them():
    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf._ctx = {}
    wf.gate_out_class = None
    wf.comm_context = SimpleNamespace(
        request=SimpleNamespace(
            external_events=[
                {
                    "type": "event.user.prompt",
                    "payload": {"event": {"text": "route me to Bari"}},
                }
            ]
        )
    )

    scratchpad = await wf.construct_turn_and_scratchpad(
        {
            "request_id": "req-1",
            "tenant": "tenant-a",
            "project": "project-a",
            "user": "user-1",
            "user_type": "registered",
            "session_id": "sess-1",
            "conversation_id": "conv-1",
            "turn_id": "turn-1",
        }
    )

    assert scratchpad.user_text == "route me to Bari"
    assert scratchpad.conversation_id == "conv-1"
    assert scratchpad.turn_id == "turn-1"


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


def test_runtime_ctx_uses_default_react_context_budget(monkeypatch, tmp_path):
    resolved_storage = tmp_path / "bundle-storage" / "tenant-a" / "project-a" / "bundle.test"

    def _fake_storage_for_spec(*, spec, tenant=None, project=None, ensure=True):
        if ensure:
            resolved_storage.mkdir(parents=True, exist_ok=True)
        return resolved_storage

    monkeypatch.setenv("AI_REACT_CONTEXT_MAX_TOKENS", "64000")
    monkeypatch.setenv("AI_REACT_READ_VISIBLE_MAX_TEXT_SYMBOLS", "24000")
    monkeypatch.setenv("AI_REACT_READ_VISIBLE_MAX_TOKENS", "6000")
    monkeypatch.setenv("AI_REACT_READ_VISIBLE_MAX_BYTES", "123456")
    monkeypatch.setenv("AI_REACT_READ_VISIBLE_CONTEXT_FRACTION", "0.10")
    monkeypatch.setenv("AI_REACT_KNOWLEDGE_READ_VISIBLE_MAX_TEXT_SYMBOLS", "64000")
    monkeypatch.setenv("AI_REACT_KNOWLEDGE_READ_VISIBLE_MAX_TOKENS", "16000")
    monkeypatch.setenv("AI_REACT_KNOWLEDGE_READ_VISIBLE_MAX_BYTES", "234567")
    monkeypatch.setenv("AI_REACT_EXEC_TEXT_PREVIEW_MAX_SYMBOLS", "4000")
    monkeypatch.setenv("AI_REACT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS", "5000")
    monkeypatch.setenv("AI_REACT_CACHE_KEEP_RECENT_TURNS", "5")
    monkeypatch.setenv("AI_REACT_CACHE_KEEP_RECENT_INTACT_TURNS", "1")
    monkeypatch.setattr(
        "kdcube_ai_app.infra.plugin.bundle_storage.storage_for_spec",
        _fake_storage_for_spec,
    )
    workflow_mod.get_settings.cache_clear()
    try:
        wf = BaseWorkflow(
            conv_idx=SimpleNamespace(),
            kb=SimpleNamespace(),
            store=SimpleNamespace(),
            comm=SimpleNamespace(delta=lambda *a, **k: None),
            model_service=SimpleNamespace(),
            conv_ticket_store=SimpleNamespace(),
            config=SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="bundle.test")),
            comm_context=_payload(tenant="tenant-a", project="project-a", turn_id="turn-budget"),
            ctx_client=SimpleNamespace(),
        )

        assert wf.runtime_ctx.max_tokens == 64000
        assert wf.runtime_ctx.read_visible_max_text_symbols == 24000
        assert wf.runtime_ctx.read_visible_max_tokens == 6000
        assert wf.runtime_ctx.read_visible_max_bytes == 123456
        assert wf.runtime_ctx.read_visible_context_fraction == 0.10
        assert wf.runtime_ctx.knowledge_read_visible_max_text_symbols == 64000
        assert wf.runtime_ctx.knowledge_read_visible_max_tokens == 16000
        assert wf.runtime_ctx.knowledge_read_visible_max_bytes == 234567
        assert wf.runtime_ctx.exec_text_preview_max_symbols == 4000
        assert wf.runtime_ctx.tool_result_preview_max_text_symbols == 5000
        assert wf.runtime_ctx.session.keep_recent_turns == 5
        assert wf.runtime_ctx.session.keep_recent_intact_turns == 1
    finally:
        workflow_mod.get_settings.cache_clear()


def test_react_render_thinking_prefers_bundle_props_over_settings():
    settings = SimpleNamespace(AI_REACT_RENDER_THINKING=True)

    assert workflow_mod._react_render_thinking({"react": {"render_thinking": False}}, settings) is False
    assert workflow_mod._react_render_thinking({"config": {"react": {"render_thinking": "off"}}}, settings) is False
    assert workflow_mod._react_render_thinking({}, settings) is True


def test_react_config_resolves_agent_blocks_before_default_and_legacy():
    settings = SimpleNamespace(
        AI_REACT_MAX_ITERATIONS=15,
        AI_REACT_RENDER_THINKING=True,
        AI_REACT_LINE_NUMBERS_MODE="lines",
        AI_REACT_EVENT_SOURCE_PIPELINE_ENABLED=False,
    )
    props = {
        "config": {
            "react": {
                "render_thinking": "off",
                "max_iterations": 2,
                "default_agent": {
                    "render_thinking": False,
                    "max_iterations": 7,
                    "event_source_pipeline": {"enabled": False},
                    "additional_instructions": "default instructions",
                },
                "review.agent": {
                    "render_thinking": True,
                    "max_iterations": 3,
                    "event_source_pipeline_enabled": "yes",
                },
                "review_agent": {
                    "line_numbers_mode": "disabled",
                },
            }
        }
    }

    assert workflow_mod._react_render_thinking(props, settings, agent_id="review.agent") is True
    assert workflow_mod._react_max_iterations(props, settings, agent_id="review.agent") == 3
    assert workflow_mod._react_line_numbers_mode(props, settings, agent_id="review.agent") == "disabled"
    assert workflow_mod._react_event_source_pipeline_enabled(props, settings, agent_id="review.agent") is True

    assert workflow_mod._react_render_thinking(props, settings, agent_id="unknown.agent") is False
    assert workflow_mod._react_max_iterations(props, settings, agent_id="unknown.agent") == 7
    value, source = workflow_mod._react_config_lookup(
        props,
        "additional_instructions",
        agent_id="unknown.agent",
    )
    assert value == "default instructions"
    assert source == "config.react.default_agent.additional_instructions"

    legacy_only = {"config": {"react": {"render_thinking": "off", "max_iterations": 4}}}
    assert workflow_mod._react_render_thinking(legacy_only, settings, agent_id="review.agent") is False
    assert workflow_mod._react_max_iterations(legacy_only, settings, agent_id="review.agent") == 4


def test_react_event_source_pipeline_enabled_prefers_bundle_props_over_settings():
    settings = SimpleNamespace(AI_REACT_EVENT_SOURCE_PIPELINE_ENABLED=False)

    assert workflow_mod._react_event_source_pipeline_enabled({"react": {"event_source_pipeline": {"enabled": True}}}, settings) is True
    assert workflow_mod._react_event_source_pipeline_enabled({"react": {"event_source_pipeline_enabled": "yes"}}, settings) is True
    assert workflow_mod._react_event_source_pipeline_enabled({"config": {"react": {"event_source_pipeline": {"enabled": "off"}}}}, settings) is False
    assert workflow_mod._react_event_source_pipeline_enabled({}, SimpleNamespace(AI_REACT_EVENT_SOURCE_PIPELINE_ENABLED=True)) is True


def test_react_debug_timeline_enabled_prefers_bundle_props_over_settings():
    settings = SimpleNamespace(AI_REACT_DEBUG_TIMELINE=True)

    assert workflow_mod._react_debug_timeline_enabled({"react": {"debug_timeline": False}}, settings, default=True) is False
    assert workflow_mod._react_debug_timeline_enabled({"config": {"react": {"debug_timeline": "off"}}}, settings, default=True) is False
    assert workflow_mod._react_debug_timeline_enabled({}, settings, default=False) is True
    assert workflow_mod._react_debug_timeline_enabled({}, SimpleNamespace(AI_REACT_DEBUG_TIMELINE=None), default=True) is True


def test_react_debug_timeline_settings_use_container_root_and_retention():
    settings = SimpleNamespace(
        HOST_REACT_DEBUG_PATH="/host/react-debug",
        PLATFORM=SimpleNamespace(
            REACT_DEBUG=SimpleNamespace(
                REACT_DEBUG_ROOT="/react-debug",
                REACT_DEBUG_KEEP_FILES=25,
            )
        ),
    )

    assert workflow_mod._react_debug_timeline_root(settings) == "/react-debug"
    assert workflow_mod._react_debug_timeline_keep_files(settings) == 25


def test_react_debug_timeline_root_falls_back_to_existing_host_path(tmp_path):
    settings = SimpleNamespace(
        HOST_REACT_DEBUG_PATH=str(tmp_path),
        PLATFORM=SimpleNamespace(
            REACT_DEBUG=SimpleNamespace(
                REACT_DEBUG_ROOT="",
                REACT_DEBUG_KEEP_FILES=0,
            )
        ),
    )

    assert workflow_mod._react_debug_timeline_root(settings) == str(tmp_path)
    assert workflow_mod._react_debug_timeline_keep_files(settings) == 100


def test_react_debug_timeline_root_ignores_missing_host_path():
    settings = SimpleNamespace(
        HOST_REACT_DEBUG_PATH="/host/react-debug",
        PLATFORM=SimpleNamespace(
            REACT_DEBUG=SimpleNamespace(
                REACT_DEBUG_ROOT="",
                REACT_DEBUG_KEEP_FILES=0,
            )
        ),
    )

    assert workflow_mod._react_debug_timeline_root(settings) is None


def test_sync_runtime_ctx_bundle_props_refreshes_render_thinking(monkeypatch):
    monkeypatch.setattr(
        workflow_mod,
        "get_settings",
        lambda: SimpleNamespace(
            AI_REACT_RENDER_THINKING=True,
            HOST_REACT_DEBUG_PATH="/host/react-debug",
            PLATFORM=SimpleNamespace(
                REACT_DEBUG=SimpleNamespace(
                    REACT_DEBUG_ROOT="/react-debug",
                    REACT_DEBUG_KEEP_FILES=17,
                )
            ),
        ),
    )
    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.bundle_props = {"react": {"render_thinking": False, "debug_timeline": True}}
    wf.runtime_ctx = RuntimeCtx(render_thinking=True, debug_timeline=False)

    wf._sync_runtime_ctx_bundle_props()

    assert wf.runtime_ctx.render_thinking is False
    assert wf.runtime_ctx.debug_timeline is True
    assert wf.runtime_ctx.debug_timeline_root == "/react-debug"
    assert wf.runtime_ctx.debug_timeline_keep_files == 17


def test_base_workflow_constructor_binds_external_event_source_when_redis_present(monkeypatch):
    sentinel = object()
    calls = []

    def _fake_build_conversation_external_event_source(**kwargs):
        calls.append(dict(kwargs))
        return sentinel

    monkeypatch.setattr(
        workflow_mod,
        "build_conversation_external_event_source",
        _fake_build_conversation_external_event_source,
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
        comm_context=_payload(tenant="tenant-a", project="project-a", turn_id="turn-ctor", agent_id="main"),
        ctx_client=SimpleNamespace(),
        redis="redis-client",
    )

    assert wf.redis == "redis-client"
    assert wf.runtime_ctx.external_event_source is sentinel
    assert calls
    assert calls[-1]["agent_id"] == "main"
    assert calls[-1]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_publish_git_workspace_if_needed_calls_publisher_in_git_mode(monkeypatch, tmp_path):
    calls = {}

    async def _fake_publish_current_turn_git_workspace(*, runtime_ctx, outdir, logger=None):
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
    async def _unexpected_publish_current_turn_git_workspace(**kwargs):
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
    async def _failing_publish_current_turn_git_workspace(**kwargs):
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


def test_stage_current_turn_text_workspace_stages_new_files_without_existing_tracked_files(monkeypatch, tmp_path):
    turn_root = tmp_path / "turn"
    files_root = turn_root / "files"
    files_root.mkdir(parents=True, exist_ok=True)
    (files_root / "README.md").write_text("# Demo\n", encoding="utf-8")

    calls = []

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace._run_git_checked",
        lambda repo_root, args, op, env=None: calls.append((repo_root, list(args), op, env)),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace._git_path_is_ignored",
        lambda *, repo_root, rel_path: False,
    )

    from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.git_workspace import _stage_current_turn_text_workspace

    _stage_current_turn_text_workspace(turn_root=turn_root)

    assert calls[0][1] == ["add", "--sparse", "-u", "--", "."]
    assert calls[1][1] == ["add", "--sparse", "--", "files/README.md"]


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


def test_service_exception_chain_detects_wrapped_connectivity_error():
    err = ServiceError(
        kind=ServiceKind.llm,
        service_name="decision",
        error_type="APIConnectionError",
        message="Connection error.",
        stage="decision",
        retryable=True,
    )
    wrapped = RuntimeError("[react.v3] Graph error: Connection error.")
    wrapped.__cause__ = ServiceException(err)

    found = workflow_mod._service_exception_from_chain(wrapped)

    assert found is not None
    assert found.err is err
    assert workflow_mod._is_service_connectivity_error(found.err) is True


@pytest.mark.asyncio
async def test_handle_turn_exception_sanitizes_wrapped_react_connection_error(monkeypatch):
    errors = []
    deleted_turns = []

    async def _error(**kwargs):
        errors.append(dict(kwargs))

    async def _delete_turn(**kwargs):
        deleted_turns.append(dict(kwargs))

    async def _report_timings(*args, **kwargs):
        del args, kwargs
        return ("timings", "| timings |", [])

    monkeypatch.setattr(workflow_mod, "_cleanup_turn_workspace", lambda *args, **kwargs: None)

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.logger = SimpleNamespace(log=lambda *args, **kwargs: None)
    wf.comm = SimpleNamespace(error=_error, delta=lambda **kwargs: None)
    wf.ctx_client = SimpleNamespace(delete_turn=_delete_turn)
    wf.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="task-tracker@1-0"))
    wf.runtime_ctx = RuntimeCtx(turn_id="turn-connection", conversation_id="conv-1")
    wf.ctx_browser = _CtxBrowserStub(wf.runtime_ctx)
    wf.report_timings = _report_timings
    wf.message_resources_fn = lambda code, fallback=None: None
    wf._ctx = {
        "service": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user": "user-a",
            "user_type": "registered",
            "request_id": "req-1",
        },
        "turn": {
            "t_turn0": time.perf_counter(),
            "ms0u": 0,
        },
    }

    service_error = ServiceError(
        kind=ServiceKind.llm,
        service_name="decision",
        provider="openai",
        model_name="model-x",
        error_type="APIConnectionError",
        message="Connection error.",
        stage="decision",
        retryable=True,
    )
    exc = RuntimeError("[react.v3] Graph error: Connection error.\nTraceback (most recent call last):\n  File \"x\", line 1")
    exc.__cause__ = ServiceException(service_error)
    scratchpad = SimpleNamespace(
        conversation_id="conv-1",
        turn_id="turn-connection",
        current_phase=SimpleNamespace(agent="react", name="decision", meta={}),
    )

    with pytest.raises(RuntimeError):
        await wf._handle_turn_exception(exc, scratchpad)

    assert errors
    message = errors[0]["message"]
    assert message.startswith("The AI service connection failed")
    assert "Traceback" not in message
    assert "File " not in message
    data = errors[0]["data"]
    assert "traceback" not in data
    assert "raw_error" not in data
    assert data["error_type"] == "APIConnectionError"
    assert data["service_error"]["error_type"] == "APIConnectionError"
    assert deleted_turns
    assert wf.ctx_browser.closed_external_event_handler is True


@pytest.mark.asyncio
async def test_emit_committed_answer_once_replaces_source_tokens_from_sources_pool():
    deltas = []

    async def _delta(**kwargs):
        deltas.append(dict(kwargs))

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.comm = SimpleNamespace(delta=_delta)
    wf._answer_delta_idx = 0
    wf.ctx_browser = SimpleNamespace(
        sources_pool=[
            {
                "sid": 1,
                "title": "Source 1",
                "url": "https://example.com/source-1",
                "text": "Source text",
            },
        ],
    )

    scratchpad = SimpleNamespace(answer="Committed answer [[S:1]]")

    await wf._emit_committed_answer_once(scratchpad)

    rendered = "".join(delta.get("text") or "" for delta in deltas)
    assert "[[S:" not in rendered
    assert "https://example.com/source-1" in rendered


@pytest.mark.asyncio
async def test_persist_turn_entries_store_multiple_user_and_assistant_rows():
    saved_messages = []
    emitted = []

    class _ConvIdxStub:
        async def add_message(self, **kwargs):
            saved_messages.append(dict(kwargs))

    class _TimelineWithBlocks(_TimelineStub):
        def __init__(self, blocks):
            self.blocks = list(blocks)

    async def _embed_texts(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.conv_idx = _ConvIdxStub()
    wf.model_service = SimpleNamespace(embed_texts=_embed_texts)
    wf.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="bundle.test"))
    wf._ctx = {
        "service": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user": "user-a",
            "user_type": "registered",
        },
        "conversation": {
            "conversation_id": "conv-1",
            "turn_id": "turn-1",
            "ts": "2026-04-26T10:00:00Z",
        },
    }
    wf._emit = lambda payload: emitted.append(payload)
    wf.logger = SimpleNamespace(log=lambda *args, **kwargs: None)
    wf.ctx_browser = SimpleNamespace(
        timeline=_TimelineWithBlocks(
            [
                {
                    "type": "user.prompt",
                    "turn_id": "turn-1",
                    "ts": "2026-04-26T10:00:00Z",
                    "path": "ar:turn-1.user.prompt",
                    "text": "Original prompt",
                    "meta": {},
                },
                {
                    "type": "user.followup",
                    "turn_id": "turn-1",
                    "ts": "2026-04-26T10:00:02Z",
                    "path": "ar:turn-1.user.followup.1",
                    "text": "Additional requirement",
                    "meta": {},
                },
                {
                    "type": "assistant.completion",
                    "turn_id": "turn-1",
                    "ts": "2026-04-26T10:00:03Z",
                    "path": "ar:turn-1.assistant.completion.1",
                    "text": "First visible completion",
                },
                {
                    "type": "assistant.completion",
                    "turn_id": "turn-1",
                    "ts": "2026-04-26T10:00:04Z",
                    "path": "ar:turn-1.assistant.completion",
                    "text": "Final completion",
                },
                {
                    "type": "conv.working.summary",
                    "turn_id": "turn-1",
                    "ts": "2026-04-26T10:00:05Z",
                    "path": "ws:turn-1.conv.working.summary.attempt.2",
                    "text": "Goal: finish the task\nOutcome: final completion persisted",
                    "meta": {
                        "kind": "working_summary",
                        "summary_scope": "completion_attempt",
                        "assistant_completion_attempt_index": 2,
                    },
                },
                {
                    "type": "react.note",
                    "turn_id": "turn-1",
                    "ts": "2026-04-26T10:00:06Z",
                    "path": "fi:turn-1.outputs/internal_notes/beacons.md",
                    "text": "[K] fi:turn-1.outputs/report.html - source report\n[D] Keep notes indexed as authored.",
                    "meta": {"channel": "internal"},
                },
            ]
        )
    )

    async def _emit_async(payload):
        emitted.append(payload)

    wf._emit = _emit_async

    scratchpad = SimpleNamespace(
        turn_topics_plain=[],
        persisted_turn_entry_paths=set(),
        answer="Final completion",
        answer_raw="",
        timings=[],
    )

    prompt_count = await wf.persist_turn_prompt_entries(scratchpad)
    await wf.persist_assistant(scratchpad)

    assert prompt_count == 2
    assert [m["role"] for m in saved_messages] == ["user", "user", "assistant", "assistant", "assistant", "artifact"]
    assert [m["text"] for m in saved_messages] == [
        "[user.message]\nOriginal prompt",
        "[user.message]\nAdditional requirement",
        "First visible completion",
        "Final completion",
        "Goal: finish the task\nOutcome: final completion persisted",
        "[K] fi:turn-1.outputs/report.html - source report\n[D] Keep notes indexed as authored.",
    ]
    assert saved_messages[1]["tags"][-1] == "event_type:event.user.followup"
    assert "chat:summary" in saved_messages[4]["tags"]
    assert "kind:working.summary" in saved_messages[4]["tags"]
    assert "summary_scope:completion_attempt" in saved_messages[4]["tags"]
    assert "summary_attempt:2" in saved_messages[4]["tags"]
    assert "kind:react.note" in saved_messages[5]["tags"]
    assert "note_tag:K" in saved_messages[5]["tags"]
    assert "note_tag:D" in saved_messages[5]["tags"]
    assert "visibility:internal" in saved_messages[5]["tags"]
    assert {
        "ar:turn-1.user.prompt",
        "ar:turn-1.user.followup.1",
        "ar:turn-1.assistant.completion.1",
        "ar:turn-1.assistant.completion",
        "ws:turn-1.conv.working.summary.attempt.2",
        "fi:turn-1.outputs/internal_notes/beacons.md",
    }.issubset(scratchpad.persisted_turn_entry_paths)
    assert any(
        payload.get("data", {}).get("count") == 4
        and payload.get("data", {}).get("assistant_completion_count") == 2
        and payload.get("data", {}).get("working_summary_count") == 1
        and payload.get("data", {}).get("internal_note_count") == 1
        for payload in emitted
        if isinstance(payload, dict) and payload.get("step") == "conversation.persist.assistant_message"
    )


@pytest.mark.asyncio
async def test_event_lane_prompt_is_not_indexed_twice_by_legacy_persist_call():
    saved_messages = []

    class _ConvIdxStub:
        async def add_message(self, **kwargs):
            saved_messages.append(dict(kwargs))

    class _TimelineWithBlocks(_TimelineStub):
        def __init__(self, blocks):
            self.blocks = list(blocks)

    async def _embed_texts(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.conv_idx = _ConvIdxStub()
    wf.model_service = SimpleNamespace(embed_texts=_embed_texts)
    wf.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="bundle.test"))
    wf._ctx = {
        "service": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user": "user-a",
            "user_type": "registered",
        },
        "conversation": {
            "conversation_id": "conv-1",
            "turn_id": "turn-1",
            "ts": "2026-04-26T10:00:00Z",
        },
    }
    wf.ctx_browser = SimpleNamespace(
        timeline=_TimelineWithBlocks(
            [
                {
                    "type": "user.prompt",
                    "turn_id": "turn-1",
                    "ts": "2026-04-26T10:00:00Z",
                    "path": "ar:turn-1.user.prompt.evt_1",
                    "text": "Original prompt",
                    "meta": {
                        "prompt_origin": "external_event_lane",
                        "event_id": "evt_1",
                        "origin_turn_id": "turn-previous",
                    },
                },
            ]
        ),
        last_external_event_reader_result=lambda: {"current_turn_user_input_materialized": True},
    )

    scratchpad = SimpleNamespace(
        turn_topics_plain=[],
        persisted_turn_entry_paths=set(),
        user_text="Original prompt",
        short_text="Original prompt",
        user_message_persisted=False,
    )

    await wf.persist_user_message(scratchpad)
    assert saved_messages == []
    assert scratchpad.user_message_persisted is True

    prompt_count = await wf.persist_turn_prompt_entries(scratchpad)

    assert prompt_count == 1
    assert [m["role"] for m in saved_messages] == ["user"]
    assert saved_messages[0]["text"] == "[user.message]\nOriginal prompt"
    assert "ar:turn-1.user.prompt.evt_1" in scratchpad.persisted_turn_entry_paths


@pytest.mark.asyncio
async def test_current_turn_prompt_persistence_ignores_stale_turn_user_blocks():
    saved_messages = []

    class _ConvIdxStub:
        async def add_message(self, **kwargs):
            saved_messages.append(dict(kwargs))

    async def _embed_texts(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    stale_prompt_block = {
        "type": "user.prompt",
        "turn_id": "turn-previous",
        "ts": "2026-04-26T10:00:00Z",
        "path": "ar:turn-previous.user.prompt.evt_1",
        "text": "Create a route to Bari using sails",
        "meta": {
            "prompt_origin": "external_event_lane",
            "event_id": "evt_1",
            "event_type": "event.user.prompt",
        },
    }

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.conv_idx = _ConvIdxStub()
    wf.model_service = SimpleNamespace(embed_texts=_embed_texts)
    wf.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="bundle.test"))
    wf._ctx = {
        "service": {
            "tenant": "tenant-a",
            "project": "project-a",
            "user": "user-a",
            "user_type": "registered",
        },
        "conversation": {
            "conversation_id": "conv-1",
            "turn_id": "turn-current",
            "ts": "2026-04-26T10:00:00Z",
        },
    }
    wf.ctx_browser = SimpleNamespace(
        timeline=SimpleNamespace(blocks=[]),
        current_turn_blocks=lambda: [stale_prompt_block],
    )
    scratchpad = SimpleNamespace(
        turn_topics_plain=[],
        persisted_turn_entry_paths=set(),
    )

    prompt_count = await wf.persist_turn_prompt_entries(scratchpad)

    assert prompt_count == 0
    assert saved_messages == []


# ---------------------------------------------------------------------------
# Per-agent role_models overlay (config.react.<agent>.role_models)
# ---------------------------------------------------------------------------

def test_react_role_models_resolves_per_agent_with_fallback():
    bundle_props = {
        "config": {
            "react": {
                "default_agent": {
                    "role_models": {
                        "solver.react.v2.decision.v2.strong": {
                            "provider": "anthropic",
                            "model": "claude-sonnet-4-6",
                        }
                    }
                },
                "research_agent": {
                    "role_models": {
                        "solver.react.v2.decision.v2.strong": {
                            "provider": "anthropic",
                            "model": "claude-opus-4-8",
                        }
                    }
                },
            }
        }
    }

    # Exact agent id wins.
    research = workflow_mod._react_role_models(bundle_props, agent_id="research_agent")
    assert research["solver.react.v2.decision.v2.strong"]["model"] == "claude-opus-4-8"

    # Unknown agent id falls back to default_agent.
    fallback = workflow_mod._react_role_models(bundle_props, agent_id="unknown_agent")
    assert fallback["solver.react.v2.decision.v2.strong"]["model"] == "claude-sonnet-4-6"

    # No react config at all -> empty (static Config.role_models base applies).
    assert workflow_mod._react_role_models({}, agent_id="research_agent") == {}


def test_bind_runtime_role_models_overlays_and_existing_wins():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime import ReactSolverV2
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
        bind_current_bundle_call_context_patch,
        get_current_bundle_call_context,
    )

    solver = ReactSolverV2.__new__(ReactSolverV2)
    solver.ctx_browser = SimpleNamespace(
        runtime_ctx=RuntimeCtx(
            agent_id="research_agent",
            agent_role_models={
                "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": "agent-model"},
                "context.compaction.summary": {"provider": "anthropic", "model": "agent-summary"},
            },
        )
    )

    # No pre-existing overlay: agent map is applied verbatim.
    with solver._bind_runtime_role_models():
        rm = get_current_bundle_call_context().get("role_models")
        assert rm["solver.react.v2.decision.v2.strong"]["model"] == "agent-model"
        assert rm["context.compaction.summary"]["model"] == "agent-summary"
    # Restored after the block.
    assert "role_models" not in get_current_bundle_call_context()

    # Explicit per-request overlay already bound wins per role; agent fills the rest.
    with bind_current_bundle_call_context_patch(
        {"role_models": {"solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": "request-model"}}}
    ):
        with solver._bind_runtime_role_models():
            rm = get_current_bundle_call_context().get("role_models")
            assert rm["solver.react.v2.decision.v2.strong"]["model"] == "request-model"  # explicit wins
            assert rm["context.compaction.summary"]["model"] == "agent-summary"           # agent fills gap


def test_bind_runtime_role_models_noop_without_agent_overlay():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime import ReactSolverV2
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_bundle_call_context

    solver = ReactSolverV2.__new__(ReactSolverV2)
    solver.ctx_browser = SimpleNamespace(runtime_ctx=RuntimeCtx(agent_id="a", agent_role_models={}))

    with solver._bind_runtime_role_models():
        assert "role_models" not in get_current_bundle_call_context()


# ---------------------------------------------------------------------------
# Named-service ReAct roster: overridable BaseWorkflow helper composes the
# section from the canonical discovery read.
# ---------------------------------------------------------------------------

class _RosterFakeRedis:
    """Minimal async redis double for RedisNamedServiceDiscovery reads/writes."""

    def __init__(self):
        self._v = {}
        self._s = {}

    async def set(self, k, v, ex=None):
        self._v[str(k)] = v
        return True

    async def get(self, k):
        return self._v.get(str(k))

    async def sadd(self, k, *vs):
        bucket = self._s.setdefault(str(k), set())
        for x in vs:
            bucket.add(str(x))
        return 1

    async def smembers(self, k):
        return set(self._s.get(str(k), set()))

    async def expire(self, k, s):
        return True

    async def persist(self, k):
        return True


def _named_service_consumer_props(*namespaces: str) -> dict:
    return {
        "surfaces": {
            "as_consumer": {
                "agents": {
                    "main": {
                        "tools": [
                            {
                                "kind": "named_service",
                                "alias": "named_services",
                                "namespaces": {
                                    ns: {"allowed": ["provider.about", "object.search"]}
                                    for ns in namespaces
                                },
                            }
                        ]
                    }
                }
            }
        }
    }


async def _register_roster_provider(redis, *, provider_id, namespaces, label, intro, bundle_id):
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
        RedisNamedServiceDiscovery,
        NamedServiceProviderSpec,
    )
    discovery = RedisNamedServiceDiscovery(redis, tenant="demo-tenant", project="demo-project")
    await discovery.register_provider(
        NamedServiceProviderSpec(
            provider_id=provider_id,
            bundle_id=bundle_id,
            namespaces=tuple(namespaces),
            label=label,
            intro=intro,
            operations={"object.search": {"transports": ["bundle_registry"]}},
        ),
        bundle_id=bundle_id,
    )


@pytest.mark.asyncio
async def test_base_workflow_named_service_roster_uses_canonical_discovery_read():
    redis = _RosterFakeRedis()
    await _register_roster_provider(
        redis,
        provider_id="sdk.memory",
        namespaces=("me", "mem"),
        label="User memories",
        intro="Durable user memory — facts, preferences …",
        bundle_id="versatile@1-0",
    )
    await _register_roster_provider(
        redis,
        provider_id="task.issue",
        namespaces=("task",),
        label="Tasks",
        intro="KDCube issue tracker — report issues …",
        bundle_id="task-tracker@1-0",
    )

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.bundle_props = _named_service_consumer_props("mem", "task")
    wf.redis = redis
    wf.runtime_ctx = RuntimeCtx(agent_id="main", tenant="demo-tenant", project="demo-project")
    wf.logger = SimpleNamespace(log=lambda *args, **kwargs: None)

    block = await wf.named_service_react_instructions()

    assert "[NAMED SERVICES — NAMESPACE OBJECT OPERATIONS]" in block
    assert "Named-service namespaces available to this agent (pass one as the `namespace` argument):" in block
    assert "- `mem` — Durable user memory — facts, preferences …" in block
    assert "- `task` — KDCube issue tracker — report issues …" in block


@pytest.mark.asyncio
async def test_base_workflow_named_service_roster_empty_without_connected_namespaces():
    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.bundle_props = {}  # no as_consumer named-service namespaces
    wf.redis = _RosterFakeRedis()
    wf.runtime_ctx = RuntimeCtx(agent_id="main", tenant="demo-tenant", project="demo-project")
    wf.logger = SimpleNamespace(log=lambda *args, **kwargs: None)

    assert await wf.named_service_react_instructions() == ""


@pytest.mark.asyncio
async def test_subclass_can_override_named_service_react_instructions():
    class CustomWorkflow(BaseWorkflow):
        async def named_service_react_instructions(self, *, client_id=None):
            return "CUSTOM ROSTER BLOCK"

    wf = CustomWorkflow.__new__(CustomWorkflow)
    wf.bundle_props = _named_service_consumer_props("mem")
    wf.redis = _RosterFakeRedis()
    wf.runtime_ctx = RuntimeCtx(agent_id="main", tenant="demo-tenant", project="demo-project")
    wf.logger = SimpleNamespace(log=lambda *args, **kwargs: None)

    assert await wf.named_service_react_instructions() == "CUSTOM ROSTER BLOCK"
