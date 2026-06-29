"""Identity-family READ aggregation: reads scope to a user_id set, writes stay single-actor."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.context.memory.models import (
    MemoryScope,
    MemorySearchRequest,
)
from kdcube_ai_app.apps.chat.sdk.context.memory.store import (
    EVENT_TABLE,
    MEMORY_TABLE,
    USER_BUNDLE_PROPS_TABLE,
    UserMemoryStore,
    _normalize_user_ids,
    _user_scope_value,
    normalize_memory_scope_pref,
)


_NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)


def _mem_row(memory_id: str, user_id: str) -> dict:
    return {
        "id": memory_id,
        "tenant": "t",
        "project": "p",
        "user_id": user_id,
        "bundle_id": "b@1",
        "canonical_key": f"ck_{memory_id}",
        "memory": f"memory of {user_id}",
        "context": "",
        "kind": "note",
        "status": "active",
        "visibility": "user",
        "visible_to_user": True,
        "labels": [],
        "keywords": [],
        "tier": 2,
        "pinned": False,
        "search_text": f"memory of {user_id}",
        "confidence_score": 0.8,
        "importance_score": 0.6,
        "freshness_score": 1.0,
        "salience_score": 0.8,
        "confirmation_rate": 1.0,
        "evidence_count": 1,
        "update_count": 1,
        "confirmation_count": 0,
        "contradiction_count": 0,
        "positive_weight": 0.5,
        "negative_weight": 0.0,
        "created_at": _NOW,
        "updated_at": _NOW,
        "last_event_at": _NOW,
        "last_confirmed_at": None,
        "source": {},
        "metadata": {},
        "revision": 1,
        "merged_into_id": None,
    }


class _FakeCon:
    """Applies only the user_id scope predicate the read methods build.

    The fake honors the bound user-scope value at $3 (a single id or a list for
    ANY) against an in-memory set of memory rows, which is exactly what the
    identity-family change drives. Other predicates are treated as pass-through.
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.last_sql = ""
        self.last_args: tuple = ()

    def _user_match(self, sql: str, args: tuple, row: dict) -> bool:
        user_value = args[2]
        if "ANY($3" in sql:
            return row["user_id"] in set(user_value)
        return row["user_id"] == user_value

    async def fetchrow(self, sql: str, *args):
        self.last_sql, self.last_args = sql, args
        mem_id = args[3] if len(args) > 3 else None
        for row in self.rows:
            if mem_id is not None and row["id"] != mem_id:
                continue
            if self._user_match(sql, args, row):
                return dict(row)
        return None

    async def fetch(self, sql: str, *args):
        self.last_sql, self.last_args = sql, args
        return [dict(row) for row in self.rows if self._user_match(sql, args, row)]

    async def fetchval(self, sql: str, *args):
        self.last_sql, self.last_args = sql, args
        return len([row for row in self.rows if self._user_match(sql, args, row)])


class _FakePool:
    def __init__(self, con: _FakeCon) -> None:
        self._con = con

    def acquire(self):
        con = self._con

        class _Acq:
            async def __aenter__(self_inner):
                return con

            async def __aexit__(self_inner, *exc):
                return False

        return _Acq()


class _ReadStore(UserMemoryStore):
    def __init__(self, rows: list[dict]) -> None:
        self.con = _FakeCon(rows)
        self.schema = "kdcube"

    def _require_pool(self):
        return _FakePool(self.con)

    # Skip the bundle scope_filter SQL fragment (not under test here).
    def _append_scope_filter(self, *, where, args, scope, scope_filter, table_alias=""):
        return None


_ACTOR = "02e53484-actor"
_LINKED = "telegram_434804821"
_SCOPE = MemoryScope(tenant="t", project="p", user_id=_ACTOR, bundle_id="b@1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_normalize_user_ids_dedups_and_drops_empty() -> None:
    assert _normalize_user_ids(None) is None
    assert _normalize_user_ids([]) is None
    assert _normalize_user_ids(["", "  "]) is None
    assert _normalize_user_ids(["a", "a", " b ", ""]) == ["a", "b"]


def test_user_scope_value_picks_single_or_set() -> None:
    assert _user_scope_value("actor", None) == ("actor", False)
    assert _user_scope_value("actor", ["actor"]) == (["actor"], True)
    assert _user_scope_value("actor", ["actor", "linked"]) == (["actor", "linked"], True)


def test_identity_family_result_payload_accepts_top_level_payload() -> None:
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
        _memory_identity_family_result_payload,
    )

    payload = {"ok": True, "memory_user_ids": [_ACTOR, _LINKED]}
    assert _memory_identity_family_result_payload(payload) is payload


