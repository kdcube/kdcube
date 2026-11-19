# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Pure intersection engine for one resident agent's effective resources."""

from __future__ import annotations

import json
from fnmatch import fnmatchcase
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from kdcube_ai_app.apps.chat.sdk.runtime.resident_resources.models import (
    AuthoritySource,
    AvailabilityReason,
    ConversationNarrowing,
    DelegatedCardSnapshot,
    DelegatedResourceGrant,
    EffectiveResidentInventory,
    InvocationPolicyState,
    RejectedResidentResource,
    ResidentAgentCeiling,
    ResidentResourceCandidate,
    ResidentToolDescriptor,
    ResolvedResidentResource,
    ResolvedResidentTool,
    ResourceFamilyCeiling,
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _identity_scope(value: Any) -> str:
    return _clean(value) or "grantor"


def _transport(value: Any) -> str:
    return _clean(value).lower().replace("_", "-")


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    return any(fnmatchcase(value, pattern) for pattern in patterns)


def _candidate_scope_matches(
    ceiling: ResidentAgentCeiling,
    candidate: ResidentResourceCandidate,
    grantor_subject: str,
) -> bool:
    base_matches = (
        candidate.tenant == ceiling.tenant
        and candidate.project == ceiling.project
        and candidate.application == ceiling.application
        and candidate.agent_id == ceiling.agent_id
    )
    if not base_matches:
        return False
    if candidate.authority_source is AuthoritySource.APPLICATION:
        return True
    return candidate.grantor_subject == grantor_subject


def _card_scope_matches(
    ceiling: ResidentAgentCeiling,
    grantor_subject: str,
    identity_scope: str,
    card: DelegatedCardSnapshot,
) -> bool:
    return (
        card.tenant == ceiling.tenant
        and card.project == ceiling.project
        and card.application == ceiling.application
        and card.agent_id == ceiling.agent_id
        and card.grantor_subject == grantor_subject
        and _identity_scope(card.identity_scope) == _identity_scope(identity_scope)
    )


def _family_matches(
    family: ResourceFamilyCeiling,
    candidate: ResidentResourceCandidate,
) -> bool:
    if candidate.family_id and candidate.family_id != family.family_id:
        return False
    if candidate.resource_kind not in family.resource_kinds:
        return False
    if candidate.authority_source not in family.authority_sources:
        return False
    if _transport(candidate.binding.transport) not in family.transports:
        return False
    if not _matches_any(candidate.resource_id, family.resource_patterns):
        return False

    if family.endpoint_schemes or family.endpoint_hosts:
        # The binding endpoint can be the aggregate Connection Hub gateway,
        # not the upstream resource. An upstream-origin ceiling therefore
        # requires a sanitized provider origin supplied by the host adapter.
        endpoint = candidate.provider_endpoint
        if not endpoint:
            return False
        parsed = urlsplit(endpoint)
        if family.endpoint_schemes and parsed.scheme.lower() not in family.endpoint_schemes:
            return False
        hostname = (parsed.hostname or "").lower()
        if family.endpoint_hosts and not _matches_any(hostname, family.endpoint_hosts):
            return False
    return True


def _admitting_family(
    ceiling: ResidentAgentCeiling,
    candidate: ResidentResourceCandidate,
) -> ResourceFamilyCeiling | None:
    for family in ceiling.resource_families:
        if _family_matches(family, candidate):
            return family
    return None


def _candidate_tool_key(
    tool: ResidentToolDescriptor,
) -> tuple[str, str, str, str, str]:
    try:
        schema = json.dumps(tool.input_schema, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        schema = ""
    try:
        output_schema = json.dumps(
            tool.output_schema,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        output_schema = ""
    return (
        _clean(tool.operation) or tool.name,
        tool.name,
        tool.description,
        schema,
        output_schema,
    )


def _unique_tools(
    tools: Iterable[ResidentToolDescriptor],
) -> tuple[ResidentToolDescriptor, ...]:
    selected: dict[str, ResidentToolDescriptor] = {}
    for tool in sorted(tools, key=_candidate_tool_key):
        operation = _clean(tool.operation) or _clean(tool.name)
        if operation and operation not in selected:
            selected[operation] = tool
    return tuple(selected[operation] for operation in sorted(selected))


def _card_grants(
    card: DelegatedCardSnapshot | None,
) -> tuple[dict[str, DelegatedResourceGrant], set[str]]:
    if card is None:
        return {}, set()
    grants: dict[str, DelegatedResourceGrant] = {}
    duplicates: set[str] = set()
    for grant in sorted(card.resources, key=lambda item: item.resource_id):
        resource_id = _clean(grant.resource_id)
        if not resource_id:
            continue
        if resource_id in grants:
            duplicates.add(resource_id)
            continue
        grants[resource_id] = grant
    return grants, duplicates


def _resource_lifecycle_reason(
    candidate: ResidentResourceCandidate,
) -> AvailabilityReason:
    if candidate.unavailable_reason is not AvailabilityReason.AVAILABLE:
        return candidate.unavailable_reason
    if not candidate.enabled:
        return AvailabilityReason.CONNECTOR_DISABLED
    if not candidate.descriptor_accepted:
        return AvailabilityReason.OPERATION_DESCRIPTOR_CHANGED
    if not candidate.credential_ready:
        return AvailabilityReason.CREDENTIAL_MISSING
    return AvailabilityReason.AVAILABLE


def _delegated_resource_reason(
    *,
    card: DelegatedCardSnapshot | None,
    card_scope_valid: bool,
    grant: DelegatedResourceGrant | None,
    resource_kind: str,
    required_claims: tuple[str, ...],
    card_unavailable_reason: AvailabilityReason | None,
    now_epoch: int,
) -> AvailabilityReason:
    if card is None:
        if (
            card_unavailable_reason is not None
            and card_unavailable_reason is not AvailabilityReason.AVAILABLE
        ):
            return card_unavailable_reason
        return AvailabilityReason.CARD_MISSING
    if not card_scope_valid:
        return AvailabilityReason.SCOPE_MISMATCH
    if not card.active:
        return AvailabilityReason.CARD_REVOKED
    if card.expires_at_epoch is not None and card.expires_at_epoch <= now_epoch:
        return AvailabilityReason.CARD_EXPIRED
    if grant is None:
        return AvailabilityReason.RESOURCE_NOT_GRANTED
    if grant.resource_kind and grant.resource_kind != resource_kind:
        return AvailabilityReason.RESOURCE_KIND_MISMATCH
    held_claims = set(grant.claims)
    if any(claim not in held_claims for claim in required_claims):
        return AvailabilityReason.CLAIM_NOT_GRANTED
    if grant.resource_state is not AvailabilityReason.AVAILABLE:
        return grant.resource_state
    return AvailabilityReason.AVAILABLE


def _tool_policy_reason(
    tool_name: str,
    grant: DelegatedResourceGrant | None,
) -> tuple[AvailabilityReason, InvocationPolicyState | None, Mapping[str, Any]]:
    if grant is None:
        return AvailabilityReason.RESOURCE_NOT_GRANTED, None, {}
    operations = set(grant.operations)
    if "*" not in operations and tool_name not in operations:
        return AvailabilityReason.OPERATION_NOT_GRANTED, None, {}
    policy = grant.invocation_policies.get(tool_name)
    if policy is None:
        policy = grant.invocation_policies.get("*")
    if policy is None:
        policy = InvocationPolicyState(mode="always")
    state = grant.operation_states.get(
        tool_name,
        grant.operation_states.get("*", AvailabilityReason.AVAILABLE),
    )
    if state is not AvailabilityReason.AVAILABLE:
        recovery = grant.operation_recovery.get(tool_name)
        if recovery is None:
            recovery = grant.operation_recovery.get("*")
        return state, policy, dict(recovery or {})
    if policy is not None and policy.mode == "once" and (policy.remaining or 0) <= 0:
        return AvailabilityReason.ONCE_EXHAUSTED, policy, {}
    return AvailabilityReason.AVAILABLE, policy, {}


def _conversation_reason(
    narrowing: ConversationNarrowing,
    resource_id: str,
    tool_name: str,
) -> AvailabilityReason:
    if resource_id not in narrowing.disabled_resources:
        return AvailabilityReason.AVAILABLE
    disabled = narrowing.disabled_resources[resource_id]
    if disabled is None or tool_name in set(disabled):
        return AvailabilityReason.CONVERSATION_DISABLED
    return AvailabilityReason.AVAILABLE


def _resolve_tool(
    *,
    tool: ResidentToolDescriptor,
    candidate: ResidentResourceCandidate,
    family: ResourceFamilyCeiling | None,
    family_tool_index: int,
    resource_reason: AvailabilityReason,
    grant: DelegatedResourceGrant | None,
    narrowing: ConversationNarrowing,
) -> ResolvedResidentTool:
    operation = _clean(tool.operation) or _clean(tool.name)
    reason = resource_reason
    policy: InvocationPolicyState | None = None
    recovery: Mapping[str, Any] = {}
    accepted_descriptor_identity = ""
    current_descriptor_identity = ""
    if reason is AvailabilityReason.AVAILABLE and family is not None:
        if not _matches_any(operation, family.allowed_tool_patterns):
            reason = AvailabilityReason.TOOL_OUTSIDE_CEILING
        elif family_tool_index >= family.max_tools_per_resource:
            reason = AvailabilityReason.TOOL_LIMIT_EXCEEDED
    if (
        reason is AvailabilityReason.AVAILABLE
        and candidate.authority_source is AuthoritySource.DELEGATED_CARD
    ):
        reason, policy, recovery = _tool_policy_reason(operation, grant)
        if grant is not None:
            accepted_descriptor_identity = grant.operation_accepted_digests.get(
                operation,
                grant.operation_accepted_digests.get("*", ""),
            )
            current_descriptor_identity = grant.operation_current_digests.get(
                operation,
                grant.operation_current_digests.get("*", ""),
            )
    if reason is AvailabilityReason.AVAILABLE:
        reason = _conversation_reason(narrowing, candidate.resource_id, operation)
    account_status = candidate.tool_status.get(operation)
    if account_status is None:
        account_status = candidate.tool_status.get(tool.name)
    if (
        reason is AvailabilityReason.AVAILABLE
        and account_status is not None
        and not account_status.ready
    ):
        reason = account_status.reason
        recovery = account_status.recovery
    return ResolvedResidentTool(
        name=tool.name,
        operation=operation,
        description=tool.description,
        input_schema=dict(tool.input_schema),
        output_schema=(
            dict(tool.output_schema) if tool.output_schema is not None else None
        ),
        available=reason is AvailabilityReason.AVAILABLE,
        reason=reason,
        invocation_policy=policy,
        recovery=dict(recovery),
        accepted_descriptor_identity=accepted_descriptor_identity,
        current_descriptor_identity=current_descriptor_identity,
    )


def _resource_reason_from_tools(
    base_reason: AvailabilityReason,
    tools: tuple[ResolvedResidentTool, ...],
) -> AvailabilityReason:
    if base_reason is not AvailabilityReason.AVAILABLE:
        return base_reason
    if any(tool.available for tool in tools):
        return AvailabilityReason.AVAILABLE
    if tools:
        priority = (
            AvailabilityReason.CONVERSATION_DISABLED,
            AvailabilityReason.ONCE_EXHAUSTED,
            AvailabilityReason.CLAIM_NOT_GRANTED,
            AvailabilityReason.OPERATION_NOT_GRANTED,
            AvailabilityReason.OPERATION_DESCRIPTOR_CHANGED,
            AvailabilityReason.OPERATION_REMOVED,
            AvailabilityReason.OPERATION_STATE_UNKNOWN,
            AvailabilityReason.CONNECTED_ACCOUNT_MISSING,
            AvailabilityReason.CONNECTED_ACCOUNT_CLAIM_MISSING,
            AvailabilityReason.CONNECTED_ACCOUNT_UNAVAILABLE,
            AvailabilityReason.TOOL_OUTSIDE_CEILING,
            AvailabilityReason.TOOL_LIMIT_EXCEEDED,
        )
        reasons = {tool.reason for tool in tools}
        return next((reason for reason in priority if reason in reasons), tools[0].reason)
    return AvailabilityReason.OPERATION_NOT_GRANTED


def _resolve_candidate(
    *,
    ceiling: ResidentAgentCeiling,
    candidate: ResidentResourceCandidate,
    family: ResourceFamilyCeiling | None,
    card: DelegatedCardSnapshot | None,
    card_scope_valid: bool,
    grant: DelegatedResourceGrant | None,
    narrowing: ConversationNarrowing,
    card_unavailable_reason: AvailabilityReason | None,
    now_epoch: int,
) -> ResolvedResidentResource:
    reason = _resource_lifecycle_reason(candidate)
    if (
        reason is AvailabilityReason.AVAILABLE
        and candidate.authority_source is AuthoritySource.DELEGATED_CARD
    ):
        reason = _delegated_resource_reason(
            card=card,
            card_scope_valid=card_scope_valid,
            grant=grant,
            resource_kind=candidate.resource_kind,
            required_claims=candidate.required_claims,
            card_unavailable_reason=card_unavailable_reason,
            now_epoch=now_epoch,
        )
    if (
        reason is AvailabilityReason.AVAILABLE
        and family is not None
        and family.family_id in narrowing.disabled_families
    ):
        reason = AvailabilityReason.CONVERSATION_DISABLED

    tools = tuple(
        _resolve_tool(
            tool=tool,
            candidate=candidate,
            family=family,
            family_tool_index=index,
            resource_reason=reason,
            grant=grant,
            narrowing=narrowing,
        )
        for index, tool in enumerate(_unique_tools(candidate.tools))
    )
    final_reason = _resource_reason_from_tools(reason, tools)
    card_applies = candidate.authority_source is AuthoritySource.DELEGATED_CARD and card is not None
    return ResolvedResidentResource(
        resource_id=candidate.resource_id,
        resource_kind=candidate.resource_kind,
        server_id=candidate.server_id,
        alias=candidate.alias,
        display_name=candidate.display_name,
        authority_source=candidate.authority_source,
        identity_scope=candidate.identity_scope,
        available=final_reason is AvailabilityReason.AVAILABLE,
        reason=final_reason,
        tools=tools,
        binding=candidate.binding,
        access_id=card.access_id if card_applies and card_scope_valid else "",
        card_revision=card.revision if card_applies and card_scope_valid else 0,
        descriptor_revision=candidate.descriptor_revision or ceiling.descriptor_revision,
        provider_revision=candidate.provider_revision,
        family_id=family.family_id if family is not None else candidate.family_id,
        accepted_descriptor_revision=grant.accepted_revision if grant is not None else "",
        accepted_descriptor_digest=grant.accepted_digest if grant is not None else "",
        current_descriptor_digest=grant.current_digest if grant is not None else "",
        named_service_operations=(
            dict(grant.named_service_operations) if grant is not None else {}
        ),
        recovery=dict(candidate.recovery),
        provenance={
            "descriptor_ceiling": ceiling.descriptor_revision,
            "declared_exact": candidate.resource_id in ceiling.declared_resource_ids,
            "resource_family": family.family_id if family is not None else "",
            "delegated_card": (
                {
                    "access_id": card.access_id,
                    "revision": card.revision,
                    "scope_valid": card_scope_valid,
                    "identity_scope": card.identity_scope,
                    "catalog_version": card.catalog_version,
                }
                if card_applies
                else None
            ),
            "conversation_narrowed": candidate.resource_id in narrowing.disabled_resources
            or (family is not None and family.family_id in narrowing.disabled_families),
            "resource_state": _resource_lifecycle_reason(candidate).value,
            "card_resource_state": (
                grant.resource_state.value if grant is not None else ""
            ),
        },
    )


def resolve_effective_resident_resources(
    *,
    ceiling: ResidentAgentCeiling,
    grantor_subject: str,
    candidates: Iterable[ResidentResourceCandidate],
    card: DelegatedCardSnapshot | None = None,
    card_unavailable_reasons: Mapping[str, AvailabilityReason] | None = None,
    conversation: ConversationNarrowing | None = None,
    now_epoch: int,
) -> EffectiveResidentInventory:
    """Resolve one deterministic, credential-free resident-agent inventory.

    Loaders must supply fresh provider facts and the profile's one Card for
    each turn. The function performs no I/O and holds no process state.
    """

    narrowing = conversation or ConversationNarrowing()
    unavailable_by_scope = {
        _identity_scope(scope): reason
        for scope, reason in (card_unavailable_reasons or {}).items()
        if reason is not AvailabilityReason.AVAILABLE
    }
    card_scope = _identity_scope(card.identity_scope) if card is not None else ""
    grants, duplicate_resources = _card_grants(card)
    duplicate_card_resources: set[tuple[str, str]] = set()
    valid_grant_keys: set[tuple[str, str]] = set()
    if card is not None:
        duplicate_card_resources.update(
            (card_scope, resource_id) for resource_id in duplicate_resources
        )
        if _card_scope_matches(
            ceiling,
            grantor_subject,
            card_scope,
            card,
        ):
            valid_grant_keys.update(
                (card_scope, resource_id) for resource_id in grants
            )
    candidate_rows = sorted(
        tuple(candidates),
        key=lambda item: (
            (
                0
                if (_identity_scope(item.identity_scope), item.resource_id)
                in valid_grant_keys
                else 1
            ),
            item.resource_id,
            _identity_scope(item.identity_scope),
            item.server_id,
            item.alias,
            item.provider_revision,
        ),
    )
    counts: dict[tuple[str, str], int] = {}
    for candidate in candidate_rows:
        key = (_identity_scope(candidate.identity_scope), candidate.resource_id)
        counts[key] = counts.get(key, 0) + 1

    family_counts: dict[str, int] = {}
    resolved: list[ResolvedResidentResource] = []
    rejected: list[RejectedResidentResource] = []
    current_resource_keys: set[tuple[str, str]] = set()
    rejected_duplicates: set[tuple[str, str]] = set()

    for identity_scope, resource_id in sorted(duplicate_card_resources):
        rejected.append(
            RejectedResidentResource(
                resource_id=resource_id,
                reason=AvailabilityReason.DUPLICATE_RESOURCE,
                detail="card",
                identity_scope=identity_scope,
            )
        )

    for candidate in candidate_rows:
        resource_id = _clean(candidate.resource_id)
        identity_scope = _identity_scope(candidate.identity_scope)
        if resource_id:
            current_resource_keys.add((identity_scope, resource_id))
        resource_key = (identity_scope, resource_id)
        if not resource_id or counts.get(resource_key, 0) > 1:
            if resource_key not in rejected_duplicates:
                rejected.append(
                    RejectedResidentResource(
                        resource_id=resource_id,
                        reason=AvailabilityReason.DUPLICATE_RESOURCE,
                        identity_scope=identity_scope,
                    )
                )
                rejected_duplicates.add(resource_key)
            continue
        if (identity_scope, resource_id) in duplicate_card_resources:
            continue
        if not _candidate_scope_matches(ceiling, candidate, grantor_subject):
            rejected.append(
                RejectedResidentResource(
                    resource_id=resource_id,
                    reason=AvailabilityReason.SCOPE_MISMATCH,
                    identity_scope=identity_scope,
                )
            )
            continue

        exact = resource_id in ceiling.declared_resource_ids
        family = None if exact else _admitting_family(ceiling, candidate)
        if not exact and family is None:
            rejected.append(
                RejectedResidentResource(
                    resource_id=resource_id,
                    reason=AvailabilityReason.RESOURCE_OUTSIDE_CEILING,
                    identity_scope=identity_scope,
                )
            )
            continue
        card_scope_valid = bool(
            card is not None
            and _card_scope_matches(
                ceiling,
                grantor_subject,
                identity_scope,
                card,
            )
        )
        if family is not None:
            counts_toward_limit = (
                candidate.authority_source is not AuthoritySource.DELEGATED_CARD
                or (card_scope_valid and resource_id in grants)
            )
            if counts_toward_limit:
                count = family_counts.get(family.family_id, 0)
                if count >= family.max_resources:
                    rejected.append(
                        RejectedResidentResource(
                            resource_id=resource_id,
                            reason=AvailabilityReason.RESOURCE_LIMIT_EXCEEDED,
                            detail=family.family_id,
                            identity_scope=identity_scope,
                        )
                    )
                    continue
                family_counts[family.family_id] = count + 1

        resolved.append(
            _resolve_candidate(
                ceiling=ceiling,
                candidate=candidate,
                family=family,
                card=card,
                card_scope_valid=card_scope_valid,
                grant=grants.get(resource_id),
                narrowing=narrowing,
                card_unavailable_reason=unavailable_by_scope.get(identity_scope),
                now_epoch=now_epoch,
            )
        )

    if card is not None:
        duplicate_resources = {
            resource_id
            for scope, resource_id in duplicate_card_resources
            if scope == card_scope
        }
        current_ids = {
            resource_id
            for scope, resource_id in current_resource_keys
            if scope == card_scope
        }
        for resource_id in sorted(set(grants) - current_ids - duplicate_resources):
            rejected.append(
                RejectedResidentResource(
                    resource_id=resource_id,
                    reason=AvailabilityReason.RESOURCE_NOT_CURRENT,
                    identity_scope=card_scope,
                )
            )

    return EffectiveResidentInventory(
        tenant=ceiling.tenant,
        project=ceiling.project,
        application=ceiling.application,
        agent_id=ceiling.agent_id,
        resources=tuple(
            sorted(
                resolved,
                key=lambda item: (
                    item.resource_id,
                    item.identity_scope,
                    item.server_id,
                    item.alias,
                ),
            )
        ),
        rejected=tuple(
            sorted(
                rejected,
                key=lambda item: (
                    item.resource_id,
                    item.identity_scope,
                    item.reason.value,
                    item.detail,
                ),
            )
        ),
    )


def conversation_narrowing_from_selection(
    disabled: Mapping[str, Any] | None,
    candidates: Iterable[ResidentResourceCandidate],
) -> ConversationNarrowing:
    """Adapt the existing picker deny map plus the new stable resource keys."""

    rows = tuple(candidates)
    known_resources = {row.resource_id for row in rows}
    by_server: dict[str, set[str]] = {}
    for row in rows:
        for server_id in {row.server_id, row.binding.server_id}:
            if server_id:
                by_server.setdefault(server_id, set()).add(row.resource_id)
    result: dict[str, tuple[str, ...] | None] = {}

    def _apply(resource_id: str, value: Any) -> None:
        if resource_id not in known_resources:
            return
        if value is True:
            result[resource_id] = None
            return
        if isinstance(value, (list, tuple, set)):
            names = tuple(sorted({_clean(item) for item in value if _clean(item)}))
            if names:
                result[resource_id] = names

    raw = disabled or {}
    mcp = raw.get("mcp")
    if isinstance(mcp, Mapping):
        for server_id, value in mcp.items():
            for resource_id in sorted(by_server.get(_clean(server_id), set())):
                _apply(resource_id, value)
    resources = raw.get("resources")
    if isinstance(resources, Mapping):
        for resource_id, value in resources.items():
            _apply(_clean(resource_id), value)
    families = raw.get("delegated_resource_families")
    disabled_families = (
        frozenset(_clean(item) for item in families if _clean(item))
        if isinstance(families, (list, tuple, set))
        else frozenset()
    )
    return ConversationNarrowing(
        disabled_resources=result,
        disabled_families=disabled_families,
    )


__all__ = [
    "conversation_narrowing_from_selection",
    "resolve_effective_resident_resources",
]
