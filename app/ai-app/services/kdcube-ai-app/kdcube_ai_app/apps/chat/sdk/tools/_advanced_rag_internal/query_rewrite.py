# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""History-aware standalone query rewrite for advanced RAG."""

from __future__ import annotations

import logging
from typing import Any, Sequence

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You rewrite follow-up questions into self-contained search queries.\n"
    "RULES:\n"
    "- Resolve pronouns and elliptical references using the conversation history.\n"
    "- Preserve technical terms, identifiers, acronyms, version numbers verbatim.\n"
    "- Output ONE rewritten query. No prefixes, no quotes, no explanations.\n"
    "- If the original is already self-contained, return it unchanged.\n"
    "- Never expand into multiple questions or add information not implied by the conversation."
)


def _format_history(history: Sequence[dict], limit: int) -> str:
    if not history:
        return ""
    pruned = list(history)[-limit:]
    out: list[str] = []
    for m in pruned:
        role = (m.get("role") or "user").lower()
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        text = (content or "").strip()
        if not text:
            continue
        label = "User" if role in ("user", "human") else "Assistant"
        out.append(f"{label}: {text[:600]}")
    return "\n".join(out)


async def rewrite_for_retrieval(
        *,
        query: str,
        history: Sequence[dict],
        model_service: Any,
        history_turns: int = 6,
        max_tokens: int = 120,
) -> str:
    """
    Rewrite `query` into a self-contained retrieval query using prior turns.
    Returns the rewritten query, or `query` unchanged on any model error or empty
    history.
    """
    q = (query or "").strip()
    if not q:
        return ""
    hist_block = _format_history(history, history_turns)
    if not hist_block:
        return q
    if model_service is None:
        return q

    user_text = (
        f"CONVERSATION HISTORY (most recent last):\n{hist_block}\n\n"
        f"FOLLOW-UP QUESTION: {q}\n\n"
        f"Rewrite the follow-up question as a self-contained query."
    )

    try:
        client = model_service.get_client("tool.rag.query_rewrite")
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
        logger.warning("query_rewrite model call failed; using original query", exc_info=True)
        return q

    text = (result or {}).get("text", "") or ""
    rewritten = text.strip().strip('"').strip("'")
    if not rewritten:
        return q
    # Reject pathological outputs (e.g., the model echoed the system rules).
    if len(rewritten) > max(2 * len(q) + 200, 400):
        return q
    return rewritten