def test_identity_family_result_payload_unwraps_operation_result() -> None:
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
        _memory_identity_family_result_payload,
    )

    payload = {"ok": True, "schema": "connection_hub.identity_family.v1", "memory_user_ids": [_ACTOR, _LINKED]}
    assert _memory_identity_family_result_payload({"ok": True, "result": payload}) == payload


def test_identity_family_result_payload_unwraps_operation_alias_result() -> None:
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
        _memory_identity_family_result_payload,
    )

    payload = {"ok": True, "schema": "connection_hub.identity_family.v1", "memory_user_ids": [_ACTOR, _LINKED]}
    wrapped = {
        "status": "ok",
        "tenant": "demo-tenant",
        "project": "demo-project",
        "bundle_id": "connection-hub@1-0",
        "identity_family_resolve": payload,
    }
    assert _memory_identity_family_result_payload(wrapped) == payload


def test_identity_family_result_payload_unwraps_json_body() -> None:
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
        _memory_identity_family_result_payload,
    )

    payload = '{"ok": true, "memory_user_ids": ["02e53484-actor", "telegram_434804821"]}'
    assert _memory_identity_family_result_payload({"body": payload})["memory_user_ids"] == [_ACTOR, _LINKED]


# ---------------------------------------------------------------------------
# Store reads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_aggregates_across_family() -> None:
    rows = [_mem_row("mem_a", _ACTOR), _mem_row("mem_b", _LINKED), _mem_row("mem_c", "stranger")]
    store = _ReadStore(rows)
    # Family of two -> sees both their memories, not the stranger's.
    n = await store.count_memories(scope=_SCOPE, user_ids=[_ACTOR, _LINKED])
    assert n == 2
    assert "ANY($3" in store.con.last_sql
    assert store.con.last_args[2] == [_ACTOR, _LINKED]


@pytest.mark.asyncio
async def test_count_single_actor_when_no_family() -> None:
    rows = [_mem_row("mem_a", _ACTOR), _mem_row("mem_b", _LINKED)]
    store = _ReadStore(rows)
    n = await store.count_memories(scope=_SCOPE, user_ids=None)
    assert n == 1  # only the actor's own
    assert "ANY($3" not in store.con.last_sql
    assert store.con.last_args[2] == _ACTOR


@pytest.mark.asyncio
async def test_search_candidates_aggregate_across_family() -> None:
    rows = [_mem_row("mem_a", _ACTOR), _mem_row("mem_b", _LINKED), _mem_row("mem_c", "stranger")]
    store = _ReadStore(rows)
    request = MemorySearchRequest(scope=_SCOPE, mode="recent", user_ids=[_ACTOR, _LINKED])
    candidates = await store._fetch_candidates(request)
    owners = {c["user_id"] for c in candidates}
    assert owners == {_ACTOR, _LINKED}
    assert "ANY($3" in store.con.last_sql


@pytest.mark.asyncio
async def test_get_memory_by_id_within_family() -> None:
    rows = [_mem_row("mem_b", _LINKED)]
    store = _ReadStore(rows)
    # The by-id read resolves a family member's memory when the family is given.
    found = await store.get_memory(scope=_SCOPE, memory_id="mem_b", user_ids=[_ACTOR, _LINKED])
    assert found is not None and found.id == "mem_b"
    # Without the family, the actor cannot see the linked member's record.
    missing = await store.get_memory(scope=_SCOPE, memory_id="mem_b", user_ids=None)
    assert missing is None


# ---------------------------------------------------------------------------
# memory_scope preference (mirrors memory_enabled storage)
# ---------------------------------------------------------------------------

def test_normalize_memory_scope_pref_defaults_family() -> None:
    assert normalize_memory_scope_pref(None) == "family"
    assert normalize_memory_scope_pref("") == "family"
    assert normalize_memory_scope_pref("nonsense") == "family"
    assert normalize_memory_scope_pref("FAMILY") == "family"
    assert normalize_memory_scope_pref("channel") == "channel"


