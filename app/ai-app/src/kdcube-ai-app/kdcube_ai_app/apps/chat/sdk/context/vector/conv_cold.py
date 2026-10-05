# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
"""Cold tier of the conversation store.

`conv_messages` is the hot index: text, embedding and tags for the messages
of the last `hot_days`. Message bodies already live in bundle storage
(`ConversationStore`), so moving a message to the cold tier moves only its
index row. The archiver writes whole rows, embedding included (it cost money
to compute and is kept for the record), to bundle storage, partitioned like
the conversation store itself, by user and conversation, then by the
message's UTC day:

    cb/tenants/{tenant}/projects/{project}/conversation-cold/{user}/{conversation}/{yyyy}/{mm}/{dd}/{batch_id}.jsonl.gz
    cb/tenants/{tenant}/projects/{project}/conversation-cold/{user}/{conversation}/{yyyy}/{mm}/{dd}/{batch_id}.manifest.json

The number of users and conversations has no bound, so a part never mixes
them: one user's archive is one folder, and one conversation's is one folder
inside it. Parts written before this layout sit directly under
`conversation-cold/{yyyy}/{mm}/{dd}/`; the batch ledger records each part's
own location, and every read and deletion goes through the ledger.

The manifest carries the row count, the row ids, the time range and the
sha256 of the part. A batch is trusted only after its part is read back and
matches the manifest; `ConvIndex.archive_before` deletes hot rows only then.
A date-filtered read whose range reaches before the archive watermark reads
the cold days in range, ordered by time, without ranking.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

MANIFEST_SCHEMA = "kdcube.conversation-cold.manifest.v1"
ROW_SCHEMA = "kdcube.conversation-cold.row.v1"


class ColdArchiveIntegrityError(RuntimeError):
    """A cold part does not match its manifest."""


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_embedding(value: Any) -> Optional[List[float]]:
    """The pgvector text form ('[0.1,0.2]') or a list, as floats."""

    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [float(v) for v in value]
    text = str(value).strip()
    if not text:
        return None
    return [float(v) for v in json.loads(text)]


def row_to_record(row: Dict[str, Any], *, edges: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """One `conv_messages` row as a JSON-safe cold record."""

    ts = row.get("ts")
    return {
        "schema": ROW_SCHEMA,
        "id": int(row["id"]),
        "user_id": row.get("user_id"),
        "bundle_id": row.get("bundle_id"),
        "agent_id": row.get("agent_id"),
        "conversation_id": row.get("conversation_id"),
        "message_id": row.get("message_id"),
        "role": row.get("role"),
        "text": row.get("text"),
        "hosted_uri": row.get("hosted_uri"),
        "ts": _utc(ts).isoformat() if ts is not None else None,
        "ttl_days": row.get("ttl_days"),
        "user_type": row.get("user_type"),
        "tags": list(row.get("tags") or []),
        "turn_id": row.get("turn_id"),
        "anchors_text": row.get("anchors_text"),
        "embedding": parse_embedding(row.get("embedding")),
        "edges": [dict(edge) for edge in edges],
    }


def encode_part(records: Sequence[Dict[str, Any]]) -> bytes:
    """Deterministic gzip JSONL: the same records always give the same bytes."""

    lines = [json.dumps(record, sort_keys=True, separators=(",", ":")) for record in sorted(records, key=lambda r: r["id"])]
    payload = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    return gzip.compress(payload, mtime=0)


def decode_part(data: bytes) -> List[Dict[str, Any]]:
    text = gzip.decompress(data).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _segment(value: Any) -> str:
    """One path segment from a user or conversation id, kept readable.

    A slash or backslash in an id would add folders, and "." or ".." would
    walk them, so those are escaped; everything else is the id as the
    conversation store writes it.
    """

    text = str(value or "").replace("%", "%25").replace("/", "%2F").replace("\\", "%5C")
    if text in ("", ".", ".."):
        return "_" if not text else text.replace(".", "%2E")
    return text


def _name(entry: str) -> str:
    # Backends differ: the local one lists names, the in-memory one full keys.
    return str(entry).rstrip("/").rsplit("/", 1)[-1]


class ConversationColdArchive:
    """Reads and writes the cold tier in bundle storage (an `IStorageBackend`)."""

    def __init__(self, backend: Any, *, tenant: str, project: str, root_prefix: str = "cb"):
        self.backend = backend
        self.root = f"{root_prefix}/tenants/{tenant}/projects/{project}/conversation-cold"

    # ---------- keys ----------
    def day_prefix(self, day: date) -> str:
        """The folder of a legacy (day-only) part."""

        return f"{self.root}/{day.year:04d}/{day.month:02d}/{day.day:02d}"

    def scope_prefix(self, day: date, user_id: Any, conversation_id: Any) -> str:
        return (
            f"{self.root}/{_segment(user_id)}/{_segment(conversation_id)}"
            f"/{day.year:04d}/{day.month:02d}/{day.day:02d}"
        )

    def part_key(self, day: date, batch_id: str, *, user_id: Any = None, conversation_id: Any = None) -> str:
        prefix = self.scope_prefix(day, user_id, conversation_id) if user_id is not None else self.day_prefix(day)
        return f"{prefix}/{batch_id}.jsonl.gz"

    def manifest_key(self, day: date, batch_id: str, *, user_id: Any = None, conversation_id: Any = None) -> str:
        prefix = self.scope_prefix(day, user_id, conversation_id) if user_id is not None else self.day_prefix(day)
        return f"{prefix}/{batch_id}.manifest.json"

    # ---------- write ----------
    async def write_batch(self, *, day: date, batch_id: str, records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Write one part and then its manifest under the records' user and conversation. Returns the manifest.

        Every record of a batch belongs to one user and one conversation; a
        batch that mixes them is refused, so no part ever does.
        """

        if not records:
            raise ValueError("a cold batch needs at least one record")
        scopes = {(r.get("user_id"), r.get("conversation_id")) for r in records}
        if len(scopes) != 1:
            raise ValueError("a cold batch holds one user's records of one conversation")
        ((user_id, conversation_id),) = scopes
        data = encode_part(records)
        stamps = [_utc(r["ts"]) for r in records if r.get("ts")]
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "batch_id": batch_id,
            "day": day.isoformat(),
            "user_id": user_id,
            "conversation_id": conversation_id,
            "row_count": len(records),
            "ids": sorted(int(r["id"]) for r in records),
            "min_ts": min(stamps).isoformat() if stamps else None,
            "max_ts": max(stamps).isoformat() if stamps else None,
            "sha256": sha256_hex(data),
            "bytes": len(data),
            "part_key": self.part_key(day, batch_id, user_id=user_id, conversation_id=conversation_id),
            "manifest_key": self.manifest_key(day, batch_id, user_id=user_id, conversation_id=conversation_id),
            "archived_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.backend.write_bytes_a(manifest["part_key"], data, meta={"ContentType": "application/gzip"})
        await self.backend.write_bytes_a(
            manifest["manifest_key"],
            json.dumps(manifest, sort_keys=True).encode("utf-8"),
            meta={"ContentType": "application/json"},
        )
        return manifest

    async def read_manifest(self, manifest_key: str) -> Dict[str, Any]:
        """The manifest at the location the batch ledger recorded for it."""

        return json.loads(await self.backend.read_bytes_a(manifest_key))

    async def read_part(self, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The part's records, after checking them against the manifest."""

        data = await self.backend.read_bytes_a(manifest["part_key"])
        if sha256_hex(data) != manifest.get("sha256"):
            raise ColdArchiveIntegrityError(f"sha256 mismatch for {manifest['part_key']}")
        records = decode_part(data)
        ids = sorted(int(r["id"]) for r in records)
        if len(records) != manifest.get("row_count") or ids != sorted(manifest.get("ids") or []):
            raise ColdArchiveIntegrityError(f"row set mismatch for {manifest['part_key']}")
        return records

    async def verify_batch(self, manifest: Dict[str, Any]) -> None:
        await self.read_part(manifest)

    async def delete_batch(self, part_key: str, manifest_key: str) -> None:
        """Remove a part and its manifest at the locations the ledger recorded."""

        for key in (part_key, manifest_key):
            if await self.backend.exists_a(key):
                await self.backend.delete_a(key)

    # ---------- read ----------
    async def day_manifests(self, day: date) -> List[Dict[str, Any]]:
        prefix = self.day_prefix(day)
        if not await self.backend.exists_a(prefix + "/"):
            return []
        names = sorted(_name(entry) for entry in await self.backend.list_dir_a(prefix))
        manifests = []
        for name in names:
            if name.endswith(".manifest.json"):
                manifests.append(json.loads(await self.backend.read_bytes_a(f"{prefix}/{name}")))
        return manifests

    async def read_range(self, from_ts: Any, to_ts: Any = None) -> List[Dict[str, Any]]:
        """Every cold record with from_ts <= ts < to_ts, ordered by time."""

        start = _utc(from_ts)
        end = _utc(to_ts) if to_ts is not None else datetime.now(timezone.utc)
        out: List[Dict[str, Any]] = []
        day = start.date()
        while day <= end.date():
            for manifest in await self.day_manifests(day):
                for record in await self.read_part(manifest):
                    ts = _utc(record["ts"])
                    if start <= ts < end:
                        out.append(record)
            day += timedelta(days=1)
        out.sort(key=lambda r: (r["ts"], r["id"]))
        return out


def filter_records(
    records: Iterable[Dict[str, Any]],
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    bundle_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    roles: Optional[Sequence[str]] = None,
    tags_all: Optional[Sequence[str]] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """The hot index's scope filters, applied to cold records (TTL included)."""

    moment = now or datetime.now(timezone.utc)
    wanted_tags = set(tags_all or ())
    out = []
    for record in records:
        if user_id is not None and record.get("user_id") != user_id:
            continue
        if conversation_id is not None and record.get("conversation_id") != conversation_id:
            continue
        if bundle_id is not None and record.get("bundle_id") != bundle_id:
            continue
        if agent_id is not None and record.get("agent_id") != agent_id:
            continue
        if roles is not None and record.get("role") not in roles:
            continue
        if wanted_tags and not wanted_tags.issubset(set(record.get("tags") or ())):
            continue
        ttl = record.get("ttl_days")
        if ttl is not None and _utc(record["ts"]) + timedelta(days=int(ttl)) < moment:
            continue
        out.append(record)
    return out
