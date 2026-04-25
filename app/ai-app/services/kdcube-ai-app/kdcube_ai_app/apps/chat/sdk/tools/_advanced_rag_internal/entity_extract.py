# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Generic open-vocabulary entity extraction for advanced RAG."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You extract specific named entities and identifiers from a single user question.\n"
    "EXTRACT (only when present in the question):\n"
    "- Proper nouns (products, libraries, frameworks, organisations, people)\n"
    "- Identifiers (model names, error codes, version numbers, ticket ids, file paths)\n"
    "- Acronyms and abbreviations\n"
    "- Domain-specific terms that disambiguate the question\n"
    "EXCLUDE: generic words, verbs, common adjectives, stop-words.\n"
    "OUTPUT: a JSON object exactly of the form {\"entities\": [\"...\", \"...\"]}.\n"
    "Output JSON only. No prose, no markdown fences."
)

_JSON_RE = re.compile(r"\{[^{}]*\"entities\"\s*:\s*\[[^\]]*\][^{}]*\}", re.DOTALL)


def _filter_in_question(question: str, candidates: list[str]) -> list[str]:
    """Drop entities that don't appear (case-insensitively) in the question — anti-hallucination."""
    q_low = question.lower()
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        s = (c or "").strip()
        if not s or len(s) > 80:
            continue
        key = s.lower()
        if key in seen:
            continue
        # Allow if the entity (or its first token) appears in the question.
        if key in q_low or any(tok in q_low for tok in re.findall(r"[A-Za-z0-9_.-]{3,}", key)):
            out.append(s)
            seen.add(key)
    return out


async def extract_entities(
        *,
        query: str,
        model_service: Any,
        max_entities: int = 5,
        max_tokens: int = 200,
) -> List[str]:
    """
    Extract a small list of entity strings from `query`. Returns [] on any model
    error or unparseable output. Entities not appearing (case-insensitively) in
    the original question are filtered out to suppress hallucinations.
    """
    q = (query or "").strip()
    if not q or model_service is None:
        return []

    user_text = f"QUESTION: {q}\n\nReturn JSON: {{\"entities\": [\"...\"]}}"

    try:
        client = model_service.get_client("tool.rag.entity_extract")
        cfg = model_service.describe_client(
            getattr(model_service, "answer_generator_client", client),
            role="answer_generator",
        )
        result = await model_service.call_model_text(
            client,
            [SystemMessage(content=_SYSTEM), HumanMessage(content=user_text)],
            temperature=0.0,
            max_tokens=max_tokens,
            client_cfg=cfg,
            role="answer_generator",
        )
    except Exception:
        logger.warning("entity_extract model call failed; returning []", exc_info=True)
        return []

    text = ((result or {}).get("text") or "").strip()
    if not text:
        return []

    # Try strict parse first; fall back to a regex-extracted JSON object.
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except Exception:
        m = _JSON_RE.search(text)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None

    if not isinstance(parsed, dict):
        return []
    raw = parsed.get("entities") or []
    if not isinstance(raw, list):
        return []

    cleaned = _filter_in_question(q, [str(x) for x in raw if x])
    return cleaned[:max_entities]
