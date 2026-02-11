# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# sdk/context/memory/conv_memories.py
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, Optional

from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient

def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"

class ConvMemoriesStore:
    """
    Conversation-level active set pointer stored as a Postgres artifact.
    Falls back to graph storage only if no artifact backend is configured.
    """
    META_KEY = "conversation_state_v1"
    KIND = "conversation.active_set.v1"
    UNIQUE_TAG = f"conv.state:{META_KEY}"

    def __init__(self, graph_ctx=None, ctx_client: Optional[ContextRAGClient] = None):
        self.graph = graph_ctx
        self.ctx = ctx_client

    def bind_ctx_client(self, ctx_client: ContextRAGClient) -> None:
        self.ctx = ctx_client

    async def _get_from_ctx(self, *, user: str, conversation: str) -> Optional[Dict[str, Any]]:
        if not self.ctx:
            return None
        try:
            res = await self.ctx.recent(
                kinds=[f"artifact:{self.KIND}"],
                scope="conversation",
                user_id=user,
                conversation_id=conversation,
                roles=("artifact",),
                limit=1,
                days=365,
                with_payload=False,
                all_tags=[self.UNIQUE_TAG],
                ctx={"bundle_id": None},
            )
            for it in (res.get("items") or []):
                text = (it.get("text") or "").strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    return payload
        except Exception:
            return None
        return None

    async def _put_to_ctx(
        self,
        *,
        tenant: str,
        project: str,
        user: str,
        conversation: str,
        turn_id: str,
        active_set: Dict[str, Any],
        user_type: str,
        bundle_id: Optional[str],
    ) -> bool:
        if not self.ctx:
            return False
        try:
            await self.ctx.upsert_artifact(
                kind=self.KIND,
                tenant=tenant,
                project=project,
                user_id=user,
                user_type=user_type,
                conversation_id=conversation,
                turn_id=turn_id,
                content=active_set,
                unique_tags=[self.UNIQUE_TAG],
                bundle_id=bundle_id or "",
                index_only=True,
            )
            return True
        except Exception:
            return False

    async def get_active_set(self, *, tenant: str, project: str, user: str, conversation: str) -> Dict[str, Any]:
        active_set = await self._get_from_ctx(user=user, conversation=conversation)
        if active_set and isinstance(active_set, dict):
            return active_set
        try:
            if self.graph:
                active_set = await self.graph.get_conversation_blob(
                    tenant=tenant, project=project, conversation=conversation,
                    key=self.META_KEY
                )
                if active_set and isinstance(active_set, dict):
                    return active_set
        except Exception:
            pass
        return {
            "version": "v1",
            "picked_bucket_ids": [],
            "selected_local_memories_turn_ids": [],
            "last_reconciled_ts": "",
            "since_last_reconcile": 0,
            "updated_at": _now_iso(),
            "new": True
        }

    async def put_active_set(
        self,
        *,
        tenant: str,
        project: str,
        user: str,
        conversation: str,
        turn_id: str,
        active_set: Dict[str, Any],
        user_type: str = "system",
        bundle_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if "new" in active_set:
            del active_set["new"]
        active_set["version"] = "v1"
        active_set["updated_at"] = _now_iso()
        wrote_ctx = await self._put_to_ctx(
            tenant=tenant,
            project=project,
            user=user,
            conversation=conversation,
            turn_id=turn_id,
            active_set=active_set,
            user_type=user_type,
            bundle_id=bundle_id,
        )
        try:
            if not wrote_ctx and self.graph:
                await self.graph.set_conversation_blob(
                    tenant=tenant, project=project, conversation=conversation,
                    key="conversation_state_v1", value=active_set
                )
        except Exception:
            pass
        return active_set
