from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Literal, Mapping, Optional, Sequence


MemorySearchMode = Literal[
    "hybrid",
    "recent",
    "recent_created",
    "recent_events",
    "important",
    "confirmed",
    "hotset",
]

MemoryStatus = Literal["active", "weakened", "unsupported", "retired", "merged"]
MemoryVisibility = Literal["private", "user", "owner", "public", "internal"]
MemoryScopeFilter = Literal["all_user_memories", "current_bundle", "global_only", "current_bundle_or_global"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_term(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_terms(values: str | Iterable[str] | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw = values.split(",")
    else:
        raw = [str(v) for v in values]
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        value = normalize_term(item)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def normalize_status(value: str | None) -> str:
    status = normalize_term(value or "active")
    if status in {"active", "weakened", "unsupported", "retired", "merged"}:
        return status
    return "active"


def normalize_visibility(value: str | None) -> str:
    visibility = normalize_term(value or "user")
    if visibility in {"private", "user", "owner", "public", "internal"}:
        return visibility
    return "user"


def normalize_scope_filter(value: str | None) -> str:
    normalized = normalize_term(value or "current_bundle").replace("-", "_").replace(" ", "_")
    aliases = {
        "all": "all_user_memories",
        "all_user": "all_user_memories",
        "user_all": "all_user_memories",
        "all_bundles": "all_user_memories",
        "any": "all_user_memories",
        "any_agent": "all_user_memories",
        "all_agents": "all_user_memories",
        "user": "all_user_memories",
        "made_by_user": "all_user_memories",
        "current": "current_bundle",
        "bundle": "current_bundle",
        "bundle_only": "current_bundle",
        "current_bundle_only": "current_bundle",
        "this_agent": "current_bundle",
        "current_agent": "current_bundle",
        "made_by_this_agent": "current_bundle",
        "global": "global_only",
        "global_memories": "global_only",
        "current_or_global": "current_bundle_or_global",
        "bundle_or_global": "current_bundle_or_global",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"all_user_memories", "current_bundle", "global_only", "current_bundle_or_global"}:
        return normalized
    return "current_bundle"


def is_user_visible(visibility: str | None) -> bool:
    return normalize_visibility(visibility) in {"user", "owner", "public"}


@dataclass(frozen=True)
class MemoryScope:
    tenant: str
    project: str
    user_id: str
    bundle_id: str = ""

    def normalized(self) -> "MemoryScope":
        return MemoryScope(
            tenant=str(self.tenant or "default").strip() or "default",
            project=str(self.project or "default").strip() or "default",
            user_id=str(self.user_id or "anonymous").strip() or "anonymous",
            bundle_id=str(self.bundle_id or "").strip(),
        )


@dataclass(frozen=True)
class MemorySignal:
    memory: str
    context: str = ""
    kind: str = "fact"
    event_type: str = "agent_observation"
    originator: str = "agent"
    status: str = "active"
    visibility: str = "user"
    labels: Sequence[str] = field(default_factory=list)
    keywords: Sequence[str] = field(default_factory=list)
    confidence: float = 0.5
    importance: float = 0.5
    pinned: Optional[bool] = None
    canonical_key: str = ""
    embedding: Optional[Sequence[float]] = None
    embedding_model: str = ""
    source: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def normalized_labels(self) -> list[str]:
        return normalize_terms(self.labels)

    def normalized_keywords(self) -> list[str]:
        return normalize_terms(self.keywords)


@dataclass(frozen=True)
class MemorySearchRequest:
    scope: MemoryScope
    query: str = ""
    mode: MemorySearchMode = "hybrid"
    labels: Sequence[str] = field(default_factory=list)
    keywords: Sequence[str] = field(default_factory=list)
    kind: str = ""
    status: str = "active"
    visible_to_user: Optional[bool] = None
    include_private: bool = True
    scope_filter: MemoryScopeFilter | str = "current_bundle"
    originator: str = ""
    limit: int = 8
    offset: int = 0
    candidate_limit: int = 200
    query_embedding: Optional[Sequence[float]] = None
    min_relevance_score: float = 0.0
    half_life_days: float = 45.0
    factor_weights: Optional[Mapping[str, float]] = None


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    scope: MemoryScope
    memory: str
    context: str
    kind: str
    status: str
    visibility: str
    labels: Sequence[str]
    keywords: Sequence[str]
    tier: int
    pinned: bool
    confidence_score: float
    importance_score: float
    freshness_score: float
    salience_score: float
    confirmation_rate: float
    evidence_count: int
    update_count: int
    confirmation_count: int
    contradiction_count: int
    created_at: datetime
    updated_at: datetime
    last_event_at: datetime
    last_confirmed_at: Optional[datetime] = None
    source: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    revision: int = 1


@dataclass(frozen=True)
class MemoryEvent:
    id: str
    memory_id: str
    scope: MemoryScope
    event_type: str
    signal_text: str
    context: str
    originator: str
    confidence: float
    importance: float
    labels: Sequence[str]
    keywords: Sequence[str]
    created_at: datetime
    source: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemorySearchResult:
    memory: MemoryRecord
    score: float
    score_breakdown: Dict[str, float]
    matched_events: Sequence[MemoryEvent] = field(default_factory=list)
