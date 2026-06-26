# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation search API with an EXPLICIT calling context.

This module owns the orchestration that used to live inside the
`react.memsearch` tool: parse params -> route catalog vs hybrid -> run the
lower-level search -> extract snippets from turn logs -> shape rich hits.

The critical design point is the calling context. The old flow read three
identity values off `ctx_browser.runtime_ctx` (a `RuntimeCtx`):

    * user_id          <- ctx_browser.runtime_ctx.user_id
    * conversation_id  <- ctx_browser.runtime_ctx.conversation_id
    * turn_id          <- ctx_browser.runtime_ctx.turn_id   (current turn; used
                          by the tool only to label result blocks — NOT a search
                          filter)

and one more identity value implicitly, via the search backend's own scoping:

    * bundle_id        <- search backend scope (conv_index filters by bundle_id
                          when present); carried here for completeness so a
                          public caller can set it.

Isolation by tenant/project is NOT a WHERE filter. It is the Postgres SCHEMA
NAME the search backend (`conv_index` via `ctx_client`/`ctx_browser`) is bound
to, which is DERIVED FROM tenant + project at backend-construction time. So the
context carries `tenant`/`project` (and an optional pre-derived `schema`)
purely as provenance: the actual schema selection happens when the search
backend is built, not inside this API. We capture them so a future public/site
API can hand the right backend + identity together.

`run_conversation_search` therefore takes:
    * an explicit `ConversationSearchContext` (identity, set by the caller), and
    * a `search_backend` (the object exposing `search` / `search_turn_catalog`
      / `get_turn_log` — today `ctx_browser`),

and reads NO ambient contextvars. The ReAct tool builds the context from
`ctx_browser.runtime_ctx` and passes `ctx_browser` as the backend; a public API
would build the context from request auth and pass a backend bound to the
caller's tenant/project schema.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import (
    TimelineView,
    build_timeline_payload,
    extract_assistant_completion_blocks,
    extract_user_attachments_from_blocks,
)

# Matches the dotted-name convention used by the sibling named-service modules
# (e.g. "kdcube.sdk.conversation.named_service", "kdcube.sdk.memory.named_service").
LOGGER = logging.getLogger("kdcube.sdk.conversation.search")

# --- Mode labels (effective_mode in the result payload) ---
# MODE_HYBRID is the topic-search path: parallel semantic + lexical retrieval
# fused by Reciprocal Rank Fusion with a recency lift (see search_context
# scoring_mode="rrf_hybrid" in ctx_rag.py). It is the only mode that runs
# when the caller passes `query`.
MODE_HYBRID = "hybrid"
# Catalog modes: deterministic turn-catalog lookups, no ranking.
MODE_ORDINAL = "ordinal"
MODE_TEMPORAL = "temporal"
MODE_TIMELINE = "timeline"
MODE_CATALOG = "catalog"  # back-compat alias for an unspecified catalog request
# Back-compat alias: older clients may still send `mode="semantic"` as input;
# it routes to the topic-search path identically to omitting `mode`.
MODE_SEMANTIC_LEGACY = "semantic"

CATALOG_MODES = frozenset({MODE_TEMPORAL, MODE_ORDINAL, MODE_TIMELINE, MODE_CATALOG})

# --- Scope ---
SCOPE_CONVERSATION = "conversation"
SCOPE_USER = "user"
ALLOWED_SCOPES = frozenset({SCOPE_CONVERSATION, SCOPE_USER})

# --- Order ---
ORDER_ASC = "asc"
ORDER_DESC = "desc"
ALLOWED_ORDERS = frozenset({ORDER_ASC, ORDER_DESC})

# Content types this realm searches. `attachment` maps onto the user side of the
# index (the user's uploaded attachment summaries), `summary` onto the assistant
# side (working summaries). `notes` are the assistant's internal notes.
DEFAULT_TARGETS = ("assistant", "user", "attachment", "summary")
ALLOWED_TARGETS = frozenset({"assistant", "user", "attachment", "summary", "notes"})

# Maximum number of hits returned per source conversation in a single response.
# Caps the visibility of a single conversation's polluted/repetitive content
# (e.g., recovery sessions about a topic) so other conversations get
# representation in the top-k. Cap of 2 keeps legitimate multi-turn matches
# inside one conversation while preventing one conversation from monopolizing
# the result.
MAX_HITS_PER_CONVERSATION = 2


