# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── mcp_bridge.py ── delegated MCP for a foreign-runtime agent, runtime-neutral ──
#
# Turn an agent's declared `kind: mcp` tool connections into the standard MCP
# server map — ``{server_id: {url, transport, headers}}`` — for THIS turn's
# user, AS this agent. The agent is a "Delegated By KDCube" entity keyed by
# ``application`` + ``agent_id``, so consent is per-agent: a `delegated: true`
# connection is served with the token the user's per-agent grant already bound
# (read through the Connection Hub named service); when the user has not
# consented, the connection is DROPPED (recorded in ``drop_sink`` as
# ``consent_pending``) instead of any blind call. Those drops are the cue to
# raise the house consent CARD (``announce_connect_required``): the same demand
# + chat event a KDCube-MCP 403 raises, so any wrapped runtime gets the chat's
# real grant affordance instead of prose.
#
# This module STOPS at the server map. The binding step — server map → runtime
# tools/config — is the caller's, per runtime: e.g.
# ``kdcube_ai_app.apps.chat.sdk.frameworks.langchain.mcp.load_mcp_tools_from_server_map``
# binds it as LangChain tools for a LangGraph agent, and a Claude Code adapter
# injects it into the generated ``.mcp.json``. No langchain/langgraph is
# imported here.
#
# ACCOUNTING (the honest rule — "marked = counted"): binding a tool via MCP does
# not by itself make it accounted; a tool whose KDCube-side implementation runs
# a marked model call IS metered, a plain lookup is not.

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_mcp import (
    DROP_CONSENT_PENDING,
    connection_resource,
    delegated_client_id_for_agent,
    resolve_mcp_server_map,
)

LOGGER = logging.getLogger("kdcube.foreign_runtime.mcp_bridge")

__all__ = [
    "current_turn_user_sub",
    "agent_grant_bearer_provider",
    "connection_server_id",
    "connection_allowed_tools",
    "narrow_mcp_connections",
    "resolve_turn_mcp",
    "claude_code_mcp_servers",
    "claude_code_tool_rules",
    "connect_required_outcome",
    "announce_connect_required",
    "load_mcp_server_instructions_safe",
]

# ``resolve_turn_mcp`` yields the neutral map (``{server_id: {url, transport,
# headers}}``); Claude Code's ``.mcp.json`` speaks ``{type, url, headers}``, and
# names the transport ``type`` with its own vocabulary.
_TRANSPORT_TO_MCP_JSON = {"streamable_http": "http", "http": "http", "sse": "sse"}


def current_turn_user_sub(entrypoint: Any) -> str:
    """This turn's user subject, resolved from the BOUND turn context at build
    time — the accounting context (bound around execute_core), else the comm.
    Used to mint the per-user delegated MCP bearer without threading identity
    through the build signatures. Empty when no user is bound (a delegated MCP
    connection then resolves to nothing — no unauthenticated call)."""
    try:
        from kdcube_ai_app.infra.accounting import _get_context
        sub = str((_get_context().to_dict() or {}).get("user_id") or "").strip()
        if sub:
            return sub
    except Exception:
        pass
    # `entrypoint.comm` is a property that BUILDS the communicator and raises
    # when no turn task is bound (e.g. a build outside a turn) — guard the side
    # effect.
    try:
        return str(getattr(entrypoint.comm, "user_id", "") or "").strip()
    except Exception:
        return ""


def current_turn_conversation(entrypoint: Any) -> tuple[str, str]:
    """This turn's ``(conversation_id, turn_id)`` from the bound communicator; empty outside a turn."""
    try:
        conversation = getattr(entrypoint.comm, "conversation", None) or {}
    except Exception:
        return "", ""
    if not isinstance(conversation, Mapping):
        return "", ""
    return (str(conversation.get("conversation_id") or "").strip(), str(conversation.get("turn_id") or "").strip())


TURN_TOKEN_HEADER = "x-connection-hub-turn"
DROP_TURN_TOKEN_UNAVAILABLE = "turn_token_unavailable"


