"""Cross-conversation user memory primitives.

This package provides the SDK-level memory store used by bundles that need
durable user context across conversations.  PostgreSQL is the authoritative
store; Redis may be layered above it for cache invalidation or short leases,
but correctness must not depend on Redis.
"""

from .models import (
    MemoryEvent,
    MemoryRecord,
    MemoryScope,
    MemorySearchMode,
    MemorySearchRequest,
    MemorySearchResult,
    MemoryScopeFilter,
    MemorySignal,
    normalize_scope_filter,
    normalize_terms,
)
from .scoring import (
    build_canonical_key,
    compute_confirmation_rate,
    compute_freshness_score,
    compute_memory_scores,
    compute_tier,
)
from .store import UserMemoryStore
from .tools import UserMemoryTools, UserMemoryToolConfig, make_user_memory_tools
from .events.resolver import (
    MEMORY_OBJECT_NAMESPACE,
    MEMORY_RESOLVER_NAME,
    memory_id_from_ref,
    memory_record_to_object_payload,
    memory_ref_capabilities,
    resolve_memory_ref_action,
)
from .reconciler_agent import (
    MemoryReconciliationAction,
    MemoryReconciliationActionType,
    MemoryReconciliationCandidate,
    MemoryReconciliationOut,
    build_reconciliation_system_prompt,
    build_reconciliation_user_prompt,
    candidate_from_memory_record,
    memory_reconciler_stream,
    parse_reconciliation_output,
    validate_reconciliation_output,
)

__all__ = [
    "MemoryEvent",
    "MemoryRecord",
    "MemoryScope",
    "MemorySearchMode",
    "MemorySearchRequest",
    "MemorySearchResult",
    "MemoryScopeFilter",
    "MemorySignal",
    "normalize_scope_filter",
    "normalize_terms",
    "MemoryReconciliationActionType",
    "UserMemoryStore",
    "UserMemoryToolConfig",
    "UserMemoryTools",
    "MemoryReconciliationAction",
    "MemoryReconciliationCandidate",
    "MemoryReconciliationOut",
    "build_reconciliation_system_prompt",
    "build_reconciliation_user_prompt",
    "build_canonical_key",
    "candidate_from_memory_record",
    "compute_confirmation_rate",
    "compute_freshness_score",
    "compute_memory_scores",
    "compute_tier",
    "make_user_memory_tools",
    "MEMORY_OBJECT_NAMESPACE",
    "MEMORY_RESOLVER_NAME",
    "memory_id_from_ref",
    "memory_record_to_object_payload",
    "memory_ref_capabilities",
    "resolve_memory_ref_action",
    "memory_reconciler_stream",
    "parse_reconciliation_output",
    "validate_reconciliation_output",
]
