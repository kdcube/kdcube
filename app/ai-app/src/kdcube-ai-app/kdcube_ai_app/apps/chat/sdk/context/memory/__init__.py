"""Cross-conversation user memory primitives.

This package provides the SDK-level memory store used by bundles that need
durable user context across conversations.  PostgreSQL is the authoritative
store; Redis may be layered above it for cache invalidation or short leases,
but correctness must not depend on Redis.
"""

from kdcube_ai_app.apps.chat.sdk.context.memory.models import (
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
from kdcube_ai_app.apps.chat.sdk.context.memory.scoring import (
    build_canonical_key,
    compute_confirmation_rate,
    compute_freshness_score,
    compute_memory_scores,
    compute_tier,
)
from kdcube_ai_app.apps.chat.sdk.context.memory.store import (
    MEMORY_SCOPE_PREF_CHANNEL,
    MEMORY_SCOPE_PREF_DEFAULT,
    MEMORY_SCOPE_PREF_FAMILY,
    UserMemoryStore,
    normalize_memory_scope_pref,
)
from kdcube_ai_app.apps.chat.sdk.context.memory.tools import UserMemoryTools, UserMemoryToolConfig, make_user_memory_tools
from kdcube_ai_app.apps.chat.sdk.context.memory.instructions import MEMORY_REACT_ADDITIONAL_INSTRUCTIONS, resolve_memory_react_additional_instructions
from kdcube_ai_app.apps.chat.sdk.context.memory.named_service import (
    KNOWN_MEMORY_KINDS,
    MEMORY_RECORD_SCHEMA,
    MEMORY_SEARCH_SCOPES,
    MemoryNamedServiceProvider,
    NAMESPACE,
    OBJECT_KIND,
    PROVIDER_ID,
    make_memory_named_service_provider,
    memory_named_service_spec,
)
from kdcube_ai_app.apps.chat.sdk.context.memory.events.resolver import (
    MEMORY_OBJECT_NAMESPACE,
    MEMORY_RESOLVER_NAME,
    memory_id_from_ref,
    memory_record_to_object_payload,
    memory_ref_capabilities,
    resolve_memory_ref_action,
)
from kdcube_ai_app.apps.chat.sdk.context.memory.reconciler_agent import (
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
    "MEMORY_REACT_ADDITIONAL_INSTRUCTIONS",
    "resolve_memory_react_additional_instructions",
    "normalize_scope_filter",
    "normalize_terms",
    "normalize_memory_scope_pref",
    "MEMORY_SCOPE_PREF_FAMILY",
    "MEMORY_SCOPE_PREF_CHANNEL",
    "MEMORY_SCOPE_PREF_DEFAULT",
    "MemoryReconciliationActionType",
    "UserMemoryStore",
    "UserMemoryToolConfig",
    "UserMemoryTools",
    "KNOWN_MEMORY_KINDS",
    "MEMORY_RECORD_SCHEMA",
    "MEMORY_SEARCH_SCOPES",
    "MemoryNamedServiceProvider",
    "NAMESPACE",
    "OBJECT_KIND",
    "PROVIDER_ID",
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
    "make_memory_named_service_provider",
    "make_user_memory_tools",
    "memory_named_service_spec",
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
