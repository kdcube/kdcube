# SPDX-License-Identifier: MIT

"""BaseWorkflow.apply_user_agent_selection: fail-open + narrowing wiring."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import AgentSkillConfig
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import AgentToolConfig
from kdcube_ai_app.apps.chat.sdk.runtime.user_selection_store import agent_selection_key
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
    )
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
