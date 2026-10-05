# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
"""Hot/cold retention of the conversation index.

`ConversationRetention` moves `conv_messages` rows older than the hot window
into the cold tier (`conv_cold.ConversationColdArchive`), reads them back for
date-filtered requests, and deletes a scope of messages from both tiers.

Every step is driven by durable rows in `conv_archive_batches`, never by
process memory: a batch is written, then read back and verified, and only
then are its hot rows deleted in the same transaction that marks it pruned.
A run that stops anywhere resumes from the ledger on the next run.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_cold import (
    ConversationColdArchive,
    filter_records,
    row_to_record,
)

logger = logging.getLogger(__name__)

DEFAULT_HOT_DAYS = 90
ARCHIVE_BATCH_SIZE = 500

_ROW_COLUMNS = (
    "id, user_id, bundle_id, agent_id, conversation_id, message_id, role, text, hosted_uri, "
    "ts, ttl_days, user_type, tags, turn_id, anchors_text, embedding::text AS embedding"
)


def _utc_day(ts: datetime) -> date:
    stamp = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).date()


def _as_utc(value: Any) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _edge_dict(edge: Any) -> Dict[str, Any]:
    out = dict(edge)
    created = out.get("created_at")
    if hasattr(created, "isoformat"):
        out["created_at"] = created.isoformat()
    return out


_START_TAGS = frozenset({"conv.start", "artifact:turn.fingerprint.v1"})


def conversation_index_rows(batch_id: str, records: Sequence[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
    """One `conv_archive_conversations` row per conversation scope in a batch."""

    groups: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for record in records:
        key = (
            str(record.get("user_id") or ""),
            str(record.get("conversation_id") or ""),
            str(record.get("bundle_id") or ""),
            str(record.get("agent_id") or ""),
        )
        ts = _as_utc(record["ts"])
        ttl = record.get("ttl_days")
        expires = ts + timedelta(days=int(ttl)) if ttl is not None else None
        entry = groups.setdefault(key, {
            "rows": 0, "min_ts": ts, "max_ts": ts, "expires_at": expires,
            "start_min_ts": None, "start_last_ts": None, "start_last_text": None,
            "starts": [],
        })
        entry["rows"] += 1
        entry["min_ts"] = min(entry["min_ts"], ts)
        entry["max_ts"] = max(entry["max_ts"], ts)
        if expires is None or entry["expires_at"] is None:
            entry["expires_at"] = None
        else:
            entry["expires_at"] = max(entry["expires_at"], expires)
        if _START_TAGS.issubset(set(record.get("tags") or ())):
            # Each start keeps its own expiry: a scope can mix TTLs, and the
            # list must never show a start that has expired.
            entry["starts"].append((ts, expires, record.get("text")))
            if entry["start_min_ts"] is None or ts < entry["start_min_ts"]:
                entry["start_min_ts"] = ts
            if entry["start_last_ts"] is None or ts >= entry["start_last_ts"]:
                entry["start_last_ts"] = ts
                entry["start_last_text"] = record.get("text")
    rows = []
    for key, e in sorted(groups.items()):
        starts = sorted(e["starts"], key=lambda s: s[0])
        rows.append((
            batch_id, *key, e["rows"], e["min_ts"], e["max_ts"], e["expires_at"],
            e["start_min_ts"], e["start_last_ts"], e["start_last_text"],
            [s[0] for s in starts], [s[1] for s in starts], [s[2] for s in starts],
        ))
    return rows


class ConversationRetention:
    """Archive, read and delete across the hot index and the cold tier."""

    def __init__(self, *, pool: Any, schema: str, archive: ConversationColdArchive, store: Any = None):
        self._pool = pool
        self.schema = schema
        self.archive = archive
        self.store = store

    # ---------- archive ----------
    async def archive_before(
        self,
        cutoff: datetime,
        *,
        batch_size: int = ARCHIVE_BATCH_SIZE,
        max_batches: Optional[int] = None,
    ) -> Dict[str, int]:
        """Move every hot row with ts < cutoff to the cold tier."""

        summary = {"resumed": 0, "batches": 0, "rows": 0, "indexed": 0, "relaid": 0}
        summary["resumed"] = await self._resume_unfinished()
        summary["indexed"] = await self._index_unindexed_batches()
        # Moving legacy parts counts toward the same budget as archiving.
        summary["relaid"] = await self._relayout_legacy_batches(limit=max_batches)
        while max_batches is None or summary["batches"] + summary["relaid"] < max_batches:
            async with self._pool.acquire() as con:
                rows = await con.fetch(
                    f"SELECT {_ROW_COLUMNS} FROM {self.schema}.conv_messages "
                    f"WHERE ts < $1 ORDER BY ts, id LIMIT $2",
                    cutoff,
                    max(1, int(batch_size)),
                )
                if not rows:
                    break
                ids = [int(r["id"]) for r in rows]
                edges = await con.fetch(
                    f"SELECT from_id, to_id, policy, created_at FROM {self.schema}.conv_artifact_edges "
                    f"WHERE from_id = ANY($1::bigint[]) OR to_id = ANY($1::bigint[])",
                    ids,
                )
            edges_by_id: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
            for edge in edges:
                as_dict = _edge_dict(edge)
                edges_by_id[int(edge["from_id"])].append(as_dict)
                if int(edge["to_id"]) != int(edge["from_id"]):
                    edges_by_id[int(edge["to_id"])].append(as_dict)
            # One part per user, conversation and UTC day: users and
            # conversations have no bound, so a part never mixes them.
            groups: Dict[Tuple[str, str, date], List[Dict[str, Any]]] = defaultdict(list)
            for row in rows:
                record = row_to_record(dict(row), edges=edges_by_id.get(int(row["id"]), ()))
                groups[(str(record.get("user_id") or ""), str(record.get("conversation_id") or ""), _utc_day(row["ts"]))].append(record)
            for key in sorted(groups, key=lambda k: (k[2], k[0], k[1])):
                if max_batches is not None and summary["batches"] + summary["relaid"] >= max_batches:
                    return summary  # the rest stays hot for the next run
                day, records = key[2], groups[key]
                await self._archive_batch(day, records)
                summary["batches"] += 1
                summary["rows"] += len(records)
        return summary

    async def _archive_batch(self, day: date, records: Sequence[Dict[str, Any]]) -> None:
        ids = [int(r["id"]) for r in records]
        batch_id = f"{day:%Y%m%d}-{min(ids)}-{max(ids)}"
        manifest = await self.archive.write_batch(day=day, batch_id=batch_id, records=records)
        async with self._pool.acquire() as con:
            async with con.transaction():
                await con.execute(
                    f"""
                    INSERT INTO {self.schema}.conv_archive_batches
                        (batch_id, day, row_count, min_id, max_id, min_ts, max_ts, part_key, manifest_key, sha256, state)
                    VALUES ($1, $2, $3, $4, $5, $6::timestamptz, $7::timestamptz, $8, $9, $10, 'written')
                    ON CONFLICT (batch_id) DO UPDATE
                       SET sha256 = EXCLUDED.sha256, row_count = EXCLUDED.row_count,
                           state = 'written', error = NULL, updated_at = now()
                     WHERE {self.schema}.conv_archive_batches.state <> 'pruned'
                    """,
                    batch_id, day, len(ids), min(ids), max(ids),
                    datetime.fromisoformat(manifest["min_ts"]) if manifest.get("min_ts") else None,
                    datetime.fromisoformat(manifest["max_ts"]) if manifest.get("max_ts") else None,
                    manifest["part_key"], manifest["manifest_key"], manifest["sha256"],
                )
                await self._index_batch(con, batch_id, records)
        await self._verify_and_prune(manifest["manifest_key"], batch_id)

    async def _index_batch(self, con: Any, batch_id: str, records: Sequence[Dict[str, Any]]) -> None:
        """Record which conversations the batch holds (replacing any earlier rows for it)."""

        await con.execute(
            f"DELETE FROM {self.schema}.conv_archive_conversations WHERE batch_id = $1", batch_id
        )
        rows = conversation_index_rows(batch_id, records)
        if rows:
            await con.executemany(
                f"""
                INSERT INTO {self.schema}.conv_archive_conversations
                    (batch_id, user_id, conversation_id, bundle_id, agent_id, row_count, min_ts, max_ts,
                     expires_at, start_min_ts, start_last_ts, start_last_text,
                     start_ts_list, start_expires_list, start_text_list)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13::timestamptz[], $14::timestamptz[], $15::text[])
                """,
                rows,
            )

    async def _index_unindexed_batches(self) -> int:
        """Index live batches archived before the conversation index (or its per-start expiry) existed."""

        async with self._pool.acquire() as con:
            pending = await con.fetch(
                f"""
                SELECT b.batch_id, b.day, b.manifest_key FROM {self.schema}.conv_archive_batches b
                 WHERE b.state = 'pruned'
                   AND NOT EXISTS (SELECT 1 FROM {self.schema}.conv_archive_conversations c
                                    WHERE c.batch_id = b.batch_id AND c.start_ts_list IS NOT NULL)
                 ORDER BY b.day, b.batch_id
                """
            )
        for row in pending:
            manifest = await self.archive.read_manifest(row["manifest_key"])
            records = await self.archive.read_part(manifest)
            async with self._pool.acquire() as con:
                async with con.transaction():
                    await self._index_batch(con, row["batch_id"], records)
        return len(pending)

    async def _verify_and_prune(self, manifest_key: str, batch_id: str) -> None:
        manifest = await self.archive.read_manifest(manifest_key)
        try:
            await self.archive.verify_batch(manifest)
        except Exception as exc:
            async with self._pool.acquire() as con:
                await con.execute(
                    f"UPDATE {self.schema}.conv_archive_batches SET error = $2, updated_at = now() WHERE batch_id = $1",
                    batch_id, str(exc)[:500],
                )
            raise
        async with self._pool.acquire() as con:
            async with con.transaction():
                await con.execute(
                    f"UPDATE {self.schema}.conv_archive_batches SET state = 'verified', updated_at = now() "
                    f"WHERE batch_id = $1 AND state IN ('written', 'verified')",
                    batch_id,
                )
                await con.execute(
                    f"DELETE FROM {self.schema}.conv_messages WHERE id = ANY($1::bigint[])",
                    [int(i) for i in manifest["ids"]],
                )
                await con.execute(
                    f"UPDATE {self.schema}.conv_archive_batches SET state = 'pruned', updated_at = now() "
                    f"WHERE batch_id = $1",
                    batch_id,
                )

    async def _resume_unfinished(self) -> int:
        async with self._pool.acquire() as con:
            pending = await con.fetch(
                f"SELECT batch_id, day, manifest_key FROM {self.schema}.conv_archive_batches "
                f"WHERE state IN ('written', 'verified') ORDER BY day, batch_id"
            )
        for row in pending:
            await self._verify_and_prune(row["manifest_key"], row["batch_id"])
        return len(pending)

    async def watermark(self) -> Optional[datetime]:
        """The newest archived message time; reads older than it may need the cold tier."""

        async with self._pool.acquire() as con:
            return await con.fetchval(
                f"SELECT max(max_ts) FROM {self.schema}.conv_archive_batches WHERE state = 'pruned'"
            )

    # ---------- read ----------
    async def _pruned_batches(self, first_day: Optional[date] = None, last_day: Optional[date] = None) -> List[Any]:
        """The ledger's live cold batches, optionally within a day range.

        Reads and deletions follow the ledger, never a storage listing: a part
        left behind by an interrupted retirement or archive is not cold data.
        """

        args: List[Any] = []
        where = ["state = 'pruned'"]
        if first_day is not None:
            args.append(first_day)
            where.append(f"day >= ${len(args)}")
        if last_day is not None:
            args.append(last_day)
            where.append(f"day <= ${len(args)}")
        async with self._pool.acquire() as con:
            return await con.fetch(
                f"SELECT batch_id, day, part_key, manifest_key FROM {self.schema}.conv_archive_batches "
                f"WHERE {' AND '.join(where)} ORDER BY day, batch_id",
                *args,
            )

    async def fetch_cold(
        self,
        *,
        from_ts: Any,
        to_ts: Any = None,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        bundle_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        roles: Optional[Sequence[str]] = None,
        tags_all: Optional[Sequence[str]] = None,
        include_embedding: bool = False,
    ) -> List[Dict[str, Any]]:
        """Cold records in [from_ts, to_ts) for this scope, by time, without ranking."""

        mark = await self.watermark()
        if mark is None:
            return []
        start = _as_utc(from_ts)
        if start > mark:
            return []
        end = _as_utc(to_ts) if to_ts is not None else mark + timedelta(microseconds=1)
        records: List[Dict[str, Any]] = []
        for batch in await self._pruned_batches(_utc_day(start), _utc_day(min(end, mark))):
            manifest = await self.archive.read_manifest(batch["manifest_key"])
            for record in await self.archive.read_part(manifest):
                if start <= _as_utc(record["ts"]) < end:
                    records.append(record)
        records.sort(key=lambda r: (r["ts"], r["id"]))
        out = filter_records(
            records,
            user_id=user_id,
            conversation_id=conversation_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            roles=roles,
            tags_all=tags_all,
        )
        if not include_embedding:
            out = [{k: v for k, v in r.items() if k != "embedding"} for r in out]
        for record in out:
            record["storage"] = "cold"
        return out

    async def list_archived_conversations(
        self,
        *,
        user_id: str,
        from_ts: Any,
        bundle_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """The user's archived conversations from the conversation index; reads no part.

        Like the hot list, a conversation start counts only inside the read's
        rolling window (`from_ts`) and while its own TTL lasts.
        """

        args: List[Any] = [user_id, _as_utc(from_ts)]
        where = [
            "b.state = 'pruned'",
            "c.user_id = $1",
            "c.max_ts >= $2",
            "(c.expires_at IS NULL OR c.expires_at >= now())",
        ]
        if bundle_id:
            args.append(bundle_id)
            where.append(f"c.bundle_id = ${len(args)}")
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                f"""
                SELECT c.conversation_id,
                       max(c.max_ts) AS last_activity_at,
                       min(s.ts) FILTER (WHERE s.ts >= $2 AND (s.expires IS NULL OR s.expires >= now()))
                           AS started_at,
                       (array_agg(s.text ORDER BY s.ts DESC)
                           FILTER (WHERE s.ts >= $2 AND (s.expires IS NULL OR s.expires >= now())))[1]
                           AS conv_start_text
                  FROM {self.schema}.conv_archive_conversations c
                  JOIN {self.schema}.conv_archive_batches b ON b.batch_id = c.batch_id
                  LEFT JOIN LATERAL unnest(c.start_ts_list, c.start_expires_list, c.start_text_list)
                         AS s(ts, expires, text) ON TRUE
                 WHERE {' AND '.join(where)}
                 GROUP BY c.conversation_id
                """,
                *args,
            )
        return [dict(r) for r in rows]

    async def fetch_cold_conversation(
        self,
        *,
        user_id: str,
        conversation_id: str,
        from_ts: Any,
        bundle_id: Optional[str] = None,
        bundle_ids: Optional[Sequence[str]] = None,
        agent_id: Optional[str] = None,
        roles: Optional[Sequence[str]] = None,
        include_embedding: bool = False,
    ) -> List[Dict[str, Any]]:
        """One conversation's cold records since from_ts, oldest first; reads only the parts that hold it."""

        start = _as_utc(from_ts)
        args: List[Any] = [user_id, conversation_id, start]
        where = ["b.state = 'pruned'", "c.user_id = $1", "c.conversation_id = $2", "c.max_ts >= $3"]
        if bundle_id:
            args.append(bundle_id)
            where.append(f"c.bundle_id = ${len(args)}")
        elif bundle_ids is not None:
            args.append([str(v).strip() for v in bundle_ids if str(v).strip()])
            where.append(f"c.bundle_id = ANY(${len(args)}::text[])")
        if agent_id:
            args.append(agent_id)
            where.append(f"c.agent_id = ${len(args)}")
        async with self._pool.acquire() as con:
            batches = await con.fetch(
                f"""
                SELECT DISTINCT b.batch_id, b.day, b.manifest_key
                  FROM {self.schema}.conv_archive_conversations c
                  JOIN {self.schema}.conv_archive_batches b ON b.batch_id = c.batch_id
                 WHERE {' AND '.join(where)}
                 ORDER BY b.day, b.batch_id
                """,
                *args,
            )
        allowed = None
        if not bundle_id and bundle_ids is not None:
            allowed = {str(v).strip() for v in bundle_ids if str(v).strip()}
        records: List[Dict[str, Any]] = []
        for batch in batches:
            manifest = await self.archive.read_manifest(batch["manifest_key"])
            for record in await self.archive.read_part(manifest):
                if _as_utc(record["ts"]) < start:
                    continue
                if allowed is not None and record.get("bundle_id") not in allowed:
                    continue
                records.append(record)
        records.sort(key=lambda r: (r["ts"], r["id"]))
        out = filter_records(
            records,
            user_id=user_id,
            conversation_id=conversation_id,
            bundle_id=bundle_id or None,
            agent_id=agent_id or None,
            roles=roles,
        )
        if not include_embedding:
            out = [{k: v for k, v in r.items() if k != "embedding"} for r in out]
        for record in out:
            record["storage"] = "cold"
        return out

    # ---------- delete ----------
    async def delete_messages(
        self,
        *,
        actor: str,
        user_id: str,
        conversation_id: str,
        bundle_id: Optional[str] = None,
        tags_all: Optional[Sequence[str]] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Delete a scope of messages from the hot index, the cold tier and both tiers' bodies.

        The deletion is recorded in `conv_archive_deletions` before anything
        is deleted (who, when, the scope), then marked `completed` with its
        counts, or `failed` with the error; a failed deletion can be run again.
        """

        if not actor or not user_id or not conversation_id:
            raise ValueError("delete_messages needs actor, user_id and conversation_id")
        tags = [str(t) for t in (tags_all or ()) if str(t)]
        deletion_id = f"del_{uuid.uuid4().hex}"
        scope = {"user_id": user_id, "conversation_id": conversation_id, "bundle_id": bundle_id, "tags_all": tags}
        async with self._pool.acquire() as con:
            await con.execute(
                f"""
                INSERT INTO {self.schema}.conv_archive_deletions (deletion_id, actor, reason, scope, state)
                VALUES ($1, $2, $3, ($4::text)::jsonb, 'started')
                """,
                # Through text: a pool with a jsonb codec (the platform's)
                # would otherwise encode this JSON again and store a string.
                deletion_id, actor, reason or None, json.dumps(scope),
            )
        counts = {"hot_rows": 0, "body_objects": 0, "cold_rows": 0}
        try:
            args: List[Any] = [user_id, conversation_id]
            where = ["user_id = $1", "conversation_id = $2"]
            if bundle_id:
                args.append(bundle_id)
                where.append(f"bundle_id = ${len(args)}")
            if tags:
                args.append(tags)
                where.append(f"tags @> ${len(args)}::text[]")
            scope_sql = " AND ".join(where)
            # Bodies first: while their records exist, a failed deletion can
            # be run again and still find every body it has not removed.
            async with self._pool.acquire() as con:
                hot = await con.fetch(
                    f"SELECT hosted_uri FROM {self.schema}.conv_messages WHERE {scope_sql}", *args
                )
            cold_plan, cold_uris = await self._cold_matches(
                user_id=user_id, conversation_id=conversation_id, bundle_id=bundle_id, tags=tags
            )
            uris = {r["hosted_uri"] for r in hot if r["hosted_uri"]} | cold_uris
            counts["body_objects"] = await self._delete_bodies(uris)
            async with self._pool.acquire() as con:
                deleted = await con.fetch(
                    f"DELETE FROM {self.schema}.conv_messages WHERE {scope_sql} RETURNING id", *args
                )
            counts["hot_rows"] = len(deleted)
            for _attempt in range(_COLD_DELETION_ATTEMPTS):
                try:
                    counts["cold_rows"] += await self._apply_cold_deletion(cold_plan)
                    break
                except _BatchChanged as changed:
                    # Another run rewrote a part this deletion planned on (an
                    # archive run moving a legacy part): plan again from the ledger.
                    counts["cold_rows"] += changed.removed
                    cold_plan, more_uris = await self._cold_matches(
                        user_id=user_id, conversation_id=conversation_id, bundle_id=bundle_id, tags=tags
                    )
                    counts["body_objects"] += await self._delete_bodies(more_uris - uris)
                    uris |= more_uris
            else:
                raise RuntimeError("the cold tier kept changing during the deletion; run it again")
        except Exception as exc:
            await self._finish_deletion(deletion_id, "failed", counts, error=f"{type(exc).__name__}: {exc}")
            raise
        await self._finish_deletion(deletion_id, "completed", counts)
        return {"deletion_id": deletion_id, **counts}

    async def _delete_bodies(self, uris: Any) -> int:
        if self.store is None:
            return 0
        deleted = 0
        for uri in sorted(uris):
            if await self.store.delete_message(uri):
                deleted += 1
        return deleted

    async def _finish_deletion(self, deletion_id: str, state: str, counts: Dict[str, int], *, error: str = "") -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                f"""
                UPDATE {self.schema}.conv_archive_deletions
                   SET state = $2, hot_rows = $3, body_objects = $4, cold_rows = $5,
                       error = $6, completed_at = now()
                 WHERE deletion_id = $1
                """,
                deletion_id, state, counts["hot_rows"], counts["body_objects"], counts["cold_rows"], error[:500] or None,
            )

    async def _cold_matches(
        self,
        *,
        user_id: str,
        conversation_id: str,
        bundle_id: Optional[str],
        tags: Sequence[str],
    ) -> Tuple[List[Dict[str, Any]], set]:
        """The live cold batches holding matching records, and those records' body URIs."""

        plan: List[Dict[str, Any]] = []
        uris: set = set()
        # Only the batches the conversation index says hold this conversation:
        # never a scan of the whole cold tier.
        args: List[Any] = [user_id, conversation_id]
        where = ["b.state = 'pruned'", "c.user_id = $1", "c.conversation_id = $2"]
        if bundle_id:
            args.append(bundle_id)
            where.append(f"c.bundle_id = ${len(args)}")
        async with self._pool.acquire() as con:
            batches = await con.fetch(
                f"""
                SELECT DISTINCT b.batch_id, b.day, b.part_key, b.manifest_key
                  FROM {self.schema}.conv_archive_conversations c
                  JOIN {self.schema}.conv_archive_batches b ON b.batch_id = c.batch_id
                 WHERE {' AND '.join(where)}
                 ORDER BY b.day, b.batch_id
                """,
                *args,
            )
        for batch in batches:
            day, batch_id = batch["day"], batch["batch_id"]
            manifest = await self.archive.read_manifest(batch["manifest_key"])
            records = await self.archive.read_part(manifest)
            matching = filter_records(
                records,
                user_id=user_id,
                conversation_id=conversation_id,
                bundle_id=bundle_id,
                tags_all=tags,
                now=datetime.min.replace(tzinfo=timezone.utc),
            )
            if not matching:
                continue
            uris |= {r["hosted_uri"] for r in matching if r.get("hosted_uri")}
            plan.append({
                "day": day,
                "batch_id": batch_id,
                "part_key": batch["part_key"],
                "manifest_key": batch["manifest_key"],
                "records": records,
                "matching_ids": {int(r["id"]) for r in matching},
            })
        return plan, uris

    async def _relayout_legacy_batches(self, *, limit: Optional[int] = None) -> int:
        """Move parts written in the legacy day-only layout to the user/conversation layout.

        Each legacy part is read and verified, rewritten as one verified part
        per user and conversation, and retired in the same transaction that
        records the new parts; then its files are removed. A run that stops
        anywhere leaves the ledger consistent: the old part is live until the
        new ones are recorded. A part that a deletion or another run rewrote
        meanwhile is left to that run. At most `limit` parts move per call.
        Returns how many legacy parts moved.
        """

        if limit is not None and limit <= 0:
            return 0
        async with self._pool.acquire() as con:
            legacy = await con.fetch(
                f"SELECT batch_id, day, part_key, manifest_key FROM {self.schema}.conv_archive_batches "
                f"WHERE state = 'pruned' AND part_key ~ '/conversation-cold/[0-9]{{4}}/[0-9]{{2}}/[0-9]{{2}}/[^/]+$' "
                f"ORDER BY day, batch_id" + ("" if limit is None else f" LIMIT {int(limit)}")
            )
        moved = 0
        for batch in legacy:
            manifest = await self.archive.read_manifest(batch["manifest_key"])
            records = await self.archive.read_part(manifest)
            try:
                await self._apply_cold_deletion([{
                    "day": batch["day"],
                    "batch_id": batch["batch_id"],
                    "part_key": batch["part_key"],
                    "manifest_key": batch["manifest_key"],
                    "records": records,
                    "matching_ids": set(),
                }])
            except _BatchChanged:
                continue
            moved += 1
        return moved

    async def _apply_cold_deletion(self, plan: Sequence[Dict[str, Any]]) -> int:
        """Rewrite each planned batch without its matching records; returns how many were removed."""

        removed = 0
        for entry in plan:
            day, batch_id, matching_ids = entry["day"], entry["batch_id"], entry["matching_ids"]
            kept = [r for r in entry["records"] if int(r["id"]) not in matching_ids]
            written: List[Tuple[str, Dict[str, Any], List[Dict[str, Any]]]] = []
            try:
                await self._rewrite_cold_batch(day, batch_id, kept, written)
            except _BatchChanged:
                # Retired by another run since it was read: what this call
                # wrote is never recorded, so its files go too.
                for _new_id, new_manifest, _group in written:
                    await self.archive.delete_batch(new_manifest["part_key"], new_manifest["manifest_key"])
                raise _BatchChanged(removed)
            removed += len(matching_ids)
            # The ledger already says retired; removing the files is cleanup,
            # and a part left behind is never read (reads follow the ledger).
            await self.archive.delete_batch(entry["part_key"], entry["manifest_key"])
        return removed

    async def _retire(self, con: Any, batch_id: str) -> None:
        """Retire a live batch, or raise `_BatchChanged` when another run already did."""

        retired = await con.fetchval(
            f"UPDATE {self.schema}.conv_archive_batches SET state = 'retired', updated_at = now() "
            f"WHERE batch_id = $1 AND state = 'pruned' RETURNING batch_id",
            batch_id,
        )
        if retired is None:
            raise _BatchChanged(0)
        await con.execute(f"DELETE FROM {self.schema}.conv_archive_conversations WHERE batch_id = $1", batch_id)

    async def _rewrite_cold_batch(
        self,
        day: date,
        batch_id: str,
        kept: Sequence[Dict[str, Any]],
        written: List[Tuple[str, Dict[str, Any], List[Dict[str, Any]]]],
    ) -> None:
        """Write `kept` as verified parts, then record them and retire `batch_id` in one transaction."""

        if kept:
            # What stays is rewritten one part per user and conversation,
            # so a legacy day part that mixed them moves to the new layout.
            kept_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
            for record in kept:
                kept_groups[(str(record.get("user_id") or ""), str(record.get("conversation_id") or ""))].append(record)
            for scope in sorted(kept_groups):
                group = kept_groups[scope]
                new_id = f"{batch_id}-r{uuid.uuid4().hex[:8]}"
                new_manifest = await self.archive.write_batch(day=day, batch_id=new_id, records=group)
                await self.archive.verify_batch(new_manifest)
                written.append((new_id, new_manifest, group))
            async with self._pool.acquire() as con:
                async with con.transaction():
                    # Retire first: a batch another run already rewrote
                    # stops here, before anything stale is recorded.
                    await self._retire(con, batch_id)
                    for new_id, new_manifest, group in written:
                        ids = [int(r["id"]) for r in group]
                        await con.execute(
                            f"""
                            INSERT INTO {self.schema}.conv_archive_batches
                                (batch_id, day, row_count, min_id, max_id, min_ts, max_ts, part_key, manifest_key, sha256, state)
                            VALUES ($1, $2, $3, $4, $5, $6::timestamptz, $7::timestamptz, $8, $9, $10, 'pruned')
                            """,
                            new_id, day, len(ids), min(ids), max(ids),
                            datetime.fromisoformat(new_manifest["min_ts"]) if new_manifest.get("min_ts") else None,
                            datetime.fromisoformat(new_manifest["max_ts"]) if new_manifest.get("max_ts") else None,
                            new_manifest["part_key"], new_manifest["manifest_key"], new_manifest["sha256"],
                        )
                        await self._index_batch(con, new_id, group)
        else:
            async with self._pool.acquire() as con:
                async with con.transaction():
                    await self._retire(con, batch_id)


_COLD_DELETION_ATTEMPTS = 3


class _BatchChanged(Exception):
    """A cold batch was retired by another run between being read and rewritten."""

    def __init__(self, removed: int) -> None:
        super().__init__("cold batch changed")
        self.removed = removed


def _turn_key(record: Dict[str, Any]) -> Optional[str]:
    if record.get("turn_id"):
        return str(record["turn_id"])
    for tag in record.get("tags") or ():
        if str(tag).startswith("turn:"):
            return str(tag)[len("turn:"):]
    return None


def cold_turn_catalog(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn-catalog entries for cold records, the hot catalog's shape without ordinals.

    One entry per turn: its latest `kind:turn.log` artifact, with the turn's
    working summary, first user message and last assistant message as `about`.
    """

    by_turn: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = _turn_key(record)
        if key:
            by_turn[key].append(record)
    entries: List[Dict[str, Any]] = []
    for turn_id, items in by_turn.items():
        items = sorted(items, key=lambda r: (r["ts"], r["id"]))
        logs = [r for r in items if r.get("role") == "artifact" and "kind:turn.log" in (r.get("tags") or ())]
        if not logs:
            continue
        log = logs[-1]
        summaries = [r for r in items if r.get("role") == "assistant" and "kind:working.summary" in (r.get("tags") or ())]
        users = [r for r in items if r.get("role") == "user"]
        assistants = [
            r for r in items
            if r.get("role") == "assistant"
            and not ({"kind:working.summary", "chat:summary"} & set(r.get("tags") or ()))
        ]
        entry = {
            "ordinal": None,
            "total_turns": None,
            "id": log["id"],
            "message_id": log.get("message_id"),
            "role": log.get("role"),
            "text": log.get("text"),
            "hosted_uri": log.get("hosted_uri"),
            "ts": log.get("ts"),
            "tags": log.get("tags"),
            "turn_id": turn_id,
            "bundle_id": log.get("bundle_id"),
            "agent_id": log.get("agent_id"),
            "conversation_id": log.get("conversation_id"),
            "working_summary_text": summaries[-1]["text"] if summaries else None,
            "working_summary_ts": summaries[-1]["ts"] if summaries else None,
            "first_user_text": users[0]["text"] if users else None,
            "first_user_ts": users[0]["ts"] if users else None,
            "last_assistant_text": assistants[-1]["text"] if assistants else None,
            "last_assistant_ts": assistants[-1]["ts"] if assistants else None,
            "storage": "cold",
        }
        entries.append(entry)
    entries.sort(key=lambda e: (e["ts"], e["id"]))
    return entries


def hot_cutoff(hot_days: int, *, now: Optional[datetime] = None) -> datetime:
    """The archive cutoff for a hot window of `hot_days` days."""

    moment = now or datetime.now(timezone.utc)
    return moment - timedelta(days=max(1, int(hot_days)))
