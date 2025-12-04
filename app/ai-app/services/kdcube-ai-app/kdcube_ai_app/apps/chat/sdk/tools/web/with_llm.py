import json
import re, yaml, jsonschema
from datetime import datetime, timezone

import time
from typing import Annotated, Optional, List, Dict, Any, Tuple, Set, Callable, Awaitable
import logging

from kdcube_ai_app.apps.chat.sdk.tools.web.filter_segmenter import filter_and_segment_stream
from kdcube_ai_app.apps.chat.sdk.tools.with_llm_backends import generate_content_llm
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase

logger = logging.getLogger(__name__)

async def sources_reconciler(
        _SERVICE,
        objective: Annotated[str, "Objective (what we are trying to achieve with these sources)."],
        queries: Annotated[List[str], "Array of [q1, q2, ...]"],
        sources_list: Annotated[List[Dict[str, Any]], 'Array of {"sid": int, "title": str, "text": str}'],
        max_items: Annotated[int, "Optional: cap of kept sources (default 12)."] = 12,
        reasoning: bool = False
) -> Annotated[str, 'JSON array of kept sources: [{sid, verdict, o_relevance, q_relevance:[{qid,score}], reasoning}]']:

    assert _SERVICE, "ReconcileTools not bound to service"

    def _get_reconciler_instruction(reasoning: bool = False) -> str:
        """Generate reconciler instruction with optional reasoning requirement."""
        reasoning_line = "- Reasoning ≤320 chars; cite concrete clues." if reasoning else ""
        array_desc = (
            "- Array of kept items ONLY: {sid, o_relevance, q_relevance:[{qid,score}], reasoning}"
            if reasoning
            else "- Array of kept items ONLY: {sid, o_relevance, q_relevance:[{qid,score}]}"
        )

        return f"""
    You are a strict source reconciler.
    
    GOAL
    - Input: (1) objective, (2) queries (qid→string), (3) sources [{{sid,url,title,text}}]. 
    - Return ONLY sources relevant to the objective AND at least one query.
    - If a source is irrelevant, DO NOT include it  at all (omit it entirely).
    - Output MUST validate against the provided JSON Schema.
    
    SCORING
    - o_relevance: overall support for objective (0..1).
    - q_relevance: per-query [{{qid,score}}] (0..1).
    Anchors: 0.90–1.00=direct; 0.60–0.89=mostly; 0.30–0.59=weak; <0.30=irrelevant.
    
    HEURISTICS (conservative)
    - Prefer official/primary sources (standards/regulators/vendor docs) over SEO blogs.
    - Penalize generic landing pages requiring click-through.
    - Use title/heading/body overlap; dedupe near-duplicates.
    - When uncertain, drop.
    
    OUTPUT (JSON ONLY)
    {array_desc}
    {reasoning_line}
    - No prose outside JSON.
    """.strip()

    def _get_reconciler_schema(reasoning: bool = False) -> dict:
        """Generate reconciler schema with optional reasoning field."""
        required_fields = ["sid", "o_relevance", "q_relevance"]
        properties = {
            "sid": {"type": "integer"},
            "o_relevance": {"type": "number", "minimum": 0, "maximum": 1},
            "q_relevance": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["qid", "score"],
                    "properties": {
                        "qid": {"type": "string"},
                        "score": {"type": "number", "minimum": 0, "maximum": 1}
                    }
                }
            }
        }

        if reasoning:
            required_fields.append("reasoning")
            properties["reasoning"] = {"type": "string", "maxLength": 320}

        return {
            "type": "array",
            "items": {
                "type": "object",
                "required": required_fields,
                "properties": properties
            },
            "minItems": 0
        }

    _RECONCILER_INSTRUCTION = _get_reconciler_instruction(reasoning=reasoning)
    _RECONCILER_SCHEMA = _get_reconciler_schema(reasoning=reasoning)

    # --- Normalize inputs ---
    queries_dict: Dict[str, str] = {
        str(i + 1): (q or "").strip()
        for i, q in enumerate(queries or [])
        if (q or "").strip()
    }

    prepared_sources: List[Dict[str, Any]] = []
    for row in (sources_list or []):
        try:
            sid = int(row.get("sid"))
        except Exception:
            continue
        title = (row.get("title") or "").strip()
        text = (row.get("text") or row.get("text") or row.get("content") or "").strip()
        url = (row.get("url") or "").strip()
        if not (sid and (title or text)):
            continue
        prepared_sources.append({"sid": sid, "url": url, "title": title, "text": text})

    input_ctx = {
        "objective": (objective or "").strip(),
        "queries": queries_dict
    }

    schema_str = json.dumps(_RECONCILER_SCHEMA, ensure_ascii=False)

    # NOTE: generate_content_llm sets its own role internally; don't pass role=...
    llm_resp_s = await generate_content_llm(
        _SERVICE=_SERVICE,
        agent_name="Sources Reconciler",
        instruction=_RECONCILER_INSTRUCTION,
        input_context=json.dumps(input_ctx, ensure_ascii=False),
        target_format="json",
        schema_json=schema_str,
        sources_json=json.dumps(prepared_sources, ensure_ascii=False),
        cite_sources=False,
        citation_embed="none",
        max_rounds=2,
        max_tokens=1200,
        strict=True,
        role="tool.source.reconciler",
        cache_instruction=True,
        artifact_name=None,
        channel_to_stream="debug",
        infra_call=True,
        include_url_in_source_digest=True
    )

    # --- Parse tool envelope ---
    try:
        env = json.loads(llm_resp_s) if llm_resp_s else {}
    except Exception:
        logger.exception("sources_reconciler: cannot parse LLM envelope")
        env = {}

    ok = bool(env.get("ok"))
    content_str = env.get("content") or ""
    stats = env.get("stats") or {}

    if not ok:
        logger.warning("sources_reconciler: LLM not-ok. stats=%s", stats)

    # Strip accidental fences
    raw = content_str.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]

    # --- Parse reconciled array (kept-only) ---
    try:
        arr = json.loads(raw) if raw else []
    except Exception:
        logger.exception("sources_reconciler: invalid JSON content from model")
        arr = []

    # Coerce/clean; there should be NO dropped items in arr by contract
    kept: List[Dict[str, Any]] = []
    for it in arr if isinstance(arr, list) else []:
        if not isinstance(it, dict):
            continue
        try:
            sid = int(it.get("sid"))
        except Exception:
            continue
        try:
            orel = float(it.get("o_relevance"))
        except Exception:
            orel = 0.0

        # normalize q_relevance
        qrel_in = it.get("q_relevance") or []
        qrel_out = []
        for qr in qrel_in:
            if not isinstance(qr, dict):
                continue
            qid = str(qr.get("qid"))
            try:
                score = float(qr.get("score"))
            except Exception:
                continue
            qrel_out.append({"qid": qid, "score": score})

        reason = (it.get("reasoning") or "").strip()
        record = {
            "sid": sid,
            "o_relevance": orel,
            "q_relevance": qrel_out,
            **({"reasoning": reason[:320]} if reasoning else {})
        }
        kept.append(record)

    # Sort + cap
    kept.sort(key=lambda x: x.get("o_relevance", 0.0), reverse=True)
    if isinstance(max_items, int) and max_items > 0:
        kept = kept[:max_items]

    # --- Logging: brief analytics
    kept_sids = [k["sid"] for k in kept]
    logger.warning(
        "sources_reconciler: objective='%s' kept=%d sids=%s stats=%s",
        objective or "", len(kept), kept_sids, stats
    )

    return json.dumps(kept, ensure_ascii=False)

