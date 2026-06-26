from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.context.memory.models import MemoryScope
from kdcube_ai_app.apps.chat.sdk.context.memory.store import (
    EVENT_TABLE,
    MEMORY_TABLE,
    UserMemoryStore,
    rederive_fields_from_events,
)


_BASE = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)


def _event(idx: int, *, event_type: str, text: str, context: str = "", confidence: float = 0.6, importance: float = 0.5) -> dict:
    return {
        "id": f"mev_{idx}",
        "event_type": event_type,
        "signal_text": text,
        "context": context,
        "confidence": confidence,
        "importance": importance,
        "created_at": _BASE + timedelta(minutes=idx),
    }


# ---------------------------------------------------------------------------
# Pure re-derivation fold
# ---------------------------------------------------------------------------

def test_rederive_canonical_is_latest_authoritative_edit() -> None:
    events = [
        _event(0, event_type="agent_observation", text="Cities: Aachen, Bonn"),
        _event(1, event_type="agent_refinement", text="Cities: Aachen, Bonn, Rotterdam"),
        _event(2, event_type="agent_observation", text="ignored observation text"),
    ]
    derived = rederive_fields_from_events(events)
    # The most recent authoritative edit wins; a later passive observation does
    # not clobber it.
    assert derived["memory"] == "Cities: Aachen, Bonn, Rotterdam"
    assert derived["evidence_count"] == 3
    assert derived["update_count"] == 3


def test_rederive_after_dropping_authoritative_edit_falls_back() -> None:
    # Full tail with a refinement, then the same tail minus that refinement.
    full = [
        _event(0, event_type="agent_observation", text="Cities: Aachen, Bonn"),
        _event(1, event_type="agent_refinement", text="Cities: Aachen, Bonn, Rotterdam"),
    ]
    assert rederive_fields_from_events(full)["memory"] == "Cities: Aachen, Bonn, Rotterdam"

    dropped = [full[0]]  # the refinement removed
    out = rederive_fields_from_events(dropped)
    # Canonical re-derives to the surviving (seed) observation; counts shrink.
    assert out["memory"] == "Cities: Aachen, Bonn"
    assert out["evidence_count"] == 1
    assert out["update_count"] == 1


def test_rederive_empty_uses_fallback() -> None:
    out = rederive_fields_from_events([], fallback_memory="kept note")
    assert out["memory"] == "kept note"
    assert out["evidence_count"] == 0


# ---------------------------------------------------------------------------
# Store apply/drop orchestration against a fake connection
# ---------------------------------------------------------------------------

class _FakeCon:
    """Minimal asyncpg-connection stand-in for the evidence SQL paths."""

    def __init__(self, memory_row: dict, events: list[dict]) -> None:
        self.memory_row = memory_row
        self.events = events  # oldest-first

    def transaction(self):
        con = self

        class _Tx:
            async def __aenter__(self_inner):
                return con

            async def __aexit__(self_inner, *exc):
                return False

        return _Tx()

    async def fetchrow(self, sql: str, *args):
        s = " ".join(sql.split())
        if f"SELECT id FROM" in s and EVENT_TABLE in s and "AND id=$2" in s:
            event_id = args[1]
            for ev in self.events:
                if ev["id"] == event_id:
                    return {"id": ev["id"]}
            return None
        if f"SELECT * FROM" in s and EVENT_TABLE in s and "AND id=$2" in s:
            event_id = args[1]
            for ev in self.events:
                if ev["id"] == event_id:
                    return dict(ev, memory_id=self.memory_row["id"], labels=[], keywords=[], source={}, metadata={})
            return None
        if f"UPDATE" in s and MEMORY_TABLE in s and "RETURNING *" in s:
            # The re-derive UPDATE: args are (id, memory, context, search_text, ...)
            self.memory_row["memory"] = args[1]
            self.memory_row["context"] = args[2]
            self.memory_row["evidence_count"] = args[4]
            self.memory_row["update_count"] = args[5]
            self.memory_row["revision"] = int(self.memory_row.get("revision") or 1) + 1
            return dict(self.memory_row)
        if "INSERT INTO" in s and EVENT_TABLE in s and "RETURNING id" in s:
            # _insert_event: positional args mirror the INSERT column order.
            new = {
                "id": args[0],
                "event_type": args[8],
                "signal_text": args[9],
                "context": args[10],
                "confidence": args[12],
                "importance": args[13],
                "created_at": args[19],
            }
            self.events.append(new)
            return {"id": new["id"]}
        raise AssertionError(f"unexpected fetchrow SQL: {s[:120]}")

    async def fetch(self, sql: str, *args):
        s = " ".join(sql.split())
        if "COUNT(*)" in s and EVENT_TABLE in s:
            return [{"n": len(self.events)}]
        if "SELECT event_type, signal_text" in s and EVENT_TABLE in s:
            return [dict(ev) for ev in sorted(self.events, key=lambda e: (e["created_at"], e["id"]))]
        raise AssertionError(f"unexpected fetch SQL: {s[:120]}")

    async def execute(self, sql: str, *args):
        s = " ".join(sql.split())
        if "DELETE FROM" in s and EVENT_TABLE in s and "AND id=$2" in s:
            event_id = args[1]
            self.events = [ev for ev in self.events if ev["id"] != event_id]
            return "DELETE 1"
        if "INSERT INTO" in s:  # alias upserts etc. — ignore
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute SQL: {s[:120]}")


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


