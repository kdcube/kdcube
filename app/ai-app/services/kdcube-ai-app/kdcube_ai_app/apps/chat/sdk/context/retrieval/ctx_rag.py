# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/context/retrieval/ctx_rag.py

from __future__ import annotations

import datetime
import pathlib, json
from typing import Optional, Sequence, List, Dict, Any

from kdcube_ai_app.apps.chat.sdk.inventory import ModelServiceBase
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnLog

from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex

TURN_LOG_TAGS_BASE = ["kind:turn.log", "artifact:turn.log"]

class ContextRAGClient:
    def __init__(self, *,
                 conv_idx: ConvIndex,
                 store: ConversationStore,
                 model_service: ModelServiceBase,
                 default_ctx_path: Optional[str] = None):
        self.idx = conv_idx
        self.store = store
        self.model_service = model_service
        self.default_ctx_path = default_ctx_path or "context.json"

    def _load_ctx(self, ctx: Optional[dict]) -> dict:
        if ctx is not None:
            return ctx
        p = pathlib.Path(self.default_ctx_path)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        from kdcube_ai_app.infra.accounting import _get_context
        context = _get_context()
        context_snapshot = context.to_dict()
        return context_snapshot

    def _scope_from_ctx(self, ctx: dict, *, user_id=None, conversation_id=None, track_id=None) -> tuple[str,str,str]:
        user = user_id or ctx.get("user_id")
        conv = conversation_id or ctx.get("conversation_id")
        track = track_id or ctx.get("track_id")
        return user, conv, track

    # ---------- public API ----------

    async def search(
            self,
            *,
            query: Optional[str] = None,
            embedding: Optional[Sequence[float]] = None,
            kinds: Optional[Sequence[str]] = None,
            scope: str = "track",
            days: int = 90,
            top_k: int = 12,
            include_deps: bool = True,
            half_life_days: float = 7.0,
            ctx: Optional[dict] = None,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            track_id: Optional[str] = None,
            roles: tuple[str,...] = ("artifact","assistant","user"),
            with_payload: bool = False,
            sort: str = "hybrid",
            any_tags: Optional[Sequence[str]] = None,
            all_tags: Optional[Sequence[str]] = None,
            not_tags: Optional[Sequence[str]] = None,
            timestamp_filters: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        """
        Semantic/Hybrid search (needs embedding unless provided).
        """
        ctx_loaded = self._load_ctx(ctx)
        user, conv, track = self._scope_from_ctx(ctx_loaded, user_id=user_id, conversation_id=conversation_id, track_id=track_id)

        qvec = list(embedding) if embedding is not None else None
        if qvec is None and query:
            [qvec] = await self.model_service.embed_texts([query])
        # if qvec is None:
        #     # If caller truly wants recency-only, use .recent() instead of .search().
        #     raise ValueError("search() needs either 'embedding' or 'query' to create one. For recency, call recent().")

        rows = await self.idx.search_context(
            user_id=user,
            conversation_id=(conv or None),
            track_id=(track or None),
            query_embedding=qvec,
            top_k=top_k,
            days=days,
            scope=scope,
            roles=roles,
            kinds=kinds,
            half_life_days=half_life_days,
            include_deps=include_deps,
            sort=sort,
            timestamp_filters=timestamp_filters,
            any_tags=any_tags,
            all_tags=all_tags,
            not_tags=not_tags,
        )

        items = []
        for r in rows:
            item = {
                "id": r["id"],
                "message_id": r["message_id"],
                "role": r["role"],
                "text": r.get("text") or "",
                "ts": r["ts"].isoformat() if hasattr(r["ts"], "isoformat") else r["ts"],
                "tags": list(r.get("tags") or []),
                "score": float(r.get("score") or 0.0),
                "sim": float(r.get("sim") or 0.0),
                "rec": float(r.get("rec") or 0.0),
                "track_id": r.get("track_id"),
                "s3_uri": r.get("s3_uri"),
            }
            if include_deps and "deps" in r:
                item["deps"] = r["deps"]
            if with_payload:
                try:
                    doc = self.store.get_message(r["s3_uri"])
                    item["payload"] = doc
                except Exception:
                    pass
            items.append(item)
        return {"items": items}

    async def recent(
            self,
            *,
            kinds: Optional[Sequence[str]] = None,
            scope: str = "track",
            days: int = 90,
            limit: int = 12,
            ctx: Optional[dict] = None,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            track_id: Optional[str] = None,
            roles: tuple[str, ...] = ("artifact","assistant","user"),
            any_tags: Optional[Sequence[str]] = None,
            all_tags: Optional[Sequence[str]] = None,
            not_tags: Optional[Sequence[str]] = None,
            with_payload: bool = False,
    ) -> dict:
        """
        Pure-recency fetch (no embeddings). Fast path for "last N in track".
        """
        ctx_loaded = self._load_ctx(ctx)
        user, conv, track = self._scope_from_ctx(ctx_loaded, user_id=user_id, conversation_id=conversation_id, track_id=track_id)
        any_tags = list(any_tags or [])

        if kinds:
            all_tags = list(all_tags or [])
            all_tags += list(kinds)
        rows = await self.idx.fetch_recent(
            user_id=user,
            conversation_id=(conv or None),
            track_id=(track or None),
            roles=roles,
            any_tags=any_tags or None,
            all_tags=list(all_tags or []) or None,
            not_tags=list(not_tags or []) or None,
            limit=limit,
            days=days
        )
        items = []
        for r in rows:
            item = {
                "id": r["id"],
                "message_id": r["message_id"],
                "role": r["role"],
                "text": r.get("text") or "",
                "ts": r["ts"].isoformat() if hasattr(r["ts"], "isoformat") else r["ts"],
                "tags": list(r.get("tags") or []),
                "track_id": r.get("track_id"),
                "s3_uri": r.get("s3_uri"),
            }
            if with_payload:
                try:
                    doc = self.store.get_message(r["s3_uri"])
                    item["payload"] = doc
                except Exception:
                    pass
            items.append(item)
        return {"items": items}

    async def pull_text_artifact(self, *, artifact_uri: str) -> dict:
        doc = self.store.get_message(artifact_uri)
        return doc.get("payload") or {}

    async def save_turn_log_as_artifact(
            self,
            *,
            tenant: str, project: str, user: str,
            conversation_id: str, user_type: str,
            turn_id: str, track_id: Optional[str],
            log: TurnLog,
            extra_tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Writes markdown to store (assistant artifact) + indexes it."""
        md = log.to_markdown()
        payload = {"turn_log": log.to_payload()}

        tags = TURN_LOG_TAGS_BASE + [f"turn:{turn_id}"] + ([f"track:{track_id}"] if track_id else [])
        if extra_tags:
            tags.extend([t for t in extra_tags if isinstance(t, str) and t.strip()])
        s3_uri, message_id, rn = self.store.put_message(
            tenant=tenant, project=project, user=user, fingerprint=None,
            conversation_id=conversation_id, role="artifact", text=md,
            payload=payload,
            meta={"kind": "turn.log", "turn_id": turn_id, "track_id": track_id},
            embedding=None, user_type=user_type, turn_id=turn_id, track_id=track_id,
        )
        await self.idx.add_message(
            user_id=user, conversation_id=conversation_id, role="artifact",
            text=md, s3_uri=s3_uri, ts=log.started_at_iso,
            tags=tags,
            ttl_days=365, user_type=user_type, embedding=None, message_id=message_id, track_id=track_id
        )
        return {"s3_uri": s3_uri, "message_id": message_id, "rn": rn}

    async def materialize_turn(
            self,
            *,
            turn_id: str,
            scope: str = "track",
            days: int = 365,
            ctx: Optional[dict] = None,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            track_id: Optional[str] = None,
            with_payload: bool = True
    ) -> dict:
        """
        Returns the user msg, assistant reply, and user-visible artifacts for the turn.
        Visible artifacts include:
          - codegen.program.presentation (project canvas / draft)
          - codegen.program.out.deliverables
        """
        # 1) user
        u = await self.recent(
            scope=scope, days=days, limit=1, ctx=ctx,
            user_id=user_id, conversation_id=conversation_id, track_id=track_id,
            roles=("user",), all_tags=[f"turn:{turn_id}"], with_payload=with_payload
        )
        # 2) assistant
        a = await self.recent(
            scope=scope, days=days, limit=1, ctx=ctx,
            user_id=user_id, conversation_id=conversation_id, track_id=track_id,
            roles=("assistant",), all_tags=[f"turn:{turn_id}"], with_payload=with_payload
        )
        # 3) presentation (draft the user saw)
        prez = await self.recent(
            kinds=("artifact:codegen.program.presentation",),  # meta.kind
            scope=scope, days=days, limit=1, ctx=ctx,
            user_id=user_id, conversation_id=conversation_id, track_id=track_id,
            roles=("artifact",), all_tags=[f"turn:{turn_id}"], with_payload=with_payload
        )
        # 4) deliverables (file list user could download)
        dels = await self.recent(
            kinds=("artifact:codegen.program.out.deliverables",),
            scope=scope, days=days, limit=1, ctx=ctx,
            user_id=user_id, conversation_id=conversation_id, track_id=track_id,
            roles=("artifact",), all_tags=[f"turn:{turn_id}"], with_payload=with_payload
        )
        # 5) errors
        solver_failure = await self.recent(
            kinds=("artifact:solver:failure",),
            scope=scope, days=days, limit=1, ctx=ctx,
            user_id=user_id, conversation_id=conversation_id, track_id=track_id,
            roles=("artifact",), all_tags=[f"turn:{turn_id}"], with_payload=with_payload
        )
        # 6) citables
        citables = await self.recent(
            kinds=("artifact:codegen.program.citables",),
            scope=scope, days=days, limit=3, ctx=ctx,
            user_id=user_id, conversation_id=conversation_id, track_id=track_id,
            roles=("artifact",), all_tags=[f"turn:{turn_id}"], with_payload=with_payload
        )

        # 7) turn log
        turn_log = await self.recent(
            kinds=("artifact:turn.log",),
            scope=scope, days=days, limit=3, ctx=ctx,
            user_id=user_id, conversation_id=conversation_id, track_id=track_id,
            roles=("artifact",), all_tags=[f"turn:{turn_id}"], with_payload=with_payload
        )
        def first(results: dict) -> Optional[dict]:
            arr = next(iter(results.get("items") or []), None)
            return arr

        return {
            "user": first(u),
            "assistant": first(a),
            "presentation": first(prez),
            "deliverables": first(dels),
            "citables": first(citables),
            "solver_failure": first(solver_failure),
            "turn_log": first(turn_log),
        }

    async def append_reaction_to_turn_log(self, *,
                                          turn_id: str, reaction: str,
                                          tenant: str, project: str, user: str,
                                          fingerprint: Optional[str],
                                          user_type: str, conversation_id: str, track_id: str):

        payload = {"reaction": {"text": reaction, "ts": datetime.datetime.utcnow().isoformat()+"Z"}}
        # persist as a small artifact tied to the same turn (don’t overwrite)
        s3_uri, message_id, rn = self.store.put_message(
            tenant=tenant, project=project, user=user,
            conversation_id=conversation_id, role="artifact",
            text=f"[turn.log.reaction]\n{reaction}",
            payload=payload,
            meta={"kind": "turn.log.reaction", "turn_id": turn_id, "track_id": track_id},
            embedding=None, user_type=user_type, turn_id=turn_id, track_id=track_id,
            fingerprint=fingerprint
        )
        await self.idx.add_message(
            user_id=user, conversation_id=conversation_id, role="artifact",
            text=f"[turn.log.reaction] {reaction}", s3_uri=s3_uri, ts=payload["reaction"]["ts"],
            tags=["kind:turn.log.reaction", f"turn:{turn_id}", f"track:{track_id}"],
            ttl_days=365, user_type=user_type, embedding=None, message_id=message_id, track_id=track_id
        )


    async def save_artifact(
            self,
            *,
            kind: str,
            tenant: str, project: str, user_id: str,
            conversation_id: str, user_type: str,
            turn_id: str, track_id: Optional[str],
            content: dict,
            content_str: Optional[str] = None,
            extra_tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Writes markdown to store (assistant artifact) + indexes it."""

        artifact_tag = f"artifact:{kind}" if not kind.startswith("artifact:") else kind
        tags = [f"turn:{turn_id}", artifact_tag] + ([f"track:{track_id}"] if track_id else [])
        if not content_str:
            content_str = json.dumps(content) if isinstance(content, dict) else str(content)
        if extra_tags:
            tags.extend([t for t in extra_tags if isinstance(t, str) and t.strip()])
        s3_uri, message_id, rn = self.store.put_message(
            tenant=tenant, project=project, user=user_id, fingerprint=None,
            conversation_id=conversation_id, role="artifact", text=content_str,
            payload=content,
            meta={"kind": kind, "turn_id": turn_id, "track_id": track_id},
            embedding=None, user_type=user_type, turn_id=turn_id, track_id=track_id,
        )
        await self.idx.add_message(
            user_id=user_id, conversation_id=conversation_id, role="artifact",
            text=content_str, s3_uri=s3_uri, ts=datetime.datetime.utcnow().isoformat()+"Z",
            tags=tags,
            ttl_days=365, user_type=user_type, embedding=None, message_id=message_id, track_id=track_id
        )
        return {"s3_uri": s3_uri, "message_id": message_id, "rn": rn}

    async def _find_latest_artifact_by_tags(
            self, *, kind: str, user_id: str, conversation_id: str, all_tags: list[str]
    ) -> Optional[dict]:
        """
        Find the latest *index row* for an artifact of `kind` that contains ALL `all_tags`.
        Returns a slim row dict (id, message_id, role, text, s3_uri, ts, tags, track_id).
        """
        artifact_tag = f"artifact:{kind}" if not kind.startswith("artifact:") else kind
        # ensure the kind tag is in all_tags
        tags = list(dict.fromkeys(list(all_tags or []) + [artifact_tag]))

        res = await self.idx.fetch_recent(
            user_id=user_id,
            conversation_id=conversation_id,
            roles=("artifact",),
            all_tags=tags,       # ALL tags must be present
            limit=1,
            days=365
        )
        return (res[0] if res else None)

    async def upsert_artifact(
            self,
            *,
            kind: str,
            tenant: str, project: str, user_id: str,
            conversation_id: str, user_type: str,
            turn_id: str, track_id: Optional[str],
            content: dict,
            unique_tags: List[str],
    ) -> Dict[str, Any]:
        """
        Idempotent write of a single logical artifact (e.g., a memory bucket) identified
        by its unique_tags (e.g., ["mem:bucket:<id>"]). If an index row exists, update
        that row (text, s3_uri, tags, ts) in place; otherwise create a fresh artifact.

        Returns: {"mode": "update"|"insert", "id": <conv_messages.id>, "message_id": "...", "s3_uri": "..."}
        """
        # 1) find the existing row
        artifact_tag = f"artifact:{kind}" if not kind.startswith("artifact:") else kind
        all_tags = [artifact_tag] + list(unique_tags or [])
        existing = await self._find_latest_artifact_by_tags(
            kind=kind, user_id=user_id, conversation_id=conversation_id, all_tags=all_tags
        )

        # Normalize payload string (what we also index as text)
        content_str = json.dumps(content, ensure_ascii=False)

        if not existing:
            # No prior row -> normal create
            saved = await self.save_artifact(
                kind=kind,
                tenant=tenant, project=project, user_id=user_id, conversation_id=conversation_id,
                user_type=user_type, turn_id=turn_id, track_id=track_id,
                content=content, content_str=content_str, extra_tags=unique_tags,
            )
            return {"mode": "insert", **saved}

        #  Write a new message blob and then point the index row at it
        s3_uri, message_id, rn = self.store.put_message(
            tenant=tenant, project=project, user=user_id, fingerprint=None,
            conversation_id=conversation_id, role="artifact", text=content_str,
            payload=content,
            meta={"kind": kind, "turn_id": turn_id, "track_id": track_id},
            embedding=None, user_type=user_type, turn_id=turn_id, track_id=track_id,
        )

        # 3) update the existing index row in place
        #    merge/normalize tags (keep artifact kind + unique tags)
        # bump ts to now
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        tags = list(dict.fromkeys((existing.get("tags") or []) +
                                  [artifact_tag, f"turn:{turn_id}"] +
                                  list(unique_tags or [])))
        await self.idx.update_message(
            id=int(existing["id"]),
            text=content_str,
            tags=tags,
            s3_uri=s3_uri,
            ts=now_iso,
        )
        return {"mode": "update", "id": int(existing["id"]), "message_id": existing.get("message_id"), "s3_uri": s3_uri}