async def sources_content_filter(
        _SERVICE,
        objective: Annotated[str, "Objective (what we are trying to achieve)."],
        queries: Annotated[List[str], "Array of queries [q1, q2, ...]"],
        # note: we now document optional date fields explicitly
        sources_with_content: Annotated[List[Dict[str, Any]], 'Array of {"sid": int, "content": str, "published_time_iso"?: str, "modified_time_iso"?: str}'],
        on_thinking_fn: Optional[Callable[[str], Awaitable[None]]] = None,
) -> Annotated[List[int], 'List of SIDs to keep']:
    """
    Fast content-based filter to remove duplicates and low-quality content.

    Args:
        _SERVICE: Service instance
        objective: What we're trying to achieve
        queries: List of search queries
        sources_with_content: List of {sid, content, published_time_iso?, modified_time_iso?}

    Returns:
        List of SIDs to keep
    """

    assert _SERVICE, "ContentFilter not bound to service"

    now_iso = datetime.now(timezone.utc).isoformat()
    _FILTER_INSTRUCTION = f"""
You are a content quality filter. Return a JSON array of SIDs to keep.

INPUTS
- objective: what we are trying to achieve
- queries: related search queries
- sources: list of items with {{sid, content, published_time_iso?, modified_time_iso?}}

GOAL: Return ONLY a JSON array of SIDs to keep. Keep the minimal set that best addresses the objective and queries.

EVALUATION CRITERIA (apply in order):

1. RELEVANCE (primary)
   - Keep: Content directly supports objective or answers queries
   - Drop: Off-topic or tangential content

2. SUBSTANCE (primary)
   - Keep: Actionable details (how-to, examples, configurations, data, analysis)
   - Keep: Meaningful text (>150 chars), clear explanations
   - Drop: Just menus/headers/boilerplate, vague overviews without depth

3. UNIQUENESS (deduplication)
   - If 2+ sources cover the same topic with >70% overlap, keep ONLY the best one
   - "Best" = more complete, more actionable, clearer

4. FRESHNESS (tie-breaker only)
   - Use modified_time_iso or published_time_iso when available
   - Prefer recent over old when substance is equal
   - Missing dates = no penalty

SAFEGUARD: If any source has substance, keep at least 1 SID (even if imperfect).

OUTPUT: [sid1, sid2, ...] - Array of integers only, no text.

TODAY: {now_iso}
""".strip()

    _FILTER_SCHEMA = {
        "type": "array",
        "items": {"type": "integer"},
        "minItems": 0
    }

    # Prepare sources for filtering
    prepared_sources: List[Dict[str, Any]] = []
    for row in (sources_with_content or []):
        try:
            sid = int(row.get("sid"))
        except Exception:
            continue
        content = (row.get("content") or "").strip()
        if not (sid and content):
            continue

        prepared_sources.append({
            "sid": sid,
            "content": content,
            "published_time_iso": row.get("published_time_iso"),
            "modified_time_iso": row.get("modified_time_iso"),
        })

    # If too few sources, keep all
    if len(prepared_sources) <= 2:
        return [s["sid"] for s in prepared_sources]

    input_ctx = {
        "objective": (objective or "").strip(),
        "queries": queries or []
    }

    schema_str = json.dumps(_FILTER_SCHEMA, ensure_ascii=False)
    user_message = f"""
TASK CLARIFICATION:
You will see an "objective" and "queries" below in INPUT CONTEXT section. This is what the USER searched for.
"""
    try:
        # Use cheaper/faster settings for content filtering
        llm_resp_s = await generate_content_llm(
            _SERVICE=_SERVICE,
            agent_name="Content Filter",
            instruction=user_message,
            sys_instruction=_FILTER_INSTRUCTION,
            input_context=json.dumps(input_ctx, ensure_ascii=False),
            on_thinking_fn=on_thinking_fn,
            target_format="json",
            citation_embed="none",
            schema_json=schema_str,
            sources_json=json.dumps(prepared_sources, ensure_ascii=False),
            cite_sources=False,
            max_rounds=1,
            max_tokens=300,
            strict=True,
            role="tool.sources.filter.by.content",
            cache_instruction=True,
            artifact_name=None,
            channel_to_stream="debug",
            temperature=0.7,
            infra_call=True,
            include_url_in_source_digest=True
        )
    except Exception:
        logger.exception("sources_content_filter: LLM call failed; keeping all sources")
        return [s["sid"] for s in prepared_sources]

    # Parse response
    try:
        env = json.loads(llm_resp_s) if llm_resp_s else {}
    except Exception:
        logger.exception("sources_content_filter: cannot parse LLM envelope")
        return [s["sid"] for s in prepared_sources]

    content_str = (env.get("content") or "").strip()
    if content_str.startswith("```"):
        content_str = content_str.split("\n", 1)[1] if "\n" in content_str else content_str
        if "```" in content_str:
            content_str = content_str.rsplit("```", 1)[0]

    try:
        kept_sids = json.loads(content_str) if content_str else []
        if not isinstance(kept_sids, list):
            logger.warning("sources_content_filter: response is not an array")
            return [s["sid"] for s in prepared_sources]

        # Validate all items are integers
        kept_sids = [int(sid) for sid in kept_sids if isinstance(sid, (int, str)) and str(sid).isdigit()]

        # Ensure we're not keeping SIDs that don't exist
        valid_sids = {s["sid"] for s in prepared_sources}
        kept_sids = [sid for sid in kept_sids if sid in valid_sids]

        logger.info(
            f"sources_content_filter: objective='{(objective or '')[:100]}' "
            f"input={len(prepared_sources)} kept={len(kept_sids)} "
            f"dropped={len(prepared_sources) - len(kept_sids)}"
        )

        return kept_sids

    except Exception:
        logger.exception("sources_content_filter: failed to parse kept SIDs; keeping all")
        return [s["sid"] for s in prepared_sources]

