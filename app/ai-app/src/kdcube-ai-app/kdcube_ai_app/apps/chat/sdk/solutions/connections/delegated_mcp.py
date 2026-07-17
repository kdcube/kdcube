# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Resolve a per-user MCP server map for delegated KDCube ``@mcp`` surfaces.

Framework-neutral. Given a bundle's declared ``kind: mcp`` tool connections and
the current turn's user, this produces the standard MCP server map —
``{server_id: {url, transport, headers}}`` — that any MCP client consumes
(``langchain-mcp-adapters``'s ``MultiServerMCPClient``, the platform's own
``runtime/mcp`` adapter, a raw client). It is the ONE place the delegated
per-user bearer is minted and injected, so every hosted agent (any framework)
reaches a delegated KDCube ``@mcp`` surface the same way.

Two kinds of connection:

  * **static** — the connection carries fixed ``headers`` (e.g. a shared
    ``Authorization: Bearer <token>``). Used as-is.
  * **delegated** — ``delegated: true`` + ``scopes: [<grant>, ...]``. A
    least-privilege per-user bearer is minted for THIS turn's user via
    ``mint_delegated_client_access_token`` (the same seam platform ``@mcp``
    surfaces authenticate; see the delegated-credentials OAuth machinery) and
    injected as ``Authorization``. The KDCube ``@mcp`` endpoint validates it and
    serves the user's own resources under the granted scopes. A delegated
    connection with NO resolvable user is SKIPPED (logged) — never a blind,
    unauthenticated call.

This module does not import any agent framework. The LangChain binding lives in
``sdk/frameworks/langchain/mcp.py`` and consumes the map this returns.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

# A minter: (sub, scopes, *, client_id, ttl_seconds) -> {"access_token": str, ...}.
# Defaults to the delegated-credentials OAuth mint; injectable for tests.
Minter = Callable[..., Awaitable[Mapping[str, Any]]]

# A bearer provider: (conn, user_sub) -> the CONSENTED bearer for a delegated
# connection, or None when consent is pending. When injected it REPLACES the mint
# for delegated connections — the per-turn token is the one the user's grant
# already bound (so the @mcp guard passes), not a fresh unbound mint. Returning
# None means the user has not granted THIS agent the connection's claims; the
# connection is dropped and the caller shapes a consent demand.
BearerProvider = Callable[[Mapping[str, Any], str], Awaitable[Optional[str]]]

_DEFAULT_CLIENT_ID = "kdcube-agent"


def delegated_client_id_for_agent(application: str, agent_id: str) -> str:
    """The delegated-client identity for one hosted agent — the agent IS a
    "Delegated By KDCube" client entity (like Claude Code), distinguished by the
    APPLICATION it is defined in and its AGENT_ID. Consent grants + the minted
    token are keyed by this, so consent is PER-AGENT and the entity is listable /
    revocable in Connection Hub. Stable + deterministic (no timestamps)."""
    app = str(application or "").strip()
    agent = str(agent_id or "").strip()
    if app and agent:
        return f"kdcube-agent:{app}:{agent}"
    return _DEFAULT_CLIENT_ID


def connection_resource(conn: Mapping[str, Any]) -> str:
    """The delegated resource identifier a connection points at — an explicit
    ``resource`` if declared, else the ``url`` (the ``@mcp`` surface URL, which is
    what the grant's ``resource_grants`` is keyed by and what the guard matches the
    request against)."""
    return str((conn or {}).get("resource") or (conn or {}).get("url") or "").strip()


def agent_bearer_provider(access_service: Any, *, client_id: str) -> "BearerProvider":
    """A ``bearer_provider`` over an ``AutomationAccessService``: the consented
    per-agent grant's already-bound token for the connection's resource, or
    ``None`` when the user has not granted THIS agent (consent pending). The one
    reusable glue that turns "reuse the consented grant's token" into the resolver
    hook; any hosted agent injects it via ``resolve_mcp_server_map``."""
    async def _provider(conn: Mapping[str, Any], user_sub: str) -> Optional[str]:
        resource = connection_resource(conn)
        if not resource:
            return None
        result = await access_service.agent_access_token(
            grantor_subject=user_sub, client_id=client_id, resources=[resource],
        )
        return str((result or {}).get("access_token") or "").strip() or None if result else None
    return _provider


def is_mcp_connection(conn: Mapping[str, Any]) -> bool:
    return str((conn or {}).get("kind") or "").strip().lower() == "mcp"


def is_delegated_connection(conn: Mapping[str, Any]) -> bool:
    return bool((conn or {}).get("delegated"))


def _server_id(conn: Mapping[str, Any]) -> str:
    return str(conn.get("server_id") or conn.get("server") or conn.get("name") or "").strip()


