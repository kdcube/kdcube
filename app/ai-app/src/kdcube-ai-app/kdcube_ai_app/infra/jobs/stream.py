# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from kdcube_ai_app.infra.namespaces import REDIS, ns_key

logger = logging.getLogger(__name__)

BACKGROUND_JOB_GROUP = "kdcube-background-jobs.v1"
BACKGROUND_JOB_OPERATION = "__kdcube_on_job__"
BACKGROUND_JOB_QUEUE_ORDER: tuple[str, ...] = ("privileged", "registered", "anonymous", "paid")
BACKGROUND_JOB_STREAM_MAXLEN = max(128, int(os.getenv("BACKGROUND_JOB_STREAM_MAXLEN", "10000") or "10000"))
BACKGROUND_JOB_DEDUPE_TTL_SECONDS = max(60, int(os.getenv("BACKGROUND_JOB_DEDUPE_TTL_SECONDS", str(7 * 24 * 3600)) or "604800"))
BACKGROUND_JOB_AUTOCLAIM_IDLE_MS = max(1000, int(os.getenv("BACKGROUND_JOB_AUTOCLAIM_IDLE_MS", "60000") or "60000"))


def _utc_ts() -> float:
    return time.time()


def _clean_queue(value: str | None) -> str:
    raw = str(value or "registered").strip().lower()
    return raw if raw in {"privileged", "registered", "paid", "anonymous"} else "registered"


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value or "")


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_load(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        return value
    return json.loads(value)


@dataclass(frozen=True)
class BackgroundJob:
    job_id: str
    work_kind: str
    tenant: str
    project: str
    queue: str = "registered"
    bundle_id: str = ""
    user_id: str = ""
    user_type: str = "registered"
    dedupe_key: str = ""
    source: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    payload: Optional[Dict[str, Any]] = None
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id or "",
            "work_kind": self.work_kind or "",
            "tenant": self.tenant or "",
            "project": self.project or "",
            "queue": _clean_queue(self.queue),
            "bundle_id": self.bundle_id or "",
            "user_id": self.user_id or "",
            "user_type": self.user_type or _clean_queue(self.queue),
            "dedupe_key": self.dedupe_key or "",
            "source": dict(self.source or {}),
            "metadata": dict(self.metadata or {}),
            "payload": dict(self.payload or {}),
            "created_at": float(self.created_at or 0.0),
        }

    def to_fields(self) -> Dict[str, str]:
        created_at = float(self.created_at or _utc_ts())
        queue = _clean_queue(self.queue or self.user_type)
        return {
            "job_id": self.job_id or f"job_{uuid.uuid4().hex}",
            "work_kind": str(self.work_kind or "").strip(),
            "tenant": str(self.tenant or ""),
            "project": str(self.project or ""),
            "queue": queue,
            "bundle_id": str(self.bundle_id or ""),
            "user_id": str(self.user_id or ""),
            "user_type": str(self.user_type or queue),
            "dedupe_key": str(self.dedupe_key or ""),
            "source_json": _json_dump(self.source or {}),
            "metadata_json": _json_dump(self.metadata or {}),
            "payload_json": _json_dump(self.payload or {}),
            "created_at": str(created_at),
        }

    @classmethod
    def from_fields(cls, fields: Dict[Any, Any]) -> "BackgroundJob":
        data = {_decode(k): _decode(v) for k, v in dict(fields or {}).items()}
        payload = _json_load(data.get("payload_json") or "{}")
        source = _json_load(data.get("source_json") or "{}")
        metadata = _json_load(data.get("metadata_json") or "{}")
        return cls(
            job_id=str(data.get("job_id") or ""),
            work_kind=str(data.get("work_kind") or ""),
            tenant=str(data.get("tenant") or ""),
            project=str(data.get("project") or ""),
            queue=_clean_queue(data.get("queue") or data.get("user_type")),
            bundle_id=str(data.get("bundle_id") or ""),
            user_id=str(data.get("user_id") or ""),
            user_type=str(data.get("user_type") or data.get("queue") or "registered"),
            dedupe_key=str(data.get("dedupe_key") or ""),
            source=source if isinstance(source, dict) else {},
            metadata=metadata if isinstance(metadata, dict) else {},
            payload=payload if isinstance(payload, dict) else {},
            created_at=float(data.get("created_at") or 0.0),
        )


