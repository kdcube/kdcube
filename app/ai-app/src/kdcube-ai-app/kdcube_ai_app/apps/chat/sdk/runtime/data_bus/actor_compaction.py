# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""W827 S2c: a Card-bound actor stores only what the worker's live Card re-check needs.

The live size report (dev-main, 00:22Z) found ``actor.identity_authority`` at about 77% of each message: every
message carried the Card's grants and operations for every resource it covers. For an actor bound to a delegated
Card (``identity_authority.delegated_card_binding.access_id``), the worker's ``_resolve_live_delegated_actor``
re-reads the current Card before any handler, reply or bundle code sees the actor. It then overwrites each of
these keys from that Card and rebuilds the binding from the Card itself. The stored copies are never read, so
they are not stored.

What stays is everything the re-check takes as input, and everything it does not rewrite:
- ``delegated_resource``, and the binding's ``access_id``, ``client_id``, ``grantor_user_id`` and
  ``delegate_identity``;
- every top-level actor field (``roles`` is the re-check's fallback, and stays authoritative when the Card
  selects no roles);
- every other ``identity_authority`` key, which consumers receive unchanged.

An actor without a Card binding is returned unchanged: its grants and operations are read as stored.
"""

from __future__ import annotations

from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.application_operations import APPLICATION_OPERATION_POLICY_PROPERTY

# Keys the worker overwrites from the live Card (worker._resolve_live_delegated_actor).
REBUILT_FROM_CARD = frozenset({
    "grants", "scopes", "operations", "resource_grants", "resource_operations", "roles", "permissions",
    "delegated_roles_selected", APPLICATION_OPERATION_POLICY_PROPERTY,
})
# The binding fields the re-check passes to resolve_live_grant_card; the rest of the binding is rebuilt.
BINDING_INPUTS = ("access_id", "client_id", "grantor_user_id", "delegate_identity")


def compact_card_actor(actor: Mapping[str, Any] | None) -> dict[str, Any]:
    """The actor to store: Card-rebuilt copies removed when a Card binding is present, otherwise unchanged."""
    current = dict(actor or {})
    authority = current.get("identity_authority")
    if not isinstance(authority, Mapping):
        return current
    binding = authority.get("delegated_card_binding")
    if not isinstance(binding, Mapping) or not str(binding.get("access_id") or "").strip():
        return current
    compact = {key: value for key, value in authority.items() if key not in REBUILT_FROM_CARD}
    compact["delegated_card_binding"] = {key: binding[key] for key in BINDING_INPUTS if key in binding}
    current["identity_authority"] = compact
    return current


__all__ = ["BINDING_INPUTS", "REBUILT_FROM_CARD", "compact_card_actor"]
