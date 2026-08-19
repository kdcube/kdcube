# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Current-catalog authorization for governed delegated calls.

Effective authority is the stored card selection intersected with the active
catalog. The card side is checked by the surface that owns it; this module owns
the catalog side and produces the structured denial when a stored capability is
no longer offered.

Inputs are explicit: a capability path is never inferred from a tool name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.named_service_policy import (
    configured_named_service_operations,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    oauth_delegated_config_from_connections,
)

CAPABILITY_RESOURCE = "resource"
CAPABILITY_RESOURCE_CLAIM = "resource_claim"
CAPABILITY_OUTER_OPERATION = "outer_operation"
CAPABILITY_NAMED_SERVICE_NAMESPACE = "named_service_namespace"
CAPABILITY_NAMED_SERVICE_OPERATION = "named_service_operation"

CAPABILITY_KINDS = (
    CAPABILITY_RESOURCE,
    CAPABILITY_RESOURCE_CLAIM,
    CAPABILITY_OUTER_OPERATION,
    CAPABILITY_NAMED_SERVICE_NAMESPACE,
    CAPABILITY_NAMED_SERVICE_OPERATION,
)

DENIAL_CODE = "delegated_capability_no_longer_available"
DENIAL_REASON = "current_catalog_excludes_requested_capability"
DENIAL_WHERE = "delegated_catalog.authorization"
NOT_GRANTED_CODE = "delegated_capability_not_granted"
NOT_GRANTED_REASON = "card_does_not_cover_requested_capability"
NOT_GRANTED_WHERE = "delegated_card.authorization"
UNAVAILABLE_CODE = "temporarily_unavailable"
UNAVAILABLE_WHERE = "delegated_catalog.resolution"

# The two sides of the same contract. A removal is addressed to whoever
# configures the deployment; a card that never covered the capability is
# addressed to its grantor, who can widen it. Saying which one it is, in words,
# is the point: a reasoning consumer that reads "not available" for both
# advises revoking a working card to recover something no consent can restore.
_MESSAGES = {
    CAPABILITY_RESOURCE: "This deployment no longer offers the requested resource. Granting more access cannot restore it.",
    CAPABILITY_RESOURCE_CLAIM: "This deployment no longer offers the requested resource claim. Granting more access cannot restore it.",
    CAPABILITY_OUTER_OPERATION: "This deployment no longer offers the requested operation. Granting more access cannot restore it.",
    CAPABILITY_NAMED_SERVICE_NAMESPACE: "This deployment no longer offers the requested named-service namespace. Granting more access cannot restore it.",
    CAPABILITY_NAMED_SERVICE_OPERATION: "This deployment no longer offers the requested named-service operation. Granting more access cannot restore it.",
}

_NOT_GRANTED_MESSAGES = {
    CAPABILITY_RESOURCE: "The delegated access card does not cover the requested resource. Its grantor can add it in Connection Hub.",
    CAPABILITY_RESOURCE_CLAIM: "The delegated access card does not carry the requested resource claim. Its grantor can add it in Connection Hub.",
    CAPABILITY_OUTER_OPERATION: "The delegated access card does not cover the requested operation. Its grantor can add it in Connection Hub.",
    CAPABILITY_NAMED_SERVICE_NAMESPACE: "The delegated access card does not cover the requested named-service namespace. Its grantor can add it in Connection Hub.",
    CAPABILITY_NAMED_SERVICE_OPERATION: "The delegated access card does not cover the requested named-service operation. Its grantor can add it in Connection Hub, under this card's named-service operations.",
}

# Fields each kind must carry, so a denial is actionable on its own.
_REQUIRED_PATH = {
    CAPABILITY_RESOURCE: ("resource",),
    CAPABILITY_RESOURCE_CLAIM: ("resource", "claim"),
    CAPABILITY_OUTER_OPERATION: ("resource", "surface", "outer_operation"),
    CAPABILITY_NAMED_SERVICE_NAMESPACE: ("resource", "surface", "namespace"),
    CAPABILITY_NAMED_SERVICE_OPERATION: ("resource", "surface", "namespace", "operation"),
}

