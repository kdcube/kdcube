"""Hot/cold retention of the conversation index.

The archiver moves rows older than the hot window to the cold tier with their
embeddings, deletes hot rows only after the cold copy is read back and
verified, resumes from the ledger, serves date-filtered reads from the cold
tier by time, and deletes a scope of messages from both tiers with an audit
row. The pool below implements exactly the statements the retention code
issues, over in-memory tables; the storage is the platform's in-memory backend.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_cold import (
    ColdArchiveIntegrityError,
    ConversationColdArchive,
    decode_part,
)
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import (
    ConversationRetention,
    cold_turn_catalog,
    hot_cutoff,
)
from kdcube_ai_app.storage.storage import InMemoryStorageBackend

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
SCHEMA = "kdcube_test_w536"


class _Con:
    def __init__(self, db: "_Db") -> None:
        self.db = db

    @asynccontextmanager
    async def transaction(self):
        snapshot = self.db.snapshot()
        try:
            yield
        except Exception:
            self.db.restore(snapshot)
            raise

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith(f"SELECT id, user_id, bundle_id") and "FROM kdcube_test_w536.conv_messages WHERE ts < $1" in q:
            cutoff, limit = args
            rows = sorted((r for r in self.db.messages if r["ts"] < cutoff), key=lambda r: (r["ts"], r["id"]))
            return [dict(r, embedding=json.dumps(r["embedding"])) for r in rows[:limit]]
        if q.startswith("SELECT from_id, to_id, policy, created_at FROM"):
            ids = set(args[0])
            return [e for e in self.db.edges if e["from_id"] in ids or e["to_id"] in ids]
        if q.startswith("SELECT batch_id, day FROM") and "state IN ('written', 'verified')" in q:
            return [b for b in self.db.sorted_batches() if b["state"] in ("written", "verified")]
        if q.startswith("SELECT batch_id, day FROM") and "state = 'pruned'" in q:
            first = args[0] if "day >= $1" in q else None
            last = args[-1] if "day <= $" in q else None
            return [
                b for b in self.db.sorted_batches()
                if b["state"] == "pruned"
                and (first is None or b["day"] >= first)
                and (last is None or b["day"] <= last)
            ]
        if q.startswith("DELETE FROM kdcube_test_w536.conv_messages WHERE user_id = $1"):
            user_id, conversation_id, *rest = args
            bundle = rest.pop(0) if "bundle_id = $3" in q else None
            tags = rest.pop(0) if "tags @>" in q else None
            hit = [
                r for r in self.db.messages
                if r["user_id"] == user_id and r["conversation_id"] == conversation_id
                and (bundle is None or r["bundle_id"] == bundle)
                and (tags is None or set(tags) <= set(r["tags"]))
            ]
            self.db.delete_ids({r["id"] for r in hit})
            return [{"id": r["id"], "hosted_uri": r["hosted_uri"]} for r in hit]
        raise AssertionError(f"unexpected fetch: {q}")

    async def fetchval(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT max(max_ts) FROM"):
            stamps = [b["max_ts"] for b in self.db.batches.values() if b["state"] == "pruned" and b["max_ts"]]
            return max(stamps) if stamps else None
        raise AssertionError(f"unexpected fetchval: {q}")

    async def execute(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO kdcube_test_w536.conv_archive_batches"):
            batch_id, day, count, min_id, max_id, min_ts, max_ts, part_key, manifest_key, sha = args
            state = "pruned" if "'pruned')" in q else "written"
            existing = self.db.batches.get(batch_id)
            if existing and existing["state"] == "pruned":
                return "INSERT 0 0"
            self.db.batches[batch_id] = {
                "batch_id": batch_id, "day": day, "row_count": count, "max_ts": max_ts,
                "sha256": sha, "state": state, "error": None,
            }
            return "INSERT 0 1"
        if q.startswith("UPDATE kdcube_test_w536.conv_archive_batches SET error"):
            self.db.batches[args[0]]["error"] = args[1]
            return "UPDATE 1"
        if q.startswith("UPDATE kdcube_test_w536.conv_archive_batches SET state = 'verified'"):
            if self.db.fail_prune_after_verify:
                self.db.fail_prune_after_verify = False
                self.db.batches[args[0]]["state"] = "verified"
                raise RuntimeError("connection lost after verify")
            self.db.batches[args[0]]["state"] = "verified"
            return "UPDATE 1"
        if q.startswith("DELETE FROM kdcube_test_w536.conv_messages WHERE id = ANY"):
            self.db.delete_ids(set(args[0]))
            return "DELETE"
        if q.startswith("UPDATE kdcube_test_w536.conv_archive_batches SET state = 'pruned'"):
            self.db.batches[args[0]]["state"] = "pruned"
            return "UPDATE 1"
        if q.startswith("UPDATE kdcube_test_w536.conv_archive_batches SET state = 'retired'"):
            self.db.batches[args[0]]["state"] = "retired"
            return "UPDATE 1"
        if q.startswith("INSERT INTO kdcube_test_w536.conv_archive_deletions"):
            deletion_id, actor, reason, scope = args
            self.db.deletions.append(
                {"deletion_id": deletion_id, "actor": actor, "reason": reason, "scope": scope, "state": "started"}
            )
            return "INSERT 0 1"
        if q.startswith("UPDATE kdcube_test_w536.conv_archive_deletions"):
            deletion_id, state, hot, bodies, cold, error = args
            for row in self.db.deletions:
                if row["deletion_id"] == deletion_id:
                    row.update(state=state, hot_rows=hot, body_objects=bodies, cold_rows=cold, error=error)
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute: {q}")


class _Db:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.edges: list[dict] = []
        self.batches: dict[str, dict] = {}
        self.deletions: list[tuple] = []
        self.fail_prune_after_verify = False

    def snapshot(self):
        return ([dict(m) for m in self.messages], {k: dict(v) for k, v in self.batches.items()})

    def restore(self, snap) -> None:
        self.messages, self.batches = snap[0], snap[1]

    def sorted_batches(self):
        return sorted(self.batches.values(), key=lambda b: (b["day"], b["batch_id"]))

    def delete_ids(self, ids) -> None:
        self.messages = [m for m in self.messages if m["id"] not in ids]
        self.edges = [e for e in self.edges if e["from_id"] not in ids and e["to_id"] not in ids]


class _Pool:
    def __init__(self, db: _Db) -> None:
        self.db = db

    @asynccontextmanager
    async def acquire(self):
        yield _Con(self.db)


class _Store:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete_message(self, uri: str) -> bool:
        self.deleted.append(uri)
        return True


def _msg(i, *, days_ago, role="user", conv="c1", tags=(), turn="t1", user="u1", bundle="b1"):
    return {
        "id": i, "user_id": user, "bundle_id": bundle, "agent_id": "codex", "conversation_id": conv,
        "message_id": f"m{i}", "role": role, "text": f"text {i}", "hosted_uri": f"cb/x/{i}.json",
        "ts": NOW - timedelta(days=days_ago, minutes=i), "ttl_days": 3650, "user_type": "registered",
        "tags": list(tags), "turn_id": turn, "anchors_text": None, "embedding": [0.25 * i, 0.5, -1.0],
    }


def _setup():
    db = _Db()
    backend = InMemoryStorageBackend()
    archive = ConversationColdArchive(backend, tenant="t", project="p")
    store = _Store()
    retention = ConversationRetention(pool=_Pool(db), schema=SCHEMA, archive=archive, store=store)
    return db, backend, archive, store, retention


@pytest.mark.asyncio
async def test_old_rows_move_to_cold_with_embeddings_and_hot_keeps_the_window():
    db, backend, archive, _, retention = _setup()
    db.messages = [_msg(1, days_ago=200), _msg(2, days_ago=120), _msg(3, days_ago=10)]
    db.edges = [{"from_id": 1, "to_id": 2, "policy": "none", "created_at": NOW}]

    summary = await retention.archive_before(hot_cutoff(90, now=NOW))

    assert summary["rows"] == 2 and summary["batches"] == 2
    assert [m["id"] for m in db.messages] == [3]
    assert {b["state"] for b in db.batches.values()} == {"pruned"}
    cold = await archive.read_range(NOW - timedelta(days=400), NOW)
    assert [r["id"] for r in cold] == [1, 2]
    assert cold[0]["embedding"] == [0.25, 0.5, -1.0]
    assert cold[0]["edges"][0]["to_id"] == 2


@pytest.mark.asyncio
async def test_a_corrupt_cold_part_deletes_nothing():
    db, backend, archive, _, retention = _setup()
    db.messages = [_msg(1, days_ago=200)]
    original = archive.write_batch

    async def corrupt(**kw):
        manifest = await original(**kw)
        await backend.write_bytes_a(manifest["part_key"], b"tampered")
        return manifest

    archive.write_batch = corrupt  # type: ignore[assignment]
    with pytest.raises(ColdArchiveIntegrityError):
        await retention.archive_before(hot_cutoff(90, now=NOW))
    assert [m["id"] for m in db.messages] == [1]
    (batch,) = db.batches.values()
    assert batch["state"] == "written" and "sha256 mismatch" in batch["error"]


@pytest.mark.asyncio
async def test_an_interrupted_prune_resumes_from_the_ledger():
    db, _, _, _, retention = _setup()
    db.messages = [_msg(1, days_ago=200)]
    db.fail_prune_after_verify = True
    with pytest.raises(RuntimeError):
        await retention.archive_before(hot_cutoff(90, now=NOW))
    assert [m["id"] for m in db.messages] == [1]  # the transaction rolled back

    summary = await retention.archive_before(hot_cutoff(90, now=NOW))
    assert summary["resumed"] == 1
    assert db.messages == []
    assert {b["state"] for b in db.batches.values()} == {"pruned"}


@pytest.mark.asyncio
async def test_a_date_read_reaches_cold_only_past_the_watermark():
    db, _, _, _, retention = _setup()
    db.messages = [_msg(1, days_ago=200, conv="c1"), _msg(2, days_ago=150, conv="c2"), _msg(3, days_ago=10)]
    await retention.archive_before(hot_cutoff(90, now=NOW))

    got = await retention.fetch_cold(from_ts=NOW - timedelta(days=365), user_id="u1", conversation_id="c1")
    assert [r["id"] for r in got] == [1]
    assert "embedding" not in got[0] and got[0]["storage"] == "cold"
    assert await retention.fetch_cold(from_ts=NOW - timedelta(days=30), user_id="u1") == []


@pytest.mark.asyncio
async def test_deleting_a_scope_removes_hot_rows_bodies_and_cold_lines_and_records_it():
    db, _, archive, store, retention = _setup()
    db.messages = [
        _msg(1, days_ago=200, tags=("pb:project:A",)),
        _msg(2, days_ago=200, tags=("pb:project:B",)),
        _msg(3, days_ago=5, tags=("pb:project:A",)),
    ]
    await retention.archive_before(hot_cutoff(90, now=NOW))

    result = await retention.delete_messages(
        actor="operator:u1", user_id="u1", conversation_id="c1", tags_all=["pb:project:A"], reason="agent removed",
    )

    assert result["hot_rows"] == 1 and result["cold_rows"] == 1 and result["body_objects"] == 2
    assert store.deleted == ["cb/x/1.json", "cb/x/3.json"]
    remaining = await archive.read_range(NOW - timedelta(days=400), NOW)
    assert [r["id"] for r in remaining] == [2]
    (deletion,) = db.deletions
    assert deletion["actor"] == "operator:u1" and json.loads(deletion["scope"])["tags_all"] == ["pb:project:A"]
    assert deletion["state"] == "completed" and deletion["cold_rows"] == 1


def test_cold_catalog_entries_carry_the_hot_catalog_shape():
    records = [
        {"id": 1, "role": "user", "text": "hello", "ts": "2026-01-01T00:00:00+00:00", "tags": [], "turn_id": "t1"},
        {"id": 2, "role": "assistant", "text": "answer", "ts": "2026-01-01T00:01:00+00:00", "tags": [], "turn_id": "t1"},
        {"id": 3, "role": "artifact", "text": "{}", "ts": "2026-01-01T00:02:00+00:00", "tags": ["kind:turn.log"], "turn_id": "t1",
         "conversation_id": "c1", "bundle_id": "b1", "agent_id": None, "message_id": "m3", "hosted_uri": None},
    ]
    (entry,) = cold_turn_catalog(records)
    assert entry["turn_id"] == "t1" and entry["ordinal"] is None and entry["storage"] == "cold"
    assert entry["first_user_text"] == "hello" and entry["last_assistant_text"] == "answer"


@pytest.mark.asyncio
async def test_the_turn_catalog_appends_cold_turns_for_a_date_range():
    class _CatalogCon:
        async def fetch(self, query, *args):
            return []

    class _CatalogPool:
        @asynccontextmanager
        async def acquire(self):
            yield _CatalogCon()

    class _Cold:
        async def fetch_cold(self, **scope):
            assert scope["from_ts"] is not None
            return [
                {"id": 3, "role": "artifact", "text": "{}", "ts": "2026-01-01T00:02:00+00:00",
                 "tags": ["kind:turn.log"], "turn_id": "t1", "conversation_id": "c1"},
            ]

    index = ConvIndex(pool=_CatalogPool(), schema=SCHEMA)  # type: ignore[arg-type]
    index.cold_retention = _Cold()
    rows = await index.fetch_turn_catalog(user_id="u1", conversation_id="c1", from_ts="2025-12-01T00:00:00+00:00")
    assert [r["turn_id"] for r in rows] == ["t1"]
    assert rows[0]["storage"] == "cold" and rows[0]["turn_index_path"] == "conv:ar:t1.react.turn.index"

    rows = await index.fetch_turn_catalog(user_id="u1", conversation_id="c1")
    assert rows == []  # no date filter: the hot index only


def test_parts_are_deterministic_gzip_jsonl():
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_cold import encode_part

    records = [{"id": 2, "ts": "x"}, {"id": 1, "ts": "y"}]
    assert encode_part(records) == encode_part(list(reversed(records)))
    assert [r["id"] for r in decode_part(encode_part(records))] == [1, 2]


def test_the_index_builds_retention_with_the_apps_store(monkeypatch):
    from types import SimpleNamespace

    import kdcube_ai_app.apps.chat.sdk.context.vector.conv_index as conv_index_module
    import kdcube_ai_app.storage.storage as storage_module

    monkeypatch.setattr(
        conv_index_module,
        "get_settings",
        lambda: SimpleNamespace(STORAGE_PATH="mem://", TENANT="t", PROJECT="p"),
    )
    monkeypatch.setattr(storage_module, "create_storage_backend", lambda uri: InMemoryStorageBackend())
    store = _Store()
    index = ConvIndex(pool=_Pool(_Db()), schema=SCHEMA)  # type: ignore[arg-type]

    retention = index.retention(store=store)

    assert retention is not None and retention.store is store and retention.schema == SCHEMA
    assert retention.archive.root == "cb/tenants/t/projects/p/conversation-cold"


# Review witnesses (kdcube #314 at dd3e207f), kept as regressions.


@pytest.mark.asyncio
async def test_deleting_an_archived_message_also_deletes_its_body():
    db, backend, archive, store, retention = _setup()
    db.messages = [_msg(1, days_ago=200), _msg(2, days_ago=200)]
    await retention.archive_before(NOW - timedelta(days=90))
    assert db.messages == []
    result = await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")
    assert result["cold_rows"] == 2
    assert sorted(store.deleted) == ["cb/x/1.json", "cb/x/2.json"]
    assert result["body_objects"] == 2


@pytest.mark.asyncio
async def test_a_retired_part_left_in_storage_never_comes_back_in_reads():
    db, backend, archive, store, retention = _setup()
    db.messages = [_msg(1, days_ago=200), _msg(2, days_ago=200, conv="c2")]
    await retention.archive_before(NOW - timedelta(days=90))
    [old] = list(db.batches)
    manifest = await archive.read_manifest(db.batches[old]["day"], old)
    part = await backend.read_bytes_a(manifest["part_key"])
    await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")
    # An interrupted retirement leaves the old files behind.
    await backend.write_bytes_a(manifest["part_key"], part)
    await backend.write_bytes_a(
        archive.manifest_key(db.batches[old]["day"], old), json.dumps(manifest, sort_keys=True).encode()
    )
    records = await retention.fetch_cold(from_ts=NOW - timedelta(days=365))
    assert [r["conversation_id"] for r in records] == ["c2"]


@pytest.mark.asyncio
async def test_a_written_batch_never_duplicates_hot_rows_in_reads():
    db, backend, archive, store, retention = _setup()
    db.messages = [_msg(1, days_ago=200)]
    db.fail_prune_after_verify = True
    with pytest.raises(RuntimeError):
        await retention.archive_before(NOW - timedelta(days=90))
    assert [m["id"] for m in db.messages] == [1]  # still hot; its part exists but is not pruned
    assert await retention.fetch_cold(from_ts=NOW - timedelta(days=365)) == []


@pytest.mark.asyncio
async def test_a_deletion_that_fails_part_way_is_still_recorded():
    db, backend, archive, store, retention = _setup()
    db.messages = [_msg(1, days_ago=200), _msg(2, days_ago=1)]
    await retention.archive_before(NOW - timedelta(days=90))

    async def broken(*_a, **_k):
        raise RuntimeError("storage unavailable")

    archive.read_manifest = broken  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")
    assert db.messages == []
    (deletion,) = db.deletions
    assert deletion["state"] == "failed" and "storage unavailable" in deletion["error"]
    assert deletion["actor"] == "operator" and deletion["hot_rows"] == 1
