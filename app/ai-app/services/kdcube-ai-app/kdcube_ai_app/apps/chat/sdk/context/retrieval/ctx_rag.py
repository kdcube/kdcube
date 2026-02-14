# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/context/retrieval/ctx_rag.py

from __future__ import annotations

import datetime, traceback, logging
import pathlib, json, asyncio
from typing import Optional, Sequence, List, Dict, Any, Union, Callable

from kdcube_ai_app.apps.chat.sdk.util import _turn_id_from_tags_safe, ts_key, isoz
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.timeline import (
    TIMELINE_KIND,
    SOURCES_POOL_KIND,
    parse_timeline_payload,
)
import kdcube_ai_app.apps.chat.sdk.tools.citations as citation_utils

from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore, MAX_CONCURRENT_ARTIFACT_FETCHES
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex

logger = logging.getLogger(__name__)
TURN_LOG_TAGS_BASE = ["kind:turn.log", "artifact:turn.log"]

UI_ARTIFACT_TAGS = {
    "artifact:conv.artifacts.stream",
    "artifact:user.attachment",
    "artifact:turn.log.reaction",
    "artifact:conv.user_shortcuts",
    "chat:user",
    "chat:assistant"
}

SOURCES_POOL_ARTIFACT_TAG = f"artifact:{SOURCES_POOL_KIND}"

FINGERPRINT_KIND = "artifact:turn.fingerprint.v1"
CONV_START_FPS_TAG = "conv.start"
TURNS_SUMMARY_TAG = "conv.range.summary"

def unwrap_payload(p: dict) -> dict:
    """
    Accepts either a raw payload dict or a message record dict (with a 'payload' field)
    and returns the payload content. If the payload itself is another message record
    (legacy double-wrap), unwrap one extra level.
    """
    if not isinstance(p, dict):
        return {}

    def _looks_like_record(d: dict) -> bool:
        if not isinstance(d, dict):
            return False
        # Message records have a payload plus at least one of these fields.
        return "payload" in d and any(k in d for k in ("role", "meta", "text", "timestamp", "message_id"))

    cur = p
    if _looks_like_record(cur):
        cur = cur.get("payload") or {}
    # Handle legacy double-wrapped payloads
    if _looks_like_record(cur):
        cur = cur.get("payload") or {}
    return cur or {}

