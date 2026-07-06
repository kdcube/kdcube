# SPDX-License-Identifier: MIT

"""UserAgentSelectionStore: merge-write semantics, clamping, round-trips."""

from __future__ import annotations

import json

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import agent_capabilities_catalog
from kdcube_ai_app.apps.chat.sdk.solutions.user_settings import (
    AGENT_SELECTION_SUBSYSTEM,
    UserAgentSelectionStore,
    agent_selection_key,
    merge_selection_patch,
)


# ── merge semantics (pure) ────────────────────────────────────────────────────


def test_merge_patch_partial_update_preserves_other_toggles():
    current = {"tools": {"gmail": True}, "mcp": {"knowledge": True}, "skills": ["public.a"]}
    merged = merge_selection_patch(current, {"named_services": {"task": True}})
    assert merged == {
        "tools": {"gmail": True},
        "mcp": {"knowledge": True},
        "named_services": {"task": True},
        "skills": ["public.a"],
    }


def test_merge_patch_false_reenables():
    current = {"tools": {"gmail": True, "web_tools": ["web_search"]}}
    merged = merge_selection_patch(current, {"tools": {"gmail": False}})
    assert merged == {"tools": {"web_tools": ["web_search"]}}


def test_merge_patch_name_list_replaces_previous_list():
    current = {"tools": {"web_tools": ["web_search"]}}
    merged = merge_selection_patch(current, {"tools": {"web_tools": ["web_fetch"]}})
    assert merged == {"tools": {"web_tools": ["web_fetch"]}}


def test_merge_patch_empty_list_reenables_group():
    current = {"tools": {"web_tools": ["web_search"]}}
    merged = merge_selection_patch(current, {"tools": {"web_tools": []}})
    assert merged == {}


def test_merge_patch_skill_toggles_via_mapping_and_list():
    current = {"skills": ["public.a", "public.b"]}
    merged = merge_selection_patch(current, {"skills": {"public.b": False, "public.c": True}})
    assert merged == {"skills": ["public.a", "public.c"]}

    replaced = merge_selection_patch(current, {"skills": ["public.z"]})
    assert replaced == {"skills": ["public.z"]}


# ── fake asyncpg pool ─────────────────────────────────────────────────────────


class _FakeConnection:
    def __init__(self, rows: dict):
        self._rows = rows
        self.executed: list[str] = []

    async def fetchrow(self, sql: str, *args):
        user_id, bundle_id, key, subsystem = args
        record = self._rows.get((user_id, bundle_id, key))
        if not record or record.get("subsystem") != subsystem:
            return None
        return {
            "value_json": record["value_json"],
            "created_at": record.get("created_at") or "",
            "updated_at": record.get("updated_at") or "",
        }

    async def execute(self, sql: str, *args):
        self.executed.append(sql.strip().split()[0].upper())
        if not args:
            return  # DDL from ensure_schema
        user_id, bundle_id, key, value_json, subsystem = args
        self._rows[(user_id, bundle_id, key)] = {
            "value_json": value_json,
            "subsystem": subsystem,
            "created_at": "now",
            "updated_at": "now",
        }


class _FakeAcquire:
    def __init__(self, con):
        self._con = con

    async def __aenter__(self):
        return self._con

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self):
        self.rows: dict = {}
        self.con = _FakeConnection(self.rows)

    def acquire(self):
        return _FakeAcquire(self.con)


def _store(pool) -> UserAgentSelectionStore:
    return UserAgentSelectionStore(pg_pool=pool, tenant="acme", project="demo")


# ── store behavior ────────────────────────────────────────────────────────────


def test_store_requires_pool():
    with pytest.raises(RuntimeError):
        UserAgentSelectionStore()._require_pool()


def test_schema_derived_from_scope():
    assert _store(_FakePool()).schema == "kdcube_acme_demo"


@pytest.mark.asyncio
async def test_get_selection_absent_row_is_empty_denylist():
    selection = await _store(_FakePool()).get_selection(
        user_id="u1", bundle_id="bundle@1-0", agent_id="main"
    )
    assert selection["disabled"] == {}
    assert selection["schema_version"] == 1


@pytest.mark.asyncio
async def test_set_selection_merge_write_round_trip():
    pool = _FakePool()
    store = _store(pool)

    first = await store.set_selection(
        user_id="u1", bundle_id="bundle@1-0", agent_id="main",
        patch={"tools": {"gmail": True}},
    )
    assert first["disabled"] == {"tools": {"gmail": True}}

    second = await store.set_selection(
        user_id="u1", bundle_id="bundle@1-0", agent_id="main",
        patch={"mcp": {"knowledge": True}},
    )
    # Partial update preserved the earlier toggle.
    assert second["disabled"] == {"tools": {"gmail": True}, "mcp": {"knowledge": True}}

    third = await store.set_selection(
        user_id="u1", bundle_id="bundle@1-0", agent_id="main",
        patch={"tools": {"gmail": False}},
    )
    assert third["disabled"] == {"mcp": {"knowledge": True}}

    stored = await store.get_selection(user_id="u1", bundle_id="bundle@1-0", agent_id="main")
    assert stored["disabled"] == {"mcp": {"knowledge": True}}

    # Row landed under the agents subsystem with the per-agent key.
    key = ("u1", "bundle@1-0", agent_selection_key("main"))
    assert pool.rows[key]["subsystem"] == AGENT_SELECTION_SUBSYSTEM
    value = json.loads(pool.rows[key]["value_json"])
    assert value["schema_version"] == 1


