# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Pre-encoding records whose stored evidence cannot be read without guessing.

A record written before the explicit ``"*" | {} | exact map`` encoding is
migrated lazily: its selection is derived from the tree it materialized. Where
that derivation would silently change authority, the server reports
``migration_confirmation_required`` and leaves the record alone.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.named_service_policy import (
    configured_named_service_operations,
)

MIGRATION_CONFIRMATION_REQUIRED = "migration_confirmation_required"

REASON_UNREADABLE_BOUNDARY = "materialized_boundary_names_no_operation"
REASON_EMPTY_AGAINST_CATALOG = "derived_set_is_empty_against_the_active_catalog"
REASON_RESOURCE_ATTRIBUTION = "boundary_cannot_be_attributed_per_resource"


def pre_migration_ambiguity(
    card: Any,
    *,
    named_service_resources: Sequence[str] = (),
    offered: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any] | None:
    """``None`` when the record's prior set can be derived without guessing.

    ``named_service_resources`` are the card's resources that configure named
    services; ``offered`` is the active catalog's ``namespace -> operations``
    for them.
    """
    selection = getattr(card, "named_service_operations", None)
    if selection is None or not getattr(selection, "is_unknown", False):
        return None
    tree = dict(getattr(card, "named_services", None) or {})
    if not tree:
        # Nothing was materialized: the prior set is empty, unambiguously.
        return None

    derived = configured_named_service_operations(tree)
    if not any(derived.values()):
        return _state(
            card,
            reason=REASON_UNREADABLE_BOUNDARY,
            evidence={"namespaces": sorted(derived)},
        )
    if len(list(named_service_resources)) > 1:
        return _state(
            card,
            reason=REASON_RESOURCE_ATTRIBUTION,
            evidence={"resources": sorted(named_service_resources)},
        )
    if offered is not None:
        current = {
            str(namespace): {str(op) for op in operations}
            for namespace, operations in offered.items()
        }
        surviving = {
            namespace: sorted(operations & current.get(namespace, set()))
            for namespace, operations in derived.items()
        }
        if not any(surviving.values()):
            return _state(
                card,
                reason=REASON_EMPTY_AGAINST_CATALOG,
                evidence={
                    "derived": {ns: sorted(ops) for ns, ops in derived.items() if ops},
                    "offered": {ns: sorted(ops) for ns, ops in current.items() if ops},
                },
            )
    return None


def _state(card: Any, *, reason: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "state": MIGRATION_CONFIRMATION_REQUIRED,
        "reason": reason,
        "access_id": str(getattr(card, "access_id", "") or ""),
        "card_revision": int(getattr(card, "card_revision", 0) or 0),
        "evidence": dict(evidence),
        "recovery": {
            "action": "submit_an_explicit_named_service_selection",
            "retry_same_request": False,
        },
    }


__all__ = [
    "MIGRATION_CONFIRMATION_REQUIRED",
    "REASON_EMPTY_AGAINST_CATALOG",
    "REASON_RESOURCE_ATTRIBUTION",
    "REASON_UNREADABLE_BOUNDARY",
    "pre_migration_ambiguity",
]
