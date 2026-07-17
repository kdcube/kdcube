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

_DEFAULT_CLIENT_ID = "kdcube-agent"


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
) -> Dict[str, Dict[str, Any]]:
    """Build ``{server_id: {url, transport, headers}}`` for the ``kind: mcp``
    connections. Delegated connections get a freshly minted per-user bearer;
    static connections keep their declared headers. A delegated connection with
    no ``user_sub`` (or whose mint fails) is omitted — no unauthenticated call.
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
