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
from typing import Any, Dict, List, Optional, Sequence

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


def _edge_dict(edge: Any) -> Dict[str, Any]:
    out = dict(edge)
    created = out.get("created_at")
    if hasattr(created, "isoformat"):
        out["created_at"] = created.isoformat()
    return out


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

        summary = {"resumed": 0, "batches": 0, "rows": 0}
        summary["resumed"] = await self._resume_unfinished()
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
        await self._verify_and_prune(day, batch_id)

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
        start = from_ts if isinstance(from_ts, datetime) else datetime.fromisoformat(str(from_ts).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start > mark:
            return []
        records = await self.archive.read_range(start, to_ts)
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
        """Delete a scope of messages from the hot index, their bodies and the cold tier.

        The deletion is recorded in `conv_archive_deletions` with who, when,
        the scope and the counts.
        """

        if not actor or not user_id or not conversation_id:
            raise ValueError("delete_messages needs actor, user_id and conversation_id")
        tags = [str(t) for t in (tags_all or ()) if str(t)]
        args: List[Any] = [user_id, conversation_id]
        where = ["user_id = $1", "conversation_id = $2"]
        if bundle_id:
            args.append(bundle_id)
            where.append(f"bundle_id = ${len(args)}")
        if tags:
            args.append(tags)
            where.append(f"tags @> ${len(args)}::text[]")
        async with self._pool.acquire() as con:
            deleted = await con.fetch(
                f"DELETE FROM {self.schema}.conv_messages WHERE {' AND '.join(where)} RETURNING id, hosted_uri",
                *args,
            )
        bodies = 0
        if self.store is not None:
            for uri in sorted({r["hosted_uri"] for r in deleted if r["hosted_uri"]}):
                try:
                    if await self.store.delete_message(uri):
                        bodies += 1
                except Exception:
                    logger.exception("[conv_retention] body delete failed uri=%s", uri)
        cold_rows = await self._delete_cold(user_id=user_id, conversation_id=conversation_id, bundle_id=bundle_id, tags=tags)
        deletion_id = f"del_{uuid.uuid4().hex}"
        scope = {"user_id": user_id, "conversation_id": conversation_id, "bundle_id": bundle_id, "tags_all": tags}
        async with self._pool.acquire() as con:
            await con.execute(
                f"""
                INSERT INTO {self.schema}.conv_archive_deletions
                    (deletion_id, actor, reason, scope, hot_rows, body_objects, cold_rows)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
                """,
                deletion_id, actor, reason or None, json.dumps(scope), len(deleted), bodies, cold_rows,
            )
        return {"deletion_id": deletion_id, "hot_rows": len(deleted), "body_objects": bodies, "cold_rows": cold_rows}

    async def _delete_cold(
        self,
        *,
        user_id: str,
        conversation_id: str,
        bundle_id: Optional[str],
        tags: Sequence[str],
    ) -> int:
        """Rewrite every pruned batch that holds matching records without them."""

        async with self._pool.acquire() as con:
            batches = await con.fetch(
                f"SELECT batch_id, day FROM {self.schema}.conv_archive_batches WHERE state = 'pruned' ORDER BY day, batch_id"
            )
        removed = 0
        for batch in batches:
            day, batch_id = batch["day"], batch["batch_id"]
            manifest = await self.archive.read_manifest(day, batch_id)
            records = await self.archive.read_part(manifest)
            matching = {
                int(r["id"])
                for r in filter_records(
                    records,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    bundle_id=bundle_id,
                    tags_all=tags,
                    now=datetime.min.replace(tzinfo=timezone.utc),
                )
            }
            if not matching:
                continue
            kept = [r for r in records if int(r["id"]) not in matching]
            removed += len(matching)
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
            else:
                async with self._pool.acquire() as con:
                    await con.execute(
                        f"UPDATE {self.schema}.conv_archive_batches SET state = 'retired', updated_at = now() WHERE batch_id = $1",
                        batch_id,
                    )
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