class ConversationSearchBackend(Protocol):
    """The lower-level search plumbing this API orchestrates.

    `ctx_browser` (ContextBrowser) satisfies this today. It is the structural
    contract — identity is NOT read off this object; it is passed explicitly via
    `ConversationSearchContext`. A public API can satisfy this with any backend
    bound to the caller's tenant/project schema.
    """

    async def search(self, **kwargs: Any) -> Any: ...

    async def search_turn_catalog(self, **kwargs: Any) -> Any: ...

    async def get_turn_log(self, *, turn_id: str, conversation_id: Optional[str] = None) -> Any: ...


@dataclass
class ConversationSearchContext:
    """Explicit identity for a conversation search.

    Every field documents WHERE it came from in the old `react.memsearch` flow
    so a future public/site API can populate it from request auth instead of
    ambient runtime context.

    user_id:
        The user whose conversations are searched. The search backend always
        filters by this (a hard isolation boundary). Old source:
        `ctx_browser.runtime_ctx.user_id`. A public API sets it from the
        authenticated principal.

    conversation_id:
        The "current" conversation. With `scope="conversation"` the search is
        confined to this conversation; with `scope="user"` it is the anchor used
        only to mark cross-conversation refs (so `conv_<id>.` is inserted only
        for OTHER conversations). Old source:
        `ctx_browser.runtime_ctx.conversation_id`.

    turn_id:
        The CURRENT turn id. It is not a search filter — it is metadata callers
        use to label produced timeline/result blocks. Old source:
        `ctx_browser.runtime_ctx.turn_id`.

    bundle_id:
        The app/bundle scope. `conv_index` filters by `bundle_id` only when
        present. Old flow relied on the backend's own scope; carried here so a
        public caller can set it explicitly. Old source (implicit):
        `ctx_browser.runtime_ctx.bundle_id`.

    tenant / project / schema:
        Tenant and project are NOT WHERE filters. Isolation is the Postgres
        SCHEMA NAME, which is DERIVED FROM tenant + project when the search
        backend is constructed. These fields are provenance only: this API does
        not select the schema — the backend handed in is already bound to one.
        A public API populates them so it can build/select the right backend.
        Old source (implicit): `ctx_browser.runtime_ctx.tenant/project`.
    """

    user_id: str
    conversation_id: str = ""
    turn_id: str = ""
    bundle_id: Optional[str] = None
    tenant: Optional[str] = None
    project: Optional[str] = None
    schema: Optional[str] = None

    @classmethod
    def from_runtime_ctx(cls, runtime_ctx: Any) -> "ConversationSearchContext":
        """Build the explicit context from a `RuntimeCtx` (the ReAct path).

        This is the ONLY place that reads identity off the runtime context; the
        rest of the API treats the context as already-explicit.
        """
        return cls(
            user_id=str(getattr(runtime_ctx, "user_id", "") or ""),
            conversation_id=str(getattr(runtime_ctx, "conversation_id", "") or ""),
            turn_id=str(getattr(runtime_ctx, "turn_id", "") or ""),
            bundle_id=getattr(runtime_ctx, "bundle_id", None),
            tenant=getattr(runtime_ctx, "tenant", None),
            project=getattr(runtime_ctx, "project", None),
        )


