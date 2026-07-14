# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""The non-hosted → hosted STORAGE transition (canonical KDCube schema model).

Each vendored agent keeps its own store (lg-solution: memory + KB + checkpointer;
lg-react: checkpointer) in its OWN Postgres. Hosted by KDCube, each agent's store
is routed onto KDCube's SHARED Postgres into the ONE per-tenant/project schema
`kdcube_{tenant}_{project}` (the same schema `conv_index` / `UserMemoryStore` use),
in bundle-prefixed tables; rows are separated by the scope columns
(tenant, project, bundle_id, agent_id, user_id) — NOT by a per-agent schema. The
ONLY selection is the injection point — no runtime toggle:

  1. pg_pool present -> KDCube shared Postgres DSN + tenant/project schema (HOSTED)
  2. else            -> the agent's own DATABASE_URL                     (LOCAL / poc)
  3. DB unreachable  -> callers degrade (empty recall + MemorySaver)     (OFFLINE)

These offline tests assert the selection (the conn string the store + checkpointer
connect through), the ONE tenant/project schema, that NO per-agent schema is ever
constructed, and the statelessness invariant — no DB, no API key, no network.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]

OWN_URL = "postgresql://own_user:own_pw@own-host:5432/lg_solution"
SCHEMA = "kdcube_demo_tenant_demo_project"


def _module(rel: str):
    _n, module = load_dynamic_module_for_path(BUNDLE_ROOT / rel)
    return module


def _pg_target():
    return _module("platform/pg_target.py")


# -- one schema per tenant/project (NOT per agent) ----------------------------

def test_schema_for_scope_is_per_tenant_project_and_libpq_safe() -> None:
    mod = _pg_target()
    # kdcube_{tenant}_{project}, unsafe chars folded — the SAME schema for BOTH
    # agents (they are separated by the agent_id column, not the schema).
    assert mod.schema_for_scope("demo-tenant", "demo-project") == "kdcube_demo_tenant_demo_project"
    assert mod.schema_for_scope("Acme Inc", "Proj.1") == "kdcube_acme_inc_proj_1"
    # Already-prefixed input is not doubled.
    assert mod.schema_for_scope("kdcube_t", "p") == "kdcube_t_p"


def test_no_per_agent_schema_is_ever_constructed() -> None:
    """The negative assertion: the per-agent schema scheme is gone. No
    `agent_schema` symbol, no `SCHEMA_PREFIX`, and no `__lg_`/`__` schema string
    is built anywhere in the storage seam."""
    mod = _pg_target()
    assert not hasattr(mod, "agent_schema")
    assert not hasattr(mod, "SCHEMA_PREFIX")
    src = inspect.getsource(mod)
    assert "agent_schema" not in src
    assert "__lg_" not in src
    assert "CREATE EXTENSION" not in src


# -- selection: HOSTED (pg_pool present) -> KDCube shared Postgres + schema ----

def test_hosted_targets_kdcube_pg_in_the_agents_own_schema(monkeypatch) -> None:
    import kdcube_ai_app.apps.chat.sdk.config as sdk_config

    class _Settings:
        PGHOST = "kdcube-shared-host"
        PGPORT = 6543
        PGUSER = "kdcube"
        PGPASSWORD = "kdcube-secret"
        PGDATABASE = "kdcube"
        PGSSL = False
        PGSSL_MODE = None
        PGSSL_ROOT_CERT = None

    monkeypatch.setattr(sdk_config, "get_settings", lambda: _Settings())

    mod = _pg_target()
    schema = mod.schema_for_scope("demo-tenant", "demo-project")
    target = mod.resolve_solution_pg(pg_pool=object(), own_database_url=OWN_URL, schema=schema)

    assert target.hosted is True
    assert target.schema == schema
    assert "kdcube-shared-host" in target.database_url
    assert "own-host" not in target.database_url
    assert "search_path" in target.database_url
    assert schema in target.database_url
    # base_url (for CREATE SCHEMA) carries NO search_path options.
    assert "search_path" not in target.base_url
    assert "kdcube-shared-host" in target.base_url


def test_hosted_carries_ssl_from_platform_settings(monkeypatch) -> None:
    import kdcube_ai_app.apps.chat.sdk.config as sdk_config

    class _Settings:
        PGHOST = "kdcube-shared-host"
        PGPORT = 5432
        PGUSER = "kdcube"
        PGPASSWORD = "pw"
        PGDATABASE = "kdcube"
        PGSSL = True
        PGSSL_MODE = "require"
        PGSSL_ROOT_CERT = None

    monkeypatch.setattr(sdk_config, "get_settings", lambda: _Settings())
    mod = _pg_target()
    target = mod.resolve_solution_pg(
        pg_pool=object(), own_database_url=OWN_URL, schema=mod.schema_for_scope("demo-tenant", "demo-project")
    )
    assert "sslmode=require" in target.database_url


# -- selection: LOCAL (no pg_pool) -> the agent's own DATABASE_URL -------------

