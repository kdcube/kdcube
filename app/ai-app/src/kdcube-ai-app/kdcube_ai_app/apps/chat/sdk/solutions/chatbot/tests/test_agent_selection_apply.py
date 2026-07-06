# SPDX-License-Identifier: MIT

"""BaseWorkflow.apply_user_agent_selection: fail-open + narrowing wiring."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import AgentSkillConfig
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import AgentToolConfig
from kdcube_ai_app.apps.chat.sdk.solutions.user_settings import agent_selection_key
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import (
    denied_named_service_namespaces,
    set_denied_named_service_namespaces,
)


class _Logger:
    def __init__(self):
        self.lines = []

    def log(self, message, level=None, **kwargs):
        self.lines.append((level, str(message)))


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    async def fetchrow(self, sql, *args):
        return self._rows.get((args[0], args[1], args[2]))

    async def execute(self, sql, *args):
        return None


class _FakeAcquire:
    def __init__(self, con):
        self._con = con

    async def __aenter__(self):
        return self._con

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, rows=None):
        self._con = _FakeConnection(rows or {})

    def acquire(self):
        return _FakeAcquire(self._con)


class _BrokenPool:
    def acquire(self):
        raise RuntimeError("db down")


def _workflow_stub(*, pg_pool, user_id="u1", bundle_id="bundle@1-0", agent_id="main", bundle_props=None):
    stub = SimpleNamespace()
    stub.pg_pool = pg_pool
    stub.logger = _Logger()
    stub.bundle_props = dict(bundle_props or {})
    stub.runtime_ctx = SimpleNamespace(
        tenant="acme",
        project="demo",
        user_id=user_id,
        bundle_id=bundle_id,
        agent_id=agent_id,
        conversation_id="conv-1",
        cold_turn_marker=None,
    )
    # Cold-cache governance collaborators: no loaded timeline in unit stubs
    # (reads as a fresh/cold conversation), warmness helper bound explicitly.
    stub.ctx_browser = SimpleNamespace(timeline=None)
    stub._conversation_cache_is_warm = lambda timeline: False
    return stub


def _tool_cfg() -> AgentToolConfig:
    return AgentToolConfig(
        tool_specs=[{"alias": "gmail", "module": "missing.gmail_mod", "use_sk": True}],
        allowed_plugins=["io_tools", "gmail"],
        allowed_tool_names_by_alias={"io_tools": ["tool_call"], "gmail": ["search_gmail"]},
    )


def _selection_row(disabled) -> dict:
    return {
        "value_json": json.dumps({"schema_version": 1, "disabled": disabled}),
        "created_at": "",
        "updated_at": "",
    }


@pytest.fixture(autouse=True)
def _reset_namespace_deny():
    set_denied_named_service_namespaces(None)
    yield
    set_denied_named_service_namespaces(None)


@pytest.mark.asyncio
async def test_absent_row_returns_configs_unchanged():
    stub = _workflow_stub(pg_pool=_FakePool())
    tool_cfg, skill_cfg = _tool_cfg(), AgentSkillConfig()
    out_tools, out_skills = await BaseWorkflow.apply_user_agent_selection(stub, tool_cfg, skill_cfg)
    assert out_tools is tool_cfg
    assert out_skills is skill_cfg


@pytest.mark.asyncio
async def test_store_error_fails_open():
    stub = _workflow_stub(pg_pool=_BrokenPool())
    tool_cfg, skill_cfg = _tool_cfg(), AgentSkillConfig()
    out_tools, out_skills = await BaseWorkflow.apply_user_agent_selection(stub, tool_cfg, skill_cfg)
    assert out_tools is tool_cfg
    assert out_skills is skill_cfg
    assert any("fail" in line.lower() or "configured set" in line for _, line in stub.logger.lines)


@pytest.mark.asyncio
async def test_missing_pool_fails_open():
    stub = _workflow_stub(pg_pool=None)
    tool_cfg, skill_cfg = _tool_cfg(), AgentSkillConfig()
    out_tools, out_skills = await BaseWorkflow.apply_user_agent_selection(stub, tool_cfg, skill_cfg)
    assert out_tools is tool_cfg
    assert out_skills is skill_cfg


@pytest.mark.asyncio
async def test_saved_selection_narrows_and_sets_namespace_deny():
    rows = {
        ("u1", "bundle@1-0", agent_selection_key("main")): _selection_row(
            {
                "tools": {"gmail": True},
                "named_services": {"task": True},
                "skills": ["public.web_search"],
            }
        ),
    }
    stub = _workflow_stub(pg_pool=_FakePool(rows))
    tool_cfg, skill_cfg = _tool_cfg(), AgentSkillConfig(agents_config={})

    out_tools, out_skills = await BaseWorkflow.apply_user_agent_selection(stub, tool_cfg, skill_cfg)

    assert "gmail" not in out_tools.allowed_plugins
    assert "io_tools" in out_tools.allowed_plugins  # system group immune
    assert out_skills.agents_config["*"]["disabled"] == ["public.web_search"]
    assert denied_named_service_namespaces() == frozenset({"task"})
    assert any("agent_selection.applied" in line for _, line in stub.logger.lines)


@pytest.mark.asyncio
async def test_apply_resets_stale_namespace_deny():
    set_denied_named_service_namespaces({"mem"})
    stub = _workflow_stub(pg_pool=_FakePool())
    await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    assert denied_named_service_namespaces() == frozenset()


# ── per-user model pick → strong decision role override ──────────────────────

from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (  # noqa: E402
    USER_MODEL_TARGET_ROLE,
)

_MODEL_PROPS = {
    "react": {
        "default_agent": {
            "supported_models": [
                {"model": "claude-sonnet-4-6", "provider": "anthropic", "label": "Sonnet 4.6"},
                {"model": "claude-haiku-4-5-20251001", "provider": "anthropic", "label": "Haiku 4.5"},
            ],
        },
    },
    "role_models": {
        USER_MODEL_TARGET_ROLE: {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    },
}


def _selection_row_with_model(model, disabled=None) -> dict:
    return {
        "value_json": json.dumps({"schema_version": 1, "disabled": disabled or {}, "model": model}),
        "created_at": "",
        "updated_at": "",
    }


def _model_stub(rows):
    stub = _workflow_stub(pg_pool=_FakePool(rows), bundle_props=_MODEL_PROPS)
    stub.runtime_ctx.agent_role_models = {"seeded": {"provider": "x", "model": "y"}}
    return stub


@pytest.mark.asyncio
async def test_model_pick_overrides_strong_decision_role():
    rows = {
        ("u1", "bundle@1-0", agent_selection_key("main")): _selection_row_with_model(
            {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
        ),
    }
    stub = _model_stub(rows)
    tool_cfg, skill_cfg = _tool_cfg(), AgentSkillConfig()
    out_tools, out_skills = await BaseWorkflow.apply_user_agent_selection(stub, tool_cfg, skill_cfg)

    # Configs untouched (no deny-list), yet the role override landed on the
    # channel the ReAct runtimes bind into the request role_models.
    assert out_tools is tool_cfg and out_skills is skill_cfg
    assert stub.runtime_ctx.agent_role_models[USER_MODEL_TARGET_ROLE] == {
        "provider": "anthropic", "model": "claude-haiku-4-5-20251001",
    }
    assert any("agent_selection.applied" in line for _, line in stub.logger.lines)


@pytest.mark.asyncio
async def test_stale_model_pick_falls_back_to_configured_default():
    rows = {
        ("u1", "bundle@1-0", agent_selection_key("main")): _selection_row_with_model(
            {"provider": "anthropic", "model": "claude-3-retired"},
        ),
    }
    stub = _model_stub(rows)
    await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    # Rebased from config; the stale pick added no override (and the stale
    # seeded map from a previous turn is gone).
    assert USER_MODEL_TARGET_ROLE not in stub.runtime_ctx.agent_role_models
    assert "seeded" not in stub.runtime_ctx.agent_role_models


@pytest.mark.asyncio
async def test_no_pick_resets_agent_role_models_to_config_base():
    stub = _model_stub({})
    await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    assert stub.runtime_ctx.agent_role_models == {}


@pytest.mark.asyncio
async def test_model_pick_applies_alongside_deny_list():
    rows = {
        ("u1", "bundle@1-0", agent_selection_key("main")): _selection_row_with_model(
            {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
            disabled={"tools": {"gmail": True}},
        ),
    }
    stub = _model_stub(rows)
    out_tools, _ = await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    assert "gmail" not in out_tools.allowed_plugins
    assert stub.runtime_ctx.agent_role_models[USER_MODEL_TARGET_ROLE]["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_model_store_error_fails_open_to_configured_role():
    stub = _workflow_stub(pg_pool=_BrokenPool(), bundle_props=_MODEL_PROPS)
    stub.runtime_ctx.agent_role_models = {"seeded": {"provider": "x", "model": "y"}}
    tool_cfg, skill_cfg = _tool_cfg(), AgentSkillConfig()
    out_tools, out_skills = await BaseWorkflow.apply_user_agent_selection(stub, tool_cfg, skill_cfg)
    assert out_tools is tool_cfg and out_skills is skill_cfg
    # No override was written; the router keeps resolving the configured spec.
    assert USER_MODEL_TARGET_ROLE not in (stub.runtime_ctx.agent_role_models or {})
