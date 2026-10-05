# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# chat/ingress/conversations/search.py
"""Conversation search REST endpoint (chat-widget search).

POST /{tenant}/{project}/search on the conversations router prefix.

Identity is the hard boundary: the searched user is ALWAYS the authenticated
session user. The route builds an explicit `ConversationSearchContext` and a
pooled search backend (`make_conversation_search_backend`) bound to the request's
tenant/project, then runs `run_conversation_search` — the same orchestration the
named-service `conv` provider uses.

Resource sourcing mirrors ingress startup (`get_conversation_system`): the app
lifespan stores `conversation_browser` (ContextRAGClient, carries the model
service), `conversation_store`, and `pg_pool` on app.state; this route reads them
from there and falls back to the resolvers when absent. The semantic arm degrades
gracefully: embedding failures are contained inside the retriever (per-arm
try/except in ctx_rag.search_context), so lexical/trigram matching still answers.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from kdcube_ai_app.apps.chat.ingress.resolvers import (
    require_auth,
    get_conversation_system,
    get_pg_pool,
)
from kdcube_ai_app.auth.AuthManager import RequireUser
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.ctx_rag import normalize_rank_weights
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    ALLOWED_TARGETS,
    SCOPE_AGENT,
    SCOPE_USER,
    ConversationSearchContext,
    ConversationSearchParams,
    ConversationSearchResult,
    run_conversation_search,
)
from kdcube_ai_app.apps.chat.sdk.event_identity import index_agent_id
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.search_backend import (
    make_conversation_search_backend,
)
from kdcube_ai_app.apps.chat.ingress.conversations.conversations import (
    _ensure_conversation_in_scope_or_404,
    _resolve_allowed_bundle_ids_or_404,
    _resolve_bundle_id_or_default,
)

logger = logging.getLogger("Conversations.Search.API")

router = APIRouter()

# Scopes this REST surface accepts. The lower-level API also knows an "agent"
# scope; the widget searches either the whole user history or one conversation.
ALLOWED_REQUEST_SCOPES = ("user", "conversation")
DEFAULT_REQUEST_TARGETS = ("user", "assistant", "summary", "attachment")

# Resolved once at import (same pattern as the sibling conversations routes);
# module-level so tests can override it via FastAPI dependency_overrides.
_user_session_dep = require_auth(RequireUser())


# -------------------- Models --------------------

class ConversationSearchWeights(BaseModel):
    """Optional rank weights for the hybrid fusion. Values are clamped to [0, 2];
    1.0 means "as today". `semantic` scales the embedding arm, `lexical` scales
    the lexical + trigram arms, `recency` scales the recency lift."""

    semantic: Optional[float] = None
    lexical: Optional[float] = None
    recency: Optional[float] = None


class ConversationSearchRequest(BaseModel):
    query: str = Field(default="", description="Topic query. May be blank only when from_ts/to_ts is set (temporal browse).")
    scope: str = Field(default="user", description="'user' (all conversations) or 'conversation' (one conversation).")
    conversation_id: str = Field(default="", description="Required when scope='conversation'; optional anchor otherwise.")
    targets: List[str] = Field(
        default_factory=lambda: list(DEFAULT_REQUEST_TARGETS),
        description=f"Content kinds to search; subset of {sorted(ALLOWED_TARGETS)}.",
    )
    from_ts: str = Field(default="", description="ISO8601 lower bound (inclusive).")
    to_ts: str = Field(default="", description="ISO8601 upper bound (exclusive).")
    days: Optional[int] = Field(default=None, ge=1, le=3650)
    limit: int = Field(default=20, ge=1, le=50)
    weights: Optional[ConversationSearchWeights] = None
    bundle_id: Optional[str] = Field(default=None, description="If omitted, the default bundle for this tenant/project is used.")
    agent_id: Optional[str] = Field(default=None, description="Narrow to one agent's conversations (an agent-bound chat widget). If omitted, all agents in the bundle.")
    include_recovery_sessions: bool = False


class ConversationSearchHitSnippet(BaseModel):
    role: Optional[str] = None
    text: Optional[str] = None
    ts: Optional[str] = None
    path: Optional[str] = None


class ConversationSearchHit(BaseModel):
    conversation_id: str
    turn_id: str
    snippets: List[ConversationSearchHitSnippet] = Field(default_factory=list)
    ordinal: Optional[int] = None
    total_turns: Optional[int] = None
    score: Optional[float] = None
    sim_score: Optional[float] = None
    recency_score: Optional[float] = None
    matched_via_role: Optional[str] = None
    ts: Optional[str] = None
    # W536: "hot" (the live index) or "cold" (the archive), as the backend read it.
    storage: Optional[str] = None


class ConversationSearchConversationInfo(BaseModel):
    title: Optional[str] = None
    last_activity_at: Optional[str] = None


class ConversationSearchResponse(BaseModel):
    user_id: str
    effective_mode: str
    warnings: List[str] = Field(default_factory=list)
    hits: List[ConversationSearchHit] = Field(default_factory=list)
    conversations: Dict[str, ConversationSearchConversationInfo] = Field(default_factory=dict)


# -------------------- Validation --------------------

def _validated_ts(value: str, field_name: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} timestamp (use ISO8601)")
    return text


def validated_search_params(req: ConversationSearchRequest) -> ConversationSearchParams:
    """Validate the REST request and map it onto ConversationSearchParams.

    Raises HTTPException(400) with a clear message on any contract violation —
    this mirrors the widget's own gating (its search button is disabled for a
    blank query without a time range).
    """
    scope = (req.scope or "user").strip().lower()
    if scope not in ALLOWED_REQUEST_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"scope must be one of {list(ALLOWED_REQUEST_SCOPES)}",
        )
    if scope == "conversation" and not (req.conversation_id or "").strip():
        raise HTTPException(
            status_code=400,
            detail="conversation_id is required when scope is 'conversation'",
        )

    targets = [t.strip().lower() for t in (req.targets or []) if isinstance(t, str) and t.strip()]
    if not targets:
        raise HTTPException(
            status_code=400,
            detail=f"targets must be a non-empty subset of {sorted(ALLOWED_TARGETS)}",
        )
    unknown = sorted(set(targets) - set(ALLOWED_TARGETS))
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown targets {unknown}; allowed: {sorted(ALLOWED_TARGETS)}",
        )

    from_ts = _validated_ts(req.from_ts, "from_ts")
    to_ts = _validated_ts(req.to_ts, "to_ts")
    query = (req.query or "").strip()
    if not query and not (from_ts or to_ts):
        raise HTTPException(
            status_code=400,
            detail="query is required unless from_ts/to_ts is provided (temporal browse)",
        )

    rank_weights = None
    if req.weights is not None:
        rank_weights = normalize_rank_weights(req.weights.model_dump(exclude_none=True))

    return ConversationSearchParams(
        query=query,
        targets=targets,
        scope=scope,
        from_ts=from_ts,
        to_ts=to_ts,
        top_k=int(req.limit),
        days=req.days,
        include_recovery_sessions=bool(req.include_recovery_sessions),
        rank_weights=rank_weights,
    )


# -------------------- Resource sourcing --------------------

class _EmbeddingUnavailableModelService:
    """Fallback model service when none is wired: every embed call raises, which
    the retriever's per-arm guard turns into an empty semantic arm — lexical and
    trigram matching still answer (degrade, never 500)."""

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        raise RuntimeError("embedding model service unavailable")


async def _search_resources(state: Any) -> tuple[Any, Any, Any]:
    """Resolve (pg_pool, model_service, store) the same way ingress startup does:
    prefer what the app lifespan stored on app.state, fall back to the resolvers."""
    pg_pool = getattr(state, "pg_pool", None)
    if pg_pool is None:
        pg_pool = await get_pg_pool()
    browser = getattr(state, "conversation_browser", None)
    store = getattr(state, "conversation_store", None)
    if browser is None or store is None:
        browser, _conv_index, store = await get_conversation_system(pg_pool)
    model_service = getattr(browser, "model_service", None)
    if model_service is None:
        # Semantic arm degrades; lexical still answers.
        model_service = _EmbeddingUnavailableModelService()
    return pg_pool, model_service, store


# -------------------- Response shaping --------------------

def _ts_text(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _shape_hit(hit: Dict[str, Any]) -> ConversationSearchHit:
    snippets = [
        ConversationSearchHitSnippet(
            role=sn.get("role"),
            text=sn.get("text"),
            ts=_ts_text(sn.get("ts")),
            path=sn.get("path"),
        )
        for sn in (hit.get("snippets") or [])
        if isinstance(sn, dict)
    ]
    return ConversationSearchHit(
        conversation_id=str(hit.get("conversation_id") or ""),
        turn_id=str(hit.get("turn_id") or ""),
        snippets=snippets,
        ordinal=hit.get("ordinal"),
        total_turns=hit.get("total_turns"),
        score=hit.get("score"),
        sim_score=hit.get("sim_score"),
        recency_score=hit.get("recency_score"),
        matched_via_role=(str(hit["matched_via_role"]) if hit.get("matched_via_role") else None),
        ts=_ts_text(hit.get("ts")),
        storage=(str(hit["storage"]) if hit.get("storage") else None),
    )


async def _conversation_infos(
    state: Any,
    *,
    user_id: str,
    bundle_id: Optional[str],
    agent_id: Optional[str] = None,
    conversation_ids: List[str],
) -> Dict[str, ConversationSearchConversationInfo]:
    """Enrich hit conversation ids with title/last_activity_at from the same
    source the list endpoint uses (conversation_browser.list_conversations), so
    the widget needs no client-side join round-trip. Best-effort: enrichment
    failures never fail the search."""
    wanted = [cid for cid in dict.fromkeys(conversation_ids) if cid]
    out: Dict[str, ConversationSearchConversationInfo] = {}
    if not wanted:
        return out
    try:
        browser = getattr(state, "conversation_browser", None)
        if browser is not None:
            data = await browser.list_conversations(
                user_id=user_id,
                days=3650,
                include_titles=True,
                bundle_id=bundle_id,
                agent_id=agent_id,
            )
            by_id = {
                str(item.get("conversation_id") or ""): item
                for item in (data or {}).get("items", [])
                if isinstance(item, dict)
            }
            for cid in wanted:
                item = by_id.get(cid)
                if item:
                    out[cid] = ConversationSearchConversationInfo(
                        title=item.get("title"),
                        last_activity_at=_ts_text(item.get("last_activity_at")),
                    )
    except Exception:
        logger.warning("[conversation.search] title enrichment failed for user=%s", user_id, exc_info=True)
    for cid in wanted:
        out.setdefault(cid, ConversationSearchConversationInfo())
    return out


def shape_search_response(
    *,
    user_id: str,
    result: ConversationSearchResult,
    conversations: Dict[str, ConversationSearchConversationInfo],
) -> ConversationSearchResponse:
    return ConversationSearchResponse(
        user_id=user_id,
        effective_mode=result.effective_mode,
        warnings=list(result.warnings or []),
        hits=[_shape_hit(h) for h in (result.hits or []) if isinstance(h, dict)],
        conversations=conversations,
    )


# -------------------- Endpoint --------------------

@router.post("/{tenant}/{project}/search", response_model=ConversationSearchResponse)
async def search_conversations(
        tenant: str,
        project: str,
        req: ConversationSearchRequest = Body(...),
        session: UserSession = Depends(_user_session_dep),
):
    """Search the authenticated user's conversations (hybrid topic search or
    temporal browse when the query is blank and a time range is set)."""
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")

    params = validated_search_params(req)

    # An agent-bound widget (one chat per agent in a multi-agent app) narrows
    # search to its own agent's conversations. Reuses the backend's existing agent
    # scope — user-wide search filtered by agent_id — by promoting a whole-history
    # ("user") search to "agent". A conversation-scoped search is left untouched (a
    # single conversation is already one agent's), and so is a search with no bound
    # agent. `index_agent_id` normalizes empty -> None, matching how the agent id
    # is stored on conv_messages.
    agent_filter = index_agent_id((req.agent_id or "").strip() or None)
    if agent_filter and params.scope == SCOPE_USER:
        params.scope = SCOPE_AGENT

    requested_bundle = (req.bundle_id or "").strip() or None
    if requested_bundle:
        # Validates the id against the active registry (404 on unknown).
        await _resolve_allowed_bundle_ids_or_404(tenant, project, requested_bundle)
        bundle_id = requested_bundle
    else:
        bundle_id = await _resolve_bundle_id_or_default(tenant, project, None)

    conversation_id = (req.conversation_id or "").strip()
    if params.scope == "conversation":
        await _ensure_conversation_in_scope_or_404(
            tenant,
            project,
            user_id=session.user_id,
            conversation_id=conversation_id,
            bundle_id=requested_bundle,
        )

    state = getattr(router, "state", None)
    pg_pool, model_service, store = await _search_resources(state)

    search_backend = make_conversation_search_backend(
        pg_pool=pg_pool,
        tenant=tenant,
        project=project,
        model_service=model_service,
        store=store,
        user_id=session.user_id,
        conversation_id=conversation_id,
    )
    context = ConversationSearchContext(
        user_id=session.user_id,
        conversation_id=conversation_id,
        bundle_id=bundle_id,
        agent_id=agent_filter,
        tenant=tenant,
        project=project,
    )

    try:
        result = await run_conversation_search(
            context=context, params=params, search_backend=search_backend,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "[conversation.search] failed user=%s tenant=%s project=%s: %s",
            session.user_id, tenant, project, e, exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Conversation search failed")

    if result.missing_query:
        # Safety net; validated_search_params already rejects this shape.
        raise HTTPException(
            status_code=400,
            detail="query is required unless from_ts/to_ts is provided (temporal browse)",
        )

    conversations = await _conversation_infos(
        state,
        user_id=session.user_id,
        bundle_id=bundle_id,
        agent_id=agent_filter,
        conversation_ids=[str(h.get("conversation_id") or "") for h in (result.hits or [])],
    )
    return shape_search_response(
        user_id=session.user_id, result=result, conversations=conversations,
    )