async def _per_call_turn_token(entrypoint: Any, *, agent_id: str) -> tuple[str, str]:
    """W40: a turn token for a runtime whose headers are fixed for the turn.

    Mode ``per_call``: valid up to the turn cap and never renewed; the guard
    checks on every call that this turn is still alive. Returns ``(token,
    refusal)``; a turn outside any conversation needs none.
    """
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control import (
        request_agent_turn_token, turn_is_automation_execution,
    )

    conversation_id, turn_id = current_turn_conversation(entrypoint)
    if not conversation_id or not turn_id or turn_is_automation_execution():
        # An automation run has no conversation choice: Agent Card defaults.
        return "", ""

    answer = await request_agent_turn_token(entrypoint, agent_id=agent_id, conversation_id=conversation_id,
                                            turn_id=turn_id, mode="per_call")
    if answer.get("ok"):
        return str(answer["turn_token"]), ""
    return "", str(answer.get("error") or DROP_TURN_TOKEN_UNAVAILABLE)


def agent_grant_bearer_provider(entrypoint: Any, agent_client_id: str):
    """A bearer provider that reads THIS agent's consented per-agent grant token
    from the Connection Hub named service (`agent_grant.get_token`) for the turn's
    user. Returns None on any absence/failure (consent pending / caller unbound /
    hub unreachable) so the delegated connection is dropped and surfaces as a
    consent demand — never a blind call, never a failed build."""
    async def _provider(conn: Mapping[str, Any], user_sub: str) -> Optional[str]:
        del user_sub  # the hub resolves the grantor from the bound turn context
        resource = connection_resource(conn)
        if not resource:
            return None
        try:
            from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_named_service
            from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connection_edges import (
                connection_hub_bundle_id_from_entrypoint,
            )
            from connection_hub.contract import (
                NAMESPACE, AGENT_GRANT_GET_TOKEN,
            )
            from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
                NamedServiceResponse,
            )
            result = await call_bundle_named_service(
                bundle_id=connection_hub_bundle_id_from_entrypoint(entrypoint),
                request={
                    "namespace": NAMESPACE,
                    "operation": AGENT_GRANT_GET_TOKEN,
                    "payload": {"client_id": agent_client_id, "resource": resource},
                },
            )
            value = getattr(result, "value", None)
            response = NamedServiceResponse.coerce(value) if value is not None else None
        except Exception:
            LOGGER.info(
                "[foreign-runtime] agent-grant token lookup failed for %s; "
                "treating as consent-pending.", resource, exc_info=True,
            )
            return None
        if response is None or not response.ok or not response.attrs.get("has_token"):
            return None
        token = str((response.object or {}).get("access_token") or "").strip()
        return token or None
    return _provider


def connection_server_id(conn: Mapping[str, Any]) -> str:
    """The SERVER ID a connection declares — the key the capabilities picker
    denies by (its catalog rows carry ``server_id``), which is not always the
    connection's model-facing ``alias``."""
    return str(
        (conn or {}).get("server_id") or (conn or {}).get("server") or (conn or {}).get("name") or ""
    ).strip()


def connection_allowed_tools(conn: Mapping[str, Any]) -> List[str]:
    """The tool names a ``kind: mcp`` connection declares (``allowed``, or the
    legacy ``tools``), or ``[]`` for a wildcard/undeclared connection.

    A concrete list is the admin's enumeration of that server's surface: it gives
    the picker per-tool rows with no handshake, and it lets a permission-grammar
    runtime name the SURVIVORS of a user's partial denial without listing the
    server live. ``["*"]`` reads as "whatever the server publishes" and yields
    ``[]`` here — the caller then has no enumerable survivor set."""
    raw = (conn or {}).get("allowed")
    if raw is None:
        raw = (conn or {}).get("tools")
    if isinstance(raw, str):
        raw = [raw]
    names = [str(item).strip() for item in (raw or []) if str(item or "").strip()]
    return [] if "*" in names else names


