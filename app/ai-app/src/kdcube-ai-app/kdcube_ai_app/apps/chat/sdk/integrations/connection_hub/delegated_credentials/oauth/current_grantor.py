# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Typed delegated-to-platform casts established by managed admission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from connection_hub.authority_projection import authority_has_platform_privilege
from connection_hub.authority_registry import CredentialEnvelope
from kdcube_ai_app.auth.role_hierarchy import PRIVILEGED_ROLES, missing_required_roles
from kdcube_ai_app.apps.chat.sdk.identity_authority import (
    PlatformAuthorityResolutionError,
    resolve_native_platform_identity,
)


_STATE_ATTR = "_delegated_current_grantor_authority"


class GrantorAuthorityFailure(Exception):
    """A known denial or an unavailable platform-authority read."""

    def __init__(self, reason: str, status_code: int = 403):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class CurrentGrantorAuthority:
    """One host-established A-to-platform cast, bound to a live edge revision."""

    tenant: str
    project: str
    subject: str
    source_authority: str
    actor: str
    target_authority: str
    edge_id: str
    edge_revision: int
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    source: str
    updated_at: int
    freshness: str

    def require_roles(self, selected: tuple[str, ...]) -> None:
        if missing_required_roles(self.roles, selected):
            raise GrantorAuthorityFailure("grantor_role_changed")

    def projection_input(self) -> dict[str, Any]:
        return {
            "grantor_roles": list(self.roles),
            "grantor_permissions": list(self.permissions),
            "economics_budget_bypass": authority_has_platform_privilege(
                self.roles, privileged_roles=PRIVILEGED_ROLES,
            ),
        }

    def provenance(self) -> dict[str, Any]:
        return {
            "schema": "kdcube.required_authority_cast.v1",
            "source_authority": self.source_authority,
            "actor_identity": self.actor,
            "target_authority": self.target_authority,
            "target_identity": self.subject,
            "card_id": self.edge_id,
            "card_revision": self.edge_revision,
            "authority_source": self.source,
            "authority_updated_at": self.updated_at,
            "authority_freshness": self.freshness,
            "status": "resolved",
        }


def current_grantor_authority(request: Any) -> CurrentGrantorAuthority | None:
    """Read host-established facts; a credential payload cannot supply them."""
    value = getattr(getattr(request, "state", None), _STATE_ATTR, None)
    return value if isinstance(value, CurrentGrantorAuthority) else None


async def resolve_current_grantor_authority(
    request: Any, *, tenant: str, project: str,
    credential: CredentialEnvelope, grant_record: Mapping[str, Any],
) -> CurrentGrantorAuthority:
    """Cast the verified Card edge to its exact native platform target.

    The current Card already supplied the target mapping. Old serialized edge
    facts may constrain that mapping, but their roles/permissions/bypass are
    never authority. No edge is created or sign-in performed by this read.
    """
    attrs = credential.attrs or {}
    subject = str(attrs.get("grantor_subject") or attrs.get("grantor_user_id") or "").strip()
    actor = str(credential.subject or "").strip()
    source_authority = str(credential.issuer_authority_id or "").strip()
    if not subject or not actor or not source_authority or subject == actor:
        raise GrantorAuthorityFailure("required_authority_not_linked")
    edge_id = str(grant_record.get("registry_access_id") or credential.credential_id or "").strip()
    edge_revision = int(grant_record.get("card_revision") or 0)
    target_authority = "platform"
    legacy = grant_record.get("grantor_authority")
    edges = legacy.get("delegation_edges") if isinstance(legacy, Mapping) else None
    if edges:
        if not isinstance(edges, list) or len(edges) != 1 or not isinstance(edges[0], Mapping):
            raise GrantorAuthorityFailure("required_authority_not_linked")
        edge = edges[0]
        target_authority = str(edge.get("authority_id") or "").strip()
        edge_subject = str(edge.get("user_id") or "").strip()
        if not edge_subject or edge_subject != subject:
            raise GrantorAuthorityFailure("required_authority_not_linked")
    cached = current_grantor_authority(request)
    if cached is not None and target_authority in {
        "platform", "kdcube.platform", "kdcube-front-shell", cached.target_authority,
    } and (
        cached.tenant, cached.project, cached.subject, cached.actor,
        cached.source_authority, cached.edge_id, cached.edge_revision,
    ) == (
        tenant, project, subject, actor, source_authority, edge_id, edge_revision,
    ):
        return cached
    try:
        native = await resolve_native_platform_identity(
            tenant=tenant, project=project, platform_user_id=subject,
            target_authority_id=target_authority,
        )
    except PlatformAuthorityResolutionError as exc:
        raise GrantorAuthorityFailure(exc.reason, exc.status_code) from exc
    resolved = CurrentGrantorAuthority(
        tenant=tenant, project=project, subject=native.user_id,
        source_authority=source_authority, actor=actor,
        target_authority=native.authority_id,
        edge_id=edge_id, edge_revision=edge_revision,
        roles=native.roles, permissions=native.permissions,
        source=native.source, updated_at=native.updated_at,
        freshness=native.freshness,
    )
    setattr(request.state, _STATE_ATTR, resolved)
    return resolved