async def sources_filter_and_segment(
        _SERVICE: ModelServiceBase,
        objective: Annotated[str, "Objective (what we are trying to achieve)."],
        queries: Annotated[List[str], "Array of queries [q1, q2, ...]"],
        sources_with_content: Annotated[List[Dict[str, Any]], 'Array of {"sid": int, "content": str, "published_time_iso"?: str, "modified_time_iso"?: str}'],
        on_thinking_fn: Optional[Callable[[str], Awaitable[None]]] = None,
        on_delta_fn: Optional[Callable[[str], Awaitable[None]]] = None,
        thinking_budget: Optional[int] = None,
        end_boundary: Annotated[str, 'Span end boundary mode: "exclusive" (default) or "inclusive"'] = "exclusive",
) -> Annotated[Dict[int, List[Dict[str, str]]], 'Mapping: sid -> [{"s": "...", "e": "..."}] (1–2 spans)']:
    """
    Combined filter + segmenter (returns {sid: [{"s":..., "e":...}], ...}).

    Parameters
    ----------
    end_boundary : {"exclusive","inclusive"}, default "exclusive"
        Controls how the 'e' anchor is interpreted when validating span size:
        - "exclusive": span is [s_pos, e_pos) — ends BEFORE the first char of 'e'
        - "inclusive": span is [s_pos, e_pos + len(e)) — includes the 'e' anchor text
    """
    assert _SERVICE, "FilterSegmenter not bound to service"
    import json
    from datetime import datetime, timezone
    import logging
    logger = logging.getLogger(__name__)

    import kdcube_ai_app.apps.chat.sdk.tools.web.content_filters as content_filters
    from kdcube_ai_app.apps.chat.sdk.tools.web.filter_segmenter import filter_and_segment_stream

    now_iso = datetime.now(timezone.utc).isoformat()
    mode = "balanced"  # or "precision" or "recall" - can be parameterized later

    # ============================================================
    # PREPARE SOURCES
    # ============================================================
    prepared_sources: List[Dict[str, Any]] = []
    for row in (sources_with_content or []):
        try:
            sid = int(row.get("sid"))
        except Exception:
            continue
        content = (row.get("content") or "").strip()
        if not (sid and content):
            continue
        prepared_sources.append({
            "sid": sid,
            "url": row.get("url"),
            "content": content,
            "published_time_iso": row.get("published_time_iso"),
            "modified_time_iso": row.get("modified_time_iso"),
        })

    if not prepared_sources:
        return {}

    input_ctx = {
        "objective": (objective or "").strip(),
        "queries": queries or []
    }

    logger.info(
        f"sources_filter_and_segment: processing {len(prepared_sources)} sources "
        f"for objective: '{(objective or '')[:120]}'"
    )

    # ============================================================
    # CALL LLM (either 2-fold streaming or traditional)
    # ============================================================
    raw_dict = None  # Will hold the raw spans dict from either path

    try:
        role = "tool.sources.filter.by.content.and.segment"
        spec = _SERVICE.router.config.ensure_role(role)

        if spec:
            provider, model = spec["provider"], spec["model"]
            logger.info(f"sources_filter_and_segment: using model {model} from provider {provider} for role {role}")

            if provider == "anthropic":
                # ===== ANTHROPIC: Use 2-fold streaming =====
                logger.info("sources_filter_and_segment: using 2-fold streaming for Anthropic")

                result = await filter_and_segment_stream(
                    _SERVICE,
                    objective=objective,
                    queries=queries,
                    sources_with_content=prepared_sources,
                    mode=mode,
                    on_thinking_fn=on_thinking_fn,
                    thinking_budget=thinking_budget or 180,
                    max_tokens=700,
                    role=role
                )

                # ===== FIX: Extract agent_response properly =====
                raw_dict = result.get("agent_response")
                # If agent_response is empty or not a dict, parse from raw_data
                if not raw_dict or not isinstance(raw_dict, dict):
                    raw_data = result.get("log", {}).get("raw_data", "")
                    if raw_data:
                        try:
                            # Strip markdown fences if present
                            cleaned = raw_data.strip()
                            if cleaned.startswith("```"):
                                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
                                if "```" in cleaned:
                                    cleaned = cleaned.rsplit("```", 1)[0]

                            raw_dict = json.loads(cleaned) if isinstance(cleaned, str) else cleaned
                            result["agent_response"] = raw_dict
                            if not isinstance(raw_dict, dict):
                                logger.warning(f"sources_filter_and_segment: parsed raw_data is not a dict: {type(raw_dict)}")
                                raw_dict = {}
                        except Exception as e:
                            logger.exception(f"sources_filter_and_segment: failed to parse raw_data: {e}")
                            raw_dict = {}
                    else:
                        raw_dict = {}

                import kdcube_ai_app.apps.chat.sdk.viz.logging_helpers as logging_helpers
                logging_helpers.log_agent_packet("ctx.reconciler", "ctx", result)

            else:
                # ===== NON-ANTHROPIC: Use traditional generate_content_llm =====
                logger.info(f"sources_filter_and_segment: using traditional backend for {provider}")

                _INSTRUCTION = content_filters.FILTER_AND_SEGMENT_GUIDE(now_iso)
                user_message = """
TASK CLARIFICATION:
You will see an "objective" and "queries" below in INPUT CONTEXT section. This is what the USER searched for.
"""

                llm_resp_s = await generate_content_llm(
                    _SERVICE=_SERVICE,
                    agent_name="Content Filter + Segmenter",
                    instruction=user_message,
                    sys_instruction=_INSTRUCTION,
                    input_context=json.dumps(input_ctx, ensure_ascii=False),
                    target_format="json",
                    schema_json="",
                    sources_json=json.dumps(prepared_sources, ensure_ascii=False),
                    citation_embed="none",
                    cite_sources=False,
                    max_rounds=1,
                    max_tokens=700,
                    thinking_budget=thinking_budget,
                    strict=True,
                    role=role,
                    cache_instruction=True,
                    artifact_name=None,
                    channel_to_stream="debug",
                    temperature=0.7,
                    on_thinking_fn=on_thinking_fn,
                    on_delta_fn=on_delta_fn,
                    infra_call=True,
                    include_url_in_source_digest=True
                )

                try:
                    env = json.loads(llm_resp_s) if llm_resp_s else {}
                except Exception:
                    logger.exception("sources_filter_and_segment: cannot parse LLM envelope")
                    return {}

                content_str = (env.get("content") or "").strip()
                if content_str.startswith("```"):
                    content_str = content_str.split("\n", 1)[1] if "\n" in content_str else content_str
                    if "```" in content_str:
                        content_str = content_str.rsplit("```", 1)[0]

                try:
                    raw_dict = json.loads(content_str) if content_str else {}
                    if not isinstance(raw_dict, dict):
                        logger.warning("sources_filter_and_segment: result is not an object")
                        return {}
                except Exception:
                    logger.exception("sources_filter_and_segment: failed to parse JSON content")
                    return {}
        else:
            logger.warning("sources_filter_and_segment: no spec found for role, cannot proceed")
            return {}

    except Exception:
        logger.exception("sources_filter_and_segment: LLM call failed")
        return {}

    # ============================================================
    # UNIFIED VALIDATION & SPAN EXTRACTION
    # ============================================================
    if raw_dict is None:
        logger.warning("sources_filter_and_segment: no raw_dict produced")
        return {}

    try:
        valid_sids = {s["sid"] for s in prepared_sources}
        sid_to_content = {s["sid"]: s["content"] for s in prepared_sources}
        out: Dict[int, List[Dict[str, str]]] = {}

        exclusive = (end_boundary.lower() == "exclusive")

        for k, arr in raw_dict.items():
            try:
                sid = int(k)
            except Exception:
                # allow numeric keys or stringified numbers; ignore others
                continue

            if sid not in valid_sids:
                logger.warning(f"sources_filter_and_segment: SID {sid} not in valid set, skipping")
                continue

            source_content = sid_to_content.get(sid, "")
            if not source_content:
                continue

            content_lower = source_content.lower()
            content_len = len(source_content)

            spans: List[Dict[str, str]] = []
            for it in (arr or []):
                if not isinstance(it, dict):
                    continue

                s = (it.get("s") or "").strip()
                e = (it.get("e") or "").strip()

                # --- Anchor length checks (allow e == "") ---
                if not (3 <= len(s) <= 150):
                    logger.debug(f"SID {sid} span rejected - start anchor length out of bounds (len={len(s)})")
                    continue
                if e != "" and not (3 <= len(e) <= 150):
                    logger.debug(f"SID {sid} span rejected - end anchor length out of bounds (len={len(e)})")
                    continue
                if e != "" and s.lower() == e.lower():
                    logger.debug(f"SID {sid} span rejected - identical start/end anchors")
                    continue

                # --- Locate 's' and 'e' (case-insensitive search) ---
                s_lower = s.lower()
                s_idx = content_lower.find(s_lower)
                if s_idx == -1:
                    logger.debug(f"SID {sid} span rejected - start anchor not found: '{s[:50]}'")
                    continue

                # Verify verbatim exactness for 's' (case-sensitive)
                if source_content[s_idx:s_idx+len(s)] != s:
                    logger.debug(f"SID {sid} span rejected - start anchor case/punct mismatch")
                    continue

                if e == "":
                    e_idx = content_len  # end of page
                else:
                    e_lower = e.lower()
                    e_idx = content_lower.find(e_lower, s_idx + len(s))
                    if e_idx == -1:
                        logger.debug(f"SID {sid} span rejected - end anchor not found after start: '{e[:50]}'")
                        continue
                    # Verify verbatim exactness for 'e' (case-sensitive)
                    if source_content[e_idx:e_idx+len(e)] != e:
                        logger.debug(f"SID {sid} span rejected - end anchor case/punct mismatch")
                        continue

                # --- Compute end position based on mode ---
                if e == "":
                    end_pos = e_idx
                else:
                    end_pos = e_idx if exclusive else e_idx + len(e)

                if end_pos <= s_idx:
                    logger.debug(f"SID {sid} span rejected - end before/at start (s_idx={s_idx}, end_pos={end_pos})")
                    continue

                span_size = end_pos - s_idx
                if span_size < 200:  # needs to be substantial
                    logger.debug(f"SID {sid} span rejected - span too small ({span_size} chars)")
                    continue

                # Guard against tiny title/nav slice at very top
                if s_idx < 50 and span_size < 300:
                    logger.debug(f"SID {sid} span rejected - likely title/nav (starts at {s_idx}, size {span_size})")
                    continue

                spans.append({"s": s, "e": e})

            # 🟢 NEW LOGIC: always keep SID if model returned it and content exists.
            # Empty list means: "use full content for this SID (no trimming)".
            out[sid] = spans or []

        logger.info(f"sources_filter_and_segment: produced spans for {len(out)} sources")
        return out

    except Exception:
        logger.exception("sources_filter_and_segment: validation/parse error")
        return {}