@dataclass(frozen=True)
class BackgroundJobClaim:
    stream_key: str
    stream_id: str
    job: BackgroundJob
    fields: Dict[str, Any]
    consumer_name: str


@dataclass(frozen=True)
class EnqueueResult:
    enqueued: bool
    job_id: str
    stream_key: str
    stream_id: str = ""
    reason: str = "enqueued"


class RedisBackgroundJobStream:
    """Redis Stream based ready-work queue for non-interactive processor jobs."""

    def __init__(
        self,
        redis: Any,
        *,
        tenant: str,
        project: str,
        group_name: str = BACKGROUND_JOB_GROUP,
        stream_maxlen: int = BACKGROUND_JOB_STREAM_MAXLEN,
    ) -> None:
        self.redis = redis
        self.tenant = tenant
        self.project = project
        self.group_name = group_name
        self.stream_maxlen = max(0, int(stream_maxlen or 0))

    def stream_key(self, queue: str | None = None) -> str:
        base = ns_key(REDIS.BACKGROUND.JOB_STREAM_PREFIX, tenant=self.tenant, project=self.project)
        return f"{base}:{_clean_queue(queue)}"

    def dedupe_key(self, value: str) -> str:
        base = ns_key(REDIS.BACKGROUND.JOB_DEDUPE_PREFIX, tenant=self.tenant, project=self.project)
        return f"{base}:{str(value or '').strip()}"

    async def enqueue(
        self,
        *,
        work_kind: str,
        payload: Dict[str, Any],
        queue: str = "registered",
        bundle_id: str = "",
        user_id: str = "",
        user_type: str = "registered",
        source: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        job_id: str = "",
        dedupe_key: str = "",
        dedupe_ttl_seconds: int = BACKGROUND_JOB_DEDUPE_TTL_SECONDS,
    ) -> EnqueueResult:
        queue_name = _clean_queue(queue or user_type)
        resolved_job_id = str(job_id or "").strip() or f"job_{uuid.uuid4().hex}"
        stream_key = self.stream_key(queue_name)
        dedupe_storage_key = ""
        if dedupe_key:
            dedupe_storage_key = self.dedupe_key(dedupe_key)
            did_set = await self.redis.set(
                dedupe_storage_key,
                resolved_job_id,
                nx=True,
                ex=max(1, int(dedupe_ttl_seconds or BACKGROUND_JOB_DEDUPE_TTL_SECONDS)),
            )
            if not did_set:
                return EnqueueResult(
                    enqueued=False,
                    job_id=resolved_job_id,
                    stream_key=stream_key,
                    reason="duplicate",
                )

        job = BackgroundJob(
            job_id=resolved_job_id,
            work_kind=work_kind,
            tenant=self.tenant,
            project=self.project,
            queue=queue_name,
            bundle_id=bundle_id,
            user_id=user_id,
            user_type=user_type or queue_name,
            dedupe_key=dedupe_key,
            source=source or {},
            metadata=metadata or {},
            payload=payload or {},
            created_at=_utc_ts(),
        )
        fields = job.to_fields()
        try:
            stream_id = await self._xadd(stream_key, fields)
        except Exception:
            if dedupe_storage_key:
                try:
                    await self.redis.delete(dedupe_storage_key)
                except Exception:
                    logger.debug("[background_jobs.enqueue] Failed to clean dedupe key %s", dedupe_storage_key, exc_info=True)
            raise
        logger.info(
            "[background_jobs.enqueue] stream=%s id=%s job_id=%s work_kind=%s bundle=%s user=%s dedupe=%s",
            stream_key,
            stream_id,
            resolved_job_id,
            work_kind,
            bundle_id,
            user_id,
            bool(dedupe_key),
        )
        return EnqueueResult(
            enqueued=True,
            job_id=resolved_job_id,
            stream_key=stream_key,
            stream_id=stream_id,
        )

    async def claim_next(
        self,
        *,
        consumer_name: str,
        queue_order: Iterable[str] = BACKGROUND_JOB_QUEUE_ORDER,
        count: int = 1,
        block_ms: int = 1,
        autoclaim_idle_ms: int = BACKGROUND_JOB_AUTOCLAIM_IDLE_MS,
    ) -> Optional[BackgroundJobClaim]:
        consumer = str(consumer_name or "background-worker").strip() or "background-worker"
        for queue in queue_order:
            stream_key = self.stream_key(queue)
            await self._ensure_group(stream_key)
            raw_items = await self._xreadgroup(stream_key, consumer_name=consumer, count=count, block_ms=block_ms)
            if not raw_items:
                raw_items = await self._xautoclaim(
                    stream_key,
                    consumer_name=consumer,
                    min_idle_ms=autoclaim_idle_ms,
                    count=count,
                )
            if not raw_items:
                continue
            stream_id, fields = raw_items[0]
            try:
                job = BackgroundJob.from_fields(fields)
            except Exception:
                logger.warning("[background_jobs.claim] Invalid job payload stream=%s id=%s", stream_key, stream_id, exc_info=True)
                await self.ack_stream_id(stream_key=stream_key, stream_id=stream_id)
                continue
            return BackgroundJobClaim(
                stream_key=stream_key,
                stream_id=stream_id,
                job=job,
                fields=dict(fields or {}),
                consumer_name=consumer,
            )
        return None

    async def ack(self, claim: BackgroundJobClaim) -> None:
        await self.ack_stream_id(stream_key=claim.stream_key, stream_id=claim.stream_id)

    async def ack_stream_id(self, *, stream_key: str, stream_id: str) -> None:
        stream_id = str(stream_id or "").strip()
        if not stream_id:
            return
        await self._ensure_group(stream_key)
        try:
            await self.redis.xack(stream_key, self.group_name, stream_id)
        except Exception:
            logger.debug("[background_jobs.ack] Failed to ack stream=%s id=%s", stream_key, stream_id, exc_info=True)

    async def _xadd(self, stream_key: str, fields: Dict[str, str]) -> str:
        try:
            stream_id = await self.redis.xadd(
                stream_key,
                fields,
                maxlen=self.stream_maxlen or None,
                approximate=True,
            )
        except TypeError:
            stream_id = await self.redis.xadd(stream_key, fields)
        if isinstance(stream_id, bytes):
            stream_id = stream_id.decode("utf-8")
        return str(stream_id or "")

    async def _ensure_group(self, stream_key: str) -> None:
        try:
            await self.redis.xgroup_create(stream_key, self.group_name, id="0-0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _xreadgroup(
        self,
        stream_key: str,
        *,
        consumer_name: str,
        count: int,
        block_ms: int,
    ) -> list[tuple[str, Dict[Any, Any]]]:
        response = await self.redis.xreadgroup(
            self.group_name,
            consumer_name,
            {stream_key: ">"},
            count=max(1, int(count or 1)),
            block=max(0, int(block_ms or 0)),
        )
        return self._flatten_xread_response(response)

    async def _xautoclaim(
        self,
        stream_key: str,
        *,
        consumer_name: str,
        min_idle_ms: int,
        count: int,
    ) -> list[tuple[str, Dict[Any, Any]]]:
        claimer = getattr(self.redis, "xautoclaim", None)
        if not callable(claimer):
            return []
        response = await claimer(
            stream_key,
            self.group_name,
            consumer_name,
            max(1, int(min_idle_ms or 1)),
            "0-0",
            count=max(1, int(count or 1)),
        )
        if isinstance(response, (tuple, list)) and len(response) >= 2:
            return self._flatten_claim_items(response[1] or [])
        return []

    @staticmethod
    def _flatten_xread_response(response: Any) -> list[tuple[str, Dict[Any, Any]]]:
        out: list[tuple[str, Dict[Any, Any]]] = []
        for stream_entry in response or []:
            if not isinstance(stream_entry, (tuple, list)) or len(stream_entry) < 2:
                continue
            for item in stream_entry[1] or []:
                if not isinstance(item, (tuple, list)) or len(item) < 2:
                    continue
                stream_id = item[0].decode("utf-8") if isinstance(item[0], bytes) else str(item[0] or "")
                fields = item[1] if isinstance(item[1], dict) else {}
                out.append((stream_id, fields))
        return out

    @staticmethod
    def _flatten_claim_items(items: Any) -> list[tuple[str, Dict[Any, Any]]]:
        out: list[tuple[str, Dict[Any, Any]]] = []
        for item in items or []:
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue
            stream_id = item[0].decode("utf-8") if isinstance(item[0], bytes) else str(item[0] or "")
            fields = item[1] if isinstance(item[1], dict) else {}
            out.append((stream_id, fields))
        return out
