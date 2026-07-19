# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Request-scoped per-agent account binding for connected-account resolution.

The broker (`delegated_to_kdcube.broker.ensure_claim`) decides which connected
account satisfies a provider claim. The calling AGENT may be bound, per
provider, to specific account(s) AND — per account — to specific claims
(`account_scope: {provider_id: {account_id: [claims]}}` on its grant card). That
binding must reach the broker, but the shared resolver
(`integrations.connected_accounts.resolve_connected_account_claim`) runs
downstream of the door through a generic transport with no HTTP request.

Whoever HAS the agent's credential sets the binding into this contextvar at the
boundary — the door bridge from `request.state.delegated_credential`; a native
agent gate from the same view — and the resolver reads back the per-account
claim map for the provider it is resolving. Unset / non-agent turns resolve to
None (no restriction), so the default behavior is unchanged.
"""

from __future__ import annotations

import contextvars
from typing import Any, Mapping

_ACCOUNT_SCOPE: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "kdcube_agent_account_scope", default={}
)


def set_agent_account_scope(scope: Mapping[str, Any] | None) -> None:
    """Bind the current agent's per-account claim scope
    ({provider_id: {account_id: [claims]}}). Accepts the legacy list form
    ({provider_id: [account_ids]}) and migrates it (account -> any claim)."""
    normalized: dict[str, dict[str, tuple[str, ...]]] = {}
    for provider, entry in dict(scope or {}).items():
        pkey = str(provider or "").strip()
        if not pkey:
            continue
        accounts: dict[str, tuple[str, ...]] = {}
        if isinstance(entry, Mapping):
            for account_id, claims in entry.items():
                akey = str(account_id or "").strip()
                if not akey:
                    continue
                cl = tuple(str(c).strip() for c in (claims or ()) if str(c or "").strip())
                accounts[akey] = cl or ("*",)
        else:
            for account_id in (entry or []):
                akey = str(account_id or "").strip()
                if akey:
                    accounts[akey] = ("*",)
        if accounts:
            normalized[pkey] = accounts
    _ACCOUNT_SCOPE.set(normalized)


def clear_agent_account_scope() -> None:
    _ACCOUNT_SCOPE.set({})


def account_claim_scope_for(provider_id: str) -> dict[str, tuple[str, ...]] | None:
    """The current agent's per-account claim binding for ``provider_id`` —
    ``{account_id: (claims...)}`` (account "*" = any account, claim "*" = any
    claim), or None for no restriction (absent provider)."""
    entry = _ACCOUNT_SCOPE.get().get(str(provider_id or "").strip())
    if not entry:
        return None
    return {
        str(account_id).strip(): tuple(claims)
        for account_id, claims in dict(entry).items()
        if str(account_id or "").strip()
    }


__all__ = [
    "set_agent_account_scope",
    "clear_agent_account_scope",
    "account_claim_scope_for",
]
