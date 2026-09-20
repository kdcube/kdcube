# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""The per-turn model-pick seam (foreign_runtime/capabilities.py).

Offline: the selection store is a stub keyed exactly like the real one, so what
is proven is the seam's own contract — `resolve_turn_model_pick` returns the
USER's pick and nothing else, clamped to the agent's admin-declared list, with
`None` standing for "the user picked nothing" so a wrapped runtime's own
deployment default stays in charge of that case.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict

import pytest

import kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control as agent_capability_control
from kdcube_ai_app.apps.chat.sdk.solutions.foreign_runtime import capabilities


SUPPORTED = [
    {"model": "claude-opus-5", "provider": "anthropic", "label": "Opus 5"},
    {"model": "claude-sonnet-5", "provider": "anthropic", "label": "Sonnet 5"},
]


def _props(agent_id: str = "press", supported: Any = SUPPORTED) -> Dict[str, Any]:
    return {
        "surfaces": {
            "as_consumer": {
                "default_agent": agent_id,
                "agents": {
                    agent_id: {
                        "capability_provider": "simple_model_pick",
                        "capabilities": {
                            "models": {
                                "default": "claude-opus-5",
                                "supported": supported,
                            }
                        },
                    }
                },
            }
        }
    }


class _Store:
    """Records the LOAD key and returns one canned selection."""

    def __init__(self, selection: Dict[str, Any] | None):
        self.selection = selection
        self.calls: list[Dict[str, Any]] = []
        self.seed_calls: list[Dict[str, Any]] = []

    async def get_selection(self, **kwargs: Any) -> Dict[str, Any] | None:
        self.calls.append(dict(kwargs))
        return self.selection

    async def get_legacy_capability_seed(self, **kwargs: Any) -> Dict[str, Any] | None:
        self.seed_calls.append(dict(kwargs))
        if self.selection is None:
            return None
        return dict(self.selection.get("disabled") or {})


def _entrypoint(selection: Dict[str, Any] | None, props: Dict[str, Any] | None = None):
    store = _Store(selection)
    entrypoint = SimpleNamespace(
        pg_pool=object(),
        bundle_props=props if props is not None else _props(),
        _agent_selection_identity=lambda: {
            "tenant": "t1",
            "project": "p1",
            "user_id": "operator-1",
            "bundle_id": "press@2026-08-16",
        },
        _agent_selection_store=lambda identity: store,
    )
    return entrypoint, store


def _state() -> Dict[str, Any]:
    return {"conversation_id": "conv-1", "session_id": "sess-1"}


@pytest.fixture(autouse=True)
def _legacy_selection_as_card_projection(monkeypatch):
    async def _sync(_entrypoint, *, initial_disabled=None, **_kwargs):
        return {"projection": {"test_disabled": dict(initial_disabled or {})}}

    def _disabled(_catalog, projection):
        return dict(projection.get("test_disabled") or {})

    monkeypatch.setattr(agent_capability_control, "sync_agent_capability_projection", _sync)
    monkeypatch.setattr(agent_capability_control, "disabled_from_projection", _disabled)


def _resolve(entrypoint, state=None, agent_id="press"):
    return asyncio.run(
        capabilities.resolve_turn_model_pick(entrypoint, state or _state(), agent_id)
    )


def test_a_stored_pick_resolves_to_the_supported_row() -> None:
    entrypoint, store = _entrypoint(
        {"model": {"provider": "anthropic", "model": "claude-sonnet-5"}}
    )
    assert _resolve(entrypoint) == {"provider": "anthropic", "model": "claude-sonnet-5"}
    # The LOAD key is the wire op's key: user + app + agent + conversation.
    assert store.calls[-1]["user_id"] == "operator-1"
    assert store.calls[-1]["bundle_id"] == "press@2026-08-16"
    assert store.calls[-1]["agent_id"] == "press"
    assert store.calls[-1]["conversation_id"] == "conv-1"


def test_no_pick_is_none_not_the_declared_default() -> None:
    """The distinguishing contract: unlike the generic provider, this seam does
    NOT substitute `capabilities.models.default` — the app's own deployment
    default must be able to win that case."""
    for selection in ({}, {"model": None}, None):
        entrypoint, _store = _entrypoint(selection)
        assert _resolve(entrypoint) is None


def test_an_unsupported_pick_never_reaches_the_runtime() -> None:
    """The declared list is THE ceiling: a stale pick outside it resolves to
    None, so the run falls back rather than leaving the allowlist."""
    entrypoint, _store = _entrypoint(
        {"model": {"provider": "anthropic", "model": "claude-fable-5"}}
    )
    assert _resolve(entrypoint) is None


def test_a_narrowed_declaration_orphans_a_previously_valid_pick() -> None:
    entrypoint, _store = _entrypoint(
        {"model": {"provider": "anthropic", "model": "claude-sonnet-5"}},
        props=_props(supported=[SUPPORTED[0]]),
    )
    assert _resolve(entrypoint) is None


def test_an_undeclared_model_list_yields_no_pick() -> None:
    """An app that declares no `capabilities.models.supported` has no picker
    inventory at all — nothing can be picked, so nothing is resolved."""
    entrypoint, _store = _entrypoint(
        {"model": {"provider": "anthropic", "model": "claude-sonnet-5"}},
        props={"surfaces": {"as_consumer": {"agents": {"press": {}}}}},
    )
    assert _resolve(entrypoint) is None


