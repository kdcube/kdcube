# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

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


def _legacy_target(actor: Mapping[str, Any], authority: Mapping[str, Any]) -> dict[str, Any] | None:
    """Recover an admitted identity mapping, never cached entitlement proof.

    Pointerless legacy credentials have no live Card to restore. Their host
    projection still identifies the exact delegate and native target. Do not
    turn the provenance's card_id (which may be a credential id) into a Card
    binding, or silently treat a malformed delegated actor as a native one.
    """
    cast = authority.get("required_authority_cast")
    delegated = (
        str(authority.get("schema") or "").startswith("connection_hub.delegated_")
        or authority.get("credential_kind") in ("delegated_client", "delegated_client_access")
        or authority.get("authority_id") == "delegated_client"
        or authority.get("issuer_authority_id") == "delegated_client"
        or authority.get("delegate_identity")
        or authority.get("grantor_user_id")
        or authority.get("delegated_card_binding") is not None
        or "required_authority_cast" in authority
    )
    if not delegated:
        return None
    if (
        not isinstance(cast, Mapping)
        or cast.get("schema") != "kdcube.required_authority_cast.v1"
        or cast.get("status") != "resolved"
    ):
        raise PlatformAuthorityResolutionError("required_authority_not_linked")
    target = str(cast.get("target_identity") or "").strip()
    delegate = str(cast.get("actor_identity") or "").strip()
    source = str(cast.get("source_authority") or "").strip()
    target_authority = str(cast.get("target_authority") or "").strip()
    if (
        not target or not delegate or delegate == target or not source or not target_authority
        or any(str(value or "").strip() != target for value in (
            actor.get("user_id"), authority.get("grantor_user_id"), authority.get("platform_user_id"),
        ))
        or str(authority.get("delegate_identity") or "").strip() != delegate
        or any(str(authority[key] or "").strip() != target for key in (
            "economics_user_id", "user_id", "subject_user_id",
        ) if key in authority)
        or any(str(authority[key] or "").strip() != delegate for key in (
            "actor_identity", "actor_user_id",
        ) if key in authority)
        or any(str(authority[key] or "").strip() != source for key in (
            "authority_id", "issuer_authority_id",
        ) if key in authority)
    ):
        raise PlatformAuthorityResolutionError("required_authority_not_linked")
    for key in ("provenance", "delegation"):
        nested = authority.get(key)
        if nested is None:
            continue
        if not isinstance(nested, Mapping) or any(
            str(nested[field] or "").strip() != expected
            for field, expected in (
                ("actor_identity", delegate), ("delegate_identity", delegate),
                ("grantor_user_id", target), ("issuer_authority_id", source),
                ("authority_id", source),
            ) if field in nested
        ):
            raise PlatformAuthorityResolutionError("required_authority_not_linked")
    return dict(cast)


async def cast_delegated_platform_actor(
    actor: Mapping[str, Any] | None, *, tenant: str, project: str,
) -> dict[str, Any]:
    """Re-resolve native roles after the live Card's operation is selected.

    The caller has restored the exact current Card and operation policy. Carried
    actor roles and serialized provenance do not prove this new invocation's
    native authority. Pointerless legacy projections supply only an exact
    identity mapping; ordinary native actors are unchanged.
    """
    out = dict(actor or {})
    raw = out.get("identity_authority")
    authority = dict(raw) if isinstance(raw, Mapping) else {}
    binding = authority.get("delegated_card_binding")
    legacy = None
    if not isinstance(binding, Mapping) or not str(binding.get("access_id") or "").strip():
        legacy = _legacy_target(out, authority)
        if legacy is None:
            return out
        subject = legacy["target_identity"]
    else:
        subject = str(binding.get("grantor_user_id") or "")
    target_options = {"target_authority_id": legacy["target_authority"]} if legacy else {}
    native = await resolve_native_platform_identity(
        tenant=tenant, project=project,
        platform_user_id=subject, **target_options,
    )
    native_privileged = bool(set(native.roles).intersection(PRIVILEGED_ROLES))
    legacy_roles = tuple(role for role in native.roles if role not in PRIVILEGED_ROLES)
    raw_grants = authority.get("grants") or ()
    if not isinstance(raw_grants, (list, tuple)) or any(not isinstance(value, str) for value in raw_grants):
        raise PlatformAuthorityResolutionError("required_authority_not_linked")
    grants = tuple(value.strip() for value in raw_grants if value.strip())
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
        out.pop(key, None)
    out.pop("budget_bypass", None)
    provenance = dict(legacy or {})
    if legacy is None:
        provenance.update({
            "source_authority": "delegated_client",
            "actor_identity": str(binding.get("delegate_identity") or ""),
            "card_id": str(binding["access_id"]),
            "card_revision": int(binding.get("card_revision") or 0),
        })
    provenance.update({
        "schema": "kdcube.required_authority_cast.v1",
        "target_authority": native.authority_id,
        "target_identity": native.user_id,
        "authority_source": native.source,
        "authority_updated_at": native.updated_at,
        "authority_freshness": native.freshness,
        "status": "resolved",
    })
    authority.update({
        "roles": list(projection.roles), "permissions": list(permissions),
        "budget_bypass": authority_has_platform_privilege(
            projection.roles, privileged_roles=tuple(PRIVILEGED_ROLES),
        ),
        "delegated_roles_selected": projection.selected_on_card,
        "grantor_authority_source": native.source,
        "required_authority_cast": provenance,
    })
    out.update({
        "user_id": native.user_id,
        "roles": list(projection.roles), "permissions": list(permissions),
        "user_type": user_type_for_roles(projection.roles),
        "identity_authority": authority,
    })
    return out