# Carried in addition when the surface knows them.
_OPTIONAL_PATH = {
    CAPABILITY_NAMED_SERVICE_NAMESPACE: ("outer_operation",),
    CAPABILITY_NAMED_SERVICE_OPERATION: ("outer_operation",),
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class CardProvenance:
    """Non-secret card identity carried into a denial."""

    access_id: str = ""
    card_revision: int = 0
    catalog_version: str = ""


@dataclass(frozen=True)
class CapabilityRequest:
    """One capability path, as the calling surface parsed it.

    ``resource`` is the configured selector that matched; ``request_resource``
    is the concrete URL the caller targeted.
    """

    kind: str
    resource: str = ""
    request_resource: str = ""
    surface: str = ""
    outer_operation: str = ""
    claim: str = ""
    namespace: str = ""
    operation: str = ""

    def path(self) -> dict[str, Any]:
        fields = {
            "kind": self.kind,
            "resource": _clean(self.resource),
            "request_resource": _clean(self.request_resource),
            "surface": _clean(self.surface),
            "outer_operation": _clean(self.outer_operation),
            "claim": _clean(self.claim),
            "namespace": _clean(self.namespace),
            "operation": _clean(self.operation),
        }
        carried = {
            "kind",
            "request_resource",
            *_REQUIRED_PATH.get(self.kind, ()),
            *_OPTIONAL_PATH.get(self.kind, ()),
        }
        return {key: value for key, value in fields.items() if value and key in carried}


class ActiveCatalogCapabilities:
    """What the active catalog offers, read through the delegated-config reader."""

    def __init__(self, document: CatalogDocument) -> None:
        self._document = document
        self._config = oauth_delegated_config_from_connections(document.connections)

    @property
    def version(self) -> str:
        return self._document.version

    def resource_config(self, request: CapabilityRequest) -> Any:
        # The card's own selector selects the row, falling back to the request
        # URL only when the caller supplies no selector. Judging a card by the
        # URL lets the all-resource admin row answer for a door the deployment
        # withdrew, which would leave the bearer holding authority nobody can
        # take away through the catalog.
        return self._config.card_selector_config(
            _clean(request.resource),
            request_resource=_clean(request.request_resource),
        )

    def resource_claims(self, request: CapabilityRequest) -> frozenset[str]:
        """The claim ceiling the active catalog still allows for this resource."""
        resource_cfg = self.resource_config(request)
        if resource_cfg is None:
            return frozenset()
        return frozenset(_clean(grant) for grant in (resource_cfg.grants or ()) if _clean(grant))

    def permits(self, request: CapabilityRequest) -> bool:
        """Whether the active catalog still offers this capability.

        A dimension the resource does not enumerate carries no ceiling and is
        not a denial. A dimension it enumerates is authoritative even when what
        it enumerates is empty — that is a removal, not an absent section.
        """
        resource_cfg = self.resource_config(request)
        if resource_cfg is None:
            return False
        if request.kind == CAPABILITY_RESOURCE:
            return True
        if request.kind == CAPABILITY_RESOURCE_CLAIM:
            configured = {_clean(grant) for grant in (resource_cfg.grants or ())}
            if not configured:
                return True
            return _clean(request.claim) in configured
        if request.kind == CAPABILITY_OUTER_OPERATION:
            configured = {
                _clean(getattr(tool, "name", "")) for tool in (resource_cfg.tools or ())
            }
            if not configured:
                return True
            return _clean(request.outer_operation) in configured
        named_services = resource_cfg.named_services
        if not isinstance(named_services, Mapping) or not named_services:
            return True
        namespaces = configured_named_service_operations(named_services)
        namespace = _clean(request.namespace).lower().rstrip(":")
        if request.kind == CAPABILITY_NAMED_SERVICE_NAMESPACE:
            return bool(namespace) and namespace in namespaces
        if request.kind == CAPABILITY_NAMED_SERVICE_OPERATION:
            operation = _clean(request.operation)
            return bool(operation) and operation in namespaces.get(namespace, set())
        return False


def capability_denial(
    *,
    catalog: ActiveCatalogCapabilities,
    provenance: CardProvenance,
    request: CapabilityRequest,
) -> dict[str, Any]:
    """The structured 403 body for a capability the catalog no longer offers."""
    return {
        "ok": False,
        "error": {
            "code": DENIAL_CODE,
            "message": _MESSAGES.get(request.kind, _MESSAGES[CAPABILITY_RESOURCE]),
            "where": DENIAL_WHERE,
            "retryable": False,
        },
        "ret": {
            "reason": DENIAL_REASON,
            "access_id": _clean(provenance.access_id),
            "card_revision": int(provenance.card_revision or 0),
            "requested_capability": request.path(),
            "card_catalog_version": _clean(provenance.catalog_version),
            "active_catalog_version": catalog.version,
            "recovery": {
                "action": "refresh_discovery_or_review_delegated_access",
                "retry_same_request": False,
                # The design's retry meaning, machine-readable: "Do not blindly
                # retry and do not request more user consent."
                "request_user_consent": False,
            },
        },
    }


def card_boundary_denial(
    *,
    provenance: CardProvenance,
    request: CapabilityRequest,
    delegable: bool = True,
) -> dict[str, Any]:
    """The structured 403 body for a capability the CARD does not cover.

    The mirror of ``capability_denial``: the active catalog still offers this,
    so a remedy exists and the grantor owns it. ``delegable=False`` says the
    deployment does not allow this capability to be asked for here, which
    changes the answer from "ask for it" to "nobody here can grant it".
    """
    return {
        "ok": False,
        "error": {
            "code": NOT_GRANTED_CODE,
            "message": _NOT_GRANTED_MESSAGES.get(
                request.kind, _NOT_GRANTED_MESSAGES[CAPABILITY_RESOURCE]
            ),
            "where": NOT_GRANTED_WHERE,
            "retryable": False,
        },
        "ret": {
            "reason": NOT_GRANTED_REASON,
            "access_id": _clean(provenance.access_id),
            "card_revision": int(provenance.card_revision or 0),
            "requested_capability": request.path(),
            "card_catalog_version": _clean(provenance.catalog_version),
            "recovery": {
                "action": (
                    "grant_capability_in_delegated_access"
                    if delegable
                    else "capability_not_delegable_here"
                ),
                "retry_same_request": False,
                "request_user_consent": bool(delegable),
            },
        },
    }


def catalog_unavailable_denial(reason: str = "") -> dict[str, Any]:
    """The structured 503 body for a catalog that cannot be established."""
    return {
        "ok": False,
        "error": {
            "code": UNAVAILABLE_CODE,
            "message": "The current delegated-service catalog is unavailable.",
            "where": UNAVAILABLE_WHERE,
            "retryable": True,
        },
        "ret": {"reason": _clean(reason) or "catalog_unavailable"},
    }


def authorize_current_capability(
    *,
    catalog: ActiveCatalogCapabilities,
    provenance: CardProvenance,
    request: CapabilityRequest,
) -> dict[str, Any] | None:
    """``None`` when the active catalog still offers the capability."""
    if catalog.permits(request):
        return None
    return capability_denial(catalog=catalog, provenance=provenance, request=request)


def denial_is_capability_removed(payload: Mapping[str, Any] | None) -> bool:
    error = (payload or {}).get("error")
    error = error if isinstance(error, Mapping) else {}
    return str(error.get("code") or "") == DENIAL_CODE


def denial_is_capability_not_granted(payload: Mapping[str, Any] | None) -> bool:
    error = (payload or {}).get("error")
    error = error if isinstance(error, Mapping) else {}
    return str(error.get("code") or "") == NOT_GRANTED_CODE


__all__ = [
    "CAPABILITY_KINDS",
    "CAPABILITY_NAMED_SERVICE_NAMESPACE",
    "CAPABILITY_NAMED_SERVICE_OPERATION",
    "CAPABILITY_OUTER_OPERATION",
    "CAPABILITY_RESOURCE",
    "CAPABILITY_RESOURCE_CLAIM",
    "DENIAL_CODE",
    "DENIAL_REASON",
    "NOT_GRANTED_CODE",
    "NOT_GRANTED_REASON",
    "ActiveCatalogCapabilities",
    "CapabilityRequest",
    "CardProvenance",
    "authorize_current_capability",
    "capability_denial",
    "card_boundary_denial",
    "catalog_unavailable_denial",
    "denial_is_capability_not_granted",
    "denial_is_capability_removed",
]