def test_an_unknown_agent_id_falls_back_to_the_default_agent_block() -> None:
    """The chat client may drive the app's default agent under the generic
    `main` id; the declared block still resolves (agent_config_block's
    default_agent fallback), so the picker inventory and the turn agree."""
    entrypoint, store = _entrypoint(
        {"model": {"provider": "anthropic", "model": "claude-sonnet-5"}}
    )
    assert _resolve(entrypoint, agent_id="main") == {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
    }
    # ...and the selection is loaded under the id the TURN declared, which is
    # the same id the picker saved under.
    assert store.calls[-1]["agent_id"] == "main"


def test_fails_open_without_storage() -> None:
    entrypoint, _store = _entrypoint({"model": {"model": "claude-sonnet-5"}})
    entrypoint.pg_pool = None
    assert _resolve(entrypoint) is None


def test_fails_open_when_the_store_errors() -> None:
    entrypoint, _store = _entrypoint({"model": {"model": "claude-sonnet-5"}})

    def _boom(identity):
        raise RuntimeError("no store")

    entrypoint._agent_selection_store = _boom
    assert _resolve(entrypoint) is None


# ── the deny map (the picker's other half) ──────────────────────────────────

_SELECTION = {
    "model": {"provider": "anthropic", "model": "claude-sonnet-5"},
    "disabled": {
        "tools": {"web": True},
        "mcp": {"named_services": True, "press": ["commit_entry"]},
        "named_services": {"conv": True, "linkedin": ["object.action"]},
        "skills": ["press.review"],
        "subagents": True,
    },
}


def test_the_whole_deny_map_is_seeded_from_the_legacy_default_once() -> None:
    entrypoint, store = _entrypoint(_SELECTION)
    disabled = asyncio.run(
        capabilities.resolve_turn_selection_disabled(entrypoint, _state(), "press")
    )
    assert disabled["mcp"] == {"named_services": True, "press": ["commit_entry"]}
    assert disabled["named_services"] == {"conv": True, "linkedin": ["object.action"]}
    assert store.calls == []
    assert len(store.seed_calls) == 1
    assert store.seed_calls[0]["agent_id"] == "press"
    assert "conversation_id" not in store.seed_calls[0]


def test_each_category_resolves_to_its_own_real_key() -> None:
    entrypoint, _store = _entrypoint(_SELECTION)
    state = _state()
    assert asyncio.run(
        capabilities.resolve_turn_disabled_tools(entrypoint, state, "press")
    ) == {"web": True}
    assert asyncio.run(
        capabilities.resolve_turn_disabled_mcp(entrypoint, state, "press")
    ) == {"named_services": True, "press": ["commit_entry"]}
    assert asyncio.run(
        capabilities.resolve_turn_disabled_namespaces(entrypoint, state, "press")
    ) == {"conv": True, "linkedin": ["object.action"]}


def test_disabled_category_slices_without_a_second_read() -> None:
    disabled = _SELECTION["disabled"]
    assert capabilities.disabled_category(disabled, capabilities.DISABLED_MCP) == disabled["mcp"]
    assert capabilities.disabled_category(disabled, "nothing_here") == {}
    assert capabilities.disabled_category(None, capabilities.DISABLED_TOOLS) == {}
    # A malformed category reads as "nothing denied" rather than raising.
    assert capabilities.disabled_category({"tools": ["web"]}, capabilities.DISABLED_TOOLS) == {}


def test_declared_python_tool_is_ceiling_minus_user_opt_out() -> None:
    declared = [
        {
            "name": "turn_context",
            "kind": "python",
            "alias": "turn_context",
            "allowed": ["record_turn_summary"],
        },
        {"name": "press", "kind": "mcp", "alias": "press", "allowed": ["search"]},
    ]
    enabled = capabilities.declared_python_tool_enabled
    assert enabled(declared, {}, "record_turn_summary") is True
    assert enabled(declared, {"turn_context": ["record_turn_summary"]}, "record_turn_summary") is False
    assert enabled(declared, {"turn_context": True}, "record_turn_summary") is False
    assert enabled(declared, {}, "undeclared") is False


def test_nothing_picked_leaves_the_full_declared_inventory() -> None:
    for selection in ({}, {"disabled": {}}, None):
        entrypoint, _store = _entrypoint(selection)
        state = _state()
        assert asyncio.run(
            capabilities.resolve_turn_selection_disabled(entrypoint, state, "press")
        ) == {}
        assert asyncio.run(
            capabilities.resolve_turn_disabled_mcp(entrypoint, state, "press")
        ) == {}
        assert asyncio.run(
            capabilities.resolve_turn_disabled_namespaces(entrypoint, state, "press")
        ) == {}


def test_preference_storage_absence_does_not_bypass_the_card() -> None:
    entrypoint, _store = _entrypoint(_SELECTION)
    entrypoint.pg_pool = None
    assert asyncio.run(
        capabilities.resolve_turn_disabled_mcp(entrypoint, _state(), "press")
    ) == {}

    entrypoint, _store = _entrypoint(_SELECTION)

    def _boom(identity):
        raise RuntimeError("no store")

    entrypoint._agent_selection_store = _boom
    assert asyncio.run(
        capabilities.resolve_turn_disabled_namespaces(entrypoint, _state(), "press")
    ) == {}


def test_the_deny_map_fails_closed_when_the_card_is_unavailable(monkeypatch) -> None:
    entrypoint, _store = _entrypoint(_SELECTION)

    async def _unavailable(*_args, **_kwargs):
        raise RuntimeError("card unavailable")

    monkeypatch.setattr(
        agent_capability_control,
        "sync_agent_capability_projection",
        _unavailable,
    )
    monkeypatch.setattr(
        agent_capability_control,
        "deny_all_capabilities",
        lambda **_kwargs: {"mcp": {"press": True}},
    )

    assert asyncio.run(
        capabilities.resolve_turn_disabled_mcp(entrypoint, _state(), "press")
    ) == {"press": True}
