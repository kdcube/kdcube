# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# sdk/context/memory/buckets.py

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import datetime
import json
import copy

from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient

def now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"

# ---------- Storage shapes (thin) ----------

class Signal(BaseModel):
    key: str
    value: Any
    weight: float = 0.5  # [0.05..1.0], decays outside LLM if you want
    last_seen_ts: Optional[str] = None
    support_turn_ids: List[str] = Field(default_factory=list)

class TimelineSlice(BaseModel):
    ts_from: str
    ts_to: str
    objective_hint: str = ""
    assertions: List[Signal] = Field(default_factory=list)
    exceptions: List[Signal] = Field(default_factory=list)
    facts: List[Signal] = Field(default_factory=list)

class MemoryBucket(BaseModel):
    version: str = "v2"
    bucket_id: str                      # stable id
    status: str = "enabled"             # "enabled" | "disabled"
    name: str                           # human title
    short_desc: str = ""                # one line summary
    topic_centroid: List[str] = Field(default_factory=list)
    objective_text: str = ""            # concise canonical objective
    updated_at: str = Field(default_factory=now_iso)
    # Compressed, ordered oldest->newest slices
    timeline: List[TimelineSlice] = Field(default_factory=list)

# ---------- “Cards” (thin form for picker/reranker) ----------
class BucketCard(BaseModel):
    bucket_id: str
    status: str = "enabled"
    name: str
    short_desc: str = ""
    topic_centroid: List[str] = Field(default_factory=list)
    objective_text: str = ""
    # show only a few top signals as a teaser for ranking
    top_signals: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)  # {"facts":[{key,value,weight}], "assertions":[...], "exceptions":[...]}

def make_bucket_card(b: MemoryBucket, *, max_per_kind: int = 4) -> BucketCard:
    def top(items: List[Signal]):
        arr = sorted(items, key=lambda s: float(s.weight), reverse=True)[:max_per_kind]
        return [{"key": s.key, "value": s.value, "weight": s.weight} for s in arr]
    facts: List[Signal] = []
    assertions: List[Signal] = []
    exceptions: List[Signal] = []
    if b.timeline:
        last = b.timeline[-1]  # freshest
        facts, assertions, exceptions = last.facts, last.assertions, last.exceptions
    return BucketCard(
        bucket_id=b.bucket_id,
        status=b.status,
        name=b.name,
        short_desc=b.short_desc,
        topic_centroid=b.topic_centroid,
        objective_text=b.objective_text,
        top_signals={
            "facts": top(facts),
            "assertions": top(assertions),
            "exceptions": top(exceptions),
        }
    )

# ---------- Helpers ----------
def clamp01(x: float, lo: float = 0.05, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))

def as_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)

ISO = lambda: datetime.datetime.utcnow().isoformat() + "Z"

# A bucket is THIN: a header + a compressed timeline (list of fused FSP “frames”).

# -------------------- Data Shapes (dict form for persistence) --------------------

def new_bucket(
        *,
        bucket_id: str,
        name: str,
        short_desc: str,
        topic_centroid: list[str],
        timeline: list[dict],
        enabled: bool = True,
) -> dict:
    return {
        "version": "v1",
        "bucket_id": bucket_id,
        "name": name,
        "short_desc": short_desc,
        "topic_centroid": list(topic_centroid or []),
        "enabled": bool(enabled),
        "timeline": list(timeline or []),   # ordered oldest→newest
        "updated_at": ISO(),
    }

def header_from_bucket(b: dict) -> dict:
    return {
        "bucket_id": b.get("bucket_id",""),
        "name": b.get("name",""),
        "short_desc": b.get("short_desc",""),
        "topic_centroid": list(b.get("topic_centroid") or []),
        "enabled": bool(b.get("enabled", True)),
        "updated_at": b.get("updated_at",""),
    }

def to_store_dict(mb: MemoryBucket) -> dict:
    """Convert MemoryBucket (pydantic) to the persisted dict shape used by BucketStore."""
    def _sig_list(arr: List[Signal]) -> List[dict]:
        return [s.model_dump() for s in arr or []]
    timeline = []
    for sl in mb.timeline or []:
        timeline.append({
            "ts_from": sl.ts_from, "ts_to": sl.ts_to,
            "objective_hint": sl.objective_hint,
            "assertions": _sig_list(sl.assertions),
            "exceptions": _sig_list(sl.exceptions),
            "facts": _sig_list(sl.facts),
        })
    return {
        "version": "v1",
        "bucket_id": mb.bucket_id,
        "name": mb.name,
        "short_desc": mb.short_desc,
        "topic_centroid": list(mb.topic_centroid or []),
        "enabled": (mb.status == "enabled"),
        "timeline": timeline,
        "updated_at": now_iso(),
    }

