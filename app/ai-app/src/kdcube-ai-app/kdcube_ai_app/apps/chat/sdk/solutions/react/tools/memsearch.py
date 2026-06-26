# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List, Optional

import json
import logging

from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import (
    tool_call_block,
    add_block,
    tc_result_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_logical_artifact_path,
    split_logical_artifact_ref,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    ConversationSearchContext,
    ConversationSearchParams,
    run_conversation_search,
)

LOGGER = logging.getLogger("kdcube.sdk.react.memsearch")

TOOL_SPEC = {
    "id": "react.memsearch",
    "purpose": (
        "Conversations are one of the user's memory realms - what was actually said in chat - "
        "alongside durable memories (`mem`) and context boards (`cnv`); together they are districts "
        "of the user's memory. Searches what the USER said (prompts and follow-ups), what the ASSISTANT "
        "said (replies and working summaries), and the user's UPLOADED attachments (their indexed "
        "summaries): pick `targets` by what the user is recalling ('I said...' -> user/attachment; "
        "'you said...' -> assistant). Reach for it whenever a look back would likely help: on an explicit "
        "recall request, AND when the user's intent implies earlier context matters - they refer to "
        "something from before, say it was clearer earlier, can't find or re-locate something, or resume "
        "a dropped thread. "
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
        "include_recovery_sessions": "bool (optional). Default false. Working summaries from turns where you only called memsearch/read/pull (no new artifact produced) are excluded by default, because they self-reference the search topic and would dominate future searches for it. Set true ONLY when explicitly asked to introspect prior memsearch activity.",
    },
    "returns": "turn hits with conversation_id, turn_id, turn_index_path, working_summary_path, snippets, timestamps, and scores/ordinals when available",
    "constraints": [
        "`query` must appear first in the params JSON object.",
        "`targets` must appear second in the params JSON object.",
    ],
}

# The search routing/snippet/result shaping logic now lives in
# `solutions.conversation.api`. This tool is a thin caller: it builds the
# explicit `ConversationSearchContext` from `ctx_browser.runtime_ctx`, runs the
# search, then shapes the user-facing envelope and contributes timeline blocks.

# Per-snippet text preview cap in the JSON result envelope. Each hit's
# snippets carry a trimmed `text` field so the agent can triage without
# react.read'ing every path. Full text is still materialized as separate
# react.tool.result blocks on the timeline.
SNIPPET_PREVIEW_CHARS = 500

# Whitelist of hit-level fields surfaced in the JSON result envelope. Anything
# else on the rich hit (sim/rec/rrf sub-scores, ranks, matched_via_role lists,
# source_query echoes, ts, best_turn_id, conversation_id, turn_id) is telemetry
# or redundant with the snippet paths and is omitted from the envelope. The
# rich struct stays available to runtime callers via state["last_tool_result"].
_ENVELOPE_HIT_FIELDS = ("score", "turn_index_path", "ordinal", "total_turns")


def _as_str(value: Any) -> str:
    return str(value or "").strip()


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


async def handle_react_memsearch(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.memsearch"
    raw_params = tool_call.get("params") or {}

    # Thin caller: parse the tool param envelope and build the EXPLICIT calling
    # context from runtime, then hand both to the conversation search API. The
    # API owns routing/search/snippet shaping; this tool keeps the user-facing
    # envelope + timeline-block contribution (its UI side-effects).
    search_params = ConversationSearchParams.from_tool_params(raw_params)
    context = ConversationSearchContext.from_runtime_ctx(ctx_browser.runtime_ctx)
    conversation_id = context.conversation_id
    turn_id = context.turn_id

    if not search_params.query and not search_params.is_catalog():
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

    LOGGER.info(
        "[react.memsearch] -> conversation.search (scope=%s, mode=%s, has_query=%s)",
        search_params.scope,
        search_params.effective_mode(),
        bool(search_params.query),
    )
    try:
        result = await run_conversation_search(
            context=context,
            params=search_params,
            search_backend=ctx_browser,
        )
    except Exception as exc:
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": f"memsearch_failed:{exc}", "managed": True}
        return state

    if result.missing_query:
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "missing_query", "managed": True}
        return state

    search_hits_formatted = result.hits
    effective_mode = result.effective_mode
    warnings = list(result.warnings)
    total_tokens = result.tokens

    summary_hits: List[Dict[str, Any]] = []
    for hit in search_hits_formatted:
        if not isinstance(hit, dict):
            continue
        # Whitelist only the fields the agent acts on. Everything else (sub-
        # scores, ranks, matched-via-role aggregates, source-query echoes,
        # timestamps, conversation_id, turn_id) is telemetry or redundant with
        # the snippet paths.
        hit_out: Dict[str, Any] = {}
        for k in _ENVELOPE_HIT_FIELDS:
            v = hit.get(k)
            if v is None or v == "":
                continue
            hit_out[k] = v
        # Snippets all belong to the same turn → same conversation as the hit.
        # The hit-level conversation is encoded in the snippet paths (cross-conv
        # via _scope_path_for_conversation), so neither side needs to carry it.
        hit_conv = _as_str(hit.get("conversation_id"))
        snippets_out: List[Dict[str, Any]] = []
        for sn in hit.get("snippets") or []:
            if not isinstance(sn, dict):
                continue
            spath = (sn.get("path") or "").strip()
            if not spath:
                continue
            srole = (sn.get("role") or "").strip()
            display_path = _scope_path_for_conversation(
                path=spath,
                source_conversation_id=hit_conv,
                current_conversation_id=conversation_id,
            )
            sn_out: Dict[str, Any] = {"path": display_path}
            if srole:
                sn_out["role"] = srole
            # Inline a trimmed text preview so the envelope is self-sufficient
            # for triage. Full text still lives as separate timeline blocks.
            stext_raw = sn.get("text")
            if isinstance(stext_raw, str):
                stext = stext_raw.strip()
                if stext:
                    sn_out["text"] = _clip(stext, limit=SNIPPET_PREVIEW_CHARS)
            snippets_out.append(sn_out)
        # Drop hits with no snippet content. A hit without snippets is pure
        # score telemetry attached to nothing — the agent can't act on it and
        # it dilutes the envelope's signal-to-noise. The rich struct on
        # state["last_tool_result"] still keeps everything for runtime callers.
        if not snippets_out:
            continue
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
