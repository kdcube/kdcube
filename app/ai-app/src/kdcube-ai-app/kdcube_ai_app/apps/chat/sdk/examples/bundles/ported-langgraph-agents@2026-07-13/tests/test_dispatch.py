# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""The dispatcher — ONE app, MANY agents (the core of this bundle).

`execute_core` resolves `agent_id` and runs the right vendored graph through its
OWN stream adapter. These tests assert:

  1. the AgentSpec REGISTRY holds both agents with distinct roles, schemas, and
     stream adapters, and the default is lg-solution;
  2. an OFFLINE SMOKE of the real dispatch: with agent_id="lg-solution" the
     research graph runs (its DEDICATED answer node surfaces as a step), with
     agent_id="lg-react" the create_react graph runs (NO retrieve/plan/answer
     nodes) — proving agent_id selects the right graph AND the right stream adapter;
     both stream and set a non-empty final_answer, degrading cleanly with no DB /
     API key;
  3. the two agents get DIFFERENT memory keys (per-agent isolation), and an unknown
     agent_id falls back to the default.

Real graphs are built + run (langgraph is a test dep), but every external edge is
forced offline: no provider key, an unreachable DB, no model service, no economics.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _module(rel: str):
    _n, module = load_dynamic_module_for_path(BUNDLE_ROOT / rel)
    return module


def _entrypoint_module():
    return _module("entrypoint.py")


class _FakeComm:
    def __init__(self) -> None:
        self.steps: list[tuple[str, str]] = []
        self.deltas: list[str] = []
        self.complete_data: dict | None = None
        self.completed = False

    async def step(self, *, step: str, status: str, **payload) -> None:
        self.steps.append((step, status))

    async def delta(self, *, text: str, index: int, marker: str = "answer", **kwargs) -> None:
        self.deltas.append(text)

    async def complete(self, *, data=None) -> None:
        self.completed = True
        self.complete_data = data


# ── 1. the registry ─────────────────────────────────────────────────────────

def test_registry_has_both_agents_with_distinct_specs() -> None:
    ep = _entrypoint_module()
    agents = ep.AGENTS
    assert set(agents.keys()) == {"lg-solution", "lg-react"}
    assert ep.DEFAULT_AGENT_ID == "lg-solution"

    sol, pre = agents["lg-solution"], agents["lg-react"]
    # Distinct answer roles.
    assert sol.role == "lg-solution.answer"
    assert pre.role == "lg-react.answer"
    # Distinct agent ids — the row-scope discriminator (there is no per-agent
    # schema; both agents share the one tenant/project schema, kept apart by the
    # agent_id column).
    assert sol.agent_id == "lg-solution"
    assert pre.agent_id == "lg-react"
    assert sol.agent_id != pre.agent_id
    # No spec carries a schema string — schema is derived from tenant/project at
    # build time, not baked per agent into the registry.
    assert not hasattr(sol, "schema")
    # DIFFERENT stream adapters (the teaching point).
    assert sol.stream is ep._stream_solution
    assert pre.stream is ep._stream_prebuilt
    assert sol.stream is not pre.stream


# ── 2. offline smoke of the real dispatch ───────────────────────────────────

def _make_offline_entrypoint(monkeypatch):
    from kdcube_ai_app.infra.service_hub.inventory import Config as InvConfig

    # Force both agents fully offline + deterministic.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nope:nope@127.0.0.1:1/none")

    ep_mod = _entrypoint_module()
    inst = ep_mod.LGPortedAgentsBundle(config=InvConfig(), pg_pool=None, redis=None)
    # No model service, no economics facade: the pure offline stub path.
    inst.models_service = None
    monkeypatch.setattr(inst, "search_model_service", lambda flow=None: None)
    # Keep the run focused on dispatch + the real graph + the real stream adapter:
    # no capabilities store, no Telegram delivery, no call-context binding infra.
    import contextlib

    async def _no_role_models(_self, _state, _agent_id):
        return {}

    async def _pass_through_delivery(_self, runner):
        return await runner()

    monkeypatch.setattr(ep_mod, "resolve_turn_role_models", _no_role_models)
    monkeypatch.setattr(ep_mod.telegram_ingress, "run_turn_with_delivery", _pass_through_delivery)
    monkeypatch.setattr(
        ep_mod, "bind_current_bundle_call_context_patch",
        lambda *_a, **_k: contextlib.nullcontext(),
    )
    return ep_mod, inst