@dataclass
class ConversationSearchParams:
    """Normalized search request, independent of any tool param envelope."""

    query: str = ""
    targets: List[str] = field(default_factory=lambda: list(DEFAULT_TARGETS))
    scope: str = SCOPE_CONVERSATION
    from_ts: str = ""
    to_ts: str = ""
    ordinal: Optional[int] = None
    order: str = ORDER_ASC
    top_k: int = 5
    days: Optional[int] = None
    mode: str = ""
    include_recovery_sessions: bool = False

    @classmethod
    def from_tool_params(cls, params: Dict[str, Any]) -> "ConversationSearchParams":
        """Parse the loose `react.memsearch` param shape into a normalized request.

        Mirrors the old field handling exactly (aliases, defaults, validation)
        so the tool's external behavior is unchanged.
        """
        params = params or {}
        query = _as_str(params.get("query"))
        raw_targets = params.get("targets")
        if raw_targets is None:
            raw_targets = list(DEFAULT_TARGETS)
        targets = [t for t in (raw_targets or []) if isinstance(t, str) and t.strip()]
        top_k = _as_int(params.get("top_k"), 5) or 5
        mode = _as_str(params.get("mode")).lower()
        if mode and mode not in CATALOG_MODES and mode != MODE_SEMANTIC_LEGACY:
            mode = ""
        scope = _as_str(params.get("scope") or SCOPE_CONVERSATION).lower()
        if scope not in ALLOWED_SCOPES:
            scope = SCOPE_CONVERSATION
        ordinal = _as_int(params.get("ordinal"))
        from_ts = _as_str(params.get("from") or params.get("from_ts") or params.get("start") or params.get("start_at"))
        to_ts = _as_str(params.get("to") or params.get("to_ts") or params.get("end") or params.get("end_at"))
        order = _as_str(params.get("order") or ORDER_ASC).lower()
        if order not in ALLOWED_ORDERS:
            order = ORDER_ASC
        days = _as_int(params.get("days"))
        return cls(
            query=query,
            targets=targets,
            scope=scope,
            from_ts=from_ts,
            to_ts=to_ts,
            ordinal=ordinal,
            order=order,
            top_k=top_k,
            days=days,
            mode=mode,
            include_recovery_sessions=bool(params.get("include_recovery_sessions")),
        )

    def has_temporal_bounds(self) -> bool:
        return bool(self.from_ts or self.to_ts)

    def is_catalog(self) -> bool:
        return (
            self.mode in CATALOG_MODES
            or self.ordinal is not None
            or (self.has_temporal_bounds() and not self.query)
        )

    def effective_mode(self) -> str:
        if self.is_catalog():
            if self.mode in CATALOG_MODES:
                return self.mode
            if self.ordinal is not None:
                return MODE_ORDINAL
            if self.has_temporal_bounds():
                return MODE_TEMPORAL
            return MODE_TIMELINE
        return MODE_HYBRID

    def effective_days(self) -> int:
        if self.days is not None:
            return self.days
        return 3650 if (self.is_catalog() or self.has_temporal_bounds()) else 365

    def timestamp_filters(self) -> List[Dict[str, Any]]:
        filters: List[Dict[str, Any]] = []
        if self.from_ts:
            filters.append({"op": ">=", "value": self.from_ts})
        if self.to_ts:
            filters.append({"op": "<", "value": self.to_ts})
        return filters


@dataclass
class ConversationSearchResult:
    """Rich result of a conversation search.

    `hits` is the rich per-turn structure (with snippets) the old tool stored on
    `state["last_tool_result"]`. `effective_mode`, `warnings`, and `tokens` feed
    the JSON envelope. `missing_query` flags the "no query and not a catalog
    request" error the old tool surfaced as a managed error.
    """

    hits: List[Dict[str, Any]] = field(default_factory=list)
    effective_mode: str = MODE_HYBRID
    warnings: List[str] = field(default_factory=list)
    tokens: int = 0
    missing_query: bool = False


# ---------------------------------------------------------------------------
# Helpers (moved verbatim in behavior from the old tool)
# ---------------------------------------------------------------------------

def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default


def _as_str(value: Any) -> str:
    return str(value or "").strip()


def _ts_to_text(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return _as_str(value)


def _clip(text: Any, limit: int = 4000) -> str:
    s = _as_str(text)
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)].rstrip() + "…"