def test_standalone_falls_back_to_own_database_url() -> None:
    mod = _pg_target()
    target = mod.resolve_solution_pg(
        pg_pool=None, own_database_url=OWN_URL, schema=mod.schema_for_scope("demo-tenant", "demo-project")
    )
    assert target.hosted is False
    assert target.database_url == OWN_URL
    assert target.schema is None
    assert target.base_url is None


def test_hosted_falls_back_when_settings_unavailable(monkeypatch) -> None:
    import kdcube_ai_app.apps.chat.sdk.config as sdk_config

    def _boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(sdk_config, "get_settings", _boom)
    mod = _pg_target()
    target = mod.resolve_solution_pg(
        pg_pool=object(), own_database_url=OWN_URL, schema=mod.schema_for_scope("demo-tenant", "demo-project")
    )
    assert target.hosted is False
    assert target.database_url == OWN_URL


# -- the search_path helper (pure string; what every store connects through) --

def test_with_search_path_is_libpq_safe() -> None:
    pg = _module("solution/lg_solution/_pg.py")
    out = pg.with_search_path("postgresql://u:p@h:5432/db", SCHEMA)
    assert f"options=-c%20search_path%3D{SCHEMA}%2Cpublic" in out
    assert "+" not in out.split("options=")[1]
    out2 = pg.with_search_path("postgresql://u:p@h/db?sslmode=require", "s")
    assert "sslmode=require" in out2 and "search_path" in out2


# -- deps carry the resolved store DSN (lg-solution) --------------------------

def test_deps_read_resolved_config() -> None:
    deps_mod = _module("solution/lg_solution/deps.py")
    hosted_url = "postgresql://kdcube:pw@kdcube-shared-host:6543/kdcube?options=x"
    cfg = deps_mod.Config(openai_api_key=None, database_url=hosted_url)
    deps = deps_mod.build_deps(config=cfg)
    assert deps.config.database_url == hosted_url
    assert deps.memory.config.database_url == hosted_url
    assert deps.knowledge.config.database_url == hosted_url


# -- statelessness: nothing per-user/per-conversation lives on the instance ----

def _make_entrypoint():
    from kdcube_ai_app.infra.service_hub.inventory import Config as InvConfig

    ep_mod = _module("entrypoint.py")
    inst = ep_mod.LGPortedAgentsBundle(config=InvConfig(), pg_pool=None, redis=None)
    return ep_mod, inst


def test_entrypoint_holds_no_per_user_state_across_turns(monkeypatch) -> None:
    ep_mod, inst = _make_entrypoint()

    class _StubGraph:
        pass

    build_calls: list[str] = []

    async def _fake_build_graph(agent_id, *, disabled_tools=None):
        # Built fresh every turn (scaled serving) — nothing cached on the instance.
        build_calls.append(agent_id)
        return _StubGraph()

    async def _fake_stream(graph, inputs, run_config, **kw):
        # Prove the turn is driven by per-turn LOCALS (identity in run_config), not
        # by anything stored on the instance.
        assert run_config["configurable"]["thread_id"]
        return "stub answer"

    async def _fake_role_models(_self, _state, _agent_id):
        return {}

    async def _fake_delivery(_self, runner):
        return await runner()

    monkeypatch.setattr(inst, "_build_graph", _fake_build_graph)
    # Both stream adapters resolve `stream_graph_turn`/`stream_react_turn` from
    # module globals at call time, so patching them here reaches both agents.
    monkeypatch.setattr(ep_mod, "stream_graph_turn", _fake_stream)
    monkeypatch.setattr(ep_mod, "stream_react_turn", _fake_stream)
    monkeypatch.setattr(ep_mod, "resolve_turn_role_models", _fake_role_models)
    monkeypatch.setattr(ep_mod.telegram_ingress, "run_turn_with_delivery", _fake_delivery)
    monkeypatch.setattr(
        ep_mod, "bind_current_bundle_call_context_patch",
        lambda *_a, **_k: contextlib.nullcontext(),
    )

    keys_before = set(inst.__dict__.keys())

    def _run_turn(agent: str, user: str, conv: str):
        state = {
            "external_events": [{"type": "chat.message", "text": "hi"}],
            "user": user,
            "conversation_id": conv,
            "agent_id": agent,
        }
        return asyncio.run(inst.execute_core(state=state, thread_id=conv, params={}))

    # Two users, two agents.
    out_a = _run_turn("lg-solution", "alice", "conv-A")
    out_b = _run_turn("lg-react", "bob", "conv-B")
    assert out_a["final_answer"] == "stub answer"
    assert out_b["final_answer"] == "stub answer"

    # Scaled serving: the graph is rebuilt EVERY turn (no in-process cache), and
    # NOTHING new lands on the instance across turns — no per-user/conversation state.
    new_keys = set(inst.__dict__.keys()) - keys_before
    assert new_keys == set(), new_keys
    assert build_calls == ["lg-solution", "lg-react"]  # one build per turn
    assert not hasattr(inst, "_graphs")                # no graph cache at all

    leaked = [
        k for k, v in inst.__dict__.items()
        if isinstance(v, str) and v in {"alice", "bob", "conv-A", "conv-B"}
    ]
    assert not leaked, leaked
