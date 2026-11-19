# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Typed, credential-free facts for resident-agent resource projection."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class AuthoritySource(str, Enum):
    """Authority under which a resident agent reaches a resource."""

    APPLICATION = "application"
    DELEGATED_CARD = "delegated_card"
    CONNECTED_ACCOUNT = "connected_account"


class AvailabilityReason(str, Enum):
    """Stable reasons returned by the pure effective-resource resolver."""

    AVAILABLE = "available"
    RESOURCE_OUTSIDE_CEILING = "resource_outside_ceiling"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    RESOURCE_NOT_CURRENT = "resource_not_current"
    RESOURCE_KIND_MISMATCH = "resource_kind_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    DUPLICATE_RESOURCE = "duplicate_resource"
    DUPLICATE_CARD_SCOPE = "duplicate_card_scope"
    CONNECTOR_DISABLED = "connector_disabled"
    CREDENTIAL_MISSING = "credential_missing"
    OPERATION_DESCRIPTOR_CHANGED = "operation_descriptor_changed"
    OPERATION_REMOVED = "operation_removed"
    OPERATION_STATE_UNKNOWN = "operation_state_unknown"
    CARD_MISSING = "card_missing"
    CARD_AUTHORITY_UNAVAILABLE = "card_authority_unavailable"
    CARD_REVOKED = "card_revoked"
    CARD_EXPIRED = "card_expired"
    RESOURCE_NOT_GRANTED = "resource_not_granted"
    CLAIM_NOT_GRANTED = "claim_not_granted"
    OPERATION_NOT_GRANTED = "operation_not_granted"
    ONCE_EXHAUSTED = "once_exhausted"
    CONVERSATION_DISABLED = "conversation_disabled"
    CONNECTED_ACCOUNT_MISSING = "connected_account_missing"
    CONNECTED_ACCOUNT_CLAIM_MISSING = "connected_account_claim_missing"
    CONNECTED_ACCOUNT_UNAVAILABLE = "connected_account_unavailable"
    RESOURCE_PROVIDER_UNAVAILABLE = "resource_provider_unavailable"
    TOOL_OUTSIDE_CEILING = "tool_outside_ceiling"
    TOOL_LIMIT_EXCEEDED = "tool_limit_exceeded"


@dataclass(frozen=True)
class ResidentToolDescriptor:
    """One current operation advertised by a resource provider."""

    name: str
    operation: str = ""
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ResidentToolStatus:
    """Pre-resolved current readiness for one resource operation."""

    ready: bool
    reason: AvailabilityReason = AvailabilityReason.AVAILABLE
    recovery: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ready != (self.reason is AvailabilityReason.AVAILABLE):
            raise ValueError("tool readiness and reason disagree")


ConnectedAccountStatus = ResidentToolStatus


@dataclass(frozen=True)
class ResourceBinding:
    """A credential-free instruction consumed by a runtime adapter."""

    mode: str
    server_id: str
    alias: str
    transport: str
    endpoint: str

    def to_dict(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "server_id": self.server_id,
            "alias": self.alias,
            "transport": self.transport,
            "endpoint": self.endpoint,
        }


@dataclass(frozen=True)
class ResidentResourceCandidate:
    """Current provider/resource facts, loaded outside the pure resolver."""

    resource_id: str
    resource_kind: str
    server_id: str
    alias: str
    display_name: str
    authority_source: AuthoritySource
    tools: tuple[ResidentToolDescriptor, ...]
    binding: ResourceBinding
    tenant: str
    project: str
    application: str
    agent_id: str
    provider_endpoint: str = ""
    grantor_subject: str = ""
    identity_scope: str = "grantor"
    family_id: str = ""
    required_claims: tuple[str, ...] = ()
    descriptor_revision: str = ""
    provider_revision: str = ""
    unavailable_reason: AvailabilityReason = AvailabilityReason.AVAILABLE
    tool_status: Mapping[str, ResidentToolStatus] = field(default_factory=dict)
    enabled: bool = True
    credential_ready: bool = True
    descriptor_accepted: bool = True
    recovery: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceFamilyCeiling:
    """Descriptor-owned maximum for one dynamic user-owned resource family."""

    family_id: str
    resource_kinds: tuple[str, ...]
    authority_sources: tuple[AuthoritySource, ...]
    transports: tuple[str, ...]
    resource_patterns: tuple[str, ...]
    allowed_tool_patterns: tuple[str, ...] = ("*",)
    endpoint_schemes: tuple[str, ...] = ()
    endpoint_hosts: tuple[str, ...] = ()
    max_resources: int = 8
    max_tools_per_resource: int = 64


@dataclass(frozen=True)
class ResidentAgentCeiling:
    """The descriptor authority for one resident app/agent identity."""

    tenant: str
    project: str
    application: str
    agent_id: str
    declared_resource_ids: tuple[str, ...] = ()
    resource_families: tuple[ResourceFamilyCeiling, ...] = ()
    descriptor_revision: str = ""


@dataclass(frozen=True)
class InvocationPolicyState:
    """Current invocation policy for one card resource operation."""

    mode: str = "always"
    remaining: int | None = None
    revision: int = 0