@pytest.mark.asyncio
async def test_set_selection_replace_swaps_record():
    pool = _FakePool()
    store = _store(pool)
    await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch={"tools": {"gmail": True}},
    )
    replaced = await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch={"skills": ["public.a"]},
        replace=True,
    )
    assert replaced["disabled"] == {"skills": ["public.a"]}


@pytest.mark.asyncio
async def test_set_selection_clamps_against_catalog():
    props = {
        "surfaces": {
            "as_consumer": {
                "agents": {
                    "main": {
                        "tools": [
                            {
                                "name": "io",
                                "kind": "python",
                                "module": "missing.io_mod",
                                "alias": "io_tools",
                                "allowed": ["tool_call"],
                            },
                            {
                                "name": "gmail",
                                "kind": "python",
                                "module": "missing.gmail_mod",
                                "alias": "gmail",
                                "allowed": ["search_gmail", "send_gmail"],
                            },
                        ],
                    },
                },
            },
        },
    }
    catalog = agent_capabilities_catalog(props, "main")
    store = _store(_FakePool())
    selection = await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch={
            "tools": {"io_tools": True, "gmail": ["send_gmail", "bogus"], "unknown": True},
            "mcp": {"ghost": True},
            "named_services": {"mem": True},
            "skills": ["public.not_in_inventory"],
        },
        catalog=catalog,
    )
    # System alias stripped, out-of-inventory ids rejected, names intersected.
    assert selection["disabled"] == {"tools": {"gmail": ["send_gmail"]}}


@pytest.mark.asyncio
async def test_selection_rows_are_per_agent():
    pool = _FakePool()
    store = _store(pool)
    await store.set_selection(user_id="u1", bundle_id="b", agent_id="main", patch={"tools": {"a": True}})
    await store.set_selection(user_id="u1", bundle_id="b", agent_id="helper", patch={"tools": {"b": True}})

    main = await store.get_selection(user_id="u1", bundle_id="b", agent_id="main")
    helper = await store.get_selection(user_id="u1", bundle_id="b", agent_id="helper")
    assert main["disabled"] == {"tools": {"a": True}}
    assert helper["disabled"] == {"tools": {"b": True}}


# ── model pick in the same record ─────────────────────────────────────────────

_MODEL_CATALOG = {
    # The real op always passes the FULL live inventory; the clamp keeps only
    # toggles inside it, so the fixture carries the toggled entries too.
    "tools": [
        {"alias": "gmail", "name": "gmail", "kind": "python", "system": False,
         "tools": [{"name": "search_gmail", "description": ""}]},
    ],
    "mcp": [{"server_id": "knowledge", "alias": "knowledge", "name": "knowledge", "tools": ["*"]}],
    "named_services": [],
    "skills": [],
    "supported_models": [
        {"model": "claude-sonnet-4-6", "provider": "anthropic", "label": "Sonnet 4.6"},
        {"model": "claude-haiku-4-5-20251001", "provider": "anthropic", "label": "Haiku 4.5"},
    ],
}


@pytest.mark.asyncio
async def test_model_pick_round_trip_preserves_toggles():
    store = _store(_FakePool())
    await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch={"tools": {"gmail": True}},
    )
    picked = await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch=None,
        model={"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
        catalog=_MODEL_CATALOG,
    )
    # The pick lands; the earlier toggle survives untouched.
    assert picked["model"] == {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}
    assert picked["disabled"] == {"tools": {"gmail": True}}

    stored = await store.get_selection(user_id="u1", bundle_id="b", agent_id="main")
    assert stored["model"]["model"] == "claude-haiku-4-5-20251001"

    # A later toggle-only write keeps the pick.
    toggled = await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch={"mcp": {"knowledge": True}},
        catalog=None,
    )
    assert toggled["model"]["model"] == "claude-haiku-4-5-20251001"
    assert toggled["disabled"] == {"tools": {"gmail": True}, "mcp": {"knowledge": True}}


@pytest.mark.asyncio
async def test_model_pick_clamped_to_supported_list():
    store = _store(_FakePool())
    await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch=None,
        model={"provider": "anthropic", "model": "claude-sonnet-4-6"},
        catalog=_MODEL_CATALOG,
    )
    # An out-of-list pick is ignored: the stored pick stays.
    kept = await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch=None,
        model={"provider": "openai", "model": "gpt-4o"},
        catalog=_MODEL_CATALOG,
    )
    assert kept["model"] == {"provider": "anthropic", "model": "claude-sonnet-4-6"}


@pytest.mark.asyncio
async def test_model_none_clears_back_to_default():
    store = _store(_FakePool())
    await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch=None,
        model={"provider": "anthropic", "model": "claude-sonnet-4-6"},
        catalog=_MODEL_CATALOG,
    )
    cleared = await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch=None,
        model=None,
        catalog=_MODEL_CATALOG,
    )
    assert cleared["model"] is None
    stored = await store.get_selection(user_id="u1", bundle_id="b", agent_id="main")
    assert stored["model"] is None