def _coerce_bucket_dict(bucket: dict | MemoryBucket) -> dict:
    if isinstance(bucket, MemoryBucket):
        return to_store_dict(bucket)
    # assume already in dict format produced by reconciler engineering
    out = copy.deepcopy(bucket)
    out["version"] = out.get("version") or "v1"
    out["updated_at"] = ISO()
    # normalize status→enabled
    status = out.get("status")
    if status in ("enabled", "disabled"):
        out["enabled"] = (status == "enabled")
    return out

# -------------------- Persistence Facade (via ContextRAGClient) --------------------

class BucketStore:
    """
    Persist buckets as single artifacts (header + timeline) under:
      kind = "conversation.memory.bucket.v1"
    Indexing rules:
      - Enabled buckets are queriable for picker.
      - Disabled buckets remain in history but are filtered out.
    """

    KIND_BUCKET = "conversation.memory.bucket.v1"

    def __init__(self, ctx_client: ContextRAGClient):
        self.ctx = ctx_client

    async def list_buckets(self, *, user: str, conversation_id: str, include_disabled: bool = False) -> list[dict]:
        res = await self.ctx.search(
            query=None,
            kinds=(f"artifact:{self.KIND_BUCKET}",),
            roles=("artifact",),
            scope="conversation",
            user_id=user, conversation_id=conversation_id,
            days=365, top_k=64, include_deps=False, sort="recency",
            with_payload=True,
            all_tags=[f"artifact:{self.KIND_BUCKET}"],
        )
        out = []
        for it in (res.get("items") or []):
            j = (it.get("payload") or {}).get("payload") or {}
            if not j:
                continue
            if not include_disabled and not bool(j.get("enabled", True)):
                continue
            out.append(j)
        return out

    async def get_bucket_by_id(self, *, user: str, conversation_id: str, bucket_id: str) -> Optional[dict]:
        arr = await self.list_buckets(user=user, conversation_id=conversation_id, include_disabled=True)
        for b in arr:
            if b.get("bucket_id") == bucket_id:
                return b
        return None

    async def save_bucket_upsert(
            self, *,
            tenant: str, project: str, user: str, user_type: str,
            conversation_id: str, turn_id: str,
            bundle_id: str,
            bucket: dict | MemoryBucket
    ) -> dict:
        """
        Idempotent: updates the *existing* index row for this bucket_id in place (if present),
        otherwise inserts a fresh artifact. Always writes a new blob and points the index to it.
        """
        b = _coerce_bucket_dict(bucket)
        tags_unique = [f"mem:bucket:{b.get('bucket_id','')}"]
        content = b
        res = await self.ctx.upsert_artifact(
            kind=self.KIND_BUCKET,
            tenant=tenant, project=project, user_id=user, user_type=user_type,
            conversation_id=conversation_id, turn_id=turn_id,
            content=content, unique_tags=tags_unique,
            bundle_id=bundle_id
        )
        return b | {"_write_mode": res.get("mode")}

    async def disable_bucket_upsert(
            self, *,
            tenant: str, project: str, user: str, user_type: str,
            conversation_id: str, turn_id: str,
            bundle_id: str,
            bucket_id: str
    ) -> Optional[dict]:
        """
        Load latest bucket doc, set enabled=False, and upsert (update) the same index row.
        """
        b = await self.get_bucket_by_id(user=user, conversation_id=conversation_id, bucket_id=bucket_id)
        if not b:
            return None
        b["enabled"] = False
        b["updated_at"] = ISO()
        tags_unique = [f"mem:bucket:{bucket_id}"]
        res = await self.ctx.upsert_artifact(
            kind=self.KIND_BUCKET,
            tenant=tenant, project=project, user_id=user, user_type=user_type,
            conversation_id=conversation_id, turn_id=turn_id,
            content=b, unique_tags=tags_unique,
            bundle_id=bundle_id
        )
        return b | {"_write_mode": res.get("mode")}