class _PrefsCon:
    """Fake connection for the user_bundle_props preferences row (one row)."""

    def __init__(self) -> None:
        self.value_json = None

    def acquire(self):
        con = self

        class _Acq:
            async def __aenter__(self_inner):
                return con

            async def __aexit__(self_inner, *exc):
                return False

        return _Acq()

    async def fetchrow(self, sql: str, *args):
        s = " ".join(sql.split())
        now = datetime(2026, 6, 28, tzinfo=timezone.utc)
        if "INSERT INTO" in s and USER_BUNDLE_PROPS_TABLE in s:
            self.value_json = args[3]  # value_json jsonb string
            return {"value_json": self.value_json, "created_at": now, "updated_at": now}
        if "SELECT value_json" in s and USER_BUNDLE_PROPS_TABLE in s:
            if self.value_json is None:
                return None
            return {"value_json": self.value_json, "created_at": now, "updated_at": now}
        raise AssertionError(f"unexpected prefs SQL: {s[:100]}")


class _PrefsStore(UserMemoryStore):
    def __init__(self) -> None:
        self.con = _PrefsCon()
        self.schema = "kdcube"

    def _require_pool(self):
        return self.con


@pytest.mark.asyncio
async def test_memory_scope_pref_default_is_family() -> None:
    store = _PrefsStore()
    prefs = await store.get_user_preferences(scope=_SCOPE)
    # Absent row -> default family (aggregate).
    assert prefs["memory_scope"] == "family"
    assert prefs["memory_enabled"] is True


@pytest.mark.asyncio
async def test_memory_scope_pref_round_trips_like_memory_enabled() -> None:
    store = _PrefsStore()
    # Set scope to channel without touching memory_enabled.
    saved = await store.set_user_preferences(scope=_SCOPE, memory_scope="channel")
    assert saved["memory_scope"] == "channel"
    assert saved["memory_enabled"] is True
    # Persisted + re-read.
    reread = await store.get_user_preferences(scope=_SCOPE)
    assert reread["memory_scope"] == "channel"
    # Toggling memory_enabled preserves the scope (merge, no clobber).
    saved2 = await store.set_user_preferences(scope=_SCOPE, memory_enabled=False)
    assert saved2["memory_enabled"] is False
    assert saved2["memory_scope"] == "channel"


# ---------------------------------------------------------------------------
# Named-service reads honor the injected family resolver
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_named_service_search_aggregates_with_family_factory() -> None:
    from kdcube_ai_app.apps.chat.sdk.context.memory.named_service import (
        make_memory_named_service_provider,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
        NamedServiceContext,
        NamedServiceRequest,
    )

    rows = [_mem_row("mem_a", _ACTOR), _mem_row("mem_b", _LINKED), _mem_row("mem_c", "stranger")]
    store = _ReadStore(rows)

    async def _family(ctx):
        return [_ACTOR, _LINKED]

    provider = make_memory_named_service_provider(
        store_factory=lambda ctx: store,
        scope_factory=lambda ctx: _SCOPE,
        bundle_id="b@1",
        embedding_enabled=False,
        ensure_schema=False,
        read_user_ids_factory=_family,
    )
    ctx = NamedServiceContext(tenant="t", project="p", user_id=_ACTOR)
    response = await provider.object_search(
        ctx,
        NamedServiceRequest(operation="object.search", namespace="mem", filters={"mode": "recent"}, limit=10),
    )
    assert response.ok is True
    # The mem search aggregated across the family, not just the actor.
    assert "ANY($3" in store.con.last_sql
    assert set(store.con.last_args[2]) == {_ACTOR, _LINKED}


@pytest.mark.asyncio
async def test_named_service_search_single_actor_without_factory() -> None:
    from kdcube_ai_app.apps.chat.sdk.context.memory.named_service import (
        make_memory_named_service_provider,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
        NamedServiceContext,
        NamedServiceRequest,
    )

    rows = [_mem_row("mem_a", _ACTOR), _mem_row("mem_b", _LINKED)]
    store = _ReadStore(rows)
    provider = make_memory_named_service_provider(
        store_factory=lambda ctx: store,
        scope_factory=lambda ctx: _SCOPE,
        bundle_id="b@1",
        embedding_enabled=False,
        ensure_schema=False,
    )
    ctx = NamedServiceContext(tenant="t", project="p", user_id=_ACTOR)
    await provider.object_search(
        ctx,
        NamedServiceRequest(operation="object.search", namespace="mem", filters={"mode": "recent"}, limit=10),
    )
    # No factory -> single actor (no aggregation).
    assert "ANY($3" not in store.con.last_sql
    assert store.con.last_args[2] == _ACTOR