def _catalog_snippets(row: Dict[str, Any], targets: List[str]) -> List[Dict[str, Any]]:
    tid = _as_str(row.get("turn_id"))
    if not tid:
        return []
    conversation_id = _as_str(row.get("conversation_id"))
    want_summary = "summary" in targets
    want_user = "user" in targets
    want_assistant = "assistant" in targets
    snippets: List[Dict[str, Any]] = []
    if want_summary and _as_str(row.get("working_summary_text")):
        snippets.append({
            "role": "summary",
            "path": row.get("working_summary_path") or f"ws:{tid}.conv.working.summary",
            "text": _clip(row.get("working_summary_text")),
            "ts": _ts_to_text(row.get("working_summary_ts") or row.get("started_at") or row.get("ts")),
            "meta": {"source": "turn_catalog", **({"conversation_id": conversation_id} if conversation_id else {})},
        })
    if want_user and _as_str(row.get("first_user_text")):
        snippets.append({
            "role": "user",
            "path": row.get("user_path") or f"ar:{tid}.user.prompt",
            "text": _clip(row.get("first_user_text")),
            "ts": _ts_to_text(row.get("first_user_ts") or row.get("started_at") or row.get("ts")),
            "meta": {"source": "turn_catalog", **({"conversation_id": conversation_id} if conversation_id else {})},
        })
    if want_assistant and _as_str(row.get("last_assistant_text")):
        snippets.append({
            "role": "assistant",
            "path": row.get("assistant_path") or f"ar:{tid}.assistant.completion",
            "text": _clip(row.get("last_assistant_text")),
            "ts": _ts_to_text(row.get("last_assistant_ts") or row.get("ended_at") or row.get("ts")),
            "meta": {"source": "turn_catalog", **({"conversation_id": conversation_id} if conversation_id else {})},
        })
    return snippets


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def run_conversation_search(
    *,
    context: ConversationSearchContext,
    params: ConversationSearchParams,
    search_backend: ConversationSearchBackend,
) -> ConversationSearchResult:
    """Run a conversation search with explicit identity.

    Reads NO ambient contextvars. Identity comes from `context`; the search
    plumbing comes from `search_backend` (today: `ctx_browser`). Returns the
    rich per-turn hit structure; callers (the ReAct tool today) shape the
    user-facing envelope and contribute timeline blocks.
    """
    from kdcube_ai_app.apps.chat.sdk.util import token_count

    user = context.user_id
    conversation_id = context.conversation_id
    targets = list(params.targets)

    effective_mode = params.effective_mode()
    catalog_mode = params.is_catalog()
    ignored_catalog_query = bool(catalog_mode and params.query)
    warnings: List[str] = []
    if ignored_catalog_query:
        warnings.append(
            f"query ignored in {effective_mode} catalog mode; use query only for topic search, "
            f"or omit catalog signals for topic+time search"
        )

    # Entry observability. Counts + params only — never the full query text at
    # INFO (a short clipped form is fine for triage). One greppable line keyed by
    # [conversation.search].
    LOGGER.info(
        "[conversation.search] start user_id=%s conversation_id=%s bundle_id=%s routing=%s "
        "effective_mode=%s scope=%s has_query=%s query=%r targets=%s top_k=%s from=%s to=%s "
        "ordinal=%s order=%s days=%s",
        user,
        conversation_id,
        context.bundle_id or "",
        "catalog" if catalog_mode else "hybrid",
        effective_mode,
        params.scope,
        bool(params.query),
        _clip(params.query, limit=80),
        targets,
        params.top_k,
        params.from_ts or "",
        params.to_ts or "",
        params.ordinal,
        params.order,
        params.effective_days(),
    )

    if not params.query and not catalog_mode:
        LOGGER.info(
            "[conversation.search] done user_id=%s conversation_id=%s effective_mode=%s "
            "missing_query=True hits=0 warnings=%s tokens=0",
            user,
            conversation_id,
            effective_mode,
            len(warnings),
        )
        return ConversationSearchResult(
            effective_mode=effective_mode,
            warnings=warnings,
            missing_query=True,
        )

    days = params.effective_days()
    top_k = params.top_k
    search_hits_formatted: List[Dict[str, Any]] = []
    total_tokens = 0

    if catalog_mode:
        try:
            rows = await search_backend.search_turn_catalog(
                user=user,
                conv=conversation_id,
                scope=params.scope,
                top_k=top_k,
                days=days,
                order=params.order,
                ordinal=params.ordinal,
                from_ts=params.from_ts or None,
                to_ts=params.to_ts or None,
            )
        except Exception:
            LOGGER.exception(
                "[conversation.search] failed user_id=%s conversation_id=%s effective_mode=%s scope=%s",
                user,
                conversation_id,
                effective_mode,
                params.scope,
            )
            raise
        hits = []
        for row in rows or []:
            tid = _as_str(row.get("turn_id"))
            if not tid:
                continue
            snippets = _catalog_snippets(row, targets)
            hit_conversation_id = _as_str(row.get("conversation_id") or conversation_id)
            for sn in snippets:
                total_tokens += token_count(sn.get("text") or "")
            hits.append({
                "conversation_id": hit_conversation_id,
                "turn_id": tid,
                "turn_index_path": row.get("turn_index_path") or f"ar:{tid}.react.turn.index",
                "working_summary_path": row.get("working_summary_path") or f"ws:{tid}.conv.working.summary",
                "snippets": snippets,
                "score": None,
                "sim_score": None,
                "recency_score": None,
                "matched_via_role": "turn_catalog",
                "source_query": "",
                **({"ignored_query": params.query} if ignored_catalog_query else {}),
                "mode": effective_mode,
                "scope": params.scope,
                "ordinal": row.get("ordinal"),
                "total_turns": row.get("total_turns"),
                "started_at": row.get("started_at") or row.get("ts"),
                "ended_at": row.get("ended_at") or "",
                "about": row.get("about") or "",
                "ts": row.get("started_at") or row.get("ts"),
                "best_turn_id": tid,
            })
        search_hits_formatted = hits
        LOGGER.info(
            "[conversation.search] done user_id=%s conversation_id=%s effective_mode=%s "
            "routing=catalog hits=%s warnings=%s tokens=%s",
            user,
            conversation_id,
            effective_mode,
            len(search_hits_formatted),
            len(warnings),
            total_tokens,
        )
        return ConversationSearchResult(
            hits=search_hits_formatted,
            effective_mode=effective_mode,
            warnings=warnings,
            tokens=total_tokens,
        )

    # --- Hybrid (topic) path ---
    # search_context expects list[dict] with {"where","query"}; map targets to that shape.
    search_targets: List[Dict[str, Any]] = []
    seen_where = set()
    for t in targets:
        where = "user" if t == "attachment" else "assistant" if t == "summary" else "notes" if t == "notes" else t
        if not where or where in seen_where:
            continue
        search_targets.append({"where": where, "query": params.query})
        seen_where.add(where)

    # Over-fetch from the retriever so the per-conversation dedup below has enough
    # material to fill the user-requested top_k after collapsing same-conversation runs.
    retriever_top_k = max(top_k * MAX_HITS_PER_CONVERSATION + top_k, top_k + 10)
    try:
        best_tid, hits = await search_backend.search(
            targets=search_targets,
            user=user,
            conv=conversation_id,
            scope=params.scope,
            scoring_mode="rrf_hybrid",
            half_life_days=7.0,
            top_k=retriever_top_k,
            days=days,
            with_payload=True,
            timestamp_filters=params.timestamp_filters(),
            include_recovery_sessions=params.include_recovery_sessions,
        )
    except Exception:
        LOGGER.exception(
            "[conversation.search] failed user_id=%s conversation_id=%s effective_mode=%s scope=%s",
            user,
            conversation_id,
            effective_mode,
            params.scope,
        )
        raise
    # Per-conversation dedup: keep at most MAX_HITS_PER_CONVERSATION hits per
    # source conversation, preserving the retriever's rank order, then trim to
    # the user-requested top_k. Prevents one noisy/repetitive conversation from
    # monopolizing the result.
    if hits:
        per_conv_count: Dict[str, int] = {}
        deduped: List[Dict[str, Any]] = []
        for h in hits:
            conv = _as_str(h.get("conversation_id") or conversation_id)
            if per_conv_count.get(conv, 0) >= MAX_HITS_PER_CONVERSATION:
                continue
            deduped.append(h)
            per_conv_count[conv] = per_conv_count.get(conv, 0) + 1
            if len(deduped) >= top_k:
                break
        hits = deduped

    want_user = "user" in targets
    want_assistant = "assistant" in targets
    want_attachment = "attachment" in targets
    want_summary = "summary" in targets
    want_notes = "notes" in targets

    for h in (hits or []):
        tid = (h.get("turn_id") or "").strip()
        if not tid:
            continue
        hit_conversation_id = _as_str(h.get("conversation_id") or conversation_id)
        try:
            turn_log = await search_backend.get_turn_log(turn_id=tid, conversation_id=hit_conversation_id)
            blocks = list(turn_log.get("blocks") or [])
            timeline_payload = build_timeline_payload(
                blocks=blocks,
                sources_pool=turn_log.get("sources_pool") or [],
            )
            TimelineView.from_payload(timeline_payload)
        except Exception:
            continue

        snippets: List[Dict[str, Any]] = []

        if want_user:
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                if (blk.get("turn_id") or "") != tid:
                    continue
                btype = (blk.get("type") or "").strip()
                if btype not in {"user.prompt", "user.followup", "user.followup.preserved", "user.steer", "user.steer.preserved"}:
                    continue
                path = (blk.get("path") or "").strip()
                text = (blk.get("text") or "").strip()
                if text:
                    total_tokens += token_count(text)
                snippets.append({
                    "conversation_id": hit_conversation_id,
                    "role": "user",
                    "path": path,
                    "text": text,
                    "ts": blk.get("ts") or "",
                    "meta": blk.get("meta") if isinstance(blk.get("meta"), dict) else {},
                })
        if want_assistant:
            for blk in extract_assistant_completion_blocks(blocks):
                if (blk.get("turn_id") or "") != tid:
                    continue
                path = (blk.get("path") or "").strip()
                text = (blk.get("text") or "").strip()
                if text:
                    total_tokens += token_count(text)
                snippets.append({
                    "conversation_id": hit_conversation_id,
                    "role": "assistant",
                    "path": path,
                    "text": text,
                    "ts": blk.get("ts") or "",
                    "meta": blk.get("meta") if isinstance(blk.get("meta"), dict) else {},
                })
        if want_summary:
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                if (blk.get("turn_id") or "") != tid:
                    continue
                if (blk.get("type") or "").strip() != "conv.working.summary":
                    continue
                path = (blk.get("path") or "").strip()
                text = (blk.get("text") or "").strip()
                if text:
                    total_tokens += token_count(text)
                snippets.append({
                    "conversation_id": hit_conversation_id,
                    "role": "summary",
                    "path": path,
                    "text": text,
                    "ts": blk.get("ts") or "",
                    "meta": blk.get("meta") if isinstance(blk.get("meta"), dict) else {},
                })
        if want_notes:
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                if (blk.get("turn_id") or "") != tid:
                    continue
                if (blk.get("type") or "").strip() not in {"react.note", "react.note.preserved"}:
                    continue
                path = (blk.get("path") or "").strip()
                text = (blk.get("text") or "").strip()
                if text:
                    total_tokens += token_count(text)
                snippets.append({
                    "conversation_id": hit_conversation_id,
                    "role": "notes",
                    "path": path,
                    "text": text,
                    "ts": blk.get("ts") or "",
                    "meta": blk.get("meta") if isinstance(blk.get("meta"), dict) else {},
                })
        if want_attachment:
            attachment_text_by_path: Dict[str, str] = {}
            attachment_meta_text_by_path: Dict[str, str] = {}
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                path = (blk.get("path") or "").strip()
                if not path:
                    continue
                btype = (blk.get("type") or "").strip()
                text = (blk.get("text") or "").strip()
                if btype == "user.attachment.text" and text:
                    attachment_text_by_path[path] = text
                elif btype == "user.attachment.meta" and text:
                    attachment_meta_text_by_path[path] = text

            for att in extract_user_attachments_from_blocks(blocks):
                if not isinstance(att, dict):
                    continue
                path = (att.get("artifact_path") or "").strip()
                if not path:
                    continue
                text = (
                    attachment_text_by_path.get(path)
                    or str(att.get("summary") or "").strip()
                    or attachment_meta_text_by_path.get(path)
                    or ""
                )
                if text:
                    total_tokens += token_count(text)
                snippets.append({
                    "conversation_id": hit_conversation_id,
                    "role": "attachment",
                    "path": path,
                    "text": text,
                    "ts": att.get("ts") or "",
                    "meta": dict(att),
                })

        hit_out_meta = {
            "conversation_id": hit_conversation_id,
            "turn_id": tid,
            "turn_index_path": f"ar:{tid}.react.turn.index",
            "snippets": snippets,
            "score": h.get("score"),
            "sim_score": h.get("sim"),
            "recency_score": h.get("rec"),
            "matched_via_role": h.get("matched_via_role"),
            "source_query": h.get("source_query"),
            "ts": h["ts"].isoformat() if hasattr(h.get("ts"), "isoformat") else h.get("ts"),
            "best_turn_id": best_tid,
        }
        for key in ("rrf_score", "sem_rank", "lex_rank", "trgm_rank", "primary_source"):
            if key in h:
                hit_out_meta[key] = h[key]
        search_hits_formatted.append(hit_out_meta)

    LOGGER.info(
        "[conversation.search] done user_id=%s conversation_id=%s effective_mode=%s "
        "routing=hybrid hits=%s warnings=%s tokens=%s",
        user,
        conversation_id,
        effective_mode,
        len(search_hits_formatted),
        len(warnings),
        total_tokens,
    )
    return ConversationSearchResult(
        hits=search_hits_formatted,
        effective_mode=effective_mode,
        warnings=warnings,
        tokens=total_tokens,
    )


__all__ = [
    "ALLOWED_ORDERS",
    "ALLOWED_SCOPES",
    "ALLOWED_TARGETS",
    "CATALOG_MODES",
    "DEFAULT_TARGETS",
    "MAX_HITS_PER_CONVERSATION",
    "MODE_HYBRID",
    "MODE_ORDINAL",
    "MODE_TEMPORAL",
    "MODE_TIMELINE",
    "ORDER_ASC",
    "ORDER_DESC",
    "SCOPE_CONVERSATION",
    "SCOPE_USER",
    "ConversationSearchBackend",
    "ConversationSearchContext",
    "ConversationSearchParams",
    "ConversationSearchResult",
    "run_conversation_search",
]
