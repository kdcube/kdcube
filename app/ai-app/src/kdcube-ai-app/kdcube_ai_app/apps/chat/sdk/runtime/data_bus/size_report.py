# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""W827 S2a: how many bytes each part of a Data Bus stream entry takes, without reading out any value.

Read-only. It samples the newest entries of one bundle's message, result and DLQ streams and reports only
aggregates: entry counts, UTF-8 byte percentiles of the stored envelope and of its top-level components, per
subject when the subject is on the caller's allowlist ("other" otherwise), plus XLEN, MEMORY USAGE, the age range
of the sampled entries and how many lacked each component.

Newest-N percentiles describe the sample, not every protected byte, and are not by themselves a safe admission
bound (codex-infra, W827): a cap derived from them is a policy proposal for the coordinator.

It never returns a value, a field name below the top-level components, a principal, Card, grant, key or user
text. The byte counts are of the JSON exactly as the stream stores it (``sort_keys``, ``ensure_ascii=False``).
Run it against the live Redis only inside a coordinator window; the Redis URL comes from the environment and is
never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from typing import Any, Iterable, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import data_bus_stream_key

SCHEMA = "kdcube.data_bus.size_report.v1"
OTHER = "other"
UNPARSABLE = "unparsable"

# Top-level parts whose size is reported, per stream kind; a dotted name is one level down.
COMPONENTS: dict[str, tuple[str, ...]] = {
    "messages": ("actor", "actor.identity_authority", "payload", "reply", "trace"),
    "results": ("data", "error"),
    "dlq": ("message", "message.actor", "message.payload", "details", "fields"),
}


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _part(document: Mapping[str, Any], dotted: str) -> Any:
    value: Any = document
    for name in dotted.split("."):
        if not isinstance(value, Mapping) or name not in value:
            return None
        value = value[name]
    return value


def percentiles(values: Iterable[int]) -> dict[str, int]:
    """Nearest-rank p50/p90/p99 plus max, count and sum; all zero for no values."""
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "sum": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0}

    def rank(p: float) -> int:
        return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]

    return {"count": len(ordered), "sum": sum(ordered), "p50": rank(50), "p90": rank(90), "p99": rank(99),
            "max": ordered[-1]}


def _label(document: Mapping[str, Any], kind: str, allowed: frozenset[str]) -> str:
    subject = document.get("subject")
    if kind == "dlq" and isinstance(document.get("message"), Mapping):
        subject = document["message"].get("subject")
    return subject if isinstance(subject, str) and subject in allowed else OTHER


def summarize_entries(kind: str, raw_entries: Iterable[Any], *, allowed_subjects: Iterable[str] = ()) -> dict:
    """Aggregate stored ``json`` field values of one stream kind; returns sizes only."""
    allowed = frozenset(str(s) for s in allowed_subjects)
    entry_sizes: list[int] = []
    parts: dict[str, list[int]] = {name: [] for name in COMPONENTS[kind]}
    by_subject: dict[str, list[int]] = {}
    unparsable = 0
    for raw in raw_entries:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw or "")
        size = len(text.encode("utf-8"))
        entry_sizes.append(size)
        try:
            document = json.loads(text)
        except ValueError:
            document = None
        if not isinstance(document, Mapping):
            unparsable += 1
            by_subject.setdefault(UNPARSABLE, []).append(size)
            continue
        by_subject.setdefault(_label(document, kind, allowed), []).append(size)
        for name in parts:
            value = _part(document, name)
            if value is not None:
                parts[name].append(_size(value))
    return {
        "entry_bytes": percentiles(entry_sizes),
        "components": {name: percentiles(sizes) for name, sizes in parts.items()},
        "components_missing": {name: len(entry_sizes) - unparsable - len(sizes) for name, sizes in parts.items()},
        "by_subject": {label: percentiles(sizes) for label, sizes in sorted(by_subject.items())},
        "unparsable": unparsable,
        "subjects_not_allowlisted": len(by_subject.get(OTHER, ())),
    }


def _id_ms(stream_id: Any) -> int | None:
    text = stream_id.decode() if isinstance(stream_id, bytes) else str(stream_id or "")
    head = text.split("-", 1)[0]
    return int(head) if head.isdigit() else None


def age_range(entry_ids: Iterable[Any], now_ms: int) -> dict[str, int | None]:
    """Seconds since the oldest and newest sampled entry, from their stream IDs and the server clock."""
    stamps = [ms for ms in (_id_ms(i) for i in entry_ids) if ms is not None]
    if not stamps:
        return {"oldest_seconds": None, "newest_seconds": None}
    return {"oldest_seconds": max(0, (now_ms - min(stamps)) // 1000),
            "newest_seconds": max(0, (now_ms - max(stamps)) // 1000)}


async def stream_size_report(redis: Any, *, tenant: str, project: str, bundle_id: str, sample: int = 1000,
                             allowed_subjects: Iterable[str] = ()) -> dict:
    """Sample the newest ``sample`` entries of each stream (read-only: XLEN, MEMORY USAGE, XREVRANGE)."""
    sample = max(1, int(sample))
    allowed = tuple(allowed_subjects)
    seconds, micros = await redis.time()
    now_ms = int(seconds) * 1000 + int(micros) // 1000
    streams: dict[str, Any] = {}
    for kind in COMPONENTS:
        key = data_bus_stream_key(tenant=tenant, project=project, bundle_id=bundle_id, kind=kind)
        length = int(await redis.xlen(key) or 0)
        memory = await redis.memory_usage(key) if length else 0
        entries = await redis.xrevrange(key, count=sample) if length else []
        raw = [dict(fields or {}).get(b"json", dict(fields or {}).get("json")) for _, fields in entries]
        streams[kind] = {"xlen": length, "memory_usage_bytes": int(memory or 0), "sampled": len(raw),
                         "sample_age": age_range((entry_id for entry_id, _ in entries), now_ms),
                         **summarize_entries(kind, raw, allowed_subjects=allowed)}
    return {"schema": SCHEMA, "sample": sample, "observed_at_ms": now_ms, "streams": streams}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Data Bus entry sizes (aggregates only, read-only).")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--sample", type=int, default=1000, help="newest entries per stream (default 1000)")
    parser.add_argument("--subject", action="append", default=[], help="a subject to report by name (repeatable)")
    parser.add_argument("--redis-url-env", default="REDIS_URL", help="environment variable holding the Redis URL")
    args = parser.parse_args(argv)
    url = os.getenv(args.redis_url_env, "")
    if not url:
        print(f"{args.redis_url_env} is not set", file=sys.stderr)
        return 2

    async def run() -> dict:
        from redis.asyncio import Redis

        client = Redis.from_url(url)
        try:
            return await stream_size_report(client, tenant=args.tenant, project=args.project,
                                            bundle_id=args.bundle_id, sample=args.sample,
                                            allowed_subjects=args.subject)
        finally:
            await client.aclose()

    print(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