class _EvidenceStore(UserMemoryStore):
    """UserMemoryStore wired to a fake connection for evidence ops."""

    def __init__(self, memory_row: dict, events: list[dict]) -> None:
        self._con = _FakeCon(memory_row, events)
        self.schema = "kdcube"

    def _require_pool(self):
        return _FakePool(self._con)

    async def _fetch_memory_for_update_scoped(self, con, *, scope, memory_id, visible_to_user=None, scope_filter="current_bundle"):
        if str(self._con.memory_row.get("id")) != str(memory_id):
            return None
        return dict(self._con.memory_row)


def _memory_row(memory_id: str = "mem_t") -> dict:
    return {
        "id": memory_id,
        "tenant": "t",
        "project": "p",
        "user_id": "u",
        "bundle_id": "b@1",
        "memory": "Cities: Aachen, Bonn",
        "context": "",
        "kind": "note",
        "status": "active",
        "visibility": "user",
        "visible_to_user": True,
        "labels": ["travel"],
        "keywords": [],
        "tier": 2,
        "pinned": False,
        "confidence_score": 0.8,
        "importance_score": 0.6,
        "freshness_score": 1.0,
        "salience_score": 0.8,
        "confirmation_rate": 1.0,
        "evidence_count": 2,
        "update_count": 2,
        "confirmation_count": 0,
        "contradiction_count": 0,
        "positive_weight": 0.5,
        "negative_weight": 0.0,
        "created_at": _BASE,
        "updated_at": _BASE,
        "last_event_at": _BASE,
        "last_confirmed_at": None,
        "source": {},
        "metadata": {},
        "revision": 2,
    }


_SCOPE = MemoryScope(tenant="t", project="p", user_id="u", bundle_id="b@1")


@pytest.mark.asyncio
async def test_apply_evidence_promotes_chosen_text_to_canonical() -> None:
    events = [
        _event(0, event_type="agent_observation", text="Cities: Aachen, Bonn"),
        _event(1, event_type="agent_refinement", text="Cities: Aachen, Bonn, Rotterdam"),
    ]
    store = _EvidenceStore(_memory_row(), events)
    # The record's canonical text starts as the seed observation.
    record = await store.apply_evidence(scope=_SCOPE, memory_id="mem_t", event_id="mev_0")
    assert record is not None
    # Applying the older observation makes ITS text canonical.
    assert record.memory == "Cities: Aachen, Bonn"

    store2 = _EvidenceStore(_memory_row(), [dict(e) for e in events])
    record2 = await store2.apply_evidence(scope=_SCOPE, memory_id="mem_t", event_id="mev_1")
    assert record2.memory == "Cities: Aachen, Bonn, Rotterdam"


@pytest.mark.asyncio
async def test_apply_evidence_is_idempotent_when_already_canonical() -> None:
    store = _EvidenceStore(
        _memory_row(),
        [_event(0, event_type="agent_observation", text="Cities: Aachen, Bonn")],
    )
    before = store._con.memory_row["revision"]
    record = await store.apply_evidence(scope=_SCOPE, memory_id="mem_t", event_id="mev_0")
    # The chosen text already equals canonical -> no new event, no revision bump.
    assert record.memory == "Cities: Aachen, Bonn"
    assert store._con.memory_row["revision"] == before


@pytest.mark.asyncio
async def test_delete_evidence_removes_entry_and_rederives() -> None:
    events = [
        _event(0, event_type="agent_observation", text="Cities: Aachen, Bonn"),
        _event(1, event_type="agent_refinement", text="Cities: Aachen, Bonn, Rotterdam"),
    ]
    row = _memory_row()
    row["memory"] = "Cities: Aachen, Bonn, Rotterdam"  # current canonical from the refinement
    store = _EvidenceStore(row, events)
    record = await store.delete_evidence(scope=_SCOPE, memory_id="mem_t", event_id="mev_1")
    assert record is not None
    # Dropping the refinement re-derives canonical back to the seed observation.
    assert record.memory == "Cities: Aachen, Bonn"
    assert record.evidence_count == 1
    assert len(store._con.events) == 1


@pytest.mark.asyncio
async def test_delete_evidence_idempotent_for_unknown_event() -> None:
    store = _EvidenceStore(
        _memory_row(),
        [_event(0, event_type="agent_observation", text="Cities: Aachen, Bonn")],
    )
    before = store._con.memory_row["revision"]
    record = await store.delete_evidence(scope=_SCOPE, memory_id="mem_t", event_id="mev_missing")
    # Unknown event -> no-op, record unchanged.
    assert record.memory == "Cities: Aachen, Bonn"
    assert store._con.memory_row["revision"] == before
    assert len(store._con.events) == 1


@pytest.mark.asyncio
async def test_delete_evidence_refuses_last_entry() -> None:
    store = _EvidenceStore(
        _memory_row(),
        [_event(0, event_type="agent_observation", text="Cities: Aachen, Bonn")],
    )
    with pytest.raises(ValueError, match="at_least_one_evidence"):
        await store.delete_evidence(scope=_SCOPE, memory_id="mem_t", event_id="mev_0")


@pytest.mark.asyncio
async def test_evidence_op_respects_base_revision() -> None:
    store = _EvidenceStore(
        _memory_row(),
        [
            _event(0, event_type="agent_observation", text="Cities: Aachen, Bonn"),
            _event(1, event_type="agent_refinement", text="Cities: Aachen, Bonn, Rotterdam"),
        ],
    )
    with pytest.raises(ValueError, match="revision_conflict"):
        await store.delete_evidence(scope=_SCOPE, memory_id="mem_t", event_id="mev_1", base_revision=99)