async def filter_search_results_by_content(
        _SERVICE,
        objective: str,
        queries: list,
        search_results: list,
        do_segment: bool = False,
        on_thinking_fn: Optional[Callable[[str], Awaitable[None]]] = None,
        on_delta_fn: Optional[Callable[[str], Awaitable[None]]] = None,
        thinking_budget: int = 0,
        end_boundary: str = "exclusive",
):
    """
    Filter and optionally segment search results based on content quality.

    Args:
        _SERVICE: Service instance
        objective: What we're trying to achieve
        queries: List of search queries
        search_results: List of search result dicts with 'sid', 'content', etc.
        do_segment: If True, also segment content using spans
        on_thinking_fn: Optional callback for thinking output
        on_delta_fn: Optional callback for delta output
        thinking_budget: Token budget for thinking
        end_boundary: "exclusive" (default) or "inclusive" - how 'e' anchor is interpreted

    Returns:
        Filtered (and possibly segmented) search results
    """
    if not search_results:
        logger.info("filter_search_results_by_content: no results to filter")
        return []

    logger.info(
        f"filter_search_results_by_content: filtering {len(search_results)} sources "
        f"(segment={do_segment}) for objective: '{objective[:100]}'"
    )

    import kdcube_ai_app.apps.chat.sdk.tools.web.content_filters as content_filters

    # Prepare items with extra signals
    sources_for_filter = []
    for row in search_results:
        # Only keep rows where content fetch really succeeded
        fetch_status = row.get("fetch_status")
        if fetch_status not in ("success", "archive"):
            continue

        content = (row.get("content") or "").strip()
        if not content:
            # Nothing to segment here
            continue
        pub_iso = row.get("published_time_iso")
        mod_iso = row.get("modified_time_iso")
        sources_for_filter.append({
            "sid": row["sid"],
            "url": row.get("url"),
            "content": content,
            "published_time_iso": pub_iso,
            "modified_time_iso": mod_iso,
        })

    try:
        if do_segment:
            # ===== Combined path: filter + segment =====
            spans_map = await sources_filter_and_segment(
                _SERVICE=_SERVICE,
                objective=objective,
                queries=queries,
                sources_with_content=sources_for_filter,
                on_thinking_fn=on_thinking_fn,
                on_delta_fn=on_delta_fn,              # ← ADD THIS
                thinking_budget=thinking_budget,
                end_boundary=end_boundary,            # ← ADD THIS
            ) or {}

            # Normalize keys to ints
            try:
                spans_map_int = {}
                for k, v in (spans_map.items() if isinstance(spans_map, dict) else []):
                    try:
                        spans_map_int[int(k)] = v or []
                    except Exception:
                        continue
                spans_map = spans_map_int
            except Exception:
                logger.exception("filter_search_results_by_content: failed to normalize spans_map keys")
                spans_map = {}

            kept_sids_set = set(spans_map.keys())
            filtered_rows = [row for row in search_results if row["sid"] in kept_sids_set]

            # Apply spans to content
            applied = 0
            failed_to_apply = 0

            for row in filtered_rows:
                sid = row["sid"]
                spans = spans_map.get(sid) or []
                if spans:
                    original = row.get("content", "") or ""
                    pruned = content_filters.trim_with_spans(
                        original,
                        spans,
                        ctx_before=600,
                        ctx_after=600,
                        min_gap=400,
                        max_joined=30000,
                        end_boundary=end_boundary,
                    )

                    if pruned:
                        row["seg_spans"] = spans
                        row["seg_end_boundary"] = end_boundary
                        row["content_original_length"] = len(original)
                        row["content_pruned_length"] = len(pruned)

                        # Consider it applied if we actually shortened meaningfully
                        # OR if coverage is intentionally full.
                        coverage_ratio = len(pruned) / max(1, len(original))
                        FULL_THRESH = 0.98  # treat >=98% as full coverage

                        if coverage_ratio < FULL_THRESH:
                            row["content"] = pruned
                            row["content_length"] = len(pruned)
                            applied += 1
                            logger.debug("filter_fetch_results: SID %s trimmed (%.1f%% of original).",
                                         sid, 100.0 * coverage_ratio)
                        else:
                            # Full-coverage is OK; leave content unchanged but mark success.
                            row["content_length"] = len(original)
                            row["seg_full_coverage"] = True
                            applied += 1
                            logger.info("filter_fetch_results: SID %s spans cover entire body (%.1f%%); leaving content intact.",
                                        sid, 100.0 * coverage_ratio)
                    else:
                        failed_to_apply += 1
                        logger.warning(
                            "filter_fetch_results: SID %s spans did not match; keeping original (spans=%r)",
                            sid, spans,
                        )


            dropped = len(search_results) - len(filtered_rows)
            logger.info(
                f"filter_search_results_by_content: filter+segment results: "
                f"kept {len(filtered_rows)}/{len(search_results)} sources, "
                f"dropped {dropped}, spans applied to {applied} sources, "
                f"failed to apply {failed_to_apply}"
            )

            search_results = filtered_rows

        else:
            # ===== Pure filter path (existing behavior) =====
            kept_sids = await sources_content_filter(
                _SERVICE=_SERVICE,
                objective=objective,
                queries=queries,
                sources_with_content=sources_for_filter
            )

            kept_sids_set = set(kept_sids)
            filtered_rows = [row for row in search_results if row["sid"] in kept_sids_set]

            dropped = len(search_results) - len(filtered_rows)
            logger.info(
                f"filter_search_results_by_content: content filter results: "
                f"kept {len(filtered_rows)}/{len(search_results)} sources, "
                f"dropped {dropped}"
            )

            search_results = filtered_rows

    except Exception:
        logger.exception(
            "filter_search_results_by_content: filter/segment failed; keeping all fetched sources"
        )

    return search_results