def narrow_mcp_connections(
    connections: List[Dict[str, Any]],
    disabled_mcp: Optional[Mapping[str, Any]] = None,
    *,
    dropped_sink: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """The declared connection list minus the servers the user turned OFF whole.

    ``disabled_mcp`` is the picker's ``disabled.mcp`` category
    (``{server_id: true | [tool names]}``): a ``true`` drops the connection here,
    a list is a PARTIAL denial and leaves the connection in place (its surviving
    tools are named later, per runtime — ``claude_code_tool_rules``).

    Call this BEFORE ``resolve_turn_mcp``. That ordering is the whole point: a
    server the user turned off is never dialled, no grant token is ever read for
    it, and it can never raise a consent card the user did not ask for. Entries of
    other kinds pass through untouched (the resolver skips them anyway).

    ``dropped_sink``: pass a list to learn WHICH server ids were removed."""
    disabled = disabled_mcp or {}
    kept: List[Dict[str, Any]] = []
    for conn in connections or []:
        if not isinstance(conn, Mapping):
            continue
        if str(conn.get("kind") or "").strip().lower() != "mcp":
            kept.append(dict(conn))
            continue
        server_id = connection_server_id(conn)
        if server_id and disabled.get(server_id) is True:
            if dropped_sink is not None:
                dropped_sink.append(server_id)
            continue
        kept.append(dict(conn))
    return kept


async def resolve_turn_mcp(
    entrypoint: Any,
    connections: List[Dict[str, Any]],
    *,
    agent_id: str,
    application: str,
    user_sub: Optional[str] = None,
    drop_sink: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """The MCP server map for THIS turn's user, AS this agent.

    Wraps ``resolve_mcp_server_map`` with the agent's delegated-client identity
    (``delegated_client_id_for_agent(application, agent_id)`` — consent grants
    and the bound token are keyed by it, so consent is per-agent and the entity
    is listable/revocable in Connection Hub) and the agent-grant bearer
    provider. Non-``mcp`` connections are skipped by the resolver; the caller
    applies any user tool opt-outs by narrowing ``connections`` first.

    ``user_sub``: the turn's user subject; when None it is resolved from the
    bound turn context (``current_turn_user_sub``). ``drop_sink``: pass a dict
    to learn WHY a delegated connection was omitted (the ``DROP_*`` reasons —
    ``consent_pending`` is the caller's cue to shape a connect-required
    outcome). The drop happens BEFORE any server contact. Never raises for a
    consent-shaped absence; the map simply omits the connection."""
    client_id = delegated_client_id_for_agent(application, agent_id)
    sub = user_sub if user_sub is not None else current_turn_user_sub(entrypoint)
    delegated = {str(conn.get("server_id") or conn.get("name") or "").strip()
                 for conn in connections or [] if isinstance(conn, Mapping) and conn.get("delegated")}
    turn_token, refusal = ("", "")
    if delegated:
        turn_token, refusal = await _per_call_turn_token(entrypoint, agent_id=agent_id)
    if refusal:
        # W40: without the turn token the guard would apply the Card's defaults,
        # not this conversation's choice; the delegated servers stay out of this turn.
        connections = [conn for conn in connections or []
                       if not (isinstance(conn, Mapping) and conn.get("delegated"))]
        if drop_sink is not None:
            for server_id in delegated:
                if server_id:
                    drop_sink[server_id] = DROP_TURN_TOKEN_UNAVAILABLE
    server_map = await resolve_mcp_server_map(
        connections or [],
        user_sub=sub,
        client_id=client_id,
        bearer_provider=agent_grant_bearer_provider(entrypoint, client_id),
        drop_sink=drop_sink,
    )
    if turn_token:
        for server_id, entry in server_map.items():
            if server_id in delegated and isinstance(entry, dict):
                entry["headers"] = {**dict(entry.get("headers") or {}), TURN_TOKEN_HEADER: turn_token}
    return server_map


def claude_code_mcp_servers(
    server_map: Mapping[str, Mapping[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """The Claude Code BINDING STEP: this module's neutral server map in the
    shape ``ClaudeCodeWorkspaceConfig.mcp_servers`` takes.

    The seam stops at the neutral map on purpose (see the module header); each
    runtime binds it its own way. This is that step for Claude Code — the SDK
    writes the resulting entries to ``.mcp.json`` itself, so an app hands the
    result to ``ClaudeCodeWorkspaceConfig`` and writes no file. The per-turn
    delegated bearer rides ``headers`` exactly as resolved; entries missing a
    server id or a URL are dropped rather than written half-formed."""
    out: Dict[str, Dict[str, Any]] = {}
    for server_id, entry in dict(server_map or {}).items():
        if not isinstance(entry, Mapping):
            continue
        url = str(entry.get("url") or "").strip()
        if not str(server_id).strip() or not url:
            continue
        transport = str(entry.get("transport") or "streamable_http").strip().lower()
        server: Dict[str, Any] = {
            "type": _TRANSPORT_TO_MCP_JSON.get(transport, "http"),
            "url": url,
        }
        headers = entry.get("headers")
        if isinstance(headers, Mapping) and headers:
            server["headers"] = {str(k): str(v) for k, v in headers.items()}
        out[str(server_id)] = server
    return out


def claude_code_tool_rules(
    connections: List[Dict[str, Any]],
    disabled_mcp: Optional[Mapping[str, Any]] = None,
    *,
    server_ids: Sequence[str],
    base_allowed: Sequence[str] = (),
    tool_overrides: Optional[Mapping[str, Sequence[str]]] = None,
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """The Claude Code permission rules for THIS turn's servers — ``(allow, deny)``
    for ``ClaudeCodeWorkspaceConfig.allowed_tools`` / ``.denied_tools``.

    Claude Code's grammar has exactly two MCP shapes and NO wildcard between them:
    ``mcp__<server>`` covers everything that server publishes, ``mcp__<server>__<tool>``
    covers one tool. So a PARTIAL denial has to be expressed one of two ways, and
    which one is cheaper depends on what the admin declared:

    * the connection enumerates its tools (``allowed: [...]``) — the survivors are
      known from config alone, so they are named one by one and no server is
      listed at runtime. This is the cheap path, and the reason a concrete
      ``allowed`` list is the better declaration.
    * the connection is a wildcard (``allowed: ["*"]`` or nothing) — the survivor
      set is unknowable without a live ``tools/list`` handshake, so the whole
      server stays allowed and the denied tools are named in the DENY list, which
      wins over the allow rule.

    Denied tools are named in the deny list in both cases: a rule the model cannot
    talk its way around costs one line and removes the question.

    ``server_ids`` are the servers that actually RESOLVED this turn (post-narrowing,
    post-consent) — a declared server that dropped out gets no rule at all.
    ``tool_overrides`` supplies a surviving tool list for a server whose narrowing
    is computed elsewhere (the named-services door, where the user's pick lands on
    namespaces rather than on the door's own tool names).

    ``base_allowed`` (the runtime's own file/search tools) is carried through
    unchanged, first."""
    disabled = disabled_mcp or {}
    overrides = {str(k): list(v or []) for k, v in dict(tool_overrides or {}).items()}
    by_server: Dict[str, Mapping[str, Any]] = {}
    for conn in connections or []:
        if not isinstance(conn, Mapping):
            continue
        sid = connection_server_id(conn)
        if sid and sid not in by_server:
            by_server[sid] = conn

    allow: List[str] = [str(item).strip() for item in base_allowed if str(item or "").strip()]
    deny: List[str] = []
    for raw_id in server_ids or ():
        server_id = str(raw_id or "").strip()
        if not server_id:
            continue
        entry = disabled.get(server_id)
        denied_names = (
            [str(item).strip() for item in entry if str(item or "").strip()]
            if isinstance(entry, (list, tuple))
            else []
        )
        if server_id in overrides:
            declared = list(overrides[server_id])
        else:
            declared = connection_allowed_tools(by_server.get(server_id) or {})
        if server_id in overrides and not declared:
            # Everything this server offered was narrowed away, yet the server
            # itself resolved (its other declarations may still matter). Deny it
            # whole rather than leaving it silently reachable.
            deny.append(f"mcp__{server_id}")
            continue
        if not denied_names and server_id not in overrides:
            allow.append(f"mcp__{server_id}")
            continue
        if not declared:
            # Wildcard surface: the survivors cannot be named without listing the
            # server live, so allow the server and deny what the user removed.
            allow.append(f"mcp__{server_id}")
            deny.extend(f"mcp__{server_id}__{name}" for name in denied_names)
            continue
        survivors = [name for name in declared if name not in set(denied_names)]
        allow.extend(f"mcp__{server_id}__{name}" for name in survivors)
        deny.extend(
            f"mcp__{server_id}__{name}" for name in declared if name in set(denied_names)
        )

    def _unique(values: List[str]) -> Tuple[str, ...]:
        seen: Dict[str, None] = {}
        for value in values:
            if value and value not in seen:
                seen[value] = None
        return tuple(seen.keys())

    return _unique(allow), _unique(deny)


def connect_required_outcome(
    drop_sink: Optional[Mapping[str, str]],
    *,
    connection_hub_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Shape the structured connect-required payload from ``consent_pending``
    drops, or None when nothing is pending.

    ``{"status": "connect_required", "connections": [server ids],
    "connection_hub_url": ...}`` — the server ids whose delegated connection was
    dropped because the user has not granted THIS agent; other drop reasons
    (``no_user``, ``provider_error``, ``mint_error``) are operational, not
    consentable, and are excluded. Pure shaping, no side effects: use it to
    learn WHICH connections are pending (e.g. to name them in the turn's
    answer). ``connection_hub_url`` stays a caller-supplied passthrough for
    non-chat callers that relay a link themselves; a chat lane leaves it unset
    and calls ``announce_connect_required`` — the card carries the action and
    the platform builds its deep link."""
    pending = [
        server_id
        for server_id, reason in (drop_sink or {}).items()
        if reason == DROP_CONSENT_PENDING
    ]
    if not pending:
        return None
    return {
        "status": "connect_required",
        "connections": pending,
        "connection_hub_url": connection_hub_url,
    }


def _connection_claims(conn: Mapping[str, Any]) -> List[str]:
    raw = (conn or {}).get("scopes") or (conn or {}).get("claims") or (conn or {}).get("grants") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item or "").strip()]


def _turn_scope(entrypoint: Any, tenant: str, project: str) -> tuple[str, str]:
    """(tenant, project) for the hub deep link — explicit args first, then the
    bound turn identity, then the entrypoint's settings. Empty is fine: the
    link builder simply yields no URL and the card still acts through its
    structured grant fields."""
    tenant_id = str(tenant or "").strip()
    project_id = str(project or "").strip()
    if not tenant_id or not project_id:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_user_identity

            identity = get_current_user_identity() or {}
            tenant_id = tenant_id or str(identity.get("tenant_id") or "").strip()
            project_id = project_id or str(identity.get("project_id") or "").strip()
        except Exception:
            pass
    if not tenant_id or not project_id:
        try:
            settings = getattr(entrypoint, "settings", None)
            tenant_id = tenant_id or str(getattr(settings, "TENANT", "") or "").strip()
            project_id = project_id or str(getattr(settings, "PROJECT", "") or "").strip()
        except Exception:
            pass
    return tenant_id, project_id


async def announce_connect_required(
    entrypoint: Any,
    connections: List[Dict[str, Any]],
    drop_sink: Optional[Mapping[str, str]],
    *,
    agent_id: str,
    application: str,
    tenant: str = "",
    project: str = "",
) -> List[Dict[str, Any]]:
    """Raise the house CONSENT CARD for every ``consent_pending`` drop.

    The chat's consent affordance is an EVENT, not prose: each pending
    connection becomes the same ``MCPConsentRequired`` demand a KDCube-MCP 403
    produces, announced through ``announce_agent_consent`` — recorded once per
    conversation, emitted as the ``delegated_to_kdcube.consent`` chat step, and
    rendered by the chat as a card whose action opens the hub's per-agent grant
    flow (Delegated by KDCube) pre-targeted at THIS agent's client id, the
    connection's resource, and its claims.

    The deep link is the PLATFORM's (``connection_hub_grant_url`` over the
    deployment's public base URL); a caller never hand-writes a hub path. When
    the deployment publishes no public base URL the link is simply absent — the
    card still acts, because the scene-contract path builds the hub view from
    the demand's structured fields.

    Returns one descriptor per announced demand
    (``{server_id, tool_name, resource, claims}``); ``[]`` when nothing is
    pending. Best effort — an announce failure never raises into the turn."""
    pending_ids = {
        server_id
        for server_id, reason in (drop_sink or {}).items()
        if reason == DROP_CONSENT_PENDING
    }
    if not pending_ids:
        return []
    try:
        from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.consent_denial import (
            connection_hub_grant_url,
        )
        from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.mcp_consent import (
            announce_agent_consent,
            mcp_consent_from_denial,
        )
    except Exception:
        LOGGER.warning(
            "[foreign-runtime] consent machinery unavailable; connect-required raised no card "
            "(connections=%s)", sorted(pending_ids), exc_info=True,
        )
        return []
    client_id = delegated_client_id_for_agent(application, agent_id)
    tenant_id, project_id = _turn_scope(entrypoint, tenant, project)
    hub_bundle_id = ""
    try:
        from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connection_edges import (
            connection_hub_bundle_id_from_entrypoint,
        )

        hub_bundle_id = str(connection_hub_bundle_id_from_entrypoint(entrypoint) or "").strip()
    except Exception:
        hub_bundle_id = ""
    announced: List[Dict[str, Any]] = []
    for conn in connections or []:
        server_id = connection_server_id(conn)
        if server_id not in pending_ids:
            continue
        resource = connection_resource(conn)
        claims = _connection_claims(conn)
        tool_name = str(conn.get("alias") or conn.get("name") or server_id).strip() or server_id
        hub_url = ""
        try:
            hub_url = connection_hub_grant_url(
                tenant=tenant_id,
                project=project_id,
                client_id=client_id,
                resource=resource,
                claims=claims,
                **({"hub_bundle_id": hub_bundle_id} if hub_bundle_id else {}),
            )
        except Exception:
            LOGGER.info(
                "[foreign-runtime] hub deep link unavailable for %s; the card acts through its "
                "structured grant fields", server_id, exc_info=True,
            )
        consent = mcp_consent_from_denial(
            {"status": 403, "reason": "authority_mismatch"},
            resource=resource,
            claims=claims,
            connection_hub_url=hub_url,
            tool_name=tool_name,
            agent_client_id=client_id,
        )
        await announce_agent_consent(consent)
        announced.append({
            "server_id": server_id,
            "tool_name": tool_name,
            "resource": resource,
            "claims": claims,
        })
    missing = pending_ids - {item["server_id"] for item in announced}
    if missing:
        LOGGER.warning(
            "[foreign-runtime] consent_pending drops with no matching connection declaration: %s "
            "— no card raised for them", sorted(missing),
        )
    return announced


async def load_mcp_server_instructions_safe(
    server_map: Dict[str, Dict[str, Any]],
) -> Dict[str, str]:
    """Each MCP server's operating guide (its initialize-result ``instructions``)
    — the text MCP-native clients surface to their model — as
    ``{server_id: instructions}``; servers publishing none are absent.

    Wraps ``frameworks.langchain.mcp.load_mcp_server_instructions``: despite its
    module home, that helper is framework-neutral in implementation (it reads
    the negotiated session via the official MCP SDK through
    ``runtime/mcp/client``; the module's langchain imports are function-local
    and never touched by this path). Deferred import + fail-open: any absence
    or failure yields ``{}``, never raises."""
    try:
        from kdcube_ai_app.apps.chat.sdk.frameworks.langchain.mcp import (
            load_mcp_server_instructions,
        )

        return dict(await load_mcp_server_instructions(server_map) or {})
    except Exception:
        LOGGER.info(
            "[foreign-runtime] mcp server-instructions fetch failed (non-fatal)",
            exc_info=True,
        )
        return {}
