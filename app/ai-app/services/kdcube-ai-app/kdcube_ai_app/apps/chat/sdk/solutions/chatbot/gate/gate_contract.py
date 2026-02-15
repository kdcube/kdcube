from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


Route = Literal[
    "general_no_tools",        # no tools (context allowed)
    "tools_general",           # generic tools
    "tools_security",          # security tools
    "service_error"
]


class ContextRefTarget(BaseModel):
    """Context search target used by gate."""
    where: Literal["assistant", "user", "assistant_artifact"] = Field(
        ...,
        description="Scope to search: 'user' | 'assistant' | 'assistant_artifact' (assistant-produced artifacts)."
    )
    query: str = Field(
        ...,
        description="Semantic-search key phrases (1–3 compact phrases; ≤ ~12 tokens; no quotes/Booleans)."
    )
    reason: Optional[str] = Field(
        None,
        description="Optional one-liner why this target/query helps. Omit if trivial."
    )


class FeedbackOut(BaseModel):
    turn_id: Optional[str] = Field(None, description="Only include if not null")
    text: str = Field("", description="Only include if not empty")
    confidence: float = Field(0.0, description="Only include if different from 0.0")
    reaction: Optional[Literal["ok", "not_ok", "neutral"]] = Field(None, description="Only include if not null")


class GateOut(BaseModel):
    # ticket/answer detection - only include if not null
    extracted_answer: Optional[str] = Field(None, description="Only include if not null")

    # routing - only include if not default
    route: Route = Field("general_no_tools", description="Only include if not 'general_no_tools'")

    # topics - only include if not empty
    topics: List[str] = Field(default_factory=list, description="Only include if not empty array")

    conversation_title: Optional[str] = Field("", description="Conversation title")

    # context retrieval hints - only include if not empty
    ctx_retrieval_queries: List[ContextRefTarget] = Field(default_factory=list, description="Context search queries (max 2).")

    # feedback & clarifications - only include if not null/False or not empty
    feedback: Optional[FeedbackOut] = Field(None, description="Only include if not null")
    feedback_match_targets: List[ContextRefTarget] = Field(
        default_factory=list,
        description="When `feedback` is emitted, include up to 2 search targets to help locate the referenced prior turn."
    )
    clarification_questions: List[str] = Field(default_factory=list, description="Only include if not empty array")

    @property
    def is_answer(self) -> bool:
        return bool((self.extracted_answer or "").strip())


def gate_topics(raw: Any) -> List[str]:
    if isinstance(raw, GateOut):
        return [t for t in raw.topics if isinstance(t, str) and t.strip()]
    if isinstance(raw, dict):
        topics = raw.get("topics") or []
        out: List[str] = []
        for t in topics:
            if isinstance(t, str) and t.strip():
                out.append(t.strip())
            elif isinstance(t, dict):
                name = (t.get("name") or "").strip()
                if name:
                    out.append(name)
        return out
    return []


def gate_ctx_queries(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, GateOut):
        return [q.model_dump() for q in raw.ctx_retrieval_queries]
    if isinstance(raw, dict):
        queries = raw.get("ctx_retrieval_queries")
        if not isinstance(queries, list):
            queries = raw.get("context_ref_targets") or []
        out: List[Dict[str, Any]] = []
        for q in queries:
            if isinstance(q, dict) and q.get("where") and q.get("query"):
                out.append({
                    "where": q.get("where"),
                    "query": q.get("query"),
                    **({"reason": q.get("reason")} if q.get("reason") else {}),
                })
        return out
    return []