@dataclass(frozen=True)
class DelegatedResourceGrant:
    """One resource row from a live delegated caller card."""

    resource_id: str
    resource_kind: str = ""
    identity_scope: str = "grantor"
    claims: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    invocation_policies: Mapping[str, InvocationPolicyState] = field(default_factory=dict)
    resource_state: AvailabilityReason = AvailabilityReason.AVAILABLE
    operation_states: Mapping[str, AvailabilityReason] = field(default_factory=dict)
    operation_recovery: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    operation_accepted_digests: Mapping[str, str] = field(default_factory=dict)
    operation_current_digests: Mapping[str, str] = field(default_factory=dict)
    accepted_revision: str = ""
    current_revision: str = ""
    accepted_digest: str = ""
    current_digest: str = ""
    named_service_operations: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class DelegatedCardSnapshot:
    """Stable caller-profile read model consumed by Projection.

    The loader owns persistence and token handling. This value contains neither
    a bearer nor a provider credential.
    """

    access_id: str
    client_id: str
    revision: int
    tenant: str
    project: str
    grantor_subject: str
    application: str
    agent_id: str
    identity_scope: str = "grantor"
    source: str = ""
    catalog_version: str = ""
    resources: tuple[DelegatedResourceGrant, ...] = ()
    account_scope: Mapping[str, Mapping[str, tuple[str, ...]]] = field(
        default_factory=dict
    )
    active: bool = True
    expires_at_epoch: int | None = None


@dataclass(frozen=True)
class ConversationNarrowing:
    """Conversation-local subtraction from the effective resident inventory.

    A ``None`` value disables the resource. A tuple disables only those tool
    names. Keys are stable resource ids, not display labels.
    """

    disabled_resources: Mapping[str, tuple[str, ...] | None] = field(default_factory=dict)
    # kdcube#270 F11: delegated resource families this conversation turned
    # off (for example the user's own MCP connectors); every resource the
    # family admits is disabled in this conversation.
    disabled_families: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ResolvedResidentTool:
    name: str
    operation: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    available: bool
    reason: AvailabilityReason
    invocation_policy: InvocationPolicyState | None = None
    recovery: Mapping[str, Any] = field(default_factory=dict)
    accepted_descriptor_identity: str = ""
    current_descriptor_identity: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "operation": self.operation,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "output_schema": (
                dict(self.output_schema) if self.output_schema is not None else None
            ),
            "available": self.available,
            "reason": self.reason.value,
        }
        if self.invocation_policy is not None:
            out["invocation_policy"] = {
                "mode": self.invocation_policy.mode,
                "remaining": self.invocation_policy.remaining,
                "revision": self.invocation_policy.revision,
            }
        if self.recovery:
            out["recovery"] = dict(self.recovery)
        if self.accepted_descriptor_identity:
            out["accepted_descriptor_identity"] = self.accepted_descriptor_identity
        if self.current_descriptor_identity:
            out["current_descriptor_identity"] = self.current_descriptor_identity
        return out


@dataclass(frozen=True)
class ResolvedResidentResource:
    resource_id: str
    resource_kind: str
    server_id: str
    alias: str
    display_name: str
    authority_source: AuthoritySource
    identity_scope: str
    available: bool
    reason: AvailabilityReason
    tools: tuple[ResolvedResidentTool, ...]
    binding: ResourceBinding
    access_id: str = ""
    card_revision: int = 0
    descriptor_revision: str = ""
    provider_revision: str = ""
    family_id: str = ""
    accepted_descriptor_revision: str = ""
    accepted_descriptor_digest: str = ""
    current_descriptor_digest: str = ""
    named_service_operations: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    recovery: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "server_id": self.server_id,
            "alias": self.alias,
            "display_name": self.display_name,
            "authority_source": self.authority_source.value,
            "identity_scope": self.identity_scope,
            "available": self.available,
            "reason": self.reason.value,
            "tools": [tool.to_dict() for tool in self.tools],
            "binding": self.binding.to_dict(),
            "access_id": self.access_id,
            "card_revision": self.card_revision,
            "descriptor_revision": self.descriptor_revision,
            "provider_revision": self.provider_revision,
            "family_id": self.family_id,
            "accepted_descriptor_revision": self.accepted_descriptor_revision,
            "accepted_descriptor_digest": self.accepted_descriptor_digest,
            "current_descriptor_digest": self.current_descriptor_digest,
            "named_service_operations": {
                namespace: list(operations)
                for namespace, operations in sorted(
                    self.named_service_operations.items()
                )
            },
            "recovery": dict(self.recovery),
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class RejectedResidentResource:
    resource_id: str
    reason: AvailabilityReason
    detail: str = ""
    identity_scope: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "resource_id": self.resource_id,
            "reason": self.reason.value,
            "detail": self.detail,
            "identity_scope": self.identity_scope,
        }


@dataclass(frozen=True)
class EffectiveResidentInventory:
    tenant: str
    project: str
    application: str
    agent_id: str
    resources: tuple[ResolvedResidentResource, ...]
    rejected: tuple[RejectedResidentResource, ...]

    @property
    def effective_resources(self) -> tuple[ResolvedResidentResource, ...]:
        return tuple(resource for resource in self.resources if resource.available)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "kdcube.resident_agent_effective_resources.v1",
            "tenant": self.tenant,
            "project": self.project,
            "application": self.application,
            "agent_id": self.agent_id,
            "resources": [resource.to_dict() for resource in self.resources],
            "rejected": [entry.to_dict() for entry in self.rejected],
        }
