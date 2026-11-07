"""The "tools, both ways" seam (platform/tools_mcp.py) — thin over the SDK.

The mechanism moved to SDK: `solutions/connections/delegated_mcp` resolves the
per-user MCP server map (minting a delegated bearer for delegated connections)
and `frameworks/langchain/mcp` binds it as LangChain tools. This bundle module
is the thin adapter (connection list + turn user -> LangChain tools). Asserts:
only `kind: mcp` connections are considered, and clean degradation (no MCP
connections / adapter absent -> [] , the agent still builds with plain tools),
and that the capabilities picker's MCP opt-outs actually govern — the deny map
is keyed by SERVER ID, whole-server denials drop the connection before any
server contact, per-tool denials narrow the bound set.
Fully offline.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _mcp_module():
    _name, module = load_dynamic_module_for_path(BUNDLE_ROOT / "platform" / "tools_mcp.py")
    return module


_PLAIN_CONNS = [
    {"name": "calc", "kind": "python", "alias": "calc", "allowed": ["calc"]},
    {"name": "code_exec", "kind": "python", "alias": "code_exec", "allowed": ["run_python"]},
]
_MCP_CONN = {"name": "memory", "kind": "mcp", "url": "https://h/api/mcp/mem", "delegated": True, "scopes": ["memories:read"]}
_NAMED_SERVICES_CONN = {
    "name": "named_services",
    "kind": "mcp",
    "server_id": "named_services",
    "alias": "named_services",
    "url": "https://h/api/mcp/named_services",
    "resource": "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*",
    "delegated": True,
    "scopes": ["named_services:use"],
}


def test_mcp_connections_filters_by_kind() -> None:
    m = _mcp_module()
    assert m.mcp_connections(_PLAIN_CONNS) == []
    assert m.mcp_connections(_PLAIN_CONNS + [_MCP_CONN]) == [_MCP_CONN]


def test_no_mcp_connection_returns_empty() -> None:
    m = _mcp_module()
    # No kind:mcp declared -> no MCP tools (the agent still builds with plain tools).
    tools, consents = asyncio.run(m.load_mcp_tools_for_connections(_PLAIN_CONNS, user_sub="u"))
    assert tools == [] and consents == []


def test_delegated_mcp_without_user_degrades_to_empty() -> None:
    m = _mcp_module()
    # No user -> the delegated server is dropped before any bind is attempted,
    # while the graph remains buildable with its plain tools.
    tools, consents = asyncio.run(m.load_mcp_tools_for_connections([_MCP_CONN], user_sub=None))
    assert tools == [] and consents == []


def test_consent_pending_drop_raises_a_demand_not_a_silent_gap() -> None:
    # REGRESSION (surfaced live 2026-07-17): with the consented-token path, a
    # connection whose user hasn't granted THIS agent is dropped BEFORE any
    # server contact — no 403 ever happens. The demand must come from the drop,
    # otherwise the agent silently loses the tool and tells the user the
    # capability does not exist.
    m = _mcp_module()

    async def no_grant(conn, user_sub):
        return None  # consent pending for this agent

    tools, consents = asyncio.run(m.load_mcp_tools_for_connections(
        [_MCP_CONN], user_sub="u1",
        application="ported-langgraph-agents@2026-07-13", agent_id="lg-react",
        bearer_provider=no_grant,
    ))
    assert tools == []
    assert len(consents) == 1
    c = consents[0]
    assert c.claims == ["memories:read"]
    # The demand is actionable: it opens THIS hosted agent's Card review for the
    # same resource and claims. A hosted agent gets no direct one-click grant.
    assert c.consent["agent_card_consent"] is True
    assert "grant" not in c.consent
    assert c.consent["agent_client_id"] == "kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react"
    assert c.consent["resource"] == "https://h/api/mcp/mem"
    assert c.consent["claims"] == ["memories:read"]


def test_demand_keys_on_the_declared_resource_id_not_the_url() -> None:
    # A deployment's delegated-resource catalog often configures a WILDCARD
    # pattern; the connection declares it via `resource` while `url` stays the
    # concrete endpoint. The demand (and the Card review it opens) must key on
    # the pattern — the grant is created, validated, and looked up under that
    # exact id, and the guard fnmatches the request URL against it.
    m = _mcp_module()
    conn = {
        **_MCP_CONN,
        "resource": "*/api/integrations/bundles/*/*/user-memories@2026-06-26/public/mcp/memories*",
    }

    async def no_grant(c, user_sub):
        return None

    tools, consents = asyncio.run(m.load_mcp_tools_for_connections(
        [conn], user_sub="u1", application="app", agent_id="lg-react", bearer_provider=no_grant,
    ))
    assert tools == [] and len(consents) == 1
    c = consents[0]
    assert c.resource == conn["resource"]
    assert c.consent["resource"] == conn["resource"]
    assert c.consent["agent_card_consent"] is True and "grant" not in c.consent


def test_consent_stub_raises_the_demand_at_the_ATTEMPT_not_at_build() -> None:
    # Consent is demand-driven per tool (surfaced live: asking about memories
    # raised the SLACK demand, because every pending connection announced at
    # build — a turn-start claim union). Pending connections bind consent-gated
    # stubs instead: nothing announces at build; CALLING the stub announces
    # exactly that connection's demand and returns the consent tool-result.
    m = _mcp_module()

    async def no_grant(conn, user_sub):
        return None

    _tools, consents = asyncio.run(m.load_mcp_tools_for_connections(
        [_MCP_CONN], user_sub="u1", application="app", agent_id="lg-react",
        bearer_provider=no_grant,
    ))
    announced: list = []

    async def announce(c):
        announced.append(c)

    stubs = m.consent_request_tools(consents, announce=announce)
    assert len(stubs) == 1
    stub = stubs[0]
    assert stub.name == "memory"                       # the connection's alias
    assert "consent" in stub.description.lower()
    assert announced == []                             # nothing at build

    result = asyncio.run(stub.coroutine(reason="user asked about cities"))
    assert announced == consents                       # the ATTEMPT announced it
    assert result["ok"] is False
    assert result["consent"]["claims"] == ["memories:read"]
    assert result["consent"]["agent_card_consent"] is True and "grant" not in result["consent"]


def test_named_services_pending_grant_asks_only_for_the_door() -> None:
    """The pre-bind stub cannot know which namespace operation will be called.

    For the named-services MCP door it must therefore ask only for the door
    admission grant. Slack account claims and conv operation grants are raised
    later by the named-services bridge from the resource catalog.
    """
    m = _mcp_module()

    async def no_grant(conn, user_sub):
        return None

    _tools, consents = asyncio.run(m.load_mcp_tools_for_connections(
        [_NAMED_SERVICES_CONN], user_sub="u1", application="app", agent_id="lg-react",
        bearer_provider=no_grant,
    ))
    assert len(consents) == 1
    assert consents[0].claims == ["named_services:use"]
    consent = consents[0].consent
    assert consent["agent_card_consent"] is True and "grant" not in consent
    assert consent["claims"] == ["named_services:use"]
    assert "conv:read" not in consent["claims"]


def test_no_user_drop_stays_silent() -> None:
    # An anonymous turn cannot grant; the delegated connection is dropped with
    # no demand (nothing for the user to act on).
    m = _mcp_module()

    async def boom(conn, user_sub):  # must not even be called without a user
        raise AssertionError("bearer provider must not run without a user")

    tools, consents = asyncio.run(m.load_mcp_tools_for_connections(
        [_MCP_CONN], user_sub=None, application="a", agent_id="b", bearer_provider=boom,
    ))
    assert tools == [] and consents == []


def test_user_opt_out_drops_the_mcp_connection() -> None:
    m = _mcp_module()
    # The picker deny-map opts the whole MCP server out this turn -> it is not
    # bound (governance: admin-declared ∩ user-enabled, same as plain/code-exec
    # tools). _MCP_CONN declares no server_id, so its name is the server id.
    assert m.mcp_connections([_MCP_CONN], None) == [_MCP_CONN]          # not opted out -> kept
    assert m.mcp_connections([_MCP_CONN], {"memory": True}) == []       # opted out -> dropped
    assert m.mcp_connections([_MCP_CONN], {"other": True}) == [_MCP_CONN]  # unrelated opt-out ignored


# ── the deny map's KEY (regression) ─────────────────────────────────────────

# A connection whose picker key (`server_id`) and model-facing `alias` differ —
# the shape that made the bug invisible while both were the same string.
_SPLIT_CONN = {
    "name": "memories", "kind": "mcp", "server_id": "user_memories", "alias": "memory_tools",
    "url": "https://h/api/mcp/mem", "delegated": True, "scopes": ["memories:read"],
}


def test_the_mcp_deny_map_is_keyed_by_server_id() -> None:
    # REGRESSION (silent until now): this module filtered on the connection
    # ALIAS against the `disabled.tools` category, while the capabilities picker
    # writes `disabled.mcp` keyed on SERVER ID. Every MCP opt-out in this app was
    # therefore inert unless alias and server id happened to be the same string.
    m = _mcp_module()
    # the picker's key removes the connection ...
    assert m.mcp_connections([_SPLIT_CONN], {"user_memories": True}) == []
    # ... and the alias — what an alias-keyed `disabled.tools` entry carries —
    # governs MCP no longer.
    assert m.mcp_connections([_SPLIT_CONN], {"memory_tools": True}) == [_SPLIT_CONN]
    assert m.mcp_connections([_SPLIT_CONN], {"memories": True}) == [_SPLIT_CONN]


def test_a_server_turned_off_is_never_dialled_and_raises_no_consent() -> None:
    # The drop happens BEFORE resolution: no bearer is read for a server the user
    # turned off, so it can raise no consent card the user did not ask for.
    m = _mcp_module()

    async def boom(conn, user_sub):
        raise AssertionError("a server turned off must never mint a bearer")

    tools, consents = asyncio.run(m.load_mcp_tools_for_connections(
        [_SPLIT_CONN], user_sub="u1", application="app", agent_id="lg-react",
        bearer_provider=boom, disabled_map={"user_memories": True},
    ))
    assert tools == [] and consents == []


def test_a_partial_denial_keeps_the_server_and_drops_only_its_tools() -> None:
    # The other half of the same deny map: a LIST under a server id names the
    # individual tools the user turned off; the connection stays.
    m = _mcp_module()
    assert m.mcp_connections([_SPLIT_CONN], {"user_memories": ["search"]}) == [_SPLIT_CONN]

    class _Tool:
        def __init__(self, name, server_id):
            self.name = name
            self.metadata = {"mcp_server_id": server_id}

    bound = [_Tool("search", "user_memories"), _Tool("get", "user_memories"), _Tool("search", "other")]
    kept = m.narrow_bound_mcp_tools(bound, {"user_memories": ["search"]})
    # the denied tool goes; the same tool name on ANOTHER server stays
    assert [(t.name, t.metadata["mcp_server_id"]) for t in kept] == [
        ("get", "user_memories"), ("search", "other"),
    ]
    assert m.narrow_bound_mcp_tools(bound, {"user_memories": True}) == bound
    assert m.narrow_bound_mcp_tools(bound, None) == bound
