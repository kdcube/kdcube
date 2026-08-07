# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Store adapters for the claim-driven consent resolver (`consent_state.py`).

These implement the resolver's reader protocols over the two real consent stores.
They map store-specific shapes to the neutral consent vocabulary; the heavy store
construction (Redis, tenant/project, config) is passed in by the caller (the
capabilities inventory), so the mapping stays unit-testable with faked stores.

- ``DelegatedGrantStoreReader`` — delegated-by-KDCube (own resources). Granted
  claims come from ``AutomationAccessService.list_access`` (the resource-grants of
  the user's approved delegations); the grant VOCABULARY (label/description/
  delegability) comes from the delegated-credentials ``capabilities`` config
  scoped by the user's roles.
- ``ConnectedAccountStoreReader`` — connected accounts (external providers). A
  claim's state comes from the broker's per-claim resolution: a clean resolution
  is ``given``; any ``connect_required`` / ``claim_upgrade_required`` /
  ``account_required`` / ``agent_grant_required`` /
  ``agent_account_binding_required`` / ``reconnect_required`` reason is
  ``pending`` (the user must act); an unconfigured provider/claim is
  ``unavailable``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.connections.consent_state import (
    GIVEN, PENDING, UNAVAILABLE,
)

logger = logging.getLogger(__name__)

# Broker reasons that mean "granted / usable now" vs "user must act".
_PENDING_REASONS = {
    "connect_required",
    "claim_upgrade_required",
    "account_required",
    "agent_grant_required",
    "agent_account_binding_required",
    "reconnect_required",
}


class DelegatedGrantStoreReader:
    """Reader over the delegated-by-KDCube store + grant vocabulary.

    ``service`` is an ``AutomationAccessService`` (or any object exposing
    ``async list_access(user) -> {access: [{resource_grants: {res: (grant,...)}}]}``).
    ``capabilities`` is the delegated-credentials ``capabilities`` list (each
    ``{grant, label, description, delegable_roles}``). ``user_roles`` scopes
    delegability to what THIS user may grant.
    """

    def __init__(self, service: Any, capabilities: Iterable[Mapping[str, Any]], user_roles: Iterable[str]) -> None:
        self._service = service
        self._cap_by_grant: Dict[str, Mapping[str, Any]] = {
            str(c.get("grant")): c for c in (capabilities or []) if c.get("grant")
        }
        self._user_roles = {str(r) for r in (user_roles or [])}

    async def granted_claims(self, user: Mapping[str, Any]) -> set:
        try:
            access = await self._service.list_access(user)
        except Exception:
            logger.warning("consent: list_access failed; treating as no grants", exc_info=True)
            return set()
        granted: set = set()
        for rec in (access.get("access") or []) if isinstance(access, dict) else []:
            for _resource, grants in (rec.get("resource_grants") or {}).items():
                for g in grants or ():
                    if g:
                        granted.add(str(g))
        return granted

    def vocabulary(self, claim: str) -> Dict[str, Any]:
        cap = self._cap_by_grant.get(claim)
        if not cap:
            # Not a declared grant for this deployment -> not delegable, shown as-is.
            return {"delegable": False}
        delegable = bool({str(r) for r in (cap.get("delegable_roles") or [])} & self._user_roles)
        return {
            "label": str(cap.get("label") or ""),
            "description": str(cap.get("description") or ""),
            "delegable": delegable,
        }


class ConnectedAccountStoreReader:
    """Reader over the connected-account broker.

    ``resolve`` is a callable ``async (claim, provider_id, connector_app_id, user)
    -> reason_or_none`` — None (or a granted verdict) means usable, a broker
    ``REASON_*`` means the user must connect/upgrade. Wrapping the broker this way
    keeps this adapter free of the broker's construction details.
    """

    def __init__(self, resolve: Any, labels: Optional[Mapping[str, Mapping[str, Any]]] = None) -> None:
        self._resolve = resolve
        self._labels = dict(labels or {})

    async def claim_state(
        self, *, claim: str, provider_id: str, connector_app_id: str, user: Mapping[str, Any]
    ) -> Dict[str, Any]:
        label = self._labels.get(claim, {})
        try:
            reason = await self._resolve(
                claim=claim, provider_id=provider_id, connector_app_id=connector_app_id, user=user,
            )
        except Exception:
            logger.warning("consent: connected-account resolve failed for %s", claim, exc_info=True)
            return {"state": UNAVAILABLE, **_label_fields(label)}
        reason = str(reason or "").strip()
        if not reason:
            return {"state": GIVEN, **_label_fields(label)}
        if reason in _PENDING_REASONS:
            grant = {"reason": reason, "provider_id": provider_id, "connector_app_id": connector_app_id}
            return {"state": PENDING, "grant_action": grant, **_label_fields(label)}
        return {"state": UNAVAILABLE, **_label_fields(label)}


def _label_fields(label: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if label.get("label"):
        out["label"] = str(label["label"])
    if label.get("description"):
        out["description"] = str(label["description"])
    return out
