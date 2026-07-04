# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""SDK-owned conversation read/export facade.

The `conv` named-service provider consumes THIS facade, never the control-plane
ingress internals. The facade owns the request/scope/record contract; the actual
materialization (SQL/store) sits behind a `ConversationMaterializationPort` so it
can be replaced without changing the provider contract.

Scope model (per the named-services collaboration decisions):

* default scope is the current user / grantor (`mode="self"`),
* admin selected-user access is explicit (`mode="user"` + `user_id`) and is
  expected to be gated by stronger grants at the managed boundary,
* all-tenant/all-project bulk export is deliberately NOT here — that stays a
  separate provider/admin operation.

Authorization is not decided here. The facade resolves an effective user id and
defensively validates the scope is well-formed; grant/consent enforcement is the
managed boundary's responsibility (Connection Hub).

`normalize_conversation` and `collapse_turn` are already SDK-owned; reusing them
keeps `object.export` on the same record family as the direct
`conversations_export` MCP tool.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.export_records import (
    collapse_turn,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.export_records import (
    normalize_conversation,
)

MAX_EXPORT_LIMIT = 500
DEFAULT_EXPORT_LIMIT = 100

SCOPE_SELF = "self"
SCOPE_USER = "user"
ALLOWED_READ_SCOPES = (SCOPE_SELF, SCOPE_USER)


class ConversationScopeError(ValueError):
    """The requested read scope is not well-formed (e.g. selected-user with no id)."""


def _parse_iso(value: Optional[str]) -> Optional[_dt.datetime]:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


@dataclass(frozen=True)
class ConversationReadScope:
    """Who the read is for.

    `mode="self"` reads the current caller's own conversations (`current_user_id`).
    `mode="user"` reads a selected user's conversations (`user_id`) — an admin
    path the managed boundary must have authorized with `:any_user` grants.
    """

    mode: str = SCOPE_SELF
    current_user_id: str = ""
    user_id: str = ""

    @property
    def normalized_mode(self) -> str:
        mode = str(self.mode or SCOPE_SELF).strip().lower()
        return mode if mode in ALLOWED_READ_SCOPES else SCOPE_SELF

    @property
    def is_selected_user(self) -> bool:
        return self.normalized_mode == SCOPE_USER

    def resolve(self) -> str:
        """Return the effective platform user id, or raise ConversationScopeError."""
        if self.normalized_mode == SCOPE_USER:
            target = str(self.user_id or "").strip()
            if not target:
                raise ConversationScopeError("selected-user scope requires scope.user_id")
            return target
        current = str(self.current_user_id or "").strip()
        if not current:
            raise ConversationScopeError("self scope requires a resolved current user id")
        return current


@dataclass(frozen=True)
class ConversationListRequest:
    scope: ConversationReadScope
    since: str = ""
    days: int = 3650
    last_n: Optional[int] = None
    include_titles: bool = True


@dataclass(frozen=True)
class ConversationGetRequest:
    scope: ConversationReadScope
    conversation_id: str = ""
    days: int = 3650


@dataclass(frozen=True)
class ConversationExportScope:
    scope: ConversationReadScope
    since: str = ""
    limit: int = DEFAULT_EXPORT_LIMIT

    @property
    def normalized_limit(self) -> int:
        try:
            requested = int(self.limit or DEFAULT_EXPORT_LIMIT)
        except Exception:
            requested = DEFAULT_EXPORT_LIMIT
        return max(1, min(requested, MAX_EXPORT_LIMIT))


@runtime_checkable
class ConversationMaterializationPort(Protocol):
    """Raw, user-scoped materialization seam the facade depends on.

    Bound to a tenant/project. Kept intentionally small: a conversation summary
    listing and a per-conversation artifact fetch. Any storage/SQL detail lives
    behind an implementation of this port.
    """

    async def list_conversations(
        self,
        *,
        user_id: str,
        started_after: Optional[_dt.datetime] = None,
        days: int = 3650,
        last_n: Optional[int] = None,
        include_titles: bool = True,
    ) -> List[Dict[str, Any]]: ...

    async def fetch_conversation_artifacts(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_ids: Optional[List[str]] = None,
        materialize: bool = True,
        days: int = 3650,
    ) -> Dict[str, Any]: ...


def _conversation_summary(conv: Dict[str, Any], *, user_id: str, tenant: str, project: str) -> Dict[str, Any]:
    conv_id = conv.get("conversation_id") or conv.get("id") or ""
    summary = {
        "conversation_id": conv_id,
        "user_id": user_id,
        "tenant": tenant,
        "project": project,
        "title": conv.get("title") or "",
        "started_at": conv.get("started_at") or conv.get("created_at") or "",
        "last_at": conv.get("last_at") or conv.get("updated_at") or conv.get("last_activity") or "",
        "turn_count": conv.get("turn_count") if conv.get("turn_count") is not None else conv.get("turns_count"),
    }
    return {key: value for key, value in summary.items() if value not in (None, "")}


def _dict_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, str):
        try:
            result = _json.loads(result)
        except Exception:
            return {}
    return result if isinstance(result, dict) else {}


