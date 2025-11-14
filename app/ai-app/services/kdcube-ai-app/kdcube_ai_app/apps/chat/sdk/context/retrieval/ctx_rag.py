# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/context/retrieval/ctx_rag.py

from __future__ import annotations

import datetime, traceback, logging
import pathlib, json
from typing import Optional, Sequence, List, Dict, Any, Union, Callable

from kdcube_ai_app.apps.chat.sdk.storage.rn import rn_file_from_file_path
from kdcube_ai_app.apps.chat.sdk.util import _turn_id_from_tags_safe
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnLog

from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex

logger = logging.getLogger(__name__)
TURN_LOG_TAGS_BASE = ["kind:turn.log", "artifact:turn.log"]

UI_ARTIFACT_TAGS = {
    "artifact:codegen.program.citables",
    "artifact:codegen.program.files",
    "artifact:conv.thinking.stream",
    "artifact:conv.canvas.stream",
    "chat:user", "chat:assistant"
}

FINGERPRINT_KIND = "artifact:turn.fingerprint.v1"
CONV_START_FPS_TAG = "conv.start"

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

    def _scope_from_ctx(self, ctx: dict, *,
                        user_id=None, conversation_id=None, track_id=None, turn_id=None, bundle_id=None) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
        user = user_id or ctx.get("user_id")
        conv = conversation_id or ctx.get("conversation_id")
        track = track_id or ctx.get("track_id")
        bundle = bundle_id or ctx.get("bundle_id") or ctx.get("app_bundle_id")
        return user, conv, track, bundle

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
        user, conv, track, bundle = self._scope_from_ctx(
            ctx_loaded, user_id=user_id, conversation_id=conversation_id,
            track_id=track_id, turn_id=turn_id, bundle_id=bundle_id
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
            track_id=(track or None),
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
                "track_id": r.get("track_id"),
                "turn_id": r.get("turn_id"),
                "bundle_id": r.get("bundle_id"),
                "s3_uri": r.get("s3_uri"),
            }
            if include_deps and "deps" in r:
                item["deps"] = r["deps"]
            if with_payload and r.get("s3_uri"):
                try:
                    item["payload"] = self.store.get_message(r["s3_uri"])
                except Exception:
                    pass
            items.append(item)
        return {"items": items}

    async def runtime_ctx(self,
                          ctx: Optional[dict] = None,
                          user_id: str = None,
                          conversation_id: str = None,
                          track_id: str = None,
                          bundle_id: str = None):
        ctx_loaded = self._load_ctx(ctx)
        user, conv, track, bundle = self._scope_from_ctx(
            ctx_loaded, user_id=user_id, conversation_id=conversation_id, track_id=track_id, bundle_id=bundle_id
        )
        return { "user_id": user, "conversation_id": conv, "track_id": track, "bundle_id": bundle }

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
            bundle_id: Optional[str] = None,
    ) -> dict:
        """
        Pure-recency fetch (no embeddings). Fast path for "last N in track".
        """
        ctx_loaded = self._load_ctx(ctx)
        user, conv, track, bundle = self._scope_from_ctx(
            ctx_loaded, user_id=user_id, conversation_id=conversation_id, track_id=track_id, bundle_id=bundle_id
        )
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
                "track_id": r.get("track_id"),
                "turn_id": r.get("turn_id"),
                "bundle_id": r.get("bundle_id"),
                "s3_uri": r.get("s3_uri"),
            }
            if with_payload and r.get("s3_uri"):
                try:
                    item["payload"] = self.store.get_message(r["s3_uri"])
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
            bundle_id: str,
            log: TurnLog,
            payload: Optional[Dict[str, Any]] = None,
            extra_tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Writes markdown to store (assistant artifact) + indexes it."""
        md = log.to_markdown()
        payload = {"turn_log": log.to_payload(), **(payload or {})}

        tags = TURN_LOG_TAGS_BASE + [f"turn:{turn_id}"] + ([f"track:{track_id}"] if track_id else [])
        if extra_tags:
            tags.extend([t for t in extra_tags if isinstance(t, str) and t.strip()])
        s3_uri, message_id, rn = await self.store.put_message(
            tenant=tenant, project=project, user=user, fingerprint=None,
            conversation_id=conversation_id,
            bundle_id=bundle_id,
            role="artifact", text=md,
            id="turn.log",
            payload=payload,
            meta={"kind": "turn.log", "turn_id": turn_id, "track_id": track_id},
            embedding=None, user_type=user_type, turn_id=turn_id, track_id=track_id,
        )
        await self.idx.add_message(
            user_id=user, conversation_id=conversation_id,
            turn_id=turn_id,
            bundle_id=bundle_id,
            role="artifact",
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

        # 8) files of the certain mime types (e.g., textual)
        files = await self.recent(
            kinds=("artifact:codegen.program.files",),
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
            "files": first(files),
        }

    async def remove_user_reaction(self, *,
                                   turn_id: str,
                                   user_id: str,
                                   conversation_id: str,
                                   track_id: Optional[str]) -> bool:
        try:
            existing = await self.search(
                query=None,
                kinds=("artifact:turn.log.reaction",),
                roles=("artifact",),
                scope="conversation",
                user_id=user_id,
                conversation_id=conversation_id,
                days=3650,
                top_k=100,
                include_deps=False,
                with_payload=True,
                # Fast-tag filter to prefer user-origin rows; still verify via payload
                any_tags=["origin:user"],
                all_tags=[f"turn:{turn_id}"],
            )
            items = existing.get("items") or []
            removed_count = 0
            for item in items:
                payload = (item.get("payload") or {}).get("payload") or {}
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
            track_id: Optional[str],
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
        payload = (turn_log_item.get("payload") or {}).get("payload") or {}
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
            s3_uri, message_id, rn = await self.store.put_message(
                tenant=tenant, project=project, user=user, fingerprint=None,
                conversation_id=conversation_id, bundle_id=bundle_id,
                role="artifact", text="", id="turn.log",
                payload=payload,
                meta={"kind": "turn.log", "turn_id": turn_id, "track_id": track_id},
                embedding=original_embedding,
                user_type=user_type, turn_id=turn_id, track_id=track_id,
            )

            # Update only s3_uri/tags; preserve ts
            artifact_tag = "artifact:turn.log"
            tags = list(dict.fromkeys((original_tags or []) + [artifact_tag, f"turn:{turn_id}"]))
            await self.idx.update_message(
                id=int(turn_log_item["id"]),
                s3_uri=s3_uri,
                tags=tags,
                ts=original_ts,
            )
            return True

        return False
    async def append_reaction_to_turn_log(self, *,
                                          turn_id: str, reaction: dict,
                                          tenant: str, project: str, user: str,
                                          fingerprint: Optional[str],
                                          user_type: str, conversation_id: str, track_id: str,
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
                conversation_id=conversation_id,
                track_id=track_id
            )

        payload = {"reaction": reaction}

        # Build tags with origin
        tags = ["artifact:turn.log.reaction", f"turn:{turn_id}", f"origin:{origin}"]
        if track_id:
            tags.append(f"track:{track_id}")

        # persist as a small artifact tied to the same turn
        s3_uri, message_id, rn = await self.store.put_message(
            tenant=tenant, project=project, user=user,
            conversation_id=conversation_id,
            bundle_id=bundle_id,
            role="artifact",
            text=f"[turn.log.reaction]\n{reaction}",
            payload=payload,
            meta={"kind": "turn.log.reaction", "turn_id": turn_id, "track_id": track_id, "origin": origin},
            id="turn.log.reaction",
            embedding=None, user_type=user_type, turn_id=turn_id, track_id=track_id,
            fingerprint=fingerprint
        )
        await self.idx.add_message(
            user_id=user, conversation_id=conversation_id, turn_id=turn_id,
            bundle_id=bundle_id,
            role="artifact",
            text=f"[turn.log.reaction] {reaction}", s3_uri=s3_uri, ts=payload["reaction"]["ts"],
            tags=tags,
            ttl_days=365, user_type=user_type, embedding=None, message_id=message_id, track_id=track_id
        )

    async def apply_feedback_to_turn_log(
            self,
            *,
            tenant: str,
            project: str,
            user: str,
            user_type: str,
            conversation_id: str,
            track_id: Optional[str],
            turn_id: str,
            bundle_id: str,
            feedback: dict,  # {"text": str, "confidence": float, "ts": str, "from_turn_id": str, "origin": str, "reaction": str}
    ) -> Optional[dict]:
        """
        Fetch the turn log, append feedback to its structure,
        then write new S3 blob and update only s3_uri in index (preserving ts, text, embedding).

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
            payload = (turn_log_item.get("payload") or {}).get("payload") or {}

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
            s3_uri, message_id, rn = await self.store.put_message(
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
                meta={"kind": "turn.log", "turn_id": turn_id, "track_id": track_id},
                embedding=original_embedding,  # PRESERVE original embedding
                user_type=user_type,
                turn_id=turn_id,
                track_id=track_id,
            )

            # 10) Update index: ONLY s3_uri and embedding, preserve ts and text
            artifact_tag = "artifact:turn.log"
            unique_tags = [f"turn:{turn_id}"]
            merged_tags = list(dict.fromkeys(
                original_tags + [artifact_tag] + unique_tags
            ))

            await self.idx.update_message(
                id=int(turn_log_item["id"]),
                s3_uri=s3_uri,
                tags=merged_tags,
                ts=original_ts,  # PRESERVE original timestamp
                # text is NOT passed, so it won't be updated in PostgreSQL
            )

            logger.info(
            f"Applied feedback to turn {turn_id}: "
            f"feedbacks_count={len(turn_log_dict.get('feedbacks', []))}, "
            f"entries_count={len(entries)}, "
            f"new_s3_uri={s3_uri}, "
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
                payload = (it.get("payload") or {}).get("payload") or {}
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
        out_turns: List[Dict[str, Any]] = []
        for tid in turn_ids:
            # Materialize via existing helpers for consistency
            mat = await self.materialize_turn(
                turn_id=tid,
                scope="conversation",
                days=days,
                user_id=user_id,
                conversation_id=conversation_id,
                with_payload=True
            )

            turn_log_item = mat.get("turn_log") or {}
            turn_log_payload = ((turn_log_item.get("payload") or {}).get("payload")) if turn_log_item else None

            # Extract feedbacks array from the turn log payload (preferred source of truth)
            feedbacks = []
            if isinstance(turn_log_payload, dict):
                tl = turn_log_payload.get("turn_log") or {}
                fb = tl.get("feedbacks") or []
                if isinstance(fb, list):
                    feedbacks = fb

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
            # (these items already have payload materialized by materialize_turn)
            assistant = mat.get("assistant")
            user_msg = mat.get("user")

            # Fallback: if for some reason materialize_turn didn't find them, try once directly
            if not assistant:
                assistant = await _first_recent(roles=("assistant",), all_tags=[f"turn:{tid}"])
            if not user_msg:
                user_msg = await _first_recent(roles=("user",), all_tags=[f"turn:{tid}"])

            out_turns.append({
                "turn_id": tid,
                "turn_log": turn_log_payload,                 # full object as requested
                "assistant": assistant,                       # full item incl. payload
                "user": user_msg,                             # full item incl. payload
                "feedbacks": feedbacks,                       # extracted from turn log payload
                "reactions": reactions.get("items") or [],    # optional convenience
            })

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
            turn_id: str, track_id: Optional[str],
            content: dict,
            content_str: Optional[str] = None,
            extra_tags: Optional[List[str]] = None,
            bundle_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Writes markdown to store (assistant artifact) + indexes it."""

        artifact_tag = f"artifact:{kind}" if not kind.startswith("artifact:") else kind
        tags = [f"turn:{turn_id}", artifact_tag] + ([f"track:{track_id}"] if track_id else [])
        if not content_str:
            content_str = json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else str(content)
        if extra_tags:
            tags.extend([t for t in extra_tags if isinstance(t, str) and t.strip()])
        s3_uri, message_id, rn = await self.store.put_message(
            tenant=tenant, project=project, user=user_id, fingerprint=None,
            conversation_id=conversation_id,
            bundle_id=bundle_id,
            role="artifact",
            text=content_str,
            id=kind,
            payload=content,
            meta={"kind": kind, "turn_id": turn_id, "track_id": track_id},
            embedding=None, user_type=user_type, turn_id=turn_id, track_id=track_id,
        )
        await self.idx.add_message(
            user_id=user_id, conversation_id=conversation_id, turn_id=turn_id,
            bundle_id=bundle_id, role="artifact",
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
            bundle_id: str,
            content: dict,
            unique_tags: List[str],
            preserve_ts: bool = False,
            original_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Idempotent write of a single logical artifact (e.g., a memory bucket) identified
        by its unique_tags (e.g., ["mem:bucket:<id>"]). If an index row exists, update
        that row (text, s3_uri, tags, ts) in place; otherwise create a fresh artifact.

        If preserve_ts=True, keep the original timestamp instead of updating to now.
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
                user_type=user_type, turn_id=turn_id, track_id=track_id, bundle_id=bundle_id,
                content=content, content_str=content_str, extra_tags=unique_tags,
            )
            return {"mode": "insert", **saved}

        # 2) Write a new message blob and then point the index row at it
        s3_uri, message_id, rn = await self.store.put_message(
            tenant=tenant, project=project, user=user_id, fingerprint=None,
            conversation_id=conversation_id, bundle_id=bundle_id,
            role="artifact", text=content_str,
            id=kind,
            payload=content,
            meta={"kind": kind, "turn_id": turn_id, "track_id": track_id},
            embedding=None, user_type=user_type, turn_id=turn_id, track_id=track_id,
        )

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
            s3_uri=s3_uri,
            ts=update_ts,  # Use preserved or new timestamp
        )
        return {"mode": "update", "id": int(existing["id"]), "message_id": existing.get("message_id"), "s3_uri": s3_uri}

    async def list_conversations_(self, user_id: str):

        FINGERPRINT_KIND = "artifact:turn.fingerprint.v1"
        CONV_START_FPS_TAG = "conv.start"

        conversation_id = None
        data = await self.search(kinds=[FINGERPRINT_KIND],
                                       user_id=user_id,
                                       conversation_id=conversation_id,
                                       all_tags=[CONV_START_FPS_TAG],
                                       )

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
        rows = await self.idx.list_user_conversations(
            user_id=user_id,
            since=started_after,
            limit=last_n,
            days=days,
            include_conv_start_text=include_titles,
            bundle_id=bundle_id,
        )

        items: List[Dict[str, Any]] = []
        for r in rows:
            item = {
                "conversation_id": r["conversation_id"],
                "started_at": r.get("started_at"),
                "last_activity_at": r.get("last_activity_at"),
            }
            if include_titles:
                title = None
                txt = r.get("conv_start_text")
                if txt:
                    # conv.start fingerprint is small JSON; try to parse title from text
                    try:
                        parsed = json.loads(txt)
                        if isinstance(parsed, dict):
                            v = parsed.get("conversation_title")
                            title = str(v).strip() if v is not None else None
                    except Exception:
                        pass
                if title:
                    item["title"] = title
            items.append(item)

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

        # 1) Find the conv.start fingerprint within this conversation (it is small and is stored as is in the index)
        data = await self.search(kinds=[FINGERPRINT_KIND],
                                 user_id=user_id,
                                 scope="conversation",
                                 top_k=1,
                                 conversation_id=conversation_id,
                                 all_tags=[CONV_START_FPS_TAG],
                                 bundle_id=bundle_id,
                                 )
        conv_start = next(iter(data.get("items") or []), None) if data else None
        try:
            conv_start_text = conv_start.get("text")
            conv_start = json.loads(conv_start_text)
            # 2) Extract the conversation title from the conv.start fingerprint
            conversation_title = conv_start.get("conversation_title") if conv_start else None
        except Exception as ex:
            conversation_title = None
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
        started_at = None
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
            "turns": turns
        }

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
        occ = await self.idx.get_conversation_turn_ids_from_tags(
            user_id=user_id,
            conversation_id=conversation_id,
            days=days,
            bundle_id=bundle_id,
            turn_ids=turn_ids or None,
        )
        turns_map: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for r in occ:
            tid = r["turn_id"]
            tags = set(r.get("tags") or [])
            tag_hits = UI_ARTIFACT_TAGS & tags
            if not tag_hits:
                continue
            tag_type = next(iter(tag_hits))
            if tid not in turns_map:
                turns_map[tid] = {"turn_id": tid, "artifacts": []}
                order.append(tid)
            item = {
                "message_id": r.get("mid"),
                "type": tag_type,
                "ts": r.get("ts"),
                "s3_uri": r.get("s3_uri"),
                "bundle_id": r.get("bundle_id"),
            }
            if materialize and r.get("s3_uri"):
                try:
                    data = self.store.get_message(r["s3_uri"]) or {}
                    item["data"] = data
                    if "embedding" in item["data"]:
                        del item["data"]["embedding"]
                    payload = item["data"].get("payload") or {}
                    meta = item["data"].get("meta") or {}
                    if not payload or not meta:
                        continue
                    kind = meta.get("kind")
                    files = []
                    if kind == "codegen.program.files":
                        files = list((payload.get("files_by_slot") or {}).values())
                        for f in files:
                            f["rn"] = rn_file_from_file_path(f["path"])
                    payload["files"] = files
                except Exception:
                    item["data"] = None
            turns_map[tid]["artifacts"].append(item)

        return {"user_id": user_id, "conversation_id": conversation_id, "turns": [turns_map[tid] for tid in order]}


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
            if row.get("s3_uri"):
                payload = self.store.get_message(row["s3_uri"])
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
            track_id: str | None = None,
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
        s3_uri, message_id, rn = await self.store.put_message(
            tenant=tenant, project=project, user=user_id, fingerprint=None,
            conversation_id=conversation_id, turn_id="conv", role="artifact", text="",
            id="conversation.state", bundle_id=bundle_id, payload=payload,
            meta={"kind": "conversation.state", "track_id": track_id},
            embedding=None, user_type=user_type, track_id=track_id,
            msg_ts=now_iso.replace(":", "-"),
        )

        res = await self.idx.try_set_conversation_state_cas(
            user_id=user_id, conversation_id=conversation_id,
            new_state=new_state, s3_uri=s3_uri, now_ts=now_iso,
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

def _ts_to_float(ts: str) -> float:
    """Convert ISO timestamp to float for recency scoring"""
    try:
        s = (ts or "").strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        import datetime as _dt
        return _dt.datetime.fromisoformat(s).timestamp()
    except Exception:
        return float("-inf")


async def search_context(
        conv_idx,
        ctx_client,
        model_service,
        targets: list[dict],
        user: str,
        conv: str,
        track: str,
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
                search_tags = ["artifact:codegen.program.presentation", "artifact:solver.failure"]
            res = await conv_idx.search_turn_logs_via_content(
                user_id=user,
                conversation_id=conv,
                track_id=track,
                query_embedding=qvec,
                search_roles=(where,),
                search_tags=search_tags,
                top_k=top_k,
                days=days,
                scope="track",
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
                "s3_uri": r.get("s3_uri"),
            }

            if "deps" in r:
                hit["deps"] = r["deps"]

            hits.append(hit)

    # Sort all hits by score (descending)
    hits.sort(key=lambda h: h["score"], reverse=True)

    # Best turn is the highest scoring one
    best_tid = hits[0]["turn_id"] if hits else None

    # Materialize payloads for top_k results only
    final_hits = []
    if with_payload and hits:
        for hit in hits[:top_k]:  # Only top_k hits
            s3_uri = hit.get("s3_uri")
            if s3_uri:
                try:
                    payload = ctx_client.store.get_message(s3_uri)
                    # Remove embedding to save memory
                    if isinstance(payload, dict) and "embedding" in payload:
                        payload = {**payload}
                        del payload["embedding"]
                    hit["payload"] = payload
                    final_hits.append(hit)
                except Exception as e:
                    if logger:
                        logger.log(f"Failed to materialize {s3_uri}: {e}", "WARN")
                    hit["payload"] = None


    return best_tid, final_hits