def _scopes(conn: Mapping[str, Any]) -> List[str]:
    raw = conn.get("scopes") or conn.get("grants") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(s).strip() for s in raw if str(s).strip()]


async def _default_minter(sub: str, scopes: List[str], *, client_id: str, ttl_seconds: Optional[int]) -> Mapping[str, Any]:
    # Lazy import: keep this module import-light and free of the OAuth stack until
    # a delegated connection is actually resolved.
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.grants import (
        mint_delegated_client_access_token,
    )

    kwargs: Dict[str, Any] = {"client_id": client_id}
    if ttl_seconds:
        kwargs["ttl_seconds"] = int(ttl_seconds)
    return await mint_delegated_client_access_token(sub, scopes, **kwargs)


async def resolve_mcp_server_map(
    connections: List[Dict[str, Any]],
    *,
    user_sub: Optional[str] = None,
    minter: Optional[Minter] = None,
    client_id: str = _DEFAULT_CLIENT_ID,
    ttl_seconds: Optional[int] = None,
    consent_gate: Optional[Callable[[List[str]], Awaitable[bool]]] = None,
    bearer_provider: Optional[BearerProvider] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build ``{server_id: {url, transport, headers}}`` for the ``kind: mcp``
    connections. Delegated connections get a per-user bearer; static connections
    keep their declared headers. A delegated connection with no ``user_sub`` (or
    no resolvable bearer) is omitted — no unauthenticated call.

    ``bearer_provider``: optional ``async (conn, user_sub) -> Optional[str]``.
    When provided it is the delegated bearer source (REPLACING the mint): it
    returns the token the user's per-agent grant already bound — so the ``@mcp``
    guard, which validates against the bound grant record, passes. ``None`` means
    consent is pending (the user has not granted THIS agent the claims); the
    connection is DROPPED so the caller can shape a consent demand. This is the
    "reuse the consented grant's token" path.

    ``consent_gate``: optional ``async (scopes) -> bool``, used only on the mint
    fallback (no ``bearer_provider``). When provided, a delegated connection is
    minted ONLY if the gate returns True. A False gate DROPS the connection
    (consent pending). The gate decides its own failure posture; this function
    honors its verdict.
    """
    mint = minter or _default_minter
    servers: Dict[str, Dict[str, Any]] = {}
    for conn in connections or []:
        if not is_mcp_connection(conn):
            continue
        server_id = _server_id(conn)
        url = conn.get("url")
        if not server_id or not url:
            continue
        entry: Dict[str, Any] = {
            "url": url,
            "transport": conn.get("transport") or "streamable_http",
        }
        headers: Dict[str, Any] = dict(conn.get("headers") or {})

        if is_delegated_connection(conn):
            scopes = _scopes(conn)
            if not user_sub:
                logger.warning(
                    "delegated_mcp: connection %s is delegated but no user is bound this "
                    "turn; skipping (no unauthenticated call).", server_id,
                )
                continue
            if bearer_provider is not None:
                # The consented-grant path: use the token the user's per-agent
                # grant already bound (guard-valid), or drop when consent pends.
                try:
                    token = str(await bearer_provider(conn, user_sub) or "").strip()
                except Exception:
                    logger.warning(
                        "delegated_mcp: bearer provider errored for %s; skipping.", server_id, exc_info=True,
                    )
                    continue
                if not token:
                    logger.info(
                        "delegated_mcp: connection %s not bound — consent pending for scopes %s "
                        "(user grants it to this agent in Connection Hub).", server_id, scopes,
                    )
                    continue
                headers["Authorization"] = f"Bearer {token}"
                if headers:
                    entry["headers"] = headers
                servers[server_id] = entry
                continue
            if consent_gate is not None:
                try:
                    consented = bool(await consent_gate(scopes))
                except Exception:
                    logger.warning(
                        "delegated_mcp: consent gate errored for %s; skipping.", server_id, exc_info=True,
                    )
                    continue
                if not consented:
                    logger.info(
                        "delegated_mcp: connection %s not bound — consent pending for scopes %s "
                        "(user grants it in Connection Hub).", server_id, scopes,
                    )
                    continue
            try:
                minted = await mint(user_sub, scopes, client_id=client_id, ttl_seconds=ttl_seconds)
                token = str((minted or {}).get("access_token") or "").strip()
            except Exception:  # noqa: BLE001 - never fail a build over token minting
                logger.warning("delegated_mcp: minting the delegated bearer for %s failed; skipping.", server_id, exc_info=True)
                continue
            if not token:
                logger.warning("delegated_mcp: minter returned no access_token for %s; skipping.", server_id)
                continue
            headers["Authorization"] = f"Bearer {token}"

        if headers:
            entry["headers"] = headers
        servers[server_id] = entry
    return servers