class ConversationReadService:
    """SDK-owned user-scoped conversation read/export.

    The provider (and any other SDK caller) depends on this class, not on the
    materialization implementation.
    """

    def __init__(self, port: ConversationMaterializationPort, *, tenant: str, project: str):
        self._port = port
        self._tenant = str(tenant or "").strip()
        self._project = str(project or "").strip()

    async def list_user_conversations(self, request: ConversationListRequest) -> List[Dict[str, Any]]:
        user_id = request.scope.resolve()
        convs = await self._port.list_conversations(
            user_id=user_id,
            started_after=_parse_iso(request.since),
            days=request.days,
            last_n=request.last_n,
            include_titles=request.include_titles,
        )
        return [
            _conversation_summary(conv, user_id=user_id, tenant=self._tenant, project=self._project)
            for conv in (convs or [])
            if isinstance(conv, dict)
        ]

    async def get_conversation(self, request: ConversationGetRequest) -> Optional[Dict[str, Any]]:
        user_id = request.scope.resolve()
        conversation_id = str(request.conversation_id or "").strip()
        if not conversation_id:
            raise ConversationScopeError("get_conversation requires a conversation_id")
        fetched = await self._port.fetch_conversation_artifacts(
            user_id=user_id, conversation_id=conversation_id, days=request.days,
        )
        if not fetched:
            return None
        raw = self._raw_record(conversation_id, user_id, conv=fetched, fetched=fetched)
        return normalize_conversation(raw, tenant=self._tenant, project=self._project)

    async def fetch_conversation(self, request: ConversationGetRequest) -> Dict[str, Any]:
        """Raw artifact passthrough for callers that need the unflattened turns."""
        user_id = request.scope.resolve()
        conversation_id = str(request.conversation_id or "").strip()
        if not conversation_id:
            raise ConversationScopeError("fetch_conversation requires a conversation_id")
        return await self._port.fetch_conversation_artifacts(
            user_id=user_id, conversation_id=conversation_id, days=request.days,
        )

    async def export_conversations(self, request: ConversationExportScope) -> Dict[str, Any]:
        """User-scoped export. Same record family as the direct conversations_export
        MCP tool, but scoped to one user (self or selected) — never all-user bulk."""
        user_id = request.scope.resolve()
        records = await self._export_records(user_id, since=request.since)
        limit = request.normalized_limit
        return {
            "ok": True,
            "count": min(len(records), limit),
            "total_available": len(records),
            "limited": len(records) > limit,
            "conversations": records[:limit],
        }

    async def _export_records(self, user_id: str, *, since: str) -> List[Dict[str, Any]]:
        convs = await self._port.list_conversations(
            user_id=user_id, started_after=_parse_iso(since), days=3650, last_n=None, include_titles=True,
        )
        out: List[Dict[str, Any]] = []
        for conv in (convs or []):
            if not isinstance(conv, dict):
                continue
            conv_id = conv.get("conversation_id") or conv.get("id")
            if not conv_id:
                continue
            fetched = await self._port.fetch_conversation_artifacts(
                user_id=user_id, conversation_id=conv_id, days=3650,
            )
            raw = self._raw_record(conv_id, user_id, conv=conv, fetched=fetched or {})
            out.append(normalize_conversation(raw, tenant=self._tenant, project=self._project))
        return out

    @staticmethod
    def _raw_record(conversation_id: str, user_id: str, *, conv: Dict[str, Any], fetched: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "started_at": conv.get("started_at") or conv.get("created_at") or "",
            "title": conv.get("title") or fetched.get("title") or "",
            "turns": [collapse_turn(t) for t in (fetched.get("turns") or []) if isinstance(t, dict)],
        }