async def filter_fetch_results(_SERVICE,
                               objective: str,
                               results: Dict[str, Dict[str, Any]],
                               *,
                               end_boundary: str = "exclusive",  # "exclusive" (default) or "inclusive"
                               on_thinking_fn: Optional[Callable[[str], Awaitable[None]]] = None,
                               thinking_budget: Optional[int] = 0,
                               ) -> List[Dict[str, Any]]:

    sources_for_seg: List[Dict[str, Any]] = []
    try:
        import kdcube_ai_app.apps.chat.sdk.tools.web.content_filters as content_filters

        obj = (objective or "").strip()

        # Build pseudo-sources for the segmenter from successful/archive pages.
        sources_for_seg: List[Dict[str, Any]] = []
        url_to_sid: Dict[str, int] = {}

        # We accept both "success" and "archive" (and optionally "ok") as "good" fetches.
        GOOD_STATUSES = {"success", "archive", "ok"}

        # Use a stable synthetic SID per URL for this segmentation call.
        sid_counter = 1
        for url, entry in results.items():
            status = (entry.get("status") or "").lower()
            if status not in GOOD_STATUSES:
                continue

            content = (entry.get("content") or "").strip()
            if not content:
                continue

            sid = sid_counter
            sid_counter += 1

            sources_for_seg.append({
                "sid": sid,
                "url": url,
                "content": content,
                "published_time_iso": entry.get("published_time_iso"),
                "modified_time_iso": entry.get("modified_time_iso"),
            })
            url_to_sid[url] = sid

        if sources_for_seg:
            # Segmenter only: we pass [objective] as a single query.
            spans_map = await sources_filter_and_segment(
                _SERVICE=_SERVICE,
                objective=obj,
                queries=[obj],
                sources_with_content=sources_for_seg,
                on_thinking_fn=on_thinking_fn,
                thinking_budget=thinking_budget,
                on_delta_fn=None,
                end_boundary=end_boundary,   # <-- propagate boundary policy to the validator
            ) or {}

            # Normalize keys to ints and ensure value is always a list (possibly empty).
            spans_map_int: Dict[int, List[Dict[str, str]]] = {}
            if isinstance(spans_map, dict):
                for k, v in spans_map.items():
                    try:
                        sid = int(k)
                    except Exception:
                        continue
                    spans_map_int[sid] = list(v or [])
            spans_map = spans_map_int

            applied = 0
            failed_to_apply = 0

            # For each "good" URL, try to apply spans. If no spans / bad spans → keep full content.
            for url, sid in url_to_sid.items():
                entry = results.get(url)
                if not entry:
                    continue

                spans = spans_map.get(sid) or []
                if not spans:
                    # No spans returned (keep full content to preserve recall).
                    continue

                original = entry.get("content") or ""
                if not original:
                    continue

                # Trimmer supports e == "" and boundary modes.
                pruned = content_filters.trim_with_spans(
                    original,
                    spans,
                    ctx_before=600,
                    ctx_after=600,
                    min_gap=400,
                    max_joined=30000,
                    end_boundary=end_boundary,
                )

                if pruned:
                    entry["seg_spans"] = spans
                    entry["seg_end_boundary"] = end_boundary
                    entry["content_original_length"] = len(original)
                    entry["content_pruned_length"] = len(pruned)

                    # Consider it applied if we actually shortened meaningfully
                    # OR if coverage is intentionally full.
                    coverage_ratio = len(pruned) / max(1, len(original))
                    FULL_THRESH = 0.98  # treat >=98% as full coverage

                    if coverage_ratio < FULL_THRESH:
                        entry["content"] = pruned
                        entry["content_length"] = len(pruned)
                        applied += 1
                        logger.debug("filter_fetch_results: SID %s trimmed (%.1f%% of original).",
                                     sid, 100.0 * coverage_ratio)
                    else:
                        # Full-coverage is OK; leave content unchanged but mark success.
                        entry["content_length"] = len(original)
                        entry["seg_full_coverage"] = True
                        applied += 1
                        logger.info("filter_fetch_results: SID %s spans cover entire body (%.1f%%); leaving content intact.",
                                    sid, 100.0 * coverage_ratio)
                else:
                    failed_to_apply += 1
                    logger.warning(
                        "filter_fetch_results: SID %s spans did not match; keeping original (spans=%r)",
                        sid, spans,
                    )

            logger.info(
                "filter_fetch_results: segmentation complete for objective='%s': applied=%d, failed_to_apply=%d, total_segmentable=%d",
                obj[:80],
                applied,
                failed_to_apply,
                len(sources_for_seg),
            )

    except Exception:
        # Defensive: segmentation is best-effort and must never break fetch semantics.
        logger.exception("filter_fetch_results: objective-based segmentation failed; returning unsegmented content")
    return sources_for_seg