def _run_turn(inst, agent_id: str, question: str, conv: str):
    async def _go():
        comm = _FakeComm()
        comm_ctx.set_comm(comm)
        state = {
            "external_events": [{"type": "chat.message", "text": question}],
            "tenant": "t", "project": "p", "user": "alice",
            "conversation_id": conv,
            "agent_id": agent_id,
        }
        out = await inst.execute_core(state=state, thread_id=conv, params={})
        return comm, out

    return asyncio.run(_go())


def test_lg_solution_dispatch_runs_the_research_graph(monkeypatch) -> None:
    _ep, inst = _make_offline_entrypoint(monkeypatch)
    comm, out = _run_turn(inst, "lg-solution", "What is a LangGraph checkpointer?", "conv-sol")

    # Streamed + a non-empty final answer.
    assert out["final_answer"]
    assert "".join(comm.deltas)
    assert comm.completed is True
    # The research graph's DEDICATED answer node surfaced as a step — the
    # lg-solution (dedicated-answer-node) stream adapter ran.
    step_names = {name for name, _status in comm.steps}
    assert "answer" in step_names


def test_lg_prebuilt_dispatch_runs_the_create_react_graph(monkeypatch) -> None:
    _ep, inst = _make_offline_entrypoint(monkeypatch)
    comm, out = _run_turn(inst, "lg-react", "What is 2 + 3?", "conv-pre")

    assert out["final_answer"]
    assert "".join(comm.deltas)
    assert comm.completed is True
    # The create_react graph has NO retrieve/plan/answer nodes — proving a
    # DIFFERENT graph + the looping-agent-node stream adapter ran.
    step_names = {name for name, _status in comm.steps}
    assert "answer" not in step_names
    assert "retrieve" not in step_names
    assert "plan" not in step_names


def test_both_agents_can_run_in_the_same_process(monkeypatch) -> None:
    _ep, inst = _make_offline_entrypoint(monkeypatch)
    _c1, out1 = _run_turn(inst, "lg-solution", "hello there", "c1")
    _c2, out2 = _run_turn(inst, "lg-react", "hello there", "c2")
    assert out1["final_answer"] and out2["final_answer"]
    # Scaled serving: NO in-process graph cache — the graph is rebuilt every turn,
    # so any worker can serve any turn. This negative assertion guards the invariant.
    assert not hasattr(inst, "_graphs")


# ── 3. isolation + fallback ─────────────────────────────────────────────────

def test_the_two_agents_get_different_memory_keys() -> None:
    ident = _module("platform/identity.py")
    state = {"tenant": "t", "project": "p", "user": "alice", "conversation_id": "c1"}
    sol = ident.turn_identity(state, agent_id="lg-solution")
    pre = ident.turn_identity(state, agent_id="lg-react")
    assert sol.user_id != pre.user_id
    assert sol.thread_id != pre.thread_id


def test_unknown_agent_id_falls_back_to_the_default(monkeypatch) -> None:
    _ep, inst = _make_offline_entrypoint(monkeypatch)
    # An unknown agent_id must run the default agent (lg-solution).
    comm, out = _run_turn(inst, "does-not-exist", "What is a checkpointer?", "conv-x")
    assert out["final_answer"]
    step_names = {name for name, _status in comm.steps}
    assert "answer" in step_names            # lg-solution's dedicated answer node