def build_conversation_ctx_client(*, pg_pool: Any, tenant: str, project: str, model_service: Any, store: Any) -> Any:
    """Build a ContextRAGClient from explicitly-provided pooled resources.

    For callers outside the control-plane app (named-service dispatch in
    chat-proc) that already hold the worker's pg_pool + model service + store.
    No app/router state, no fallback — resources are always passed in from above.
    """
    from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
    from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema

    conv_idx = ConvIndex(pool=pg_pool)
    conv_idx.schema = project_schema(tenant, project)
    return ContextRAGClient(conv_idx=conv_idx, store=store, model_service=model_service)


class _PooledMaterializationPort:
    """Materialization over a ContextRAGClient bound to the caller's tenant/project.

    Built from pooled resources supplied by the bundle (pg_pool, model service,
    store) — no control-plane app state.
    """

    def __init__(self, *, pg_pool: Any, tenant: str, project: str, model_service: Any, store: Any):
        self._pg_pool = pg_pool
        self._tenant = tenant
        self._project = project
        self._model_service = model_service
        self._store = store
        self._ctx: Any = None

    def _ensure_ctx(self) -> Any:
        if self._ctx is None:
            self._ctx = build_conversation_ctx_client(
                pg_pool=self._pg_pool, tenant=self._tenant, project=self._project,
                model_service=self._model_service, store=self._store,
            )
        return self._ctx

    async def list_conversations(
        self,
        *,
        user_id: str,
        started_after: Optional[_dt.datetime] = None,
        days: int = 3650,
        last_n: Optional[int] = None,
        include_titles: bool = True,
    ) -> List[Dict[str, Any]]:
        ctx = self._ensure_ctx()
        result = await ctx.list_conversations(
            user_id=user_id, last_n=last_n, started_after=started_after,
            days=days, include_titles=include_titles, ctx={},  # ctx={} bypasses ambient bundle_id filtering
        )
        # ContextRAGClient.list_conversations returns {"user_id", "items": [...]},
        # NOT a bare list — unwrap items (list(dict) would yield the keys).
        return list((result or {}).get("items") or [])

    async def fetch_conversation_artifacts(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_ids: Optional[List[str]] = None,
        materialize: bool = True,
        days: int = 3650,
    ) -> Dict[str, Any]:
        ctx = self._ensure_ctx()
        result = await ctx.fetch_conversation_artifacts(
            user_id=user_id, conversation_id=conversation_id, turn_ids=turn_ids,
            materialize=materialize, days=days, ctx={},
        )
        return _dict_result(result)


def make_conversation_read_service(
    *, pg_pool: Any, tenant: str, project: str, model_service: Any, store: Any,
) -> ConversationReadService:
    """Build a read service over a ContextRAGClient built from pooled resources."""
    port = _PooledMaterializationPort(
        pg_pool=pg_pool, tenant=tenant, project=project, model_service=model_service, store=store,
    )
    return ConversationReadService(port, tenant=tenant, project=project)


__all__ = [
    "ALLOWED_READ_SCOPES",
    "SCOPE_SELF",
    "SCOPE_USER",
    "ConversationExportScope",
    "ConversationGetRequest",
    "ConversationListRequest",
    "ConversationMaterializationPort",
    "ConversationReadScope",
    "ConversationReadService",
    "ConversationScopeError",
    "build_conversation_ctx_client",
    "make_conversation_read_service",
]
