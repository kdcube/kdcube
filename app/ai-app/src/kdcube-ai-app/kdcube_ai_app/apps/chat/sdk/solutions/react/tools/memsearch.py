# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List, Optional

import json

import time
from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import (
    build_timeline_payload,
    TimelineView,
    extract_assistant_completion_blocks,
    extract_user_attachments_from_blocks,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import (
    tool_call_block,
    notice_block,
    add_block,
    tc_result_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_logical_artifact_path,
    split_logical_artifact_ref,
)

TOOL_SPEC = {
    "id": "react.memsearch",
    "purpose": (
        "Search prior conversation memory and return turn-level recovery handles. "
        "Use only when the exact needed path is not already visible. "
        "Behavior is inferred from which fields you set: "
        "topic clue -> set `query` (hybrid semantic+lexical+recency search); "
        "topic inside a time window -> `query` + `from`/`to`; "
        "second/first/nth turn -> `ordinal` (no `query`); "
        "date-only clue with no topic -> `from`/`to` (no `query`); "
        "broad conversation overview -> no `query`, no `ordinal`, no bounds, with `targets=['summary']`. "
        "Scope: the default `scope=\"conversation\"` only searches the CURRENT conversation. "
        "To recover material from another conversation the same user has had with you "
        "(\"last week we talked about ...\", \"yesterday you helped me with ...\", a topic "
        "the user clearly worked on before but not in this conversation), pass `scope=\"user\"`. "
        "Recovery path: memsearch -> read returned refs; if refs are incomplete, "
        "read ar:turn_<id>.react.turn.index, then batch-read/pull exact ar:/tc:/fi:/so: refs. "
        "If a returned `fi:` path starts `fi:conv_<conversation_id>.turn_<id>...`, the `conv_` segment is the "
        "conversation scope and the artifact belongs to that other conversation; pass that exact path to "
        "react.read/react.pull/react.checkout/react.rg."
    ),
    "args": {
        "query": "str (FIRST FIELD). Natural-language query. Required for topic search. Omit when you only want a catalog lookup (ordinal/date-window/overview).",
        "targets": "list[str] (SECOND FIELD). Any of: assistant|user|attachment|summary|notes. Defaults to all except notes.",
        "scope": "str (optional). conversation|user. Default `conversation` searches only this conversation. Set `user` to also search the same user's other conversations with you — required for cross-conversation recall.",
        "from": "ISO timestamp (optional). Start of temporal window.",
        "to": "ISO timestamp (optional). End of temporal window, exclusive.",
        "ordinal": "int (optional). 1-based turn ordinal in the selected scope/window.",
        "order": "str (optional). asc|desc for catalog results. Default asc.",
        "top_k": "int (optional). Max hits to return (default 5).",
        "days": "int (optional). Lookback window in days (default 365).",
    },
    "returns": "turn hits with conversation_id, turn_id, turn_index_path, working_summary_path, snippets, timestamps, and scores/ordinals when available",
    "constraints": [
        "`query` must appear first in the params JSON object.",
        "`targets` must appear second in the params JSON object.",
    ],
}

# --- Mode labels (effective_mode in the result payload) ---
# MODE_HYBRID is the topic-search path: parallel semantic + lexical retrieval
# fused by Reciprocal Rank Fusion with a recency lift (see search_context
# scoring_mode="rrf_hybrid" in ctx_rag.py). It is the only mode that runs
# when the agent passes `query`.
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

# Per-snippet text preview cap in the JSON result envelope. Each hit's
# snippets carry a trimmed `text` field so the agent can triage without
# react.read'ing every path. Full text is still materialized as separate
# react.tool.result blocks on the timeline.
SNIPPET_PREVIEW_CHARS = 500


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default


def _as_str(value: Any) -> str:
    return str(value or "").strip()


def _timestamp_filters_from_params(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    from_ts = _as_str(params.get("from") or params.get("from_ts") or params.get("start") or params.get("start_at"))
    to_ts = _as_str(params.get("to") or params.get("to_ts") or params.get("end") or params.get("end_at"))
    filters: List[Dict[str, Any]] = []
    if from_ts:
        filters.append({"op": ">=", "value": from_ts})
    if to_ts:
        filters.append({"op": "<", "value": to_ts})
    return filters


def _ts_to_text(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return _as_str(value)


def _clip(text: Any, limit: int = 4000) -> str:
    s = _as_str(text)
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)].rstrip() + "…"


# Namespaces whose logical paths can carry a `conv_<id>.` segment after the
# scheme prefix. Adding a new prefix here is safe — `_scope_path_for_conversation`
# is a path-rewrite only; the read-side must understand the same convention for
# round-trip resolution to work.
_CROSS_CONV_NAMESPACE_PREFIXES = ("fi:", "ev:", "ws:", "ar:", "tc:", "so:")


def _scope_path_for_conversation(*, path: Any, source_conversation_id: str, current_conversation_id: str) -> str:
    """
    Rewrite a logical path so it self-describes its source conversation when
    that conversation differs from the current one. The agent can then pass
    the path verbatim to `react.read` / `react.pull` / `react.checkout` /
    `react.rg` without also having to track the conversation_id externally.

    Convention: insert `conv_<id>.` immediately after the namespace prefix
    (e.g. `ws:turn_X...` becomes `ws:conv_<id>.turn_X...`). If the path
    already carries a `conv_<id>.` segment, or the source/current conversations
    are the same, the path is returned unchanged. For `fi:` paths the canonical
    artifact builder is used so external-attachment and other special shapes
    stay correct.
    """
    raw = _as_str(path)
    source_conv = _as_str(source_conversation_id)
    current_conv = _as_str(current_conversation_id)
    if not raw or not source_conv or source_conv == current_conv:
        return raw
    if raw.startswith("fi:"):
        existing_conv, turn_id, namespace, rel = split_logical_artifact_ref(raw)
        if existing_conv or not (turn_id and namespace and rel):
            return raw
        scoped = build_logical_artifact_path(
            turn_id=turn_id,
            namespace=namespace,
            relpath=rel,
            conversation_id=source_conv,
        )
        return scoped or raw
    for prefix in _CROSS_CONV_NAMESPACE_PREFIXES:
        if prefix == "fi:":
            continue
        if not raw.startswith(prefix):
            continue
        body = raw[len(prefix):]
        # Already self-scoped (or starts with a path component that looks scoped).
        if body.startswith("conv_"):
            return raw
        return f"{prefix}conv_{source_conv}.{body}"
    return raw


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


async def handle_react_memsearch(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.memsearch"
    params = tool_call.get("params") or {}
    query = (params.get("query") or "").strip()
    raw_targets = params.get("targets")
    if raw_targets is None:
        raw_targets = ["assistant", "user", "attachment", "summary"]
    targets = [t for t in (raw_targets or []) if isinstance(t, str) and t.strip()]
    top_k = int(params.get("top_k") or 5)
    # `mode` is an input field kept for back-compat. Empty/unknown/explicit
    # "semantic" all route to the topic-search path; only the catalog values
    # actually steer routing. There is no agent-facing "semantic" default —
    # the topic path runs hybrid retrieval (see MODE_HYBRID).
    mode = _as_str(params.get("mode")).lower()
    if mode and mode not in CATALOG_MODES and mode != MODE_SEMANTIC_LEGACY:
        mode = ""
    scope = _as_str(params.get("scope") or SCOPE_CONVERSATION).lower()
    if scope not in ALLOWED_SCOPES:
        scope = SCOPE_CONVERSATION
    ordinal = _as_int(params.get("ordinal"))
    from_ts = _as_str(params.get("from") or params.get("from_ts") or params.get("start") or params.get("start_at"))
    to_ts = _as_str(params.get("to") or params.get("to_ts") or params.get("end") or params.get("end_at"))
    has_temporal_bounds = bool(from_ts or to_ts)
    order = _as_str(params.get("order") or ORDER_ASC).lower()
    if order not in ALLOWED_ORDERS:
        order = ORDER_ASC
    catalog_mode = mode in CATALOG_MODES or ordinal is not None or (has_temporal_bounds and not query)
    if catalog_mode:
        effective_mode = mode if mode in CATALOG_MODES else (
            MODE_ORDINAL if ordinal is not None
            else MODE_TEMPORAL if has_temporal_bounds
            else MODE_TIMELINE
        )
    else:
        effective_mode = MODE_HYBRID
    ignored_catalog_query = bool(catalog_mode and query)
    warnings: List[str] = []
    if ignored_catalog_query:
        warnings.append(
            f"query ignored in {effective_mode} catalog mode; use query only for topic search, or omit catalog signals for topic+time search"
        )
    days = int(params.get("days") or (3650 if catalog_mode or has_temporal_bounds else 365))

    if not query and not catalog_mode:
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "missing_query", "managed": True}
        return state

    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        payload={
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "params": tool_call.get("params") or {},
        },
    )

    search_hits_formatted: List[Dict[str, Any]] = []
    total_tokens = 0
    user = ctx_browser.runtime_ctx.user_id
    conversation_id = ctx_browser.runtime_ctx.conversation_id
    turn_id = ctx_browser.runtime_ctx.turn_id
    from kdcube_ai_app.apps.chat.sdk.util import token_count
    try:
        if catalog_mode:
            rows = await ctx_browser.search_turn_catalog(
                user=user,
                conv=conversation_id,
                scope=scope,
                top_k=top_k,
                days=days,
                order=order,
                ordinal=ordinal,
                from_ts=from_ts or None,
                to_ts=to_ts or None,
            )
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
                    **({"ignored_query": query} if ignored_catalog_query else {}),
                    "mode": effective_mode,
                    "scope": scope,
                    "ordinal": row.get("ordinal"),
                    "total_turns": row.get("total_turns"),
                    "started_at": row.get("started_at") or row.get("ts"),
                    "ended_at": row.get("ended_at") or "",
                    "about": row.get("about") or "",
                    "ts": row.get("started_at") or row.get("ts"),
                    "best_turn_id": tid,
                })
            search_hits_formatted = hits
        else:
            # search_context expects list[dict] with {"where","query"}; map targets to that shape
            search_targets: List[Dict[str, Any]] = []
            seen_where = set()
            for t in targets:
                where = "user" if t == "attachment" else "assistant" if t == "summary" else "notes" if t == "notes" else t
                if not where or where in seen_where:
                    continue
                search_targets.append({"where": where, "query": query})
                seen_where.add(where)

            best_tid, hits = await ctx_browser.search(
                targets=search_targets,
                user=user,
                conv=conversation_id,
                scope=scope,
                scoring_mode="rrf_hybrid",
                half_life_days=7.0,
                top_k=top_k,
                days=days,
                with_payload=True,
                timestamp_filters=_timestamp_filters_from_params(params),
            )
        for h in ([] if catalog_mode else (hits or [])):
            tid = (h.get("turn_id") or "").strip()
            if not tid:
                continue
            hit_conversation_id = _as_str(h.get("conversation_id") or conversation_id)
            try:
                turn_log = await ctx_browser.get_turn_log(turn_id=tid, conversation_id=hit_conversation_id)
                blocks = list(turn_log.get("blocks") or [])
                timeline_payload = build_timeline_payload(
                    blocks=blocks,
                    sources_pool=turn_log.get("sources_pool") or [],
                )
                tv = TimelineView.from_payload(timeline_payload)
            except Exception as ex:
                continue

            snippets: List[Dict[str, Any]] = []
            want_user = "user" in targets
            want_assistant = "assistant" in targets
            want_attachment = "attachment" in targets
            want_summary = "summary" in targets
            want_notes = "notes" in targets

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
            for key in ("rrf_score", "sem_rank", "lex_rank", "primary_source"):
                if key in h:
                    hit_out_meta[key] = h[key]
            search_hits_formatted.append(hit_out_meta)
    except Exception as exc:
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": f"memsearch_failed:{exc}", "managed": True}
        return state

    summary_hits: List[Dict[str, Any]] = []
    for hit in search_hits_formatted:
        if not isinstance(hit, dict):
            continue
        hit_out = {k: v for k, v in hit.items() if k != "snippets"}
        # All snippets in a hit come from the same turn, hence the same
        # conversation. The hit-level `conversation_id` already conveys that;
        # snippet-level conversation_id would be redundant. For cross-conv
        # hits we encode the conv in the path itself (self-describing) via
        # _scope_path_for_conversation.
        hit_conv = _as_str(hit.get("conversation_id"))
        snippets_out: List[Dict[str, Any]] = []
        for sn in hit.get("snippets") or []:
            if not isinstance(sn, dict):
                continue
            spath = (sn.get("path") or "").strip()
            if not spath:
                continue
            srole = (sn.get("role") or "").strip()
            sts = sn.get("ts") or ""
            display_path = _scope_path_for_conversation(
                path=spath,
                source_conversation_id=hit_conv,
                current_conversation_id=conversation_id,
            )
            sn_out: Dict[str, Any] = {"path": display_path}
            if srole:
                sn_out["role"] = srole
            if sts:
                sn_out["ts"] = sts
            # Inline a trimmed text preview so the envelope is self-sufficient
            # for triage. Full text still lives as separate timeline blocks.
            stext_raw = sn.get("text")
            if isinstance(stext_raw, str):
                stext = stext_raw.strip()
                if stext:
                    sn_out["text"] = _clip(stext, limit=SNIPPET_PREVIEW_CHARS)
            snippets_out.append(sn_out)
        if snippets_out:
            hit_out["snippets"] = snippets_out
        summary_hits.append(hit_out)
    summary_payload = {"mode": effective_mode, "hits": summary_hits, "tokens": total_tokens}
    if warnings:
        summary_payload["warnings"] = warnings
    summary_block = {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": json.dumps(summary_payload, ensure_ascii=False, indent=2),
        "meta": {
            "tool_call_id": tool_call_id,
            "render_role": "summary",
        },
    }
    add_block(ctx_browser, summary_block)
    for hit in search_hits_formatted:
        for sn in hit.get("snippets") or []:
            if not isinstance(sn, dict):
                continue
            spath = (sn.get("path") or "").strip()
            stext = sn.get("text") or ""
            if not spath or not isinstance(stext, str) or not stext.strip():
                continue
            sconv = _as_str(sn.get("conversation_id") or hit.get("conversation_id"))
            display_path = _scope_path_for_conversation(
                path=spath,
                source_conversation_id=sconv,
                current_conversation_id=conversation_id,
            )
            snippet_block = {
                "turn": turn_id,
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "mime": "text/markdown",
                "path": display_path,
                "text": stext.strip(),
                "meta": {
                    "tool_call_id": tool_call_id,
                    **({"conversation_id": sconv} if sconv and sconv != conversation_id and display_path == spath else {}),
                    **({"physical_path": (sn.get("meta") or {}).get("physical_path")} if isinstance(sn.get("meta"), dict) else {}),
                    **({"hosted_uri": (sn.get("meta") or {}).get("hosted_uri")} if isinstance(sn.get("meta"), dict) else {}),
                    **({"rn": (sn.get("meta") or {}).get("rn")} if isinstance(sn.get("meta"), dict) else {}),
                    **({"key": (sn.get("meta") or {}).get("key")} if isinstance(sn.get("meta"), dict) else {}),
                },
            }
            add_block(ctx_browser, snippet_block)
    state["last_tool_result"] = search_hits_formatted
    return state
