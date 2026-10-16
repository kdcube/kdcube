"""Native platform-authority cast at a queued delegated invocation boundary."""
from __future__ import annotations

from typing import Any, Mapping

from connection_hub.authority_projection import authority_has_platform_privilege
from kdcube_ai_app.apps.chat.sdk.identity_authority import (
    PlatformAuthorityResolutionError, resolve_native_platform_identity,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_roles import (
    delegated_role_projection, user_type_for_roles,
)
from kdcube_ai_app.auth.role_hierarchy import PRIVILEGED_ROLES, missing_required_roles


async def cast_delegated_platform_actor(
    actor: Mapping[str, Any] | None, *, tenant: str, project: str,
) -> dict[str, Any]:
    """Re-resolve native roles after the live Card's operation is selected.

    The caller has restored the exact current Card and operation policy. Carried
    actor roles and serialized provenance do not prove this new invocation's
    native authority. Ordinary native actors have no delegated binding here.
    """
    out = dict(actor or {})
    raw = out.get("identity_authority")
    authority = dict(raw) if isinstance(raw, Mapping) else {}
    binding = authority.get("delegated_card_binding")
    if not isinstance(binding, Mapping) or not binding.get("access_id"):
        return out
    native = await resolve_native_platform_identity(
        tenant=tenant, project=project,
        platform_user_id=str(binding.get("grantor_user_id") or ""),
    )
    native_privileged = bool(set(native.roles).intersection(PRIVILEGED_ROLES))
    legacy_roles = tuple(role for role in native.roles if role not in PRIVILEGED_ROLES)
    grants = tuple(authority.get("grants") or ())
    projection = delegated_role_projection(grants, fallback_roles=legacy_roles)
    if missing_required_roles(native.roles, projection.roles):
        raise PlatformAuthorityResolutionError("grantor_role_changed")
    permissions = (
        grants if projection.selected_on_card
        else (() if native_privileged else native.permissions) or grants
    )
    # Remove older aliases too: generic economics readers give these priority
    # over roles/permissions and must not revive carried snapshot privilege.
    for key in (
        "platform_roles", "grantor_roles", "platform_permissions",
        "grantor_permissions", "economics_budget_bypass",
    ):
        authority.pop(key, None)
    authority.update({
        "roles": list(projection.roles), "permissions": list(permissions),
        "budget_bypass": authority_has_platform_privilege(
            projection.roles, privileged_roles=tuple(PRIVILEGED_ROLES),
        ),
        "delegated_roles_selected": projection.selected_on_card,
        "grantor_authority_source": native.source,
        "required_authority_cast": {
            "schema": "kdcube.required_authority_cast.v1",
            "source_authority": "delegated_client",
            "actor_identity": str(binding.get("delegate_identity") or ""),
            "target_authority": native.authority_id,
            "target_identity": native.user_id,
            "card_id": str(binding["access_id"]),
            "card_revision": int(binding.get("card_revision") or 0),
            "authority_source": native.source,
            "authority_updated_at": native.updated_at,
            "authority_freshness": native.freshness,
            "status": "resolved",
        },
    })
    out.update({
        "user_id": native.user_id,
        "roles": list(projection.roles), "permissions": list(permissions),
        "user_type": user_type_for_roles(projection.roles),
        "identity_authority": authority,
    })
    return out
