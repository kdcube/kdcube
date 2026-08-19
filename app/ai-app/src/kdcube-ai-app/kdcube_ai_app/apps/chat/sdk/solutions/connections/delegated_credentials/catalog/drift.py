# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Backend-computed drift between a card's baseline and the active catalog.

The comparison is transient: it explains a card, and never rewrites one. A
surface renders what this module returns instead of comparing catalogs itself.

Removals are computed against the active catalog alone, so they survive a
missing baseline. Additions need the baseline, because "new" means "absent when
this card was last saved".
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.named_service_policy import (
    configured_named_service_operations,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    oauth_delegated_config_from_connections,
)

DRIFT_CURRENT = "current"
DRIFT_CHANGED = "changed"
DRIFT_NO_RELEVANT_CHANGE = "no_relevant_change"
DRIFT_BASELINE_MISSING = "baseline_missing"
DRIFT_UNAVAILABLE = "unavailable"

EFFECT_DENIED = "denied_immediately"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _namespace(value: Any) -> str:
    return _clean(value).lower().rstrip(":")


class _CatalogView:
    """What one catalog generation offers, per card resource."""

    def __init__(self, document: CatalogDocument) -> None:
        self.version = document.version
        self._config = oauth_delegated_config_from_connections(document.connections)

    def resource(self, resource: str) -> Any:
        # A card selector, so the all-resource row answers only a card that
        # holds it: otherwise a withdrawn door reads as still offered and its
        # claims are reported "already ineffective" while they still work.
        return self._config.card_selector_config(resource)

    def claims(self, resource: str) -> set[str] | None:
        """``None`` when the resource declares no claim ceiling."""
        cfg = self.resource(resource)
        if cfg is None:
            return set()
        configured = {_clean(grant) for grant in (cfg.grants or ()) if _clean(grant)}
        return configured or None

    def outer_operations(self, resource: str) -> set[str] | None:
        cfg = self.resource(resource)
        if cfg is None:
            return set()
        configured = {
            _clean(getattr(tool, "name", "")) for tool in (cfg.tools or ())
        } - {""}
        return configured or None

    def named_service_operations(self, resource: str) -> dict[str, set[str]] | None:
        """``None`` when the resource declares no named-service block."""
        cfg = self.resource(resource)
        if cfg is None:
            return {}
        block = getattr(cfg, "named_services", None)
        if not isinstance(block, Mapping) or not block:
            return None
        return configured_named_service_operations(block)


def selected_named_service_operations(card: Any) -> dict[str, dict[str, set[str]]]:
    """``resource -> namespace -> operations`` the card selected.

    An exact selection says it directly. A wildcard or a pre-encoding card is
    read from the materialized boundary, which is that selection expanded under
    the catalog version the card was saved against.

    Derived, never authority. Drift computes against it, and the public card
    projection carries it so a surface can render a wildcard card's actual
    coverage instead of an empty picker.
    """
    selection = card.named_service_operations
    out: dict[str, dict[str, set[str]]] = {}
    if selection.is_none:
        return out
    if selection.is_exact:
        for resource, namespaces in selection.operations.items():
            per_resource = out.setdefault(_clean(resource), {})
            for namespace, operations in namespaces.items():
                per_resource[_namespace(namespace)] = {
                    _clean(op) for op in operations if _clean(op)
                }
        return out
    materialized = configured_named_service_operations(card.named_services)
    if not materialized:
        return out
    # The boundary is one tree for the whole card; attribute it to the resources
    # the card actually holds.
    for resource in card.resource_grants:
        out[_clean(resource)] = {
            namespace: set(operations) for namespace, operations in materialized.items()
        }
    return out


