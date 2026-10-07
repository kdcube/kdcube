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
            if rows:  # one batch: the oldest row's user, conversation and UTC day
                group = lambda r: (r["user_id"], r["conversation_id"], r["ts"].astimezone(timezone.utc).date())
                rows = [r for r in rows if group(r) == group(rows[0])]
            return [dict(r, embedding=json.dumps(r["embedding"])) for r in rows[:limit]]
        if q.startswith("SELECT from_id, to_id, policy, created_at FROM"):
            ids = set(args[0])
            return [e for e in self.db.edges if e["from_id"] in ids or e["to_id"] in ids]
        if q.startswith("SELECT b.batch_id, b.part_key, b.manifest_key") and "b.state IN ('written', 'verified')" in q:
            user_id, conversation_id = args
            hits = {c["batch_id"] for c in self.db.conversations
                    if c["user_id"] == user_id and c["conversation_id"] == conversation_id}
            return [b for b in self.db.sorted_batches() if b["state"] in ("written", "verified") and b["batch_id"] in hits]
        if q.startswith("SELECT batch_id, day") and "state IN ('written', 'verified')" in q:
            return [b for b in self.db.sorted_batches() if b["state"] in ("written", "verified")]
        if q.startswith("SELECT batch_id, day, part_key, manifest_key") and "part_key ~" in q:
            import re
            legacy = re.compile(r"/conversation-cold/[0-9]{4}/[0-9]{2}/[0-9]{2}/[^/]+$")
            rows = [b for b in self.db.sorted_batches() if b["state"] == "pruned" and legacy.search(b["part_key"] or "")]
            limit = re.search(r"LIMIT ([0-9]+)$", q)
            return rows[: int(limit.group(1))] if limit else rows
        if q.startswith("SELECT batch_id, day") and "state = 'pruned'" in q:
            first = args[0] if "day >= $1" in q else None
            last = args[-1] if "day <= $" in q else None
            return [
                b for b in self.db.sorted_batches()
                if b["state"] == "pruned"
                and (first is None or b["day"] >= first)
                and (last is None or b["day"] <= last)
            ]
        if q.startswith("SELECT b.batch_id, b.day") and "conv_archive_batches b WHERE b.state = 'pruned' AND NOT EXISTS" in q:
            indexed = {c["batch_id"] for c in self.db.conversations if c.get("start_ts_list") is not None}
            return [b for b in self.db.sorted_batches() if b["state"] == "pruned" and b["batch_id"] not in indexed]
        if q.startswith("SELECT c.conversation_id, max(c.max_ts) AS last_activity_at"):
            user_id, from_ts, *rest = args
            since = None
            bundle = rest.pop(0) if "c.bundle_id = $" in q else None
            groups: dict = {}
            for c in self.db.live_conversations():
                if c["user_id"] != user_id or c["max_ts"] < from_ts:
                    continue
                if since is not None and c["max_ts"] < since:
                    continue
                if bundle is not None and c["bundle_id"] != bundle:
                    continue
                if c["expires_at"] is not None and c["expires_at"] < datetime.now(timezone.utc):
                    continue
                groups.setdefault(c["conversation_id"], []).append(c)
            out = []
            now = datetime.now(timezone.utc)
            for cid, rows in groups.items():
                live = [
                    (ts, text)
                    for r in rows
                    for ts, expires, text in zip(r["start_ts_list"] or [], r["start_expires_list"] or [], r["start_text_list"] or [])
                    if ts >= from_ts and (expires is None or expires >= now)
                ]
                out.append({
                    "conversation_id": cid,
                    "last_activity_at": max(r["max_ts"] for r in rows),
                    "started_at": min(ts for ts, _ in live) if live else None,
                    "conv_start_text": max(live)[1] if live else None,
                })
            return out
        if q.startswith("SELECT DISTINCT b.batch_id, b.day") and "FROM kdcube_test_w536.conv_archive_conversations c" in q:
            if "c.max_ts >= $3" in q:
                user_id, conversation_id, from_ts, *rest = args
            else:  # a deletion: every batch of the conversation, whatever its age
                user_id, conversation_id, *rest = args
                from_ts = None
            bundle = rest[0] if "c.bundle_id = $" in q and rest else None
            hits = {
                c["batch_id"] for c in self.db.live_conversations()
                if c["user_id"] == user_id and c["conversation_id"] == conversation_id
                and (from_ts is None or c["max_ts"] >= from_ts)
                and (bundle is None or c["bundle_id"] == bundle)
            }
            return [b for b in self.db.sorted_batches() if b["batch_id"] in hits]
        if q.startswith("SELECT hosted_uri FROM kdcube_test_w536.conv_messages WHERE user_id = $1"):
            return [{"hosted_uri": r["hosted_uri"]} for r in self._scope(q, args)]
        if q.startswith("DELETE FROM kdcube_test_w536.conv_messages WHERE user_id = $1"):
            hit = self._scope(q, args)
            self.db.delete_ids({r["id"] for r in hit})
            return [{"id": r["id"], "hosted_uri": r["hosted_uri"]} for r in hit]
        raise AssertionError(f"unexpected fetch: {q}")

    def _scope(self, q: str, args) -> list[dict]:
        user_id, conversation_id, *rest = args
        bundle = rest.pop(0) if "bundle_id = $3" in q else None
        tags = rest.pop(0) if "tags @>" in q else None
        return [
            r for r in self.db.messages
            if r["user_id"] == user_id and r["conversation_id"] == conversation_id
            and (bundle is None or r["bundle_id"] == bundle)
            and (tags is None or set(tags) <= set(r["tags"]))
        ]

    async def fetchval(self, query: str, *args):
        q = " ".join(query.split())
        if q in ("SELECT pg_try_advisory_lock($1)", "SELECT pg_advisory_lock($1)", "SELECT pg_advisory_unlock($1)"):
            # One process here; the lock itself is exercised against real PostgreSQL.
            self.db.locks.append((q.split()[1].split("(")[0], args[0]))
            return True
        if q == "SELECT set_config('lock_timeout', $1, false)":
            self.db.locks.append(("lock_timeout", args[0]))
            return args[0]
        if q.startswith("UPDATE kdcube_test_w536.conv_archive_batches SET state = 'retired', error = $2"):
            batch = self.db.batches.get(args[0])
            if not batch or batch["state"] not in ("written", "verified"):
                return None
            batch.update(state="retired", error=args[1])
            return args[0]
        if q.startswith("UPDATE kdcube_test_w536.conv_archive_batches SET state = 'retired'"):
            batch = self.db.batches.get(args[0])
            if not batch or batch["state"] != "pruned":
                return None
            batch["state"] = "retired"
            return args[0]
        if q.startswith("SELECT max(max_ts) FROM"):
            stamps = [b["max_ts"] for b in self.db.batches.values() if b["state"] == "pruned" and b["max_ts"]]
            return max(stamps) if stamps else None
        raise AssertionError(f"unexpected fetchval: {q}")

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT state, part_key FROM kdcube_test_w536.conv_archive_batches WHERE batch_id = $1"):
            batch = self.db.batches.get(args[0])
            return {"state": batch["state"], "part_key": batch["part_key"]} if batch else None
        raise AssertionError(f"unexpected fetchrow: {q}")

    async def executemany(self, query: str, rows):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO kdcube_test_w536.conv_archive_conversations"):
            keys = ("batch_id", "user_id", "conversation_id", "bundle_id", "agent_id", "row_count", "min_ts",
                    "max_ts", "expires_at", "start_min_ts", "start_last_ts", "start_last_text",
                    "start_ts_list", "start_expires_list", "start_text_list")
            self.db.conversations.extend(dict(zip(keys, row)) for row in rows)
            return None
        raise AssertionError(f"unexpected executemany: {q}")

    async def execute(self, query: str, *args):
        q = " ".join(query.split())
        if q == "RESET lock_timeout":
            self.db.locks.append(("reset", None))
            return "RESET"
        if q.startswith("DELETE FROM kdcube_test_w536.conv_archive_conversations WHERE batch_id = $1"):
            self.db.conversations = [c for c in self.db.conversations if c["batch_id"] != args[0]]
            return "DELETE"
        if q.startswith("INSERT INTO kdcube_test_w536.conv_archive_batches"):
            batch_id, day, count, min_id, max_id, min_ts, max_ts, part_key, manifest_key, sha = args
            state = "pruned" if "'pruned')" in q else "written"
            existing = self.db.batches.get(batch_id)
            if existing and existing["state"] == "pruned":
                return "INSERT 0 0"
            self.db.batches[batch_id] = {
                "batch_id": batch_id, "day": day, "row_count": count, "max_ts": max_ts,
                "sha256": sha, "state": state, "error": None,
                "part_key": part_key, "manifest_key": manifest_key,
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
                {"deletion_id": deletion_id, "actor": actor, "reason": reason, "scope": scope, "state": "started",
                 "sql": q}
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
        self.conversations: list[dict] = []  # conv_archive_conversations
        self.fail_prune_after_verify = False
        self.locks: list[tuple] = []

    def snapshot(self):
        return (
            [dict(m) for m in self.messages],
            {k: dict(v) for k, v in self.batches.items()},
            [dict(c) for c in self.conversations],
        )

    def restore(self, snap) -> None:
        self.messages, self.batches, self.conversations = snap[0], snap[1], snap[2]

    def live_conversations(self):
        return [c for c in self.conversations if self.batches.get(c["batch_id"], {}).get("state") == "pruned"]

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
        # Like ConversationStore.delete_message: False when the body is already gone.
        if uri in self.deleted:
            return False
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
    cold = await retention.fetch_cold(from_ts=NOW - timedelta(days=400), include_embedding=True)
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
    remaining = await retention.fetch_cold(from_ts=NOW - timedelta(days=400))
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
async def test_the_deletion_scope_is_stored_as_a_json_object_through_any_pool():
    # The platform pool registers a jsonb codec; JSON text bound straight to
    # a jsonb parameter would be encoded again and stored as a string.
    db, _, _, _, retention = _setup()
    await retention.delete_messages(actor="operator:u1", user_id="u1", conversation_id="c1", reason="agent removed")

    (row,) = db.deletions
    assert "($4::text)::jsonb" in row["sql"]
    assert json.loads(row["scope"]) == {"user_id": "u1", "conversation_id": "c1", "bundle_id": None, "tags_all": []}


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
    [old] = [b for b in db.batches.values() if "/c1/" in b["part_key"]]
    manifest = await archive.read_manifest(old["manifest_key"])
    part = await backend.read_bytes_a(manifest["part_key"])
    await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")
    # An interrupted retirement leaves the old files behind.
    await backend.write_bytes_a(manifest["part_key"], part)
    await backend.write_bytes_a(old["manifest_key"], json.dumps(manifest, sort_keys=True).encode())
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
    # Nothing is deleted before the failing step; the attempt is still recorded.
    assert [m["id"] for m in db.messages] == [2]
    (deletion,) = db.deletions
    assert deletion["state"] == "failed" and "storage unavailable" in deletion["error"]
    assert deletion["actor"] == "operator" and deletion["hot_rows"] == 0


@pytest.mark.asyncio
async def test_a_failed_body_delete_can_be_completed_by_running_the_deletion_again():
    db, backend, archive, store, retention = _setup()
    db.messages = [_msg(1, days_ago=200), _msg(2, days_ago=1)]
    await retention.archive_before(NOW - timedelta(days=90))
    original = store.delete_message
    calls = []

    async def fail_second(uri):
        calls.append(uri)
        if len(calls) == 2:
            raise RuntimeError("storage unavailable")
        return await original(uri)

    store.delete_message = fail_second  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")
    assert db.deletions[0]["state"] == "failed"
    assert [m["id"] for m in db.messages] == [2]  # records stay until their bodies are gone
    store.delete_message = original  # type: ignore[assignment]
    result = await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")
    assert sorted(store.deleted) == ["cb/x/1.json", "cb/x/2.json"]
    assert result["hot_rows"] == 1 and result["cold_rows"] == 1 and db.deletions[1]["state"] == "completed"


def test_the_daily_archive_is_on_by_default():
    # Operator, 2026-10-04: "yes that's correct - by default its ON".
    from kdcube_ai_app.apps.chat.sdk.config import Settings

    assert Settings.model_fields["CONVERSATION_ARCHIVE_ENABLED"].default is True


@pytest.mark.asyncio
async def test_the_daily_archive_runs_unless_the_setting_turns_it_off(monkeypatch):
    from types import SimpleNamespace

    import kdcube_ai_app.infra.plugin.admin_bundle.entrypoint as admin
    import kdcube_ai_app.apps.chat.sdk.context.vector.conv_index as conv_index_module

    calls = []

    class _Retention:
        async def archive_before(self, cutoff):
            calls.append(cutoff)
            return {"resumed": 0, "batches": 0, "rows": 0}

    monkeypatch.setattr(conv_index_module.ConvIndex, "retention", lambda self, store=None: _Retention())
    owner = SimpleNamespace(pg_pool=_Pool(_Db()))

    monkeypatch.setattr(admin, "get_settings", lambda: SimpleNamespace(CONVERSATION_ARCHIVE_ENABLED=False, CONVERSATION_HOT_DAYS=90))
    await admin.AdminBundleEntrypoint.archive_conversations(owner)
    assert calls == []

    monkeypatch.setattr(admin, "get_settings", lambda: SimpleNamespace(CONVERSATION_ARCHIVE_ENABLED=True, CONVERSATION_HOT_DAYS=90))
    await admin.AdminBundleEntrypoint.archive_conversations(owner)
    assert len(calls) == 1


# Opening and listing conversations reach the cold tier (A2): an archived
# conversation is still listed, and opening one shows every turn.


class _HotCon:
    def __init__(self, rows_by_kind: dict) -> None:
        self.rows_by_kind = rows_by_kind

    async def fetch(self, query, *args):
        if "JOIN LATERAL unnest(m.tags)" in query:
            return self.rows_by_kind.get("turns", [])
        if "WITH recent AS" in query:
            return self.rows_by_kind.get("conversations", [])
        return self.rows_by_kind.get("recent", [])


class _HotPool:
    def __init__(self, rows_by_kind: dict) -> None:
        self.con = _HotCon(rows_by_kind)

    @asynccontextmanager
    async def acquire(self):
        yield self.con


async def _archived_index(hot_rows_by_kind: dict, messages: list[dict]):
    db, _, _, _, retention = _setup()
    db.messages = messages
    await retention.archive_before(datetime.now(timezone.utc) - timedelta(days=90))
    index = ConvIndex(pool=_HotPool(hot_rows_by_kind), schema=SCHEMA)  # type: ignore[arg-type]
    index.cold_retention = retention
    return index


def _days_ago(days: int, minutes: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days, minutes=minutes)


def _live_msg(i, *, days_ago, **kw):
    row = _msg(i, days_ago=0, **kw)
    row["ts"] = _days_ago(days_ago, i)
    return row


@pytest.mark.asyncio
async def test_opening_a_conversation_shows_its_archived_turns_first():
    hot_turn = {"turn_id": "t9", "ts": _days_ago(1), "tags": ["turn:t9"], "mid": "m9",
                "hosted_uri": "cb/x/9.json", "bundle_id": "b1", "agent_id": "codex"}
    index = await _archived_index({"turns": [hot_turn]}, [
        _live_msg(1, days_ago=200, turn="t1", tags=["turn:t1", "artifact:conv.user_shortcuts"]),
        _live_msg(2, days_ago=150, turn="t2", tags=["turn:t2"]),
        _live_msg(3, days_ago=150, turn="t3", conv="other", tags=["turn:t3"]),
    ])
    occ = await index.get_conversation_turn_ids_from_tags(user_id="u1", conversation_id="c1")
    assert [o["turn_id"] for o in occ] == ["t1", "t2", "t9"]
    assert occ[0]["hosted_uri"] == "cb/x/1.json" and occ[0]["mid"] == "m1"
    only = await index.get_conversation_turn_ids_from_tags(user_id="u1", conversation_id="c1", turn_ids=["t2"])
    assert [o["turn_id"] for o in only] == ["t2", "t9"]  # the hot query applies its own filter


@pytest.mark.asyncio
async def test_a_conversations_recent_messages_continue_into_the_cold_tier():
    hot = {"id": 9, "message_id": "m9", "role": "artifact", "text": "hot", "hosted_uri": "cb/x/9.json",
           "ts": _days_ago(1), "tags": ["artifact:timeline"], "turn_id": "t9", "bundle_id": "b1",
           "agent_id": "codex", "conversation_id": "c1"}
    index = await _archived_index({"recent": [hot]}, [
        _live_msg(1, days_ago=200, role="artifact", tags=["artifact:timeline"]),
        _live_msg(2, days_ago=150, role="artifact", tags=["artifact:timeline", "secret"]),
        _live_msg(3, days_ago=120, role="user", tags=["artifact:timeline"]),
    ])
    rows = await index.fetch_recent(user_id="u1", conversation_id="c1", roles=("artifact",),
                                    all_tags=["artifact:timeline"], not_tags=["secret"], limit=5, days=365)
    assert [r["id"] for r in rows] == [9, 1]
    assert rows[1]["storage"] == "cold" and isinstance(rows[1]["ts"], datetime)
    limited = await index.fetch_recent(user_id="u1", conversation_id="c1", roles=("artifact",), limit=1, days=365)
    assert [r["id"] for r in limited] == [9]
    across = await index.fetch_recent(user_id="u1", roles=("artifact",), limit=5, days=365)
    assert [r["id"] for r in across] == [9]  # cross-conversation reads stay hot


@pytest.mark.asyncio
async def test_a_wholly_archived_conversation_is_still_listed_after_the_active_ones():
    hot = {"conversation_id": "c1", "last_activity_at": _days_ago(1), "started_at": None, "conv_start_text": None}
    start = ["conv.start", "artifact:turn.fingerprint.v1"]
    index = await _archived_index({"conversations": [hot]}, [
        _live_msg(1, days_ago=200, conv="c1", tags=start),
        _live_msg(2, days_ago=150, conv="old", tags=start),
        _live_msg(3, days_ago=140, conv="old"),
        _live_msg(4, days_ago=130, conv="mine-not", user="u2"),
    ])
    rows = await index.list_user_conversations(user_id="u1", include_conv_start_text=True)
    assert [r["conversation_id"] for r in rows] == ["c1", "old"]
    # c1 still has hot messages; its archived start moves its started_at earlier.
    assert rows[0]["started_at"] is not None and rows[0]["started_at"] < rows[1]["started_at"]
    assert rows[0]["conv_start_text"] == "text 1"
    assert rows[1]["storage"] == "cold" and rows[1]["conv_start_text"] == "text 2"
    assert rows[1]["started_at"] < rows[1]["last_activity_at"]
    assert [r["conversation_id"] for r in await index.list_user_conversations(user_id="u1", limit=1)] == ["c1"]
    assert await index.list_user_conversations(user_id="u1", since=_days_ago(100)) == [
        {"conversation_id": "c1", "last_activity_at": hot["last_activity_at"].isoformat(), "started_at": rows[0]["started_at"]},
    ]


@pytest.mark.asyncio
async def test_listing_reads_no_part_and_opening_reads_only_that_conversations_parts():
    index = await _archived_index({}, [
        _live_msg(1, days_ago=200, conv="a", tags=["turn:a1", "conv.start", "artifact:turn.fingerprint.v1"], turn="a1"),
        _live_msg(2, days_ago=150, conv="b", tags=["turn:b1"], turn="b1"),
        _live_msg(3, days_ago=120, conv="c", tags=["turn:c1"], turn="c1"),
    ])
    retention = index.cold_retention
    reads: list = []
    original = retention.archive.read_part

    async def counting(manifest):
        reads.append(manifest["batch_id"])
        return await original(manifest)

    retention.archive.read_part = counting
    listed = await index.list_user_conversations(user_id="u1")
    assert sorted(r["conversation_id"] for r in listed) == ["a", "b", "c"] and reads == []
    occ = await index.get_conversation_turn_ids_from_tags(user_id="u1", conversation_id="b")
    assert [o["turn_id"] for o in occ] == ["b1"]
    assert len(reads) == 1 and reads[0].startswith((_days_ago(150) - timedelta(minutes=2)).strftime("%Y%m%d"))


@pytest.mark.asyncio
async def test_deleting_an_archived_conversation_removes_it_from_the_index():
    index = await _archived_index({}, [
        _live_msg(1, days_ago=150, conv="a", tags=["turn:a1"], turn="a1"),
        _live_msg(2, days_ago=150, conv="b", tags=["turn:b1"], turn="b1"),
    ])
    retention = index.cold_retention
    await retention.delete_messages(actor="owner", user_id="u1", conversation_id="a")
    assert [r["conversation_id"] for r in await index.list_user_conversations(user_id="u1")] == ["b"]
    assert [o["turn_id"] for o in await index.get_conversation_turn_ids_from_tags(user_id="u1", conversation_id="b")] == ["b1"]


@pytest.mark.asyncio
async def test_batches_archived_before_the_index_existed_are_indexed_on_the_next_run():
    db, _, _, _, retention = _setup()
    db.messages = [_live_msg(1, days_ago=150, conv="a", tags=["turn:a1"], turn="a1")]
    await retention.archive_before(datetime.now(timezone.utc) - timedelta(days=90))
    for row in db.conversations:  # as if indexed by the release without per-start expiry
        row["start_ts_list"] = None
    summary = await retention.archive_before(datetime.now(timezone.utc) - timedelta(days=90))
    assert summary["indexed"] == 1
    assert [c["conversation_id"] for c in db.live_conversations()] == ["a"]


@pytest.mark.asyncio
async def test_an_expired_start_never_shows_in_the_list_even_when_the_scope_mixes_ttls():
    # Infra's audit of #318 (P2): one conversation, one UTC day, a start that
    # has expired (30-day TTL) beside a live message (365-day TTL).
    start = _live_msg(1, days_ago=120, conv="mix", turn="m1",
                      tags=["turn:m1", "conv.start", "artifact:turn.fingerprint.v1"])
    start["ttl_days"], start["text"] = 30, "SYNTHETIC_EXPIRED_START"
    live = _live_msg(2, days_ago=120, conv="mix", turn="m2", tags=["turn:m2"])
    live["ttl_days"] = 365
    later = _live_msg(3, days_ago=110, conv="mix", turn="m3",
                      tags=["turn:m3", "conv.start", "artifact:turn.fingerprint.v1"])
    later["ttl_days"], later["text"] = 365, "LIVE_START"
    index = await _archived_index({}, [start, live, later])
    [row] = await index.list_user_conversations(user_id="u1", include_conv_start_text=True)
    assert row["conversation_id"] == "mix" and row["conv_start_text"] == "LIVE_START"
    assert row["started_at"] == later["ts"].isoformat()
    occ = await index.get_conversation_turn_ids_from_tags(user_id="u1", conversation_id="mix")
    assert [o["turn_id"] for o in occ] == ["m2", "m3"]  # opening already drops the expired start


@pytest.mark.asyncio
async def test_a_start_that_expires_after_archiving_leaves_the_list():
    start = _live_msg(1, days_ago=120, conv="c", turn="t1",
                      tags=["turn:t1", "conv.start", "artifact:turn.fingerprint.v1"])
    start["ttl_days"], start["text"] = 130, "START"
    other = _live_msg(2, days_ago=120, conv="c", turn="t2", tags=["turn:t2"])
    index = await _archived_index({}, [start, other])
    [row] = await index.list_user_conversations(user_id="u1", include_conv_start_text=True)
    assert row["conv_start_text"] == "START"
    # Time passes past the start's own expiry; the list reads it at query time.
    db_rows = index.cold_retention._pool.db.conversations
    for c in db_rows:
        c["start_expires_list"] = [datetime.now(timezone.utc) - timedelta(seconds=1) for _ in c["start_expires_list"]]
    [row] = await index.list_user_conversations(user_id="u1", include_conv_start_text=True)
    assert row["conv_start_text"] is None and row["started_at"] is None


@pytest.mark.asyncio
async def test_a_start_outside_the_read_window_is_not_listed_even_when_its_batch_straddles_the_cutoff():
    # Infra's review of #319: one batch (one UTC day) holds a start just older
    # than the read's window floor and a message just inside it. The hot list
    # and opening both apply the window; the archived list must too.
    floor = (datetime.now(timezone.utc) - timedelta(days=300)).replace(hour=12, minute=0, second=0, microsecond=0)
    start = _live_msg(1, days_ago=0, conv="edge", turn="e1",
                      tags=["turn:e1", "conv.start", "artifact:turn.fingerprint.v1"])
    start["ts"], start["text"] = floor - timedelta(minutes=1), "OUTSIDE_WINDOW_MARKER"
    inside = _live_msg(2, days_ago=0, conv="edge", turn="e2", tags=["turn:e2"])
    inside["ts"] = floor + timedelta(minutes=1)
    index = await _archived_index({}, [start, inside])
    assert len(index.cold_retention._pool.db.batches) == 1  # one batch straddles the floor
    [row] = await index.cold_retention.list_archived_conversations(user_id="u1", from_ts=floor)
    assert row["conversation_id"] == "edge"
    assert row["conv_start_text"] is None and row["started_at"] is None
    [wider] = await index.cold_retention.list_archived_conversations(user_id="u1", from_ts=floor - timedelta(hours=1))
    assert wider["conv_start_text"] == "OUTSIDE_WINDOW_MARKER"


# W536 layout (operator, 2026-10-05: "in kdcube, amount of logged in users has
# no bound. they can participate many projects. liekwise the agents. on one
# day the amount of conversations can be HUGE"): a part never mixes users or
# conversations, and lives under the user's and the conversation's folder.


@pytest.mark.asyncio
async def test_a_part_holds_one_users_conversation_under_its_folder():
    db, backend, archive, _, retention = _setup()
    db.messages = [
        _msg(1, days_ago=200), _msg(2, days_ago=200, conv="c2"),
        _msg(3, days_ago=200, user="u2"), _msg(4, days_ago=199),
    ]

    summary = await retention.archive_before(NOW - timedelta(days=90))

    assert summary["rows"] == 4 and summary["batches"] == 4
    keys = sorted(b["part_key"] for b in db.batches.values())
    for key in keys:
        user, conversation, yyyy, mm, dd = key.split("/conversation-cold/")[1].split("/")[:5]
        records = await archive.read_part(await archive.read_manifest(key.replace(".jsonl.gz", ".manifest.json")))
        assert {(r["user_id"], r["conversation_id"]) for r in records} == {(user, conversation)}
        assert {r["ts"][:10] for r in records} == {f"{yyyy}-{mm}-{dd}"}
    assert [k.split("/conversation-cold/")[1].rsplit("/", 2)[0] for k in keys][:1][0].startswith("u1/c1/")


@pytest.mark.asyncio
async def test_a_batch_that_mixes_users_is_refused():
    archive = ConversationColdArchive(InMemoryStorageBackend(), tenant="t", project="p")
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_cold import row_to_record

    records = [row_to_record(dict(_msg(1, days_ago=200), embedding=None)), row_to_record(dict(_msg(2, days_ago=200, user="u2"), embedding=None))]
    with pytest.raises(ValueError):
        await archive.write_batch(day=NOW.date(), batch_id="x", records=records)


def test_an_id_cannot_add_or_walk_folders():
    archive = ConversationColdArchive(InMemoryStorageBackend(), tenant="t", project="p")
    key = archive.part_key(NOW.date(), "b", user_id="a/../b", conversation_id="..")
    tail = key.split("/conversation-cold/")[1]
    assert tail.split("/")[0] == "a%2F..%2Fb" and tail.split("/")[1] == "%2E%2E"
    assert archive.part_key(NOW.date(), "b", user_id="cognito:1-2", conversation_id="telegram_9").split("/conversation-cold/")[1].startswith("cognito:1-2/telegram_9/")


@pytest.mark.asyncio
async def test_a_deletion_reads_only_that_conversations_parts():
    db, backend, archive, store, retention = _setup()
    db.messages = [_msg(1, days_ago=200), _msg(2, days_ago=200, user="u2", conv="c9")]
    await retention.archive_before(NOW - timedelta(days=90))
    reads: list[str] = []
    original = backend.read_bytes_a

    async def counting(path, *a, **k):
        reads.append(str(path))
        return await original(path, *a, **k)

    backend.read_bytes_a = counting
    await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")
    assert reads and all("/u1/c1/" in r for r in reads)


@pytest.mark.asyncio
async def test_a_legacy_day_part_stays_readable_and_a_deletion_splits_what_stays():
    db, backend, archive, store, retention = _setup()
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_cold import encode_part, row_to_record, sha256_hex

    day = (NOW - timedelta(days=200)).date()
    legacy = [_msg(1, days_ago=200), _msg(2, days_ago=200, conv="c2"), _msg(3, days_ago=200, user="u2")]
    records = [row_to_record(dict(m)) for m in legacy]
    data = encode_part(records)
    part_key, manifest_key = archive.part_key(day, "legacy"), archive.manifest_key(day, "legacy")
    assert "/conversation-cold/" + day.strftime("%Y/%m/%d") + "/legacy" in part_key
    manifest = {"batch_id": "legacy", "day": day.isoformat(), "row_count": 3, "ids": [1, 2, 3],
                "sha256": sha256_hex(data), "part_key": part_key,
                "min_ts": min(r["ts"] for r in records), "max_ts": max(r["ts"] for r in records)}
    await backend.write_bytes_a(part_key, data)
    await backend.write_bytes_a(manifest_key, json.dumps(manifest).encode())
    db.batches["legacy"] = {"batch_id": "legacy", "day": day, "row_count": 3,
                            "max_ts": datetime.fromisoformat(manifest["max_ts"]), "sha256": manifest["sha256"],
                            "state": "pruned", "error": None, "part_key": part_key, "manifest_key": manifest_key}
    await retention._index_batch(_Con(db), "legacy", records)

    assert sorted(r["id"] for r in await retention.fetch_cold(from_ts=NOW - timedelta(days=400))) == [1, 2, 3]

    await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")

    assert db.batches["legacy"]["state"] == "retired"
    live = [b for b in db.batches.values() if b["state"] == "pruned"]
    assert sorted(b["part_key"].split("/conversation-cold/")[1].rsplit("/", 1)[0] for b in live) == [
        "u1/c2/" + day.strftime("%Y/%m/%d"), "u2/c1/" + day.strftime("%Y/%m/%d"),
    ]
    assert sorted(r["id"] for r in await retention.fetch_cold(from_ts=NOW - timedelta(days=400))) == [2, 3]


@pytest.mark.asyncio
async def test_the_next_archive_run_moves_legacy_day_parts_to_the_user_layout():
    db, backend, archive, store, retention = _setup()
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_cold import encode_part, row_to_record, sha256_hex

    day = (NOW - timedelta(days=200)).date()
    legacy = [_msg(1, days_ago=200), _msg(2, days_ago=200, conv="c2"), _msg(3, days_ago=200, user="u2")]
    records = [row_to_record(dict(m)) for m in legacy]
    data = encode_part(records)
    part_key, manifest_key = archive.part_key(day, "legacy"), archive.manifest_key(day, "legacy")
    manifest = {"batch_id": "legacy", "day": day.isoformat(), "row_count": 3, "ids": [1, 2, 3],
                "sha256": sha256_hex(data), "part_key": part_key,
                "min_ts": min(r["ts"] for r in records), "max_ts": max(r["ts"] for r in records)}
    await backend.write_bytes_a(part_key, data)
    await backend.write_bytes_a(manifest_key, json.dumps(manifest).encode())
    db.batches["legacy"] = {"batch_id": "legacy", "day": day, "row_count": 3,
                            "max_ts": datetime.fromisoformat(manifest["max_ts"]), "sha256": manifest["sha256"],
                            "state": "pruned", "error": None, "part_key": part_key, "manifest_key": manifest_key}
    await retention._index_batch(_Con(db), "legacy", records)

    summary = await retention.archive_before(NOW - timedelta(days=90))

    assert summary["relaid"] == 1
    assert db.batches["legacy"]["state"] == "retired" and not await backend.exists_a(part_key)
    live = sorted(b["part_key"].split("/conversation-cold/")[1].rsplit("/", 1)[0] for b in db.batches.values() if b["state"] == "pruned")
    d = day.strftime("%Y/%m/%d")
    assert live == [f"u1/c1/{d}", f"u1/c2/{d}", f"u2/c1/{d}"]
    assert sorted(r["id"] for r in await retention.fetch_cold(from_ts=NOW - timedelta(days=400))) == [1, 2, 3]
    assert (await retention.archive_before(NOW - timedelta(days=90)))["relaid"] == 0


async def _legacy_part(db, backend, archive, retention, batch_id="legacy", ids=(1, 2, 3)):
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_cold import encode_part, row_to_record, sha256_hex

    day = (NOW - timedelta(days=200)).date()
    shapes = {1: {}, 2: {"conv": "c2"}, 3: {"user": "u2"}, 4: {"conv": "c3"}, 5: {"user": "u3"}}
    records = [row_to_record(dict(_msg(i, days_ago=200, **shapes[i]))) for i in ids]
    data = encode_part(records)
    part_key, manifest_key = archive.part_key(day, batch_id), archive.manifest_key(day, batch_id)
    manifest = {"batch_id": batch_id, "day": day.isoformat(), "row_count": len(records), "ids": list(ids),
                "sha256": sha256_hex(data), "part_key": part_key,
                "min_ts": min(r["ts"] for r in records), "max_ts": max(r["ts"] for r in records)}
    await backend.write_bytes_a(part_key, data)
    await backend.write_bytes_a(manifest_key, json.dumps(manifest).encode())
    db.batches[batch_id] = {"batch_id": batch_id, "day": day, "row_count": len(records),
                            "max_ts": datetime.fromisoformat(manifest["max_ts"]), "sha256": manifest["sha256"],
                            "state": "pruned", "error": None, "part_key": part_key, "manifest_key": manifest_key}
    await retention._index_batch(_Con(db), batch_id, records)
    return part_key


# Review witnesses (kdcube #323 at 8498b26e, Infra), kept as regressions.


@pytest.mark.asyncio
async def test_a_deletion_completed_during_a_legacy_move_never_comes_back():
    db, backend, archive, store, retention = _setup()
    await _legacy_part(db, backend, archive, retention)
    written: list[str] = []
    original_read, original_write = archive.read_part, archive.write_batch
    armed = {"race": True}

    async def read_then_delete(manifest):
        records = await original_read(manifest)
        if armed.pop("race", False):  # the move has read the part; a deletion finishes now
            done = await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")
            assert done["cold_rows"] == 1
        return records

    async def recording_write(**kwargs):
        manifest = await original_write(**kwargs)
        written.append(manifest["part_key"])
        return manifest

    archive.read_part, archive.write_batch = read_then_delete, recording_write
    summary = await retention.archive_before(NOW - timedelta(days=90))

    assert summary["relaid"] == 0  # the deletion already moved it
    assert sorted(r["id"] for r in await retention.fetch_cold(from_ts=NOW - timedelta(days=400))) == [2, 3]
    live = {b["part_key"] for b in db.batches.values() if b["state"] == "pruned"}
    assert written and [await backend.exists_a(k) for k in written] == [k in live for k in written]  # the losing move left no files


@pytest.mark.asyncio
async def test_a_legacy_move_during_a_deletion_makes_the_deletion_plan_again():
    db, backend, archive, store, retention = _setup()
    await _legacy_part(db, backend, archive, retention)
    original = retention._cold_matches
    armed = {"race": True}

    async def plan_then_move(**kwargs):
        plan = await original(**kwargs)
        if armed.pop("race", False):  # the deletion has planned; an archive run moves the part now
            assert await retention._relayout_legacy_batches() == 1
        return plan

    retention._cold_matches = plan_then_move
    done = await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")

    assert done["cold_rows"] == 1 and db.deletions[-1]["state"] == "completed"
    assert sorted(r["id"] for r in await retention.fetch_cold(from_ts=NOW - timedelta(days=400))) == [2, 3]


@pytest.mark.asyncio
async def test_moving_legacy_parts_counts_toward_the_run_budget():
    db, backend, archive, store, retention = _setup()
    await _legacy_part(db, backend, archive, retention, "legacy-a", ids=(1, 2))
    await _legacy_part(db, backend, archive, retention, "legacy-b", ids=(4, 5))

    assert (await retention.archive_before(NOW - timedelta(days=90), max_batches=0))["relaid"] == 0
    assert db.batches["legacy-a"]["state"] == db.batches["legacy-b"]["state"] == "pruned"
    assert (await retention.archive_before(NOW - timedelta(days=90), max_batches=1))["relaid"] == 1
    assert sorted(b["state"] for k, b in db.batches.items() if k in ("legacy-a", "legacy-b")) == ["pruned", "retired"]



@pytest.mark.asyncio
async def test_the_run_budget_holds_when_one_fetch_spans_several_conversations():
    db, backend, archive, store, retention = _setup()
    db.messages = [_msg(1, days_ago=200), _msg(2, days_ago=200, conv="c2")]  # same day, two conversations

    summary = await retention.archive_before(NOW - timedelta(days=90), max_batches=1)

    assert summary["batches"] == 1 and summary["rows"] == 1
    # The oldest row's conversation (2 is a minute older) goes first; the other stays hot for the next run.
    assert [m["id"] for m in db.messages] == [1]
    assert (await retention.archive_before(NOW - timedelta(days=90), max_batches=1))["batches"] == 1
    assert db.messages == []


# The hot window is strict (G1): a value that is not a whole number of days
# >= 1 never becomes the default 90 or a 1-day window; the cron refuses to run.


@pytest.mark.parametrize(
    "assembly_value, env_value, resolved",
    [
        ("14", None, 14), ("'14'", None, 14), ("14d", None, "14d"), ("0", None, 0), ("-5", None, -5),
        ("14.5", None, 14.5), ("14", "0", 0), ("14", "-3", -3), ("14", "30", 30),
    ],
)
def test_hot_days_is_never_replaced_by_another_window(monkeypatch, tmp_path, assembly_value, env_value, resolved):
    from kdcube_ai_app.apps.chat.sdk.config import Settings
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import valid_hot_days

    path = tmp_path / "assembly.yaml"
    path.write_text(f"routines:\n  conversation_store:\n    hot_days: {assembly_value}\n")
    monkeypatch.setenv("ASSEMBLY_YAML_DESCRIPTOR_PATH", str(path))
    if env_value is None:
        monkeypatch.delenv("CONVERSATION_HOT_DAYS", raising=False)
    else:
        monkeypatch.setenv("CONVERSATION_HOT_DAYS", env_value)
    value = Settings().CONVERSATION_HOT_DAYS
    assert value == resolved and type(value) is type(resolved)
    assert valid_hot_days(value) == (resolved if isinstance(resolved, int) and resolved >= 1 else None)


def test_an_unparseable_env_hot_days_fails_loudly(monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.config import Settings

    monkeypatch.setenv("CONVERSATION_HOT_DAYS", "14d")
    with pytest.raises(Exception, match="CONVERSATION_HOT_DAYS"):
        Settings()


@pytest.mark.asyncio
@pytest.mark.parametrize("hot_days", ["14d", 0, -5, 14.5, None, True])
async def test_the_daily_archive_refuses_an_invalid_hot_days(monkeypatch, caplog, hot_days):
    from types import SimpleNamespace

    import kdcube_ai_app.infra.plugin.admin_bundle.entrypoint as admin
    import kdcube_ai_app.apps.chat.sdk.context.vector.conv_index as conv_index_module

    calls = []

    class _Retention:
        async def archive_before(self, cutoff):
            calls.append(cutoff)
            return {"resumed": 0, "stuck": 0, "batches": 0, "rows": 0}

    monkeypatch.setattr(conv_index_module.ConvIndex, "retention", lambda self, store=None: _Retention())
    owner = SimpleNamespace(pg_pool=_Pool(_Db()))
    monkeypatch.setattr(admin, "get_settings", lambda: SimpleNamespace(CONVERSATION_ARCHIVE_ENABLED=True, CONVERSATION_HOT_DAYS=hot_days))
    with caplog.at_level("INFO", logger=admin.logger.name):
        await admin.AdminBundleEntrypoint.archive_conversations(owner)
    assert calls == []
    assert any(r.levelname == "ERROR" and "[conversation-archive] invalid hot_days" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_the_daily_archive_logs_stuck_batches(monkeypatch, caplog):
    from types import SimpleNamespace

    import kdcube_ai_app.infra.plugin.admin_bundle.entrypoint as admin
    import kdcube_ai_app.apps.chat.sdk.context.vector.conv_index as conv_index_module

    calls = []

    class _Retention:
        async def archive_before(self, cutoff):
            calls.append(cutoff)
            return {"resumed": 0, "stuck": 1, "stuck_batches": ["20260910-1-1"], "batches": 2, "rows": 3}

    monkeypatch.setattr(conv_index_module.ConvIndex, "retention", lambda self, store=None: _Retention())
    owner = SimpleNamespace(pg_pool=_Pool(_Db()))
    monkeypatch.setattr(admin, "get_settings", lambda: SimpleNamespace(CONVERSATION_ARCHIVE_ENABLED=True, CONVERSATION_HOT_DAYS=14))
    with caplog.at_level("INFO", logger=admin.logger.name):
        await admin.AdminBundleEntrypoint.archive_conversations(owner)
    assert len(calls) == 1 and timedelta(days=13, hours=23) < datetime.now(timezone.utc) - calls[0] <= timedelta(days=14, minutes=1)
    (line,) = [r for r in caplog.records if r.getMessage().startswith("[conversation-archive] cutoff=")]
    assert line.levelname == "WARNING"
    assert "hot_days=14" in line.getMessage() and "stuck=1 stuck_batches=20260910-1-1" in line.getMessage()


@pytest.mark.asyncio
async def test_archive_runs_and_deletions_hold_the_same_schema_lock_and_release_it():
    db, _, _, _, retention = _setup()
    db.messages = [_msg(1, days_ago=200)]
    await retention.archive_before(NOW - timedelta(days=90))
    await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")

    # The archive takes the lock per step (one batch, then the empty selection);
    # the deletion queues for it with a bounded wait.
    assert db.locks[:4] == [("pg_try_advisory_lock", retention._lock_key()), ("pg_advisory_unlock", retention._lock_key())] * 2
    assert db.locks[4:] == [("lock_timeout", "30000ms"), ("pg_advisory_lock", retention._lock_key()), ("reset", None),
                            ("pg_advisory_unlock", retention._lock_key())]


@pytest.mark.asyncio
async def test_a_deletion_that_cannot_get_the_lock_in_time_is_refused_as_busy_and_records_nothing():
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import ConversationDeleteBusy

    db, _, _, _, retention = _setup()
    db.messages = [_msg(1, days_ago=10)]

    class LockNotAvailable(Exception):
        sqlstate = "55P03"

    original = _Con.fetchval

    async def held(self, query, *args):
        if " ".join(query.split()) == "SELECT pg_advisory_lock($1)":
            raise LockNotAvailable("canceling statement due to lock timeout")
        return await original(self, query, *args)

    _Con.fetchval = held
    try:
        with pytest.raises(ConversationDeleteBusy) as refused:
            await retention.delete_messages(actor="u1", user_id="u1", conversation_id="c1", lock_wait_s=0.25)
    finally:
        _Con.fetchval = original
    assert refused.value.code == "conversation_delete_busy"
    assert db.locks == [("lock_timeout", "250ms"), ("reset", None)]
    assert db.deletions == [] and [m["id"] for m in db.messages] == [1]


@pytest.mark.asyncio
async def test_a_retired_batch_whose_files_cannot_be_removed_is_logged_for_the_orphan_sweep(caplog):
    db, backend, archive, _, retention = _setup()
    db.messages = [_msg(1, days_ago=200)]
    original = archive.write_batch

    async def corrupt(**kw):
        manifest = await original(**kw)
        await backend.write_bytes_a(manifest["part_key"], b"tampered")
        return manifest

    archive.write_batch = corrupt  # type: ignore[assignment]
    with pytest.raises(ColdArchiveIntegrityError):
        await retention.archive_before(NOW - timedelta(days=90))
    (batch,) = db.batches.values()

    async def unavailable(part_key, manifest_key):
        raise OSError("storage unavailable")

    archive.delete_batch = unavailable  # type: ignore[assignment]
    with caplog.at_level("ERROR", logger="kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention"):
        done = await retention.delete_messages(actor="operator", user_id="u1", conversation_id="c1")

    assert done["unfinished_batches"] == {"finished": 0, "retired": 1} and done["hot_rows"] == 1
    assert batch["state"] == "retired" and db.deletions[0]["state"] == "completed"
    (line,) = [r for r in caplog.records if r.levelname == "ERROR"]
    message = line.getMessage()
    assert batch["batch_id"] in message and done["deletion_id"] in message
    assert f"part_key={batch['part_key']}" in message and f"manifest_key={batch['manifest_key']}" in message
    assert "storage unavailable" in message


@pytest.mark.asyncio
async def test_the_app_hard_delete_goes_through_retention_with_the_user_as_actor():
    from types import SimpleNamespace

    from kdcube_ai_app.apps.chat.sdk.solutions.conversation.ctx_rag import ContextRAGClient

    calls = []

    class _Retention:
        async def delete_messages(self, **scope):
            calls.append(scope)
            return {"hot_rows": 1, "cold_rows": 2, "body_objects": 3}

    class _Idx:
        def retention(self, *, store):
            calls.append(("store", store))
            return _Retention()

        async def delete_conversation(self, **scope):
            raise AssertionError("the hot-only path must not run when the cold tier is available")

    class _Sweep:
        def _who_and_id(self, user_id, fingerprint):
            return "registered", user_id

        async def delete_conversation(self, **scope):
            return {"messages": 4, "attachments": 0, "executions": 0}

    store = _Sweep()
    result = await ContextRAGClient.delete_conversation(
        SimpleNamespace(idx=_Idx(), store=store), tenant="t", project="p", user_id="u1", conversation_id="c1",
        user_type="registered", bundle_ids=["b1"],
    )

    assert result["deleted_messages"] == 3 and result["deleted_storage_messages"] == 4
    assert calls == [("store", store), {"actor": "u1", "user_id": "u1", "conversation_id": "c1", "bundle_id": None,
                                        "bundle_ids": ["b1"], "reason": "user delete", "lock_wait_s": 30.0}]


@pytest.mark.asyncio
async def test_the_app_hard_delete_refuses_retryably_when_the_cold_tier_is_unavailable():
    from types import SimpleNamespace

    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import ConversationDeleteUnavailable
    from kdcube_ai_app.apps.chat.sdk.solutions.conversation.ctx_rag import ContextRAGClient

    class _Idx:
        def retention(self, *, store):
            return None

        async def delete_conversation(self, **scope):
            raise AssertionError("a hot-only delete would leave the archived messages behind")

    class _Sweep:
        async def delete_conversation(self, **scope):
            raise AssertionError("nothing is deleted when the deletion is refused")

    with pytest.raises(ConversationDeleteUnavailable) as refused:
        await ContextRAGClient.delete_conversation(
            SimpleNamespace(idx=_Idx(), store=_Sweep()), tenant="t", project="p", user_id="u1", conversation_id="c1",
            user_type="registered", bundle_ids=["b1"],
        )
    assert refused.value.code == "conversation_delete_cold_unavailable"
