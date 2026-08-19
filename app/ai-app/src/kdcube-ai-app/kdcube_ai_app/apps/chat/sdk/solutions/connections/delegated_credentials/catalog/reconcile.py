# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Save-time reconciliation of a submitted selection against the active catalog.

A drifted card must stay editable. Strict validation alone makes it a dead end:
the picker renders rows from the current catalog, so an operator cannot untick
what the catalog no longer offers, and every save then fails on the invisible
value. Pruning first removes exactly those values and validates what survives.

Pruning never adds. An option the catalog gained is not selected here; it is
offered unselected by the drift response and granted only if the operator
submits it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    NamedServiceSelection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.named_service_policy import (
    configured_named_service_operations,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    oauth_delegated_config_from_connections,
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _namespace(value: Any) -> str:
    return _clean(value).lower().rstrip(":")


@dataclass(frozen=True)
class Reconciled:
    """What survives the active catalog, and what did not."""

    resource_grants: dict[str, list[str]]
    named_service_operations: NamedServiceSelection
    pruned_resources: list[str] = field(default_factory=list)
    pruned_claims: list[dict[str, str]] = field(default_factory=list)
    pruned_named_service_operations: list[dict[str, str]] = field(default_factory=list)

    @property
    def anything_pruned(self) -> bool:
        return bool(
            self.pruned_resources
            or self.pruned_claims
            or self.pruned_named_service_operations
        )

    @property
    def empty(self) -> bool:
        """No resource carries a claim: the card has no authority left."""
        return not any(self.resource_grants.values())

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "resources": list(self.pruned_resources),
            "claims": list(self.pruned_claims),
            "named_service_operations": list(self.pruned_named_service_operations),
        }


def reconcile_selection(
    *,
    resource_grants: Mapping[str, Iterable[str]],
    named_service_operations: NamedServiceSelection,
    active: CatalogDocument,
) -> Reconciled:
    """Drop everything the active catalog no longer offers.

    A wildcard selection is kept as a wildcard: it means "everything shown by
    the catalog this save acknowledges", so it re-expands rather than being
    pruned.
    """
    config = oauth_delegated_config_from_connections(active.connections)

    kept_grants: dict[str, list[str]] = {}
    pruned_resources: list[str] = []
    pruned_claims: list[dict[str, str]] = []

    for raw_resource, grants in resource_grants.items():
        resource = _clean(raw_resource)
        cfg = config.card_selector_config(resource)
        if cfg is None:
            pruned_resources.append(resource)
            continue
        ceiling = {_clean(grant) for grant in (cfg.grants or ()) if _clean(grant)}
        kept: list[str] = []
        for grant in grants or ():
            claim = _clean(grant)
            if not claim:
                continue
            # A resource that enumerates no claims carries no ceiling.
            if ceiling and claim not in ceiling:
                pruned_claims.append({"resource": resource, "claim": claim})
                continue
            if claim not in kept:
                kept.append(claim)
        kept_grants[resource] = kept

    selection = named_service_operations
    pruned_operations: list[dict[str, str]] = []
    if selection.is_exact:
        surviving: dict[str, dict[str, list[str]]] = {}
        for raw_resource, namespaces in selection.operations.items():
            resource = _clean(raw_resource)
            if resource not in kept_grants:
                for namespace, operations in namespaces.items():
                    for operation in operations:
                        pruned_operations.append(
                            {
                                "resource": resource,
                                "namespace": _namespace(namespace),
                                "operation": _clean(operation),
                            }
                        )
                continue
            cfg = config.card_selector_config(resource)
            block = getattr(cfg, "named_services", None)
            offered = (
                configured_named_service_operations(block)
                if isinstance(block, Mapping) and block
                else None
            )
            for raw_namespace, operations in namespaces.items():
                namespace = _namespace(raw_namespace)
                for raw_operation in operations:
                    operation = _clean(raw_operation)
                    if not operation:
                        continue
                    if offered is not None and operation not in offered.get(namespace, set()):
                        pruned_operations.append(
                            {
                                "resource": resource,
                                "namespace": namespace,
                                "operation": operation,
                            }
                        )
                        continue
                    surviving.setdefault(resource, {}).setdefault(namespace, []).append(
                        operation
                    )
        # Rebuilt only when something was dropped, so an untouched selection
        # keeps its exact stored shape.
        if pruned_operations:
            selection = NamedServiceSelection.exact(surviving)

    return Reconciled(
        resource_grants=kept_grants,
        named_service_operations=selection,
        pruned_resources=pruned_resources,
        pruned_claims=pruned_claims,
        pruned_named_service_operations=pruned_operations,
    )


__all__ = ["Reconciled", "reconcile_selection"]