class ContextRAGClient:
    def __init__(self, *,
                 conv_idx: ConvIndex,
                 store: ConversationStore,
                 model_service: ModelServiceBase):
        self.idx = conv_idx
        self.store = store
        self.model_service = model_service

    def _load_ctx(self, ctx: Optional[dict]) -> dict:
        if ctx is not None:
            return ctx
        from kdcube_ai_app.infra.accounting import _get_context
        context = _get_context()
        context_snapshot = context.to_dict()
        return context_snapshot

    def _scope_from_ctx(self, ctx: dict, *,
                        user_id=None, conversation_id=None, turn_id=None, bundle_id=None) -> tuple[str, Optional[str], Optional[str]]:
        user = user_id or ctx.get("user_id")
        conv = conversation_id or ctx.get("conversation_id")
        bundle = bundle_id or ctx.get("bundle_id") or ctx.get("app_bundle_id")
        return user, conv, bundle

    async def _materialize_payloads_for_items(
            self,
            items: List[Dict[str, Any]],
            *,
            s3_field: str = "hosted_uri",
            out_field: str = "payload",
            max_concurrent: int = MAX_CONCURRENT_ARTIFACT_FETCHES,
    ) -> None:
        """
        Concurrently load message JSONs for items, storing the result under `out_field`.

        - Does NOT drop items on failure: if get_message() fails, item is left as-is.
        - Strips 'embedding' from the loaded record to save memory.
        - Uses a per-process semaphore to avoid unbounded fan-out.
        """
        if not items:
            return

        sem = asyncio.Semaphore(max_concurrent)

        async def _one(item: Dict[str, Any]) -> None:
            hosted_uri = item.get(s3_field)
            if not hosted_uri:
                return

            async with sem:
                try:
                    data = await self.store.get_message(hosted_uri)
                except Exception:
                    # Keep item, just skip payload
                    return

            if isinstance(data, dict) and "embedding" in data:
                # Avoid mutating shared dicts accidentally
                data = dict(data)
                data.pop("embedding", None)

            if isinstance(data, dict):
                # Merge full record (meta, payload, timestamps) onto the item.
                for key, value in data.items():
                    item[key] = value

            if isinstance(data, dict) and isinstance(data.get("payload"), dict):
                item[out_field] = data.get("payload")
            else:
                item[out_field] = data

        await asyncio.gather(*(_one(it) for it in items if it.get(s3_field)))

    async def _materialize_artifact(
            self,
            item: Dict[str, Any],
            sem: asyncio.Semaphore,
    ) -> bool:
        """
        Enrich an artifact item with its message data.

        Returns:
          True  -> keep this artifact
          False -> drop this artifact (payload/meta missing)

        On read error we keep the artifact but set data=None
        (matches previous behavior where we did `item["data"] = None` in except).
        """
        hosted_uri = item.get("hosted_uri")
        if not hosted_uri:
            return True

        async with sem:
            try:
                data = await self.store.get_message(hosted_uri) or {}
            except Exception:
                # Best-effort: keep artifact but signal missing body
                item["data"] = None
                return True

        # Strip embedding if present
        if "embedding" in data:
            data.pop("embedding", None)

        payload = data.get("payload") or {}
        meta = data.get("meta") or {}

        # In the old code, this caused a `continue` => artifact was not appended
        if not payload or not meta:
            return False

        kind = meta.get("kind")

        item["data"] = data
        return True


    # ---------- public API ----------

    async def search(
            self,
            *,
            query: Optional[str] = None,
            embedding: Optional[Sequence[float]] = None,
            kinds: Optional[Sequence[str]] = None,
            scope: str = "conversation",
            days: int = 90,
            top_k: int = 12,
            include_deps: bool = True,
            half_life_days: float = 7.0,
            ctx: Optional[dict] = None,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            turn_id: Optional[str] = None,
            roles: tuple[str,...] = ("artifact","assistant","user"),
            with_payload: bool = False,
            sort: str = "hybrid",
            any_tags: Optional[Sequence[str]] = None,
            all_tags: Optional[Sequence[str]] = None,
            not_tags: Optional[Sequence[str]] = None,
            timestamp_filters: Optional[List[Dict[str, Any]]] = None,
            bundle_id: Optional[str] = None,
    ) -> dict:
        """
        Semantic/Hybrid search (needs embedding unless provided).
        """
        ctx_loaded = self._load_ctx(ctx)
        user, conv, bundle = self._scope_from_ctx(
            ctx_loaded, user_id=user_id, conversation_id=conversation_id,
            turn_id=turn_id, bundle_id=bundle_id
        )

        qvec = list(embedding) if embedding is not None else None
        if qvec is None and query and self.model_service:
            [qvec] = await self.model_service.embed_texts([query])
        # if qvec is None:
        #     # If caller truly wants recency-only, use .recent() instead of .search().
        #     raise ValueError("search() needs either 'embedding' or 'query' to create one. For recency, call recent().")

        rows = await self.idx.search_context(
            user_id=user,
            conversation_id=(conv or None),
            turn_id=turn_id,
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
            bundle_id=bundle,
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
                "turn_id": r.get("turn_id"),
                "bundle_id": r.get("bundle_id"),
                "hosted_uri": r.get("hosted_uri"),
            }
            if include_deps and "deps" in r:
                item["deps"] = r["deps"]
            items.append(item)

        if with_payload:
            await self._materialize_payloads_for_items(
                items,
                s3_field="hosted_uri",
                out_field="payload",
            )

        return {"items": items}

    async def runtime_ctx(self,
                          ctx: Optional[dict] = None,
                          user_id: str = None,
                          conversation_id: str = None,
                          bundle_id: str = None):
        ctx_loaded = self._load_ctx(ctx)
        user, conv, bundle = self._scope_from_ctx(
            ctx_loaded, user_id=user_id, conversation_id=conversation_id, bundle_id=bundle_id
        )
        return { "user_id": user, "conversation_id": conv, "bundle_id": bundle }

    async def recent(
            self,
            *,
            kinds: Optional[Sequence[str]] = None,
            scope: str = "conversation",
            days: int = 90,
            limit: int = 12,
            ctx: Optional[dict] = None,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            roles: tuple[str, ...] = ("artifact","assistant","user"),
            any_tags: Optional[Sequence[str]] = None,
            all_tags: Optional[Sequence[str]] = None,
            not_tags: Optional[Sequence[str]] = None,
            with_payload: bool = False,
            bundle_id: Optional[str] = None,
    ) -> dict:
        """
        Pure-recency fetch (no embeddings). Fast path for "last N in track".
        """
        ctx_loaded = self._load_ctx(ctx)
        user, conv, bundle = self._scope_from_ctx(
            ctx_loaded, user_id=user_id, conversation_id=conversation_id, bundle_id=bundle_id
        )
        any_tags = list(any_tags or [])

        if kinds:
            all_tags = list(all_tags or [])
            all_tags += list(kinds)

        rows = await self.idx.fetch_recent(
            user_id=user,
            conversation_id=(conv or None),
            roles=roles,
            any_tags=any_tags or None,
            all_tags=list(all_tags or []) or None,
            not_tags=list(not_tags or []) or None,
            limit=limit,
            days=days,
            bundle_id=bundle,
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
                "turn_id": r.get("turn_id"),
                "bundle_id": r.get("bundle_id"),
                "hosted_uri": r.get("hosted_uri"),
            }
            items.append(item)

        if with_payload:
            await self._materialize_payloads_for_items(
                items,
                s3_field="hosted_uri",
                out_field="payload",
            )

        return {"items": items}

    async def recent_after_ts(
            self,
            *,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            roles: tuple[str, ...] = ("user", "assistant", "artifact"),
            any_tags: Optional[Sequence[str]] = None,
            all_tags: Optional[Sequence[str]] = None,
            not_tags: Optional[Sequence[str]] = None,
            limit: int = 5000,
            days: int = 365,
            with_payload: bool = True,
            ctx: Optional[dict] = None,
            bundle_id: Optional[str] = None,
            after_ts: Optional[str] = None,
    ) -> dict:
        ctx_loaded = self._load_ctx(ctx)
        user, conv, bundle = self._scope_from_ctx(ctx_loaded,
                                                  user_id=user_id,
                                                  conversation_id=conversation_id,
                                                  bundle_id=bundle_id)
        if not user or not conv:
            return {"items": []}
        rows = await self.idx.fetch_recent_after_ts(
            user_id=user,
            conversation_id=conv,
            roles=roles,
            any_tags=any_tags,
            all_tags=all_tags,
            not_tags=not_tags,
            limit=limit,
            days=days,
            bundle_id=bundle,
            after_ts=after_ts,
        )
        items = []
        for r in rows:
            ts = r.get("ts")
            item = {
                "id": r["id"],
                "message_id": r.get("message_id"),
                "role": r.get("role"),
                "text": r.get("text") or "",
                "ts": ts.isoformat() if hasattr(ts, "isoformat") else ts,
                "tags": list(r.get("tags") or []),
                "turn_id": r.get("turn_id"),
                "bundle_id": r.get("bundle_id"),
                "hosted_uri": r.get("hosted_uri"),
            }
            items.append(item)
        if with_payload:
            await self._materialize_payloads_for_items(
                items,
                s3_field="hosted_uri",
                out_field="payload",
            )
        return {"items": items}

    async def recent_summaries(
            self,
            *,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            limit: int = 8,
            days: int = 365,
            with_payload: bool = True,
            ctx: Optional[dict] = None,
            bundle_id: Optional[str] = None,
    ) -> dict:
        """
        Fetch recent compacted conversation summaries (conv.range.summary).
        """
        return await self.recent(
            kinds=(TURNS_SUMMARY_TAG,),
            roles=("artifact",),
            limit=limit,
            days=days,
            ctx=ctx,
            user_id=user_id,
            conversation_id=conversation_id,
            with_payload=with_payload,
            bundle_id=bundle_id,
        )

    async def pull_text_artifact(self, *, artifact_uri: str) -> dict:
        doc = await self.store.get_message(artifact_uri)
        return doc.get("payload") or {}

    async def save_turn_log_as_artifact(
            self,
            *,
            tenant: str, project: str, user: str,
            conversation_id: str, user_type: str,
            turn_id: str,
            bundle_id: str,
            payload: Optional[Dict[str, Any]] = None,
            extra_tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Writes artifact to store and/or index (see index_only/store_only flags)."""
        from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.turn_log import TurnLog as V2TurnLog
        log = V2TurnLog.from_dict(payload or {})
        md = log.to_markdown()

        tags = TURN_LOG_TAGS_BASE + [f"turn:{turn_id}"]
        if extra_tags:
            tags.extend([t for t in extra_tags if isinstance(t, str) and t.strip()])
        hosted_uri, message_id, rn = await self.store.put_message(
            tenant=tenant, project=project, user=user, fingerprint=None,
            conversation_id=conversation_id,
            bundle_id=bundle_id,
            role="artifact", text=md,
            id="turn.log",
            payload=payload,
            meta={"kind": "turn.log", "turn_id": turn_id},
            embedding=None, user_type=user_type, turn_id=turn_id,
        )
        await self.idx.add_message(
            user_id=user, conversation_id=conversation_id,
            turn_id=turn_id,
            bundle_id=bundle_id,
            role="artifact",
            text=md, hosted_uri=hosted_uri, ts=log.ts,
            tags=tags,
            ttl_days=365, user_type=user_type, embedding=None, message_id=message_id
        )
        return {"hosted_uri": hosted_uri, "message_id": message_id, "rn": rn}

    async def materialize_turn(
            self,
            *,
            turn_id: str,
            scope: str = "conversation",         # kept for API compatibility, not used directly now
            days: int = 365,
            ctx: Optional[dict] = None,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            with_payload: bool = True,
            include_turn_log_payload: bool = True,
            turn_log_payload_override: Optional[Dict[str, Any]] = None,
            extra_artifact_tags: Optional[Sequence[str]] = None,
    ) -> dict:
        """
        Returns the user msg, assistant reply, and user-visible artifacts for the turn.

        Uses ConvIndex.search_context with an explicit turn_id filter, which leverages
        the (user_id, conversation_id, turn_id) index instead of doing multiple
        tag-based recent() calls.
        """

        # Resolve scope/user/conv/bundle from ctx if needed
        ctx_loaded = self._load_ctx(ctx)
        user, conv, bundle = self._scope_from_ctx(
            ctx_loaded,
            user_id=user_id,
            conversation_id=conversation_id,
        )

        # If we don't know user or conversation, we can't hit the index safely
        if not user or not conv:
            return {
                "user": None,
                "assistant": None,
                "presentation": None,
                "citables": None,
                "solver_failure": None,
                "turn_log": None,
                "files": None,
            }

        # 1) Fetch all messages for this turn
        rows = await self.idx.fetch_recent(
            user_id=user,
            conversation_id=conv,
            turn_id=turn_id,                         # <--- uses our index directly
            roles=("artifact",),
            limit=64,
            days=days,
            bundle_id=bundle,
        )

        if not rows:
            return {
                "user": None,
                "assistant": None,
                "presentation": None,
                "citables": None,
                "solver_failure": None,
                "turn_log": None,
                "files": None,
            }

        def _make_item(r: Dict[str, Any]) -> Dict[str, Any]:
            ts = r.get("ts")
            return {
                "id": r["id"],
                "message_id": r.get("message_id"),
                "role": r["role"],
                "text": r.get("text") or "",
                "ts": ts.isoformat() if hasattr(ts, "isoformat") else ts,
                "tags": list(r.get("tags") or []),
                "turn_id": r.get("turn_id"),
                "bundle_id": r.get("bundle_id"),
                "hosted_uri": r.get("hosted_uri"),
            }

        user_item: Optional[Dict[str, Any]] = None
        assistant_item: Optional[Dict[str, Any]] = None
        presentation_item: Optional[Dict[str, Any]] = None
        solver_failure_item: Optional[Dict[str, Any]] = None
        citables_item: Optional[Dict[str, Any]] = None
        turn_log_item: Optional[Dict[str, Any]] = None
        files_item: Optional[Dict[str, Any]] = None
        extra_items: Dict[str, Dict[str, Any]] = {}
        extra_tags = {
            (t if t.startswith("artifact:") else f"artifact:{t}")
            for t in (extra_artifact_tags or [])
            if isinstance(t, str) and t.strip()
        }

        # rows are ordered newest-first by ts; first hit per slot wins (same semantics
        # as the old recent(..., limit=1) + first()).
        for r in rows:
            item = _make_item(r)
            role = r["role"]
            tags = set(r.get("tags") or [])

            if role == "user":
                if user_item is None:
                    user_item = item
                continue

            if role == "assistant":
                if assistant_item is None:
                    assistant_item = item
                continue

            if "artifact:turn.log" in tags:
                if turn_log_item is None:
                    turn_log_item = item
                continue

            if extra_tags:
                extra_hits = tags & extra_tags
                for hit in extra_hits:
                    if hit not in extra_items:
                        extra_items[hit] = item

        # 2) Optionally materialize payloads (ConversationStore blobs) for the items we found
        if with_payload:
            items_to_materialize = [
                it
                for it in [
                    user_item,
                    assistant_item,
                    presentation_item,
                    solver_failure_item,
                    citables_item,
                    (turn_log_item if include_turn_log_payload else None),
                    files_item,
                ]
                if it is not None and it.get("hosted_uri") and it.get("hosted_uri") != "index_only"
            ]
            if extra_items:
                items_to_materialize.extend(
                    it for it in extra_items.values()
                    if it.get("hosted_uri") and it.get("hosted_uri") != "index_only"
                )
            await self._materialize_payloads_for_items(
                items_to_materialize,
                s3_field="hosted_uri",
                out_field="payload",
            )

            if turn_log_payload_override is not None:
                if turn_log_item is None:
                    turn_log_item = {
                        "id": None,
                        "message_id": None,
                        "role": "artifact",
                        "text": "",
                        "ts": None,
                        "tags": ["artifact:turn.log", "source:override"],
                        "turn_id": turn_id,
                        "bundle_id": bundle,
                        "hosted_uri": None,
                    }
                turn_log_item["payload"] = turn_log_payload_override

            if extra_items:
                sources_item = extra_items.get(SOURCES_POOL_ARTIFACT_TAG)
                sources_payload = sources_item.get("payload") if sources_item else None
                sources_pool = None
                if isinstance(sources_payload, dict) and "sources_pool" in sources_payload:
                    sources_pool = sources_payload.get("sources_pool")
                elif isinstance(sources_payload, list):
                    sources_pool = sources_payload
                if sources_pool is not None and turn_log_item and isinstance(turn_log_item.get("payload"), dict):
                    turn_log_item["payload"]["sources_pool"] = sources_pool

            # Prefer user/assistant text from turn_log payload (source of truth).
            try:
                turn_log_payload = (turn_log_item.get("payload") if turn_log_item else None)
                if isinstance(turn_log_payload, dict):
                    tl_user = turn_log_payload.get("user") or {}
                    tl_prompt = tl_user.get("prompt") or {}
                    prompt_text = (tl_prompt.get("text") or tl_prompt.get("prompt") or "").strip()
                    if prompt_text:
                        user_item = {
                            "id": None,
                            "message_id": tl_prompt.get("message_id"),
                            "role": "user",
                            "text": prompt_text,
                            "ts": turn_log_item.get("ts"),
                            "tags": ["source:turn_log"],
                            "turn_id": turn_log_item.get("turn_id"),
                            "bundle_id": turn_log_item.get("bundle_id"),
                            "hosted_uri": None,
                        }

                    tl_asst = turn_log_payload.get("assistant") or {}
                    tl_comp = tl_asst.get("completion") or {}
                    comp_text = (tl_comp.get("text") or tl_comp.get("completion") or "").strip()
                    if comp_text:
                        assistant_item = {
                            "id": None,
                            "message_id": tl_comp.get("message_id"),
                            "role": "assistant",
                            "text": comp_text,
                            "ts": turn_log_item.get("ts"),
                            "tags": ["source:turn_log"],
                            "turn_id": turn_log_item.get("turn_id"),
                            "bundle_id": turn_log_item.get("bundle_id"),
                            "hosted_uri": None,
                        }
            except Exception:
                logger.exception("Failed to extract user/assistant text from turn_log payload")
            # Prefer citables/files from turn_log (source of truth).
            try:
                turn_log_payload = (turn_log_item.get("payload") if turn_log_item else None)
                if isinstance(turn_log_payload, dict):
                    sources_pool = turn_log_payload.get("sources_pool") or []
                    used_sources = [
                        s for s in sources_pool
                        if isinstance(s, dict) and s.get("used") is True and s.get("url")
                    ]
                    if used_sources:
                        citables_item = {
                            "id": None,
                            "message_id": None,
                            "role": "artifact",
                            "text": "",
                            "ts": turn_log_item.get("ts"),
                            "tags": ["artifact:solver.program.citables", "source:turn_log"],
                            "turn_id": turn_log_item.get("turn_id"),
                            "bundle_id": turn_log_item.get("bundle_id"),
                            "hosted_uri": None,
                            "payload": {"items": used_sources},
                        }
                    assistant = turn_log_payload.get("assistant") or {}
                    assistant_files = assistant.get("files") or []
                    if assistant_files:
                        files_item = {
                            "id": None,
                            "message_id": None,
                            "role": "artifact",
                            "text": "",
                            "ts": turn_log_item.get("ts"),
                            "tags": ["artifact:solver.program.files", "source:turn_log"],
                            "turn_id": turn_log_item.get("turn_id"),
                            "bundle_id": turn_log_item.get("bundle_id"),
                            "hosted_uri": None,
                            "payload": {"files": assistant_files},
                        }
            except Exception:
                logger.exception("Failed to derive citables/files from turn_log payload")

            # Resolve inline citations in assistant completion using the turn sources pool.
            try:
                from kdcube_ai_app.apps.chat.sdk.tools.citations import (
                    build_citation_map_from_sources,
                    replace_citation_tokens_batch,
                )
                turn_log_payload = (turn_log_item.get("payload") if turn_log_item else None)
                sources_pool = (turn_log_payload or {}).get("sources_pool") or []
                citation_map = build_citation_map_from_sources(sources_pool)
                if citation_map:
                    if assistant_item and isinstance(assistant_item.get("text"), str):
                        assistant_item["text"] = replace_citation_tokens_batch(
                            assistant_item["text"], citation_map
                        )
                    if isinstance(turn_log_payload, dict):
                        asst = turn_log_payload.get("assistant") or {}
                        completion = asst.get("completion")
                        if isinstance(completion, dict) and isinstance(completion.get("text"), str):
                            completion["text"] = replace_citation_tokens_batch(
                                completion["text"], citation_map
                            )
            except Exception:
                logger.exception("Failed to resolve citations in assistant completion")

        return {
            "user": user_item,
            "assistant": assistant_item,
            "presentation": presentation_item,
            "citables": citables_item,
            "solver_failure": solver_failure_item,
            "turn_log": turn_log_item,
            "files": files_item,
        }


    async def remove_user_reaction(self, *,
                                   turn_id: str,
                                   user_id: str,
                                   conversation_id: str) -> bool:
        try:
            existing = await self.recent(
                kinds=("artifact:turn.log.reaction",),
                scope="conversation",
                days=3650,
                limit=100,
                user_id=user_id,
                conversation_id=conversation_id,
                roles=("artifact",),
                any_tags=["origin:user"],
                all_tags=[f"turn:{turn_id}"],
                with_payload=True,
            )
            items = existing.get("items") or []
            removed_count = 0
            for item in items:
                payload = item.get("payload") or {}
                reaction_data = payload.get("reaction") or {}
                origin = reaction_data.get("origin")
                if origin == "user":
                    try:
                        await self.idx.delete_message(id=int(item["id"]))
                        removed_count += 1
                        logger.info(f"Removed user reaction id={item['id']} for turn {turn_id}")
                    except Exception as e:
                        logger.warning(f"Failed to delete reaction id={item['id']}: {e}")
            return removed_count > 0
        except Exception as e:
            logger.error(f"Failed to remove user reaction for turn {turn_id}: {e}", exc_info=True)
            return False

    async def clear_user_feedback_in_turn_log(
            self,
            *,
            tenant: str,
            project: str,
            user: str,
            user_type: str,
            conversation_id: str,
            turn_id: str,
            bundle_id: str,
    ) -> bool:
        """
        Remove all user-origin feedbacks (and matching timeline entries) from the
        artifact:turn.log blob. Preserves ts/text/embedding in index.
        Returns True if a turn log existed and was updated.
        """
        # Fetch latest turn log for the turn (with payload)
        existing = await self.search(
            query=None,
            kinds=("artifact:turn.log",),
            roles=("artifact",),
            scope="conversation",
            user_id=user,
            conversation_id=conversation_id,
            days=3650,
            top_k=1,
            include_deps=False,
            with_payload=True,
            all_tags=[f"turn:{turn_id}"],
        )
        items = existing.get("items") or []
        if not items:
            return False

        turn_log_item = items[0]
        payload = turn_log_item.get("payload") or {}
        tl = payload.get("turn_log") or {}
        changed = False

        # Strip feedbacks with origin == "user"
        fbs = list(tl.get("feedbacks") or [])
        new_fbs = [fb for fb in fbs if (fb.get("origin") != "user")]
        if len(new_fbs) != len(fbs):
            tl["feedbacks"] = new_fbs
            changed = True

        # Strip timeline entries that were feedbacks authored by user
        entries = list(tl.get("entries") or [])
        new_entries = []
        for e in entries:
            data = e.get("data") or {}
            if e.get("area") == "feedback" and data.get("origin") == "user":
                changed = True
                continue
            new_entries.append(e)
        if changed:
            tl["entries"] = new_entries
            payload["turn_log"] = tl
            # Leave payload["text"] untouched (optional: you could also rewrite printable text)

            original_ts = turn_log_item.get("ts")
            original_embedding = turn_log_item.get("embedding")
            original_tags = turn_log_item.get("tags") or []

            # Write new blob
            hosted_uri, message_id, rn = await self.store.put_message(
                tenant=tenant, project=project, user=user, fingerprint=None,
                conversation_id=conversation_id, bundle_id=bundle_id,
                role="artifact", text="", id="turn.log",
                payload=payload,
                meta={"kind": "turn.log", "turn_id": turn_id},
                embedding=original_embedding,
                user_type=user_type, turn_id=turn_id,
            )

            # Update only hosted_uri/tags; preserve ts
            artifact_tag = "artifact:turn.log"
            tags = list(dict.fromkeys((original_tags or []) + [artifact_tag, f"turn:{turn_id}"]))
            await self.idx.update_message(
                id=int(turn_log_item["id"]),
                hosted_uri=hosted_uri,
                tags=tags,
                ts=original_ts,
            )
            return True

        return False
    async def append_reaction_to_turn_log(self, *,
                                          turn_id: str, reaction: dict,
                                          tenant: str, project: str, user: str,
                                          fingerprint: Optional[str],
                                          user_type: str, conversation_id: str,
                                          bundle_id: str,
                                          origin: str = "machine"):
        """
        Add a reaction to a turn log.

        Args:
            origin: "user" (explicit feedback) or "machine" (inferred feedback)
                   If origin="user", removes any existing user reaction first (only one allowed)
        """
        # Ensure origin is in reaction data
        reaction = {**reaction, "origin": origin}

        # For user reactions, remove any existing user reaction first
        if origin == "user":
            await self.remove_user_reaction(
                turn_id=turn_id,
                user_id=user,
                conversation_id=conversation_id
            )

        payload = {"reaction": reaction}

        # Build tags with origin
        tags = ["artifact:turn.log.reaction", f"turn:{turn_id}", f"origin:{origin}"]

        # persist as a small artifact tied to the same turn
        hosted_uri, message_id, rn = await self.store.put_message(
            tenant=tenant, project=project, user=user,
            conversation_id=conversation_id,
            bundle_id=bundle_id,
            role="artifact",
            text=f"[turn.log.reaction]\n{reaction}",
            payload=payload,
            meta={"kind": "turn.log.reaction", "turn_id": turn_id, "origin": origin},
            id="turn.log.reaction",
            embedding=None, user_type=user_type, turn_id=turn_id,
            fingerprint=fingerprint
        )
        await self.idx.add_message(
            user_id=user, conversation_id=conversation_id, turn_id=turn_id,
            bundle_id=bundle_id,
            role="artifact",
            text=f"[turn.log.reaction] {reaction}", hosted_uri=hosted_uri, ts=payload["reaction"]["ts"],
            tags=tags,
            ttl_days=365, user_type=user_type, embedding=None, message_id=message_id
        )

    async def apply_feedback_to_turn_log(
            self,
            *,
            tenant: str,
            project: str,
            user: str,
            user_type: str,
            conversation_id: str,
            turn_id: str,
            bundle_id: str,
            feedback: dict,  # {"text": str, "confidence": float, "ts": str, "from_turn_id": str, "origin": str, "reaction": str}
    ) -> Optional[dict]:
        """
        Fetch the turn log, append feedback to its structure,
        then write new S3 blob and update only hosted_uri in index (preserving ts, text, embedding).

        Returns the updated turn log payload or None if turn not found.
        """
        try:
            # 1) Fetch the existing turn log
            existing = await self.search(
                query=None,
                kinds=("artifact:turn.log",),
                roles=("artifact",),
                scope="conversation",
                user_id=user,
                conversation_id=conversation_id,
                days=365,
                top_k=1,
                include_deps=False,
                with_payload=True,
                all_tags=[f"turn:{turn_id}"],
            )

            items = existing.get("items") or []
            if not items:
                logger.warning(f"Turn log not found for turn_id={turn_id}")
                return None

            turn_log_item = items[0]
            payload = turn_log_item.get("payload") or {}

            # 2) Extract current turn_log array and text
            turn_log_dict = payload.get("turn_log") or {}
            if not isinstance(turn_log_dict, dict):
                logger.error(f"turn_log is not a dict for turn_id={turn_id}, skipping feedback")
                return None


            # 3) Create feedback entry
            feedback_entry = {
                "type": "feedback",
                "ts": feedback.get("ts") or (datetime.datetime.utcnow().isoformat() + "Z"),
                "text": feedback.get("text", ""),
                "confidence": feedback.get("confidence", 0.0),
                "from_turn_id": feedback.get("from_turn_id"),  # The turn where feedback was given
                "origin": feedback.get("origin", "machine"),   # NEW: user or machine
                "reaction": feedback.get("reaction"),          # NEW: ok | not_ok | null
            }

            # 4) Add to feedbacks array (create if doesn't exist)
            if "feedbacks" not in turn_log_dict:
                turn_log_dict["feedbacks"] = []

            # For user feedbacks, remove any existing user feedback first (only one allowed)
            if feedback_entry["origin"] == "user":
                turn_log_dict["feedbacks"] = [
                    fb for fb in turn_log_dict["feedbacks"]
                    if fb.get("origin") != "user"
                ]

            turn_log_dict["feedbacks"].append(feedback_entry)

            # 5) Also add to entries array for chronological view
            entries = turn_log_dict.get("entries") or []
            reaction_str = f" [{feedback_entry['reaction']}]" if feedback_entry.get("reaction") else ""
            entries.append({
                "t": feedback_entry["ts"][11:19],  # Extract HH:MM:SS
                "area": "feedback",
                "msg": f"{feedback.get('text', '')}{reaction_str}",
                "level": "info",
                "data": {
                    "confidence": feedback.get("confidence", 0.0),
                    "from_turn_id": feedback.get("from_turn_id"),
                    "origin": feedback_entry["origin"],
                    "reaction": feedback_entry.get("reaction")
                }
            })
            turn_log_dict["entries"] = entries

            # 6) Update text representation (append feedback as a new line)
            current_text = payload.get("text") or ""
            origin_label = "USER" if feedback_entry["origin"] == "user" else "AUTO"
            feedback_text_line = (
                f"\n[{origin_label} FEEDBACK{reaction_str} from turn {feedback.get('from_turn_id', 'unknown')} "
                f"at {feedback_entry['ts'][:19]}] {feedback.get('text', '')}"
            )
            updated_text = current_text + feedback_text_line

            # 7) Update payload
            payload["turn_log"] = turn_log_dict
            payload["text"] = updated_text
            payload["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"

            # 8) Preserve original metadata
            original_ts = turn_log_item.get("ts")
            original_embedding = turn_log_item.get("embedding")  # Preserve embedding!
            original_tags = turn_log_item.get("tags") or []

            # 9) Write NEW S3 blob with updated payload
            hosted_uri, message_id, rn = await self.store.put_message(
                tenant=tenant,
                project=project,
                user=user,
                fingerprint=None,
                conversation_id=conversation_id,
                bundle_id=bundle_id,
                role="artifact",
                text="",  # Not used for S3 storage
                id="turn.log",
                payload=payload,
                meta={"kind": "turn.log", "turn_id": turn_id},
                embedding=original_embedding,  # PRESERVE original embedding
                user_type=user_type,
                turn_id=turn_id,
            )

            # 10) Update index: ONLY hosted_uri and embedding, preserve ts and text
            artifact_tag = "artifact:turn.log"
            unique_tags = [f"turn:{turn_id}"]
            merged_tags = list(dict.fromkeys(
                original_tags + [artifact_tag] + unique_tags
            ))

            await self.idx.update_message(
                id=int(turn_log_item["id"]),
                hosted_uri=hosted_uri,
                tags=merged_tags,
                ts=original_ts,  # PRESERVE original timestamp
                # text is NOT passed, so it won't be updated in PostgreSQL
            )

            logger.info(
            f"Applied feedback to turn {turn_id}: "
            f"feedbacks_count={len(turn_log_dict.get('feedbacks', []))}, "
            f"entries_count={len(entries)}, "
            f"new_hosted_uri={hosted_uri}, "
            f"ts_preserved={original_ts}, "
            f"embedding_preserved={original_embedding is not None}, "
            f"origin={feedback_entry['origin']}, "
            f"reaction={feedback_entry.get('reaction')}"
            )
            return payload

        except Exception as e:
            logger.error(f"Failed to apply feedback to turn log: {e}")
            logger.error(traceback.format_exc())
            return None

    async def fetch_turns_with_feedbacks(
            self,
            *,
            user_id: str,
            conversation_id: str,
            turn_ids: Optional[List[str]] = None,
            days: int = 365,
            bundle_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return per-turn package for turns that have feedbacks (reaction artifacts and/or
        non-empty feedbacks array in the turn log).

        Shape:
        {
          "user_id": ...,
          "conversation_id": ...,
          "turns": [
            {
              "turn_id": "t-001",
              "turn_log": <payload dict of artifact:turn.log>,
              "assistant": <assistant message item with payload>,
              "user": <user message item with payload>,
              "feedbacks": [ ... ],               # from turn_log.turn_log.feedbacks (if present)
              "reactions": [ <reaction items> ]   # optional convenience (raw artifacts with payload)
            },
            ...
          ]
        }
        """

        # Helper to read a single recent item for role/kind with payload
        async def _first_recent(
                *,
                roles: tuple[str, ...],
                all_tags: List[str]
        ) -> Optional[dict]:
            res = await self.recent(
                scope="conversation",
                days=days,
                limit=1,
                user_id=user_id,
                conversation_id=conversation_id,
                roles=roles,
                all_tags=all_tags,
                with_payload=True,
                bundle_id=bundle_id,
            )
            return (res.get("items") or [None])[0]

        # 1) If caller didn't specify turn_ids, discover turns with feedback by scanning reaction artifacts
        discovered_turn_ids: List[str] = []
        if not turn_ids:
            reactions = await self.search(
                query=None,
                kinds=("artifact:turn.log.reaction",),
                roles=("artifact",),
                scope="conversation",
                user_id=user_id,
                conversation_id=conversation_id,
                days=days,
                top_k=500,               # reasonable upper bound
                include_deps=False,
                with_payload=True,
                sort="hybrid",
                bundle_id=bundle_id,
            )
            for it in (reactions.get("items") or []):
                tid = it.get("turn_id")
                if tid:
                    discovered_turn_ids.append(tid)

            # Also consider any turn logs that already contain feedbacks even if reaction artifacts were pruned
            # (we only do a light pass: fetch the latest 200 turn logs and inspect payload)
            # This keeps it safe/fast while covering corner cases.
            turn_logs_recent = await self.search(
                query=None,
                kinds=("artifact:turn.log",),
                roles=("artifact",),
                scope="conversation",
                user_id=user_id,
                conversation_id=conversation_id,
                days=days,
                top_k=200,
                include_deps=False,
                with_payload=True,
                sort="hybrid",
                bundle_id=bundle_id,
            )
            for it in (turn_logs_recent.get("items") or []):
                tid = it.get("turn_id")
                if not tid:
                    continue
                payload = it.get("payload") or {}
                tl = payload.get("turn_log") or {}
                fbs = tl.get("feedbacks") or []
                if fbs and tid not in discovered_turn_ids:
                    discovered_turn_ids.append(tid)

            # Dedup while preserving order (most recent first from search already)
            seen = set()
            ordered = []
            for tid in discovered_turn_ids:
                if tid in seen:
                    continue
                seen.add(tid)
                ordered.append(tid)
            turn_ids = ordered

        # Guard: if still empty, return empty result quickly
        if not turn_ids:
            return {"user_id": user_id, "conversation_id": conversation_id, "turns": []}

        # 2) Build per-turn package
        if not turn_ids:
            return {"user_id": user_id, "conversation_id": conversation_id, "turns": []}

        # 2) Build per-turn package concurrently, with bounded concurrency
        sem = asyncio.Semaphore(MAX_CONCURRENT_ARTIFACT_FETCHES)

        async def _build_turn(tid: str) -> Dict[str, Any]:
            async with sem:
                # Materialize via existing helpers for consistency
                mat = await self.materialize_turn(
                    turn_id=tid,
                    scope="conversation",
                    days=days,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    with_payload=True,
                )

                turn_log_item = mat.get("turn_log") or {}
                turn_log_payload = (turn_log_item.get("payload") if turn_log_item else None)

                # Extract feedbacks from blocks if present
                feedbacks = []
                if isinstance(turn_log_payload, dict):
                    blocks = turn_log_payload.get("blocks")
                    if isinstance(blocks, list):
                        for blk in blocks:
                            if not isinstance(blk, dict):
                                continue
                            if (blk.get("type") or "") == "stage.feedback":
                                feedbacks.append({
                                    "ts": blk.get("ts"),
                                    "text": blk.get("text"),
                                })

                # Optional: also return raw reaction artifacts (already persisted separately)
                reactions = await self.recent(
                    kinds=("artifact:turn.log.reaction",),
                    scope="conversation",
                    days=days,
                    limit=50,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    roles=("artifact",),
                    all_tags=[f"turn:{tid}"],
                    with_payload=True,
                    bundle_id=bundle_id,
                )

                # For assistant/user messages, prefer materialized bundle
                assistant = mat.get("assistant")
                user_msg = mat.get("user")

                # Fallback: if for some reason materialize_turn didn't find them, try once directly
                if not assistant:
                    assistant = await _first_recent(roles=("assistant",), all_tags=[f"turn:{tid}"])
                if not user_msg:
                    user_msg = await _first_recent(roles=("user",), all_tags=[f"turn:{tid}"])

                return {
                    "turn_id": tid,
                    "turn_log": turn_log_payload,                 # full object as requested
                    "assistant": assistant,                       # full item incl. payload
                    "user": user_msg,                             # full item incl. payload
                    "feedbacks": feedbacks,                       # extracted from turn log payload
                    "reactions": reactions.get("items") or [],    # optional convenience
                }

        out_turns = await asyncio.gather(*(_build_turn(tid) for tid in turn_ids))

        return {"user_id": user_id, "conversation_id": conversation_id, "turns": out_turns}

    async def fetch_feedback_conversations_in_period(
            self,
            *,
            user_id: str,
            tenant: str,
            project: str,
            start_iso: str,
            end_iso: str,
            include_turns: bool = False,
            limit: int = 100,
            cursor: Optional[str] = None,
            bundle_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregate conversations (and optionally turns) that have feedback reactions within a time window,
        using ONLY reaction artifacts (artifact:turn.log.reaction).

        Returns:
          {
            "tenant": tenant,
            "project": project,
            "window": {"start": start_iso, "end": end_iso},
            "items": [
              {
                "conversation_id": "...",
                "last_activity_at": "...",  # from list_user_conversations (overall) or fallback to last reaction ts
                "started_at": "...",        # from list_user_conversations
                "feedback_counts": {
                   "total": N, "user": a, "machine": b, "ok": c, "not_ok": d, "neutral": e
                },
                # present only when include_turns=True
                "turns": [
                  {
                    "turn_id": "...",
                    "ts": "<first reaction ts in window for this turn>",
                    "feedbacks": [
                      {
                        "turn_id": "...",
                        "ts": "...",
                        "text": "...",
                        "reaction": "ok" | "not_ok" | "neutral" | None,
                        "confidence": 1.0,
                        "origin": "user" | "machine",
                        "rn": "<meta.rn>"
                      },
                      ...
                    ]
                  },
                  ...
                ]
              },
              ...
            ],
            "next_cursor": "opaque-or-null"
          }
        """
        import base64, json
        import datetime as _dt

        # ------- helpers -------
        def _parse_iso(ts: str) -> _dt.datetime:
            s = ts.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return _dt.datetime.fromisoformat(s)

        def _encode_cursor(idx: int) -> str:
            payload = json.dumps({"i": int(idx)}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            return base64.urlsafe_b64encode(payload).decode("ascii")

        def _decode_cursor(cur: Optional[str]) -> int:
            if not cur:
                return 0
            try:
                data = base64.urlsafe_b64decode(cur.encode("ascii"))
                obj = json.loads(data.decode("utf-8"))
                return int(obj.get("i", 0))
            except Exception:
                return 0

        # ------- validate window -------
        start_dt = _parse_iso(start_iso)
        end_dt = _parse_iso(end_iso)
        if end_dt < start_dt:
            raise ValueError("end must be >= start")

        # ------- pull ONLY reaction artifacts in window, across user's conversations -------
        reactions_res = await self.search(
            query=None,
            kinds=("artifact:turn.log.reaction",),
            scope="user",               # across all conversations for this user
            days=3650,                  # wide, bounded by timestamp_filters
            top_k=5000,                 # generous cap; grouping will reduce
            include_deps=False,
            with_payload=True,
            roles=("artifact",),
            sort="recency",
            timestamp_filters=[
                {"op": ">=", "value": start_dt.isoformat()},
                {"op": "<=", "value": end_dt.isoformat()},
            ],
            user_id=user_id,
            conversation_id=None,
            bundle_id=bundle_id,
        )
        items = reactions_res.get("items") or []
        if not items:
            return {
                "tenant": tenant,
                "project": project,
                "window": {"start": start_iso, "end": end_iso},
                "items": [],
                "next_cursor": None,
            }

        # ------- group by conversation, then by turn, aggregating counts and feedbacks -------
        by_conv: Dict[str, Dict[str, Any]] = {}
        for it in items:
            # reaction payload layout (as in your example)
            # it['payload'] = {
            #   'conversation_id': ...,
            #   'payload': {'reaction': {...}},
            #   'meta': {'rn': ..., 'turn_id': ... , 'origin': ...},
            #   ...
            # }
            p = (it.get("payload") or {})
            conversation_id = p.get("conversation_id")
            if not conversation_id:
                logger.warning("fetch_feedback_conversations_in_period: missing conversation_id in reaction payload; skipping")
                continue

            meta = p.get("meta") or {}
            rn = meta.get("rn")  # required in each feedback item
            # Prefer meta.turn_id; fall back to top-level turn_id
            tid = meta.get("turn_id") or it.get("turn_id")
            if not tid:
                # If somehow no turn_id, skip
                logger.warning("fetch_feedback_conversations_in_period: missing turn_id in reaction; skipping")
                continue

            reaction_obj = (p.get("payload") or {}).get("reaction") or {}
            # Use reaction.ts when present (authoritative), fall back to index ts
            ts = reaction_obj.get("ts") or it.get("ts")
            # Normalize fields
            origin = (reaction_obj.get("origin") or "").strip() or None
            reaction_val = reaction_obj.get("reaction")  # "ok" | "not_ok" | "neutral" | None
            confidence = reaction_obj.get("confidence", 0.0)
            text = reaction_obj.get("text", "")

            # Conversation bucket
            conv_bucket = by_conv.setdefault(conversation_id, {
                "conversation_id": conversation_id,
                "counts": {"total": 0, "user": 0, "machine": 0, "ok": 0, "not_ok": 0, "neutral": 0},
                "period_last_ts": None,  # latest reaction ts seen in window for this conversation
                "turns": {},             # turn_id -> { "first_ts": ..., "feedbacks": [ ... ] }
            })

            # Update conversation-level counts
            conv_bucket["counts"]["total"] += 1
            if origin == "user":
                conv_bucket["counts"]["user"] += 1
            elif origin == "machine":
                conv_bucket["counts"]["machine"] += 1
            if reaction_val == "ok":
                conv_bucket["counts"]["ok"] += 1
            elif reaction_val == "not_ok":
                conv_bucket["counts"]["not_ok"] += 1
            elif reaction_val in (None, "neutral"):
                conv_bucket["counts"]["neutral"] += 1

            # Update conversation's latest ts
            if ts and ((conv_bucket["period_last_ts"] is None) or (ts > conv_bucket["period_last_ts"])):
                conv_bucket["period_last_ts"] = ts

            # Turn bucket
            t = conv_bucket["turns"].setdefault(tid, {
                "turn_id": tid,
                "first_ts": ts,     # earliest reaction ts for this turn within window
                "feedbacks": [],    # list of reaction-derived feedbacks
            })
            if ts and (t["first_ts"] is None or ts < t["first_ts"]):
                t["first_ts"] = ts

            # Append feedback (include rn)
            t["feedbacks"].append({
                "turn_id": tid,
                "ts": ts,
                "text": text,
                "reaction": reaction_val,
                "confidence": confidence,
                "origin": origin,
                "rn": rn,
            })

        if not by_conv:
            return {
                "tenant": tenant,
                "project": project,
                "window": {"start": start_iso, "end": end_iso},
                "items": [],
                "next_cursor": None,
            }

        # ------- enrich with conversation meta (started_at / last_activity_at) -------
        conv_meta_rows = await self.idx.list_user_conversations(
            user_id=user_id,
            since=None,
            limit=None,
            days=3650,
            include_conv_start_text=False,
            bundle_id=bundle_id,
        )
        meta_map = {r["conversation_id"]: r for r in (conv_meta_rows or [])}

        # Build conversation list (sorted by last_activity desc, fallback to period_last_ts)
        conv_list: List[Dict[str, Any]] = []
        for cid, data in by_conv.items():
            meta = meta_map.get(cid) or {}
            conv_list.append({
                "conversation_id": cid,
                "started_at": meta.get("started_at"),
                "last_activity_at": meta.get("last_activity_at") or data.get("period_last_ts"),
                "counts": data["counts"],
                "turns": data["turns"],  # keep dict for now (turn_id -> {...})
            })

        conv_list.sort(key=lambda x: (x.get("last_activity_at") or ""), reverse=True)

        # ------- pagination over conversations -------
        start_idx = _decode_cursor(cursor)
        end_idx = min(len(conv_list), start_idx + max(1, int(limit)))
        page = conv_list[start_idx:end_idx]
        next_cursor = _encode_cursor(end_idx) if end_idx < len(conv_list) else None

        # ------- build final items -------
        items_out: List[Dict[str, Any]] = []
        for row in page:
            turns_out = None
            if include_turns:
                # Convert {turn_id: {...}} → list, sort by first_ts asc for readability,
                # and sort feedbacks within each turn by ts asc.
                turns_map = row["turns"] or {}
                turns_out = []
                for tid, tb in turns_map.items():
                    fbs = list(tb.get("feedbacks") or [])
                    fbs.sort(key=lambda fb: fb.get("ts") or "")
                    turns_out.append({
                        "turn_id": tid,
                        "ts": tb.get("first_ts"),
                        "feedbacks": fbs,
                    })
                turns_out.sort(key=lambda t: t.get("ts") or "")

            items_out.append({
                "conversation_id": row["conversation_id"],
                "last_activity_at": row["last_activity_at"],
                "started_at": row["started_at"],
                "feedback_counts": {
                    "total": row["counts"]["total"],
                    "user": row["counts"]["user"],
                    "machine": row["counts"]["machine"],
                    "ok": row["counts"]["ok"],
                    "not_ok": row["counts"]["not_ok"],
                    "neutral": row["counts"]["neutral"],
                },
                **({"turns": turns_out} if include_turns else {}),
            })

        return {
            "tenant": tenant,
            "project": project,
            "window": {"start": start_iso, "end": end_iso},
            "items": items_out,
            "next_cursor": next_cursor,
        }


    async def save_artifact(
            self,
            *,
            kind: str,
            tenant: str, project: str, user_id: str,
            conversation_id: str, user_type: str,
            turn_id: str,
            content: dict,
            content_str: Optional[str] = None,
            meta: Optional[Dict[str, Any]] = None,
            extra_tags: Optional[List[str]] = None,
            bundle_id: Optional[str] = None,
            index_only: bool = False,
            store_only: bool = False,
            embedding: Optional[List[float]] = None,
            ttl_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Writes artifact to store and/or index.

        Notes:
          - content_str is the text persisted into the index row (conv_messages.text).
          - index_only=True  => skip blob store, write index only (hosted_uri="index_only").
          - store_only=True  => write blob store only, skip index row.
        """

        artifact_tag = f"artifact:{kind}" if not kind.startswith("artifact:") else kind
        tags = [f"turn:{turn_id}", artifact_tag]
        if not content_str:
            content_str = json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else str(content)
        if extra_tags:
            tags.extend([t for t in extra_tags if isinstance(t, str) and t.strip()])
        if index_only and store_only:
            raise ValueError("save_artifact: index_only and store_only cannot both be True.")
        hosted_uri = None
        message_id = None
        rn = None
        if not index_only:
            base_meta = {"kind": kind, "turn_id": turn_id}
            if meta:
                base_meta.update({k: v for k, v in meta.items() if v is not None})
            hosted_uri, message_id, rn = await self.store.put_message(
                tenant=tenant, project=project, user=user_id, fingerprint=None,
                conversation_id=conversation_id,
                bundle_id=bundle_id,
                role="artifact",
                text=content_str,
                id=kind,
                payload=content,
                meta=base_meta,
                embedding=None, user_type=user_type, turn_id=turn_id,
            )
        else:
            hosted_uri = "index_only"
        if not store_only:
            ttl_days = int(ttl_days or 365)
            await self.idx.add_message(
                user_id=user_id, conversation_id=conversation_id, turn_id=turn_id,
                bundle_id=bundle_id, role="artifact",
                text=content_str, hosted_uri=hosted_uri, ts=datetime.datetime.utcnow().isoformat()+"Z",
                tags=tags,
                ttl_days=ttl_days, user_type=user_type, embedding=embedding, message_id=message_id
            )
        return {"hosted_uri": hosted_uri, "message_id": message_id, "rn": rn}

    async def _find_latest_artifact_by_tags(
            self, *, kind: str, user_id: str, conversation_id: str, all_tags: list[str]
    ) -> Optional[dict]:
        """
        Find the latest *index row* for an artifact of `kind` that contains ALL `all_tags`.
        Returns a slim row dict (id, message_id, role, text, hosted_uri, ts, tags).
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
            turn_id: str,
            bundle_id: str,
            content: dict,
            unique_tags: List[str],
            preserve_ts: bool = False,
            original_ts: Optional[str] = None,
            index_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Idempotent write of a single logical artifact (e.g., a memory bucket) identified
        by its unique_tags (e.g., ["mem:bucket:<id>"]). If an index row exists, update
        that row (text, hosted_uri, tags, ts) in place; otherwise create a fresh artifact.

        If preserve_ts=True, keep the original timestamp instead of updating to now.
        Returns: {"mode": "update"|"insert", "id": <conv_messages.id>, "message_id": "...", "hosted_uri": "..."}
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
                user_type=user_type, turn_id=turn_id, bundle_id=bundle_id,
                content=content, content_str=content_str, extra_tags=unique_tags,
                index_only=index_only,
            )
            return {"mode": "insert", **saved}

        # 2) Write a new message blob and then point the index row at it
        hosted_uri = None
        message_id = existing.get("message_id")
        rn = None
        if not index_only:
            hosted_uri, message_id, rn = await self.store.put_message(
                tenant=tenant, project=project, user=user_id, fingerprint=None,
                conversation_id=conversation_id, bundle_id=bundle_id,
                role="artifact", text=content_str,
                id=kind,
                payload=content,
                meta={"kind": kind, "turn_id": turn_id},
                embedding=None, user_type=user_type, turn_id=turn_id,
            )
        else:
            hosted_uri = "index_only"

        # 3) update the existing index row in place
        # Determine timestamp: preserve original or update to now
        if preserve_ts and original_ts:
            update_ts = original_ts
        elif preserve_ts:
            # Try to get from existing record
            update_ts = existing.get("ts")
        else:
            # Normal behavior: update to now
            update_ts = datetime.datetime.utcnow().isoformat() + "Z"

        # Merge/normalize tags (keep artifact kind + unique tags)
        tags = list(dict.fromkeys((existing.get("tags") or []) +
                                  [artifact_tag, f"turn:{turn_id}"] +
                                  list(unique_tags or [])))

        await self.idx.update_message(
            id=int(existing["id"]),
            text=content_str,
            tags=tags,
            hosted_uri=hosted_uri,
            ts=update_ts,  # Use preserved or new timestamp
        )
        return {"mode": "update", "id": int(existing["id"]), "message_id": message_id, "hosted_uri": hosted_uri}

    async def list_conversations(
            self,
            user_id: str,
            *,
            last_n: Optional[int] = None,
            started_after: Optional[Union[str, datetime]] = None,
            days: int = 365,
            include_titles: bool = True,
            bundle_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List conversations for a user.

        Filters:
          - started_after: only conversations with any activity on/after this timestamp
          - last_n: return only the newest N conversations by last activity

        Returns:
          {
            "user_id": ...,
            "items": [
              {
                "conversation_id": "...",
                "started_at": "<ISO8601 or null>",
                "last_activity_at": "<ISO8601>",
                ["title": "..."]
              }, ...
            ]
          }
        """
        # Fetch latest timeline artifacts and group by conversation
        tag = f"artifact:{TIMELINE_KIND}"
        fetch_limit = max(5000, int(last_n or 0) * 5) if last_n else 5000
        rows = await self.idx.fetch_recent(
            user_id=user_id,
            roles=("artifact",),
            any_tags=[tag],
            days=days,
            limit=fetch_limit,
            bundle_id=bundle_id,
        )

        by_conv: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            cid = r.get("conversation_id")
            if not cid or cid in by_conv:
                continue
            txt = r.get("text") or ""
            parsed = {}
            if txt:
                try:
                    parsed = json.loads(txt)
                except Exception:
                    parsed = {}
            started_at = parsed.get("conversation_started_at") or None
            last_activity_at = parsed.get("last_activity_at") or r.get("ts")
            if last_activity_at is not None:
                try:
                    last_activity_at = isoz(str(last_activity_at)) if not isinstance(last_activity_at, str) else isoz(last_activity_at)
                except Exception:
                    pass
            title = parsed.get("conversation_title") if include_titles else None
            item = {
                "conversation_id": cid,
                "started_at": started_at,
                "last_activity_at": last_activity_at,
            }
            if include_titles and title:
                item["title"] = str(title).strip()
            by_conv[cid] = item

        items = list(by_conv.values())
        if started_after is not None:
            try:
                cutoff = ts_key(started_after)
            except Exception:
                cutoff = float("-inf")
            items = [i for i in items if ts_key(i.get("last_activity_at")) >= cutoff]

        items.sort(key=lambda x: ts_key(x.get("last_activity_at")), reverse=True)
        if last_n:
            items = items[: max(1, int(last_n))]

        return {"user_id": user_id, "items": items}

    async def get_conversation_details(
        self, user_id: str, conversation_id: str, *, bundle_id: Optional[str] = None
    ):
        """
        Reconstructed from conv timeseries
        :param user_id:
        :param conversation_id:
        :return:
        """

        # 1) Load the latest timeline artifact and extract conversation details.
        conversation_title = None
        conversation_started_at = None
        sources_pool = []
        try:
            res_ws = await self.recent(
                kinds=[f"artifact:{TIMELINE_KIND}"],
                roles=("artifact",),
                limit=1,
                user_id=user_id,
                conversation_id=conversation_id,
                with_payload=False,
            )
            ws_items = list(res_ws.get("items") or [])
            if ws_items:
                timeline_metadata = ws_items[0]
                timeline_metadata = json.loads(timeline_metadata.get("text") or "") or {}
                title = (timeline_metadata.get("conversation_title") or "").strip()
                if title:
                    conversation_title = title
                conversation_started_at = (timeline_metadata.get("conversation_started_at") or "").strip() or None
                if isinstance(timeline_metadata.get("sources_pool"), list):
                    sources_pool = timeline_metadata.get("sources_pool") or []
        except Exception:
            conversation_title = None
            conversation_started_at = None
        ui_artifacts_tags = UI_ARTIFACT_TAGS
        # 3) Get raw turn-tag occurrences (chronological, duplicates preserved)
        occurrences = await self.idx.get_conversation_turn_ids_from_tags(
            user_id=user_id,
            conversation_id=conversation_id,
            bundle_id=bundle_id
        )
        # 4) Aggregate to first/last timestamps per turn_id, preserving first-seen order
        turns_map: Dict[str, Dict[str, str|List]] = {}
        order: List[str] = []
        started_at = conversation_started_at
        last_activity_at = None

        for occ in occurrences:

            tid = occ.get("turn_id")
            ts = occ.get("ts")
            if not tid or not ts:
                continue
            if not started_at:
                started_at = ts

            tags = occ.get("tags") or []
            hit = next((t for t in tags if t in ui_artifacts_tags), None)
            turn_ui_artifacts = turns_map.get(tid, {}).get("artifacts", [])
            if hit:
                turn_ui_artifacts.append({"message_id": occ.get("mid"), "type": hit})
            if tid not in turns_map:
                turns_map[tid] = {"turn_id": tid, "ts_first": ts, "ts_last": ts, "artifacts": turn_ui_artifacts}
                order.append(tid)
            else:
                # update last-seen timestamp
                turns_map[tid]["ts_last"] = ts
                last_activity_at = ts

        turns = [turns_map[tid] for tid in order]
        return {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "conversation_title": conversation_title,
            "started_at": started_at,
            "last_activity_at": last_activity_at,
            "turns": turns,
            "sources_pool": sources_pool
        }

    async def conversation_exists(
            self,
            *,
            user_id: str,
            conversation_id: str,
            bundle_id: Optional[str] = None,
    ) -> bool:
        """
        Best-effort check whether a conversation exists for a given user.

        Strategy:
          1) Look for the conv.start fingerprint (artifact:turn.fingerprint.v1 + conv.start tag)
          2) Fallback: check if there is any turn activity in the index for this conversation.

        Returns:
          True if conversation appears to exist, False otherwise.
        """
        if not user_id or not conversation_id:
            return False

        try:
            # 1) Check conv.start fingerprint within this conversation
            data = await self.search(
                query=None,
                embedding=None,
                kinds=(FINGERPRINT_KIND,),
                scope="conversation",
                days=3650,
                top_k=1,
                include_deps=False,
                with_payload=False,
                ctx=None,
                user_id=user_id,
                conversation_id=conversation_id,
                any_tags=None,
                all_tags=[CONV_START_FPS_TAG],
                not_tags=None,
                timestamp_filters=None,
                bundle_id=bundle_id,
            )

            if data.get("items"):
                return True

            # 2) Fallback: any turn activity seen via tags
            occ = await self.idx.get_conversation_turn_ids_from_tags(
                user_id=user_id,
                conversation_id=conversation_id,
                bundle_id=bundle_id,
            )
            return bool(occ)

        except Exception as e:
            logger.error(
                f"conversation_exists check failed for user={user_id}, "
                f"conversation_id={conversation_id}: {e}",
                exc_info=True,
            )
            # Best-effort: on error, treat as non-existent
            return False

    async def fetch_conversation_artifacts(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_ids: Optional[List[str]] = None,
        materialize: bool = False,
        days: int = 365,
        bundle_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 1) Collect UI-visible artifacts per turn (existing behavior)
        occ = await self.idx.get_conversation_turn_ids_from_tags(
            user_id=user_id,
            conversation_id=conversation_id,
            days=days,
            bundle_id=bundle_id,
            turn_ids=turn_ids or None,
        )

        conversation_title = None
        timeline_sources_pool: List[Dict[str, Any]] = []
        try:
            # Fetch timeline artifact and read conversation_title from its payload
            res_ws = await self.recent(
                kinds=(f"artifact:{TIMELINE_KIND}",),
                roles=("artifact",),
                limit=1,
                days=days,
                user_id=user_id,
                conversation_id=conversation_id,
                with_payload=False,
            )
            ws_items = list(res_ws.get("items") or [])
            if ws_items:
                timeline_metadata = ws_items[0]
                timeline_metadata = json.loads(timeline_metadata.get("text") or "") or {}
                title = (timeline_metadata.get("conversation_title") or "").strip()
                if title:
                    conversation_title = title
                if isinstance(timeline_metadata.get("sources_pool"), list):
                    timeline_sources_pool = timeline_metadata.get("sources_pool") or []
                logger.info(
                    "fetch_conversation_artifacts: timeline title=%s conversation_id=%s items=%d",
                    "set" if conversation_title else "missing",
                    conversation_id,
                    len(ws_items),
                )
            else:
                logger.info(
                    "fetch_conversation_artifacts: no timeline items conversation_id=%s",
                    conversation_id,
                )
        except Exception:
            timeline_sources_pool = []
            conversation_title = None

        if not occ and not (turn_ids or []):
            return {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "conversation_title": conversation_title,
                "turns": []
            }

        turns_map: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        to_materialize: List[Dict[str, Any]] = []
        skip_types = {"artifact:user.attachment", "artifact:assistant.file"}

        if turn_ids:
            for tid in turn_ids:
                if tid in turns_map:
                    continue
                turns_map[tid] = {"turn_id": tid, "artifacts": []}
                order.append(tid)

        for r in occ:
            tid = r["turn_id"]
            tags = set(r.get("tags") or [])
            if tid not in turns_map:
                turns_map[tid] = {"turn_id": tid, "artifacts": []}
                order.append(tid)

            tag_hits = UI_ARTIFACT_TAGS & tags
            if not tag_hits:
                continue

            tag_type = next(iter(tag_hits))
            if tag_type in skip_types:
                continue

            # only user-origin reactions are considered "feedback"
            if "artifact:turn.log.reaction" in tag_hits and "origin:user" not in tags:
                continue

            item: Dict[str, Any] = {
                "message_id": r.get("mid"),
                "type": tag_type,
                "ts": r.get("ts"),
                "hosted_uri": r.get("hosted_uri"),
                "bundle_id": r.get("bundle_id"),
            }

            turns_map[tid]["artifacts"].append(item)

            if materialize and item.get("hosted_uri") and item.get("hosted_uri") != "index_only":
                # Keep a reference so we can materialize later
                to_materialize.append(item)

        # 2) Materialize artifacts concurrently (bounded)
        if materialize and to_materialize:
            sem = asyncio.Semaphore(MAX_CONCURRENT_ARTIFACT_FETCHES)

            tasks = [self._materialize_artifact(item, sem) for item in to_materialize]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Determine which items to drop (payload/meta missing)
            drop_ids = set()
            for item, result in zip(to_materialize, results):
                if isinstance(result, Exception):
                    # Extremely defensive: keep artifact but mark as not loaded
                    item["data"] = None
                    continue
                if result is False:
                    drop_ids.add(id(item))

            if drop_ids:
                # Remove dropped artifacts while preserving order
                for turn in turns_map.values():
                    turn["artifacts"] = [
                        a for a in turn["artifacts"] if id(a) not in drop_ids
                    ]
        async def _materialize_turn_log_artifacts(
            tid: str,
            sources_pool_for_conv: List[Dict[str, Any]],
        ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, bool]]:
            mat = await self.materialize_turn(
                turn_id=tid,
                scope="conversation",
                days=days,
                user_id=user_id,
                conversation_id=conversation_id,
                with_payload=True,
            )
            turn_log_item = mat.get("turn_log") or {}
            turn_log_payload = unwrap_payload(turn_log_item)
            if not isinstance(turn_log_payload, dict):
                return [], [], {}

            ts = turn_log_payload.get("ts") or turn_log_item.get("ts")
            out: List[Dict[str, Any]] = []
            sources_pool = turn_log_payload.get("sources_pool") or []

            blocks = turn_log_payload.get("blocks")
            if not isinstance(blocks, list):
                blocks = []
            replace_types: Dict[str, bool] = {}
            view: Dict[str, Any] = {}

            if blocks:
                from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.timeline import Timeline
                from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import RuntimeCtx
                view = Timeline(runtime=RuntimeCtx()).build_turn_view(
                    turn_id=tid,
                    blocks=blocks,
                    sources_pool=(sources_pool or sources_pool_for_conv or []),
                )
                user_text = (view.get("user") or {}).get("text") or ""
                user_ts = (view.get("user") or {}).get("ts") or ts
                if user_text:
                    out.append({
                        "message_id": None,
                        "type": "chat:user",
                        "ts": user_ts,
                        "hosted_uri": None,
                        "bundle_id": turn_log_item.get("bundle_id"),
                        "data": {"text": user_text, "meta": {"source": "turn_log"}},
                    })
                    replace_types["chat:user"] = True

                for a in view.get("attachments") or []:
                    out.append({
                        "message_id": a.get("message_id"),
                        "type": "artifact:user.attachment",
                        "ts": ts,
                        "hosted_uri": a.get("hosted_uri"),
                        "bundle_id": turn_log_item.get("bundle_id"),
                        "data": {"payload": a, "meta": {"kind": "user.attachment", "turn_id": tid}},
                    })

                asst_text = (view.get("assistant") or {}).get("text") or ""
                asst_ts = (view.get("assistant") or {}).get("ts") or ts
                if asst_text:
                    out.append({
                        "message_id": None,
                        "type": "chat:assistant",
                        "ts": asst_ts,
                        "hosted_uri": None,
                        "bundle_id": turn_log_item.get("bundle_id"),
                        "data": {"text": asst_text, "meta": {"source": "turn_log"}},
                    })
                    replace_types["chat:assistant"] = True

                for f in view.get("files") or []:
                    out.append({
                        "message_id": f.get("message_id"),
                        "type": "artifact:assistant.file",
                        "ts": ts,
                        "hosted_uri": f.get("hosted_uri"),
                        "bundle_id": turn_log_item.get("bundle_id"),
                        "data": {"payload": f, "meta": {"kind": "assistant.file", "turn_id": tid}},
                    })

                followups = [x for x in (view.get("followups") or []) if isinstance(x, str)]
                if followups:
                    out.append({
                        "message_id": None,
                        "type": "artifact:conv.user_shortcuts",
                        "ts": ts,
                        "hosted_uri": None,
                        "bundle_id": turn_log_item.get("bundle_id"),
                        "data": {"payload": {"items": followups}, "meta": {"kind": "conv.user_shortcuts", "turn_id": tid}},
                    })

                clar_qs = [x for x in (view.get("clarification_questions") or []) if isinstance(x, str)]
                if clar_qs:
                    out.append({
                        "message_id": None,
                        "type": "artifact:conv.clarification_questions",
                        "ts": ts,
                        "hosted_uri": None,
                        "bundle_id": turn_log_item.get("bundle_id"),
                        "data": {"payload": {"items": clar_qs}, "meta": {"kind": "conv.clarification_questions", "turn_id": tid}},
                    })

                timeline_items = [i for i in (view.get("timeline_text") or []) if isinstance(i, dict) and i.get("text")]
                if timeline_items:
                    cleaned: List[Dict[str, Any]] = []
                    for it in timeline_items:
                        item: Dict[str, Any] = {}
                        for key in ("artifact_name", "text", "ts_first", "ts_last"):
                            val = it.get(key)
                            if val is not None:
                                item[key] = val
                        if item.get("text"):
                            cleaned.append(item)
                    if cleaned:
                        out.append({
                            "message_id": None,
                            "type": "artifact:conv.timeline_text.stream",
                            "ts": ts,
                            "hosted_uri": None,
                            "bundle_id": turn_log_item.get("bundle_id"),
                            "data": {
                                "payload": {"version": "v1", "items": cleaned},
                                "meta": {"kind": "conv.timeline_text.stream", "turn_id": tid},
                            },
                        })

                # Thinking blocks (from timeline) -> synthesize stream items for UI.
                thinking_items: List[Dict[str, Any]] = []
                for blk in blocks:
                    if not isinstance(blk, dict):
                        continue
                    if blk.get("turn_id") != tid:
                        continue
                    if (blk.get("type") or "") != "react.thinking":
                        continue
                    txt = blk.get("text")
                    if not isinstance(txt, str) or not txt.strip():
                        continue
                    meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
                    title = (meta.get("title") or "").strip() or "react"
                    ts_val = blk.get("ts") or ts
                    ts_ms = None
                    try:
                        sec = ts_key(ts_val)
                        if sec != float("-inf"):
                            ts_ms = int(sec * 1000)
                    except Exception:
                        ts_ms = None
                    item = {
                        "agent": title,
                        "text": txt,
                    }
                    if ts_ms is not None:
                        item["ts_first"] = ts_ms
                        item["ts_last"] = ts_ms
                    thinking_items.append(item)
                if thinking_items:
                    out.append({
                        "message_id": None,
                        "type": "artifact:conv.thinking.stream",
                        "ts": ts,
                        "hosted_uri": None,
                        "bundle_id": turn_log_item.get("bundle_id"),
                        "data": {
                            "payload": {"version": "v1", "items": thinking_items},
                            "meta": {"kind": "conv.thinking.stream", "turn_id": tid},
                        },
                    })

            # Synthesized artifacts expected by frontend from turn_log data
            if not sources_pool:
                sources_pool = list(sources_pool_for_conv or [])
            used_sources = [s for s in (view.get("citations") or []) if isinstance(s, dict) and s.get("url")]
            if used_sources:
                ts_candidates = []
                for s in used_sources:
                    if not isinstance(s, dict):
                        continue
                    for k in ("fetched_at", "fetched", "ts"):
                        val = (s.get(k) or "").strip() if isinstance(s.get(k), str) else s.get(k)
                        if val:
                            ts_candidates.append(val)
                            break
                citables_ts = max(ts_candidates) if ts_candidates else ts
                out.append({
                    "message_id": None,
                    "type": "artifact:solver.program.citables",
                    "ts": citables_ts,
                    "hosted_uri": None,
                    "bundle_id": turn_log_item.get("bundle_id"),
                    "data": {"payload": {"items": used_sources}, "meta": {"kind": "solver.program.citables", "turn_id": tid}},
                })

            # Per-file artifacts are emitted above; no grouped solver.program.files artifact.

            return out, sources_pool, replace_types

        if order:
            sem = asyncio.Semaphore(MAX_CONCURRENT_ARTIFACT_FETCHES)

            async def _build_turn_artifacts(
                tid: str,
            ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, bool]]:
                async with sem:
                    return await _materialize_turn_log_artifacts(tid, timeline_sources_pool)

            turn_artifacts = await asyncio.gather(*(_build_turn_artifacts(tid) for tid in order))
            sources_pool_by_turn: Dict[str, List[Dict[str, Any]]] = {}
            replace_by_turn: Dict[str, Dict[str, bool]] = {}
            for tid, result in zip(order, turn_artifacts):
                artifacts, sources_pool, replace_types = result
                if artifacts:
                    turns_map[tid]["artifacts"].extend(artifacts)
                if isinstance(sources_pool, list) and sources_pool:
                    sources_pool_by_turn[tid] = sources_pool
                if replace_types:
                    replace_by_turn[tid] = replace_types

            if replace_by_turn:
                for tid, rep in replace_by_turn.items():
                    if not rep:
                        continue
                    kept = []
                    for a in (turns_map.get(tid) or {}).get("artifacts") or []:
                        if a.get("type") in rep and (a.get("data") or {}).get("meta", {}).get("source") != "turn_log":
                            continue
                        kept.append(a)
                    turns_map[tid]["artifacts"] = kept

            if materialize and sources_pool_by_turn:
                for tid in order:
                    sources_pool = sources_pool_by_turn.get(tid) or []
                    if not sources_pool:
                        continue
                    citation_map = citation_utils.build_citation_map_from_sources(sources_pool)
                    if not citation_map:
                        continue
                    for item in (turns_map.get(tid) or {}).get("artifacts") or []:
                        if item.get("type") != "chat:assistant":
                            continue
                        data = item.get("data") or {}
                        raw_text = (data.get("text") or "").strip()
                        if not raw_text:
                            continue
                        opts = citation_utils.CitationRenderOptions(
                            mode="links",
                            embed_images=False,
                            keep_unresolved=False,
                            first_only=False,
                        )
                        rendered = citation_utils.replace_citation_tokens_batch(raw_text, citation_map, opts)
                        data["text"] = rendered

        return {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "conversation_title": conversation_title,
            "turns": [turns_map[tid] for tid in order],
        }

    async def get_conversation_state(self, *, user_id: str, conversation_id: str) -> dict:
        row = await self.idx.get_conversation_state_row(user_id=user_id, conversation_id=conversation_id)
        if not row:
            return {"state": "idle", "updated_at": None, "meta": {}}
        tags = set(row.get("tags") or [])
        state = "idle"
        for t in tags:
            if isinstance(t, str) and t.startswith("conv.state:"):
                state = t.split(":", 1)[1]
                break
        payload = {}
        try:
            if row.get("hosted_uri"):
                payload = await self.store.get_message(row["hosted_uri"])
        except Exception:
            payload = {}
        return {
            "state": state,
            "updated_at": row.get("ts"),
            "meta": (payload.get("payload") or {}) if isinstance(payload, dict) else {},
        }

    async def set_conversation_state(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            conversation_id: str,
            new_state: str,                     # 'idle' | 'in_progress' | 'error'
            by_instance: str | None = None,
            request_id: str | None = None,
            last_turn_id: str | None = None,    # <— still stored in S3 payload; also passed to index as tag
            require_not_in_progress: bool = False,
            user_type: str = "system",
            bundle_id: str | None = None,
    ) -> dict:
        """
        Returns: {
          "ok": bool,
          "updated_at": str,
          "state": str | None,
          "current_turn_id": str | None,
          "row": {...}   # index row if available
        }
        """
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        payload = {
            "state": new_state,
            "updated_at": now_iso,
            **({"by_instance": by_instance} if by_instance else {}),
            **({"request_id": request_id} if request_id else {}),
            **({"last_turn_id": last_turn_id} if last_turn_id else {}),
        }

        # Persist a tiny artifact for lineage
        hosted_uri, message_id, rn = await self.store.put_message(
            tenant=tenant, project=project, user=user_id, fingerprint=None,
            conversation_id=conversation_id, turn_id="conv", role="artifact", text="",
            id="conversation.state", bundle_id=bundle_id, payload=payload,
            meta={"kind": "conversation.state"},
            embedding=None, user_type=user_type,
            msg_ts=now_iso.replace(":", "-"),
        )

        res = await self.idx.try_set_conversation_state_cas(
            user_id=user_id, conversation_id=conversation_id,
            new_state=new_state, hosted_uri=hosted_uri, now_ts=now_iso,
            require_not_in_progress=require_not_in_progress,
            last_turn_id=last_turn_id,   # <— NEW
            bundle_id=bundle_id,
        )

        return {
            "ok": bool(res.get("ok")),
            "updated_at": now_iso,
            "state": res.get("state"),
            "current_turn_id": res.get("current_turn_id"),
            "row": res.get("row"),
        }

    async def delete_conversation(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            conversation_id: str,
            user_type: str,
            bundle_id: Optional[str] = None,
            fingerprint: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        Hard-delete a conversation for a user:

          1) Remove all conv_messages rows (and edges) from the index
          2) Best-effort delete blobs in ConversationStore under
             conversation/attachments/executions for this conversation.

        Returns a dict with counts:
          {
            "deleted_messages": ...,
            "deleted_storage_messages": ...,
            "deleted_storage_attachments": ...,
            "deleted_storage_executions": ...
          }
        """
        # 1) Delete index rows
        deleted_rows = await self.idx.delete_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            bundle_id=bundle_id,
        )

        # 2) Delete blobs from storage
        # user_or_fp is the stable id used in RNs; same logic as put_message/attachments
        try:
            who, user_or_fp = self.store._who_and_id(user_id, fingerprint)  # type: ignore[attr-defined]
        except Exception:
            # Fallback: use raw user_id
            user_or_fp = user_id

        storage_counts = {"messages": 0, "attachments": 0, "executions": 0}
        try:
            storage_counts = await self.store.delete_conversation(
                tenant=tenant,
                project=project,
                user_type=user_type,
                user_or_fp=user_or_fp,
                conversation_id=conversation_id,
            )
        except Exception as e:
            logger.error(
                f"Failed to delete blobs for conversation={conversation_id}: {e}",
                exc_info=True
            )

        return {
            "deleted_messages": int(deleted_rows or 0),
            "deleted_storage_messages": int(storage_counts.get("messages", 0) or 0),
            "deleted_storage_attachments": int(storage_counts.get("attachments", 0) or 0),
            "deleted_storage_executions": int(storage_counts.get("executions", 0) or 0),
        }

    async def delete_turn(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            conversation_id: str,
            turn_id: str,
            user_type: str,
            bundle_id: Optional[str] = None,
            fingerprint: Optional[str] = None,
            where: str = "index_only",   # "index_only" | "full"
    ) -> Dict[str, int]:
        """
        Delete a single turn.

        where:
          - "index_only" (default): remove from ConvIndex only (conv_messages + edges),
            keep ConversationStore blobs.
          - "full": also delete blobs for this turn from ConversationStore.

        Returns:
          {
            "deleted_messages": <int>,              # index rows
            "deleted_storage_messages": <int>,      # store message blobs (if full)
            "deleted_storage_attachments": <int>,   # store attachments (if full)
            "deleted_storage_executions": <int>,    # store executions (if full)
          }
        """
        if not user_id or not conversation_id or not turn_id:
            logger.warning(
                "delete_turn called with missing identifiers: "
                "user_id=%r conversation_id=%r turn_id=%r bundle_id=%r",
                user_id, conversation_id, turn_id, bundle_id,
            )
            return {
                "deleted_messages": 0,
                "deleted_storage_messages": 0,
                "deleted_storage_attachments": 0,
                "deleted_storage_executions": 0,
            }

        # 1) Always delete from index (this is what you want for rollback)
        try:
            deleted_rows = await self.idx.delete_turn(
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                bundle_id=bundle_id,
            )
        except Exception as e:
            logger.error(
                "ConvIndex.delete_turn failed for user=%s conv=%s turn=%s bundle=%s: %s",
                user_id, conversation_id, turn_id, bundle_id, e, exc_info=True
            )
            deleted_rows = 0

        storage_counts = {"messages": 0, "attachments": 0, "executions": 0}

        # 2) Optionally delete from storage as well
        if where == "full":
            try:
                # user_or_fp is the stable id used in RNs; same logic as put_message/attachments
                try:
                    who, user_or_fp = self.store._who_and_id(user_id, fingerprint)  # type: ignore[attr-defined]
                except Exception:
                    # Fallback: use raw user_id
                    user_or_fp = user_id

                storage_counts = await self.store.delete_turn(
                    tenant=tenant,
                    project=project,
                    user_type=user_type,
                    user_or_fp=user_or_fp,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            except Exception as e:
                logger.error(
                    "ConversationStore.delete_turn failed for user=%s conv=%s turn=%s: %s",
                    user_id, conversation_id, turn_id, e, exc_info=True
                )

        logger.info(
            "delete_turn(where=%s) user=%s conv=%s turn=%s bundle=%s "
            "→ idx_rows=%d, store=%s",
            where, user_id, conversation_id, turn_id, bundle_id,
            deleted_rows, storage_counts,
        )

        return {
            "deleted_messages": int(deleted_rows or 0),
            "deleted_storage_messages": int(storage_counts.get("messages", 0) or 0),
            "deleted_storage_attachments": int(storage_counts.get("attachments", 0) or 0),
            "deleted_storage_executions": int(storage_counts.get("executions", 0) or 0),
        }

async def search_context(
        conv_idx,
        ctx_client,
        model_service,
        targets: list[dict],
        user: str,
        conv: str,
        *,
        top_k: int = 5,
        days: int = 365,
        half_life_days: float = 7.0,
        scoring_mode: str = "hybrid",
        sim_weight: float = 0.8,
        rec_weight: float = 0.2,
        custom_score_fn: Optional[Callable] = None,
        with_payload: bool = False,
        logger = None,
) -> tuple[str | None, list[dict]]:
    """
    Unified search across turn logs with flexible scoring.

    Materialization strategy:
    - Collects all hits from all targets
    - Sorts by score
    - If with_payload=True, materializes only top_k results

    Returns:
        (best_turn_id, all_hits_sorted)
    """

    async def _search_one(where: str, query: str, embedding: List[float]|None = None) -> list[dict]:
        try:

            search_tags = None
            [qvec] = [embedding] if embedding else await model_service.embed_texts([query])
            if where in ("assistant_artifact", "artifact", "project_log"):
                where = "artifact"
                # search_tags = ["artifact:project.log"]
                search_tags = ["artifact:react.log.presentation", "artifact:solver.failure"]
            if where == "user":
                search_roles = ("user", "artifact")
                search_tags = ["chat:user", "artifact:user.attachment"]
            else:
                search_roles = (where,)
            res = await conv_idx.search_turn_logs_via_content(
                user_id=user,
                conversation_id=conv,
                query_embedding=qvec,
                search_roles=search_roles,
                search_tags=search_tags,
                top_k=top_k,
                days=days,
                scope="conversation",
                half_life_days=half_life_days,
            )
            return res or []
        except Exception as e:
            if logger:
                logger.log(f"Search failed for where={where}: {e}", "WARN")
            return []

    # Collect all hits
    hits = []

    for t in targets:
        where = t.get("where", "assistant")
        query = (t.get("query") or "")[:256]
        if not query:
            continue

        rows = await _search_one(where, query, embedding=t.get("embedding"))

        for r in rows:
            sim = float(r.get("sim") or 0.0)
            rec = float(r.get("rec") or 0.0)
            score = float(r.get("score") or 0.0)

            if sim == 0.0 and "relevance_score" in r:
                sim = float(r.get("relevance_score") or 0.0)
                score = sim if score == 0.0 else score

            if scoring_mode == "hybrid":
                final_score = score
            elif scoring_mode == "sim_only":
                final_score = sim
            elif scoring_mode == "custom" and custom_score_fn:
                final_score = custom_score_fn(sim, rec, r.get("ts"))
            else:
                final_score = sim_weight * sim + rec_weight * rec

            tid = r.get("turn_id") or _turn_id_from_tags_safe(r.get("tags") or [])

            hit = {
                "turn_id": tid,
                "role": r.get("role", "artifact"),
                "ts": r.get("ts"),
                "sim": sim,
                "rec": rec,
                "score": final_score,
                "original_score": score,
                "matched_via_role": r.get("matched_role"),
                "source_query": query,
                "source_where": where,
                "text": r.get("text", ""),
                "hosted_uri": r.get("hosted_uri"),
            }

            if "deps" in r:
                hit["deps"] = r["deps"]

            hits.append(hit)

    # Sort all hits by score (descending)
    hits.sort(key=lambda h: h["score"], reverse=True)

    # Best turn is the highest scoring one
    best_tid = hits[0]["turn_id"] if hits else None

    # Materialize payloads for top_k results only
    if with_payload and hits:
        top_hits = hits[:top_k]
        try:
            await ctx_client._materialize_payloads_for_items(
                top_hits,
                s3_field="hosted_uri",
                out_field="payload",
                max_concurrent=MAX_CONCURRENT_ARTIFACT_FETCHES,
            )
        except Exception as e:
            if logger:
                logger.log(f"Failed to materialize payloads for search_context: {e}", "WARN")
        final_hits = top_hits
    else:
        final_hits = hits[:top_k] if top_k else hits

    return best_tid, final_hits