def _removed(
    *, card: Any, active: _CatalogView
) -> dict[str, list[dict[str, Any]]]:
    resources: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    outer_operations: list[dict[str, Any]] = []
    named_service_operations: list[dict[str, Any]] = []

    selected_inner = selected_named_service_operations(card)
    selected_outer = {_clean(name) for name in (card.operations or ()) if _clean(name)}

    for raw_resource, grants in card.resource_grants.items():
        resource = _clean(raw_resource)
        if active.resource(resource) is None:
            resources.append(
                {"resource": resource, "was_selected": True, "effect": EFFECT_DENIED}
            )
            continue

        ceiling = active.claims(resource)
        if ceiling is not None:
            for claim in sorted({_clean(g) for g in (grants or ()) if _clean(g)} - ceiling):
                claims.append(
                    {
                        "resource": resource,
                        "claim": claim,
                        "was_selected": True,
                        "effect": EFFECT_DENIED,
                    }
                )

        offered_outer = active.outer_operations(resource)
        if offered_outer is not None:
            for operation in sorted(selected_outer - offered_outer):
                outer_operations.append(
                    {
                        "resource": resource,
                        "operation": operation,
                        "was_selected": True,
                        "effect": EFFECT_DENIED,
                    }
                )

        offered_inner = active.named_service_operations(resource)
        if offered_inner is None:
            continue
        for namespace, operations in sorted(selected_inner.get(resource, {}).items()):
            for operation in sorted(operations - offered_inner.get(namespace, set())):
                named_service_operations.append(
                    {
                        "resource": resource,
                        "namespace": namespace,
                        "operation": operation,
                        "was_selected": True,
                        "effect": EFFECT_DENIED,
                    }
                )

    return {
        "resources": resources,
        "claims": claims,
        "outer_operations": outer_operations,
        "named_service_operations": named_service_operations,
    }


def _added(
    *, card: Any, active: _CatalogView, baseline: _CatalogView
) -> dict[str, list[dict[str, Any]]]:
    """What the active catalog offers for this card's resources and the baseline
    did not. Never selected: an addition is an option, not a grant."""
    claims: list[dict[str, Any]] = []
    outer_operations: list[dict[str, Any]] = []
    named_service_operations: list[dict[str, Any]] = []

    for raw_resource in card.resource_grants:
        resource = _clean(raw_resource)
        if active.resource(resource) is None:
            continue

        for claim in sorted((active.claims(resource) or set()) - (baseline.claims(resource) or set())):
            claims.append({"resource": resource, "claim": claim, "selected": False})

        for operation in sorted(
            (active.outer_operations(resource) or set())
            - (baseline.outer_operations(resource) or set())
        ):
            outer_operations.append(
                {"resource": resource, "operation": operation, "selected": False}
            )

        offered = active.named_service_operations(resource) or {}
        known = baseline.named_service_operations(resource) or {}
        for namespace in sorted(offered):
            for operation in sorted(offered[namespace] - known.get(namespace, set())):
                named_service_operations.append(
                    {
                        "resource": resource,
                        "namespace": namespace,
                        "operation": operation,
                        "selected": False,
                    }
                )

    return {
        "claims": claims,
        "outer_operations": outer_operations,
        "named_service_operations": named_service_operations,
    }


def _any(block: Mapping[str, Iterable[Any]]) -> bool:
    return any(bool(rows) for rows in block.values())


def card_drift(
    *,
    card: Any,
    active: CatalogDocument,
    baseline: CatalogDocument | None,
    baseline_confirmed_absent: bool = False,
) -> dict[str, Any]:
    """The drift block for one card.

    ``baseline`` is the card's saved version document; pass ``None`` with
    ``baseline_confirmed_absent`` when durable absence was confirmed.
    """
    saved_version = _clean(getattr(card, "catalog_version", ""))
    if saved_version and saved_version == active.version:
        return {
            "status": DRIFT_CURRENT,
            "saved_version": saved_version,
            "current_version": active.version,
        }

    active_view = _CatalogView(active)
    removed = _removed(card=card, active=active_view)

    if baseline is None:
        # Removals still hold against the verified current catalog; additions
        # since the last save cannot be established without the baseline.
        return {
            "status": DRIFT_BASELINE_MISSING,
            "saved_version": saved_version,
            "current_version": active.version,
            "removed": removed,
            "added": {"claims": [], "outer_operations": [], "named_service_operations": []},
            "baseline_confirmed_absent": bool(baseline_confirmed_absent),
        }

    added = _added(card=card, active=active_view, baseline=_CatalogView(baseline))
    changed = _any(removed) or _any(added)
    return {
        "status": DRIFT_CHANGED if changed else DRIFT_NO_RELEVANT_CHANGE,
        "saved_version": saved_version,
        "current_version": active.version,
        "removed": removed,
        "added": added,
    }


def drift_unavailable(reason: str = "") -> dict[str, Any]:
    """Editing authority is disabled: the comparison could not be made."""
    return {
        "status": DRIFT_UNAVAILABLE,
        "reason": _clean(reason) or "catalog_unavailable",
    }


__all__ = [
    "DRIFT_BASELINE_MISSING",
    "DRIFT_CHANGED",
    "DRIFT_CURRENT",
    "DRIFT_NO_RELEVANT_CHANGE",
    "DRIFT_UNAVAILABLE",
    "EFFECT_DENIED",
    "card_drift",
    "drift_unavailable",
    "selected_named_service_operations",
]
