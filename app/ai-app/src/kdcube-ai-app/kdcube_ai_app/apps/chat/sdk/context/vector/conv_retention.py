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

        summary = {"resumed": 0, "batches": 0, "rows": 0, "indexed": 0}
        summary["resumed"] = await self._resume_unfinished()
        summary["indexed"] = await self._index_unindexed_batches()
        while max_batches is None or summary["batches"] < max_batches:
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
            by_day: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
            for row in rows:
                record = row_to_record(dict(row), edges=edges_by_id.get(int(row["id"]), ()))
                by_day[_utc_day(row["ts"])].append(record)
            for day in sorted(by_day):
                records = by_day[day]
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
                    manifest["part_key"], self.archive.manifest_key(day, batch_id), manifest["sha256"],
                )
                await self._index_batch(con, batch_id, records)
        await self._verify_and_prune(day, batch_id)

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
                SELECT b.batch_id, b.day FROM {self.schema}.conv_archive_batches b
                 WHERE b.state = 'pruned'
                   AND NOT EXISTS (SELECT 1 FROM {self.schema}.conv_archive_conversations c
                                    WHERE c.batch_id = b.batch_id AND c.start_ts_list IS NOT NULL)
                 ORDER BY b.day, b.batch_id
                """
            )
        for row in pending:
            manifest = await self.archive.read_manifest(row["day"], row["batch_id"])
            records = await self.archive.read_part(manifest)
            async with self._pool.acquire() as con:
                async with con.transaction():
                    await self._index_batch(con, row["batch_id"], records)
        return len(pending)

    async def _verify_and_prune(self, day: date, batch_id: str) -> None:
        manifest = await self.archive.read_manifest(day, batch_id)
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
                f"SELECT batch_id, day FROM {self.schema}.conv_archive_batches "
                f"WHERE state IN ('written', 'verified') ORDER BY day, batch_id"
            )
        for row in pending:
            await self._verify_and_prune(row["day"], row["batch_id"])
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
                f"SELECT batch_id, day FROM {self.schema}.conv_archive_batches "
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
            manifest = await self.archive.read_manifest(batch["day"], batch["batch_id"])
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
                SELECT DISTINCT b.batch_id, b.day
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
            manifest = await self.archive.read_manifest(batch["day"], batch["batch_id"])
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
                VALUES ($1, $2, $3, $4::jsonb, 'started')
                """,
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
            counts["cold_rows"] = await self._apply_cold_deletion(cold_plan)
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
        for batch in await self._pruned_batches():
            day, batch_id = batch["day"], batch["batch_id"]
            manifest = await self.archive.read_manifest(day, batch_id)
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
                "records": records,
                "matching_ids": {int(r["id"]) for r in matching},
            })
        return plan, uris

    async def _apply_cold_deletion(self, plan: Sequence[Dict[str, Any]]) -> int:
        """Rewrite each planned batch without its matching records; returns how many were removed."""

        removed = 0
        for entry in plan:
            day, batch_id, matching_ids = entry["day"], entry["batch_id"], entry["matching_ids"]
            kept = [r for r in entry["records"] if int(r["id"]) not in matching_ids]
            removed += len(matching_ids)
            if kept:
                new_id = f"{batch_id}-r{uuid.uuid4().hex[:8]}"
                new_manifest = await self.archive.write_batch(day=day, batch_id=new_id, records=kept)
                await self.archive.verify_batch(new_manifest)
                ids = [int(r["id"]) for r in kept]
                async with self._pool.acquire() as con:
                    async with con.transaction():
                        await con.execute(
                            f"""
                            INSERT INTO {self.schema}.conv_archive_batches
                                (batch_id, day, row_count, min_id, max_id, min_ts, max_ts, part_key, manifest_key, sha256, state)
                            VALUES ($1, $2, $3, $4, $5, $6::timestamptz, $7::timestamptz, $8, $9, $10, 'pruned')
                            """,
                            new_id, day, len(ids), min(ids), max(ids),
                            datetime.fromisoformat(new_manifest["min_ts"]) if new_manifest.get("min_ts") else None,
                            datetime.fromisoformat(new_manifest["max_ts"]) if new_manifest.get("max_ts") else None,
                            new_manifest["part_key"], self.archive.manifest_key(day, new_id), new_manifest["sha256"],
                        )
                        await con.execute(
                            f"UPDATE {self.schema}.conv_archive_batches SET state = 'retired', updated_at = now() WHERE batch_id = $1",
                            batch_id,
                        )
                        await self._index_batch(con, new_id, kept)
                        await con.execute(
                            f"DELETE FROM {self.schema}.conv_archive_conversations WHERE batch_id = $1", batch_id
                        )
            else:
                async with self._pool.acquire() as con:
                    async with con.transaction():
                        await con.execute(
                            f"UPDATE {self.schema}.conv_archive_batches SET state = 'retired', updated_at = now() WHERE batch_id = $1",
                            batch_id,
                        )
                        await con.execute(
                            f"DELETE FROM {self.schema}.conv_archive_conversations WHERE batch_id = $1", batch_id
                        )
            # The ledger already says retired; removing the files is cleanup,
            # and a part left behind is never read (reads follow the ledger).
            await self.archive.delete_batch(day, batch_id)
        return removed


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
