# SPDX-License-Identifier: MIT

"""Per-user agent inventory: catalog, clamp, and narrowing semantics.

Selection is a deny-list; effective = configured − disabled for every
category; system tool groups are immune; absent selection = identity.
"""

from __future__ import annotations

import sys
import types

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
    agent_capabilities_catalog,
    clamp_selection,
    narrow_agent_skill_config,
    narrow_agent_tool_config,
)
from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import (
    AgentSkillConfig,
    agent_skill_config_from_bundle_props,
)
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import (
    agent_tool_config_from_bundle_props,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import (
    connected_named_service_namespaces,
    named_service_namespace_client_tools_config,
    named_service_namespaces,
    set_denied_named_service_namespaces,
)

FAKE_WEB_MODULE = "kdcube_fake_web_tools_for_inventory_tests"


@pytest.fixture(autouse=True)
def _fake_web_module():
    mod = types.ModuleType(FAKE_WEB_MODULE)

    def list_tools():
        return {
            "web_search": {"description": "Search the web.\n\nLong tail."},
            "web_fetch": {"description": "Fetch a page."},
        }

    mod.list_tools = list_tools
    sys.modules[FAKE_WEB_MODULE] = mod
    try:
        yield
    finally:
        sys.modules.pop(FAKE_WEB_MODULE, None)


@pytest.fixture(autouse=True)
def _reset_namespace_deny():
    set_denied_named_service_namespaces(None)
    try:
        yield
    finally:
        set_denied_named_service_namespaces(None)


def _props(*, web_allowed=("web_search", "web_fetch")) -> dict:
    """Trimmed workspace-shaped inventory for agent `main`."""
    web: dict = {
        "name": "web",
        "kind": "python",
        "module": FAKE_WEB_MODULE,
        "alias": "web_tools",
    }
    if web_allowed is not None:
        web["allowed"] = list(web_allowed)
    return {
        "surfaces": {
            "as_consumer": {
                "default_agent": "main",
                "agents": {
                    "main": {
                        "tools": [
                            {
                                "name": "io",
                                "kind": "python",
                                "module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools",
                                "alias": "io_tools",
                                "allowed": ["tool_call"],
                            },
                            {
                                "name": "context",
                                "kind": "python",
                                "module": "kdcube_ai_app.apps.chat.sdk.tools.ctx_tools",
                                "alias": "ctx_tools",
                                "allowed": ["merge_sources", "fetch_ctx"],
                            },
                            web,
                            {
                                "name": "gmail",
                                "kind": "python",
                                "module": "kdcube_ai_app.apps.chat.sdk.integrations.google.gmail_tools",
                                "alias": "gmail",
                                "allowed": ["search_gmail", "send_gmail"],
                                "tool_claims": {
                                    "send_gmail": {
                                        "connections": {
                                            "delegated_to_kdcube": {
                                                "connected_accounts": [
                                                    {
                                                        "provider_id": "google",
                                                        "connector_app_id": "gmail",
                                                        "claims": ["gmail:send"],
                                                    }
                                                ]
                                            }
                                        }
                                    },
                                },
                            },
                            {
                                "name": "knowledge",
                                "kind": "mcp",
                                "server_id": "knowledge",
                                "alias": "knowledge",
                                "allowed": ["*"],
                            },
                            {
                                "name": "memory_service",
                                "kind": "named_service",
                                "alias": "named_services",
                                "namespaces": {
                                    "task": {
                                        "allowed": ["provider.about", "object.host_file"],
                                    },
                                    "mem": {
                                        "allowed": ["provider.about", "object.list"],
                                    },
                                },
                            },
                        ],
                        "skills": {
                            "consumers": {
                                "solver.react.v2.decision.v2.strong": {
                                    "enabled": ["public.*"],
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def _tool_cfg(props=None):
    return agent_tool_config_from_bundle_props(props or _props(), "main")


# ── catalog ───────────────────────────────────────────────────────────────────


def test_catalog_lists_python_groups_with_docs_and_system_flags():
    catalog = agent_capabilities_catalog(_props(), "main")
    by_alias = {g["alias"]: g for g in catalog["tools"]}

    assert by_alias["io_tools"]["system"] is True
    assert by_alias["ctx_tools"]["system"] is True
    assert by_alias["web_tools"]["system"] is False
    assert by_alias["gmail"]["system"] is False

    web_tools = {t["name"]: t for t in by_alias["web_tools"]["tools"]}
    assert set(web_tools) == {"web_search", "web_fetch"}
    # First paragraph only, via the module's own list_tools() introspection.
    assert web_tools["web_search"]["description"] == "Search the web."

    # Modules that cannot be imported still list their allowed names.
    gmail_tools = [t["name"] for t in by_alias["gmail"]["tools"]]
    assert gmail_tools == ["search_gmail", "send_gmail"]


def test_catalog_lists_mcp_servers_and_named_service_namespaces():
    catalog = agent_capabilities_catalog(_props(), "main")

    assert [e["server_id"] for e in catalog["mcp"]] == ["knowledge"]
    assert catalog["mcp"][0]["tools"] == ["*"]

    by_ns = {e["namespace"]: e for e in catalog["named_services"]}
    assert set(by_ns) == {"task", "mem"}
    assert by_ns["task"]["tools"] == ["provider_about", "host_file"]
    assert by_ns["mem"]["tools"] == ["provider_about", "list_objects"]


def test_catalog_agent_defaults():
    catalog = agent_capabilities_catalog(_props(), None, default_agent_id="main")
    assert catalog["agent"] == "main"
    assert catalog["tools"]


# ── clamp ─────────────────────────────────────────────────────────────────────


def test_clamp_rejects_out_of_inventory_ids():
    catalog = agent_capabilities_catalog(_props(), "main")
    clamped = clamp_selection(
        {
            "tools": {
                "not_configured": True,
                "web_tools": ["web_search", "not_a_tool"],
            },
            "mcp": {"unknown_server": True, "knowledge": True},
            "named_services": {"cnv": True, "task": True},
            "skills": ["public.never_heard_of_it"],
        },
        catalog,
    )
    assert clamped["tools"] == {"web_tools": ["web_search"]}
    assert clamped["mcp"] == {"knowledge": True}
    assert clamped["named_services"] == {"task": True}
    assert "skills" not in clamped


def test_clamp_strips_system_aliases():
    catalog = agent_capabilities_catalog(_props(), "main")
    clamped = clamp_selection(
        {"tools": {"io_tools": True, "ctx_tools": ["merge_sources"], "gmail": True}},
        catalog,
    )
    assert clamped == {"tools": {"gmail": True}}


# ── narrowing: absent selection = identity ────────────────────────────────────


def test_narrow_with_empty_selection_is_identity():
    cfg = _tool_cfg()
    assert narrow_agent_tool_config(cfg, {}) is cfg
    assert narrow_agent_tool_config(cfg, None) is cfg

    skill_cfg = AgentSkillConfig(agents_config={"a": {"enabled": ["public.*"]}})
    assert narrow_agent_skill_config(skill_cfg, []) is skill_cfg


# ── narrowing: python groups + tools ──────────────────────────────────────────


def test_narrow_python_group_off():
    cfg = _tool_cfg()
    narrowed = narrow_agent_tool_config(cfg, {"tools": {"gmail": True}})

    assert "gmail" not in narrowed.allowed_plugins
    assert "gmail" not in narrowed.allowed_tool_names_by_alias
    assert all(s.get("alias") != "gmail" for s in narrowed.tool_specs)
    # Claim policies for the disabled group are dropped, so consent preflight
    # never demands an account for a tool the user turned off.
    assert all(
        not str(getattr(p, "tool_name", "")).startswith("gmail")
        for p in narrowed.tool_claim_policies
    )
    # Everything else is untouched.
    assert "web_tools" in narrowed.allowed_plugins
    assert narrowed.allowed_tool_names_by_alias["web_tools"] == ["web_search", "web_fetch"]


def test_narrow_system_group_immune():
    cfg = _tool_cfg()
    narrowed = narrow_agent_tool_config(cfg, {"tools": {"io_tools": True, "ctx_tools": True}})
    assert "io_tools" in narrowed.allowed_plugins
    assert "ctx_tools" in narrowed.allowed_plugins


def test_narrow_single_tool_off():
    cfg = _tool_cfg()
    narrowed = narrow_agent_tool_config(cfg, {"tools": {"web_tools": ["web_fetch"]}})
    assert narrowed.allowed_tool_names_by_alias["web_tools"] == ["web_search"]
    assert "web_tools" in narrowed.allowed_plugins


def test_narrow_single_tool_off_materializes_wildcard():
    # No `allowed` list configured -> wildcard (None); the narrower must
    # materialize the module's concrete names before subtracting.
    cfg = _tool_cfg(_props(web_allowed=None))
    assert cfg.allowed_tool_names_by_alias["web_tools"] is None

    narrowed = narrow_agent_tool_config(cfg, {"tools": {"web_tools": ["web_search"]}})
    assert narrowed.allowed_tool_names_by_alias["web_tools"] == ["web_fetch"]


def test_narrow_all_tools_of_group_off_removes_group():
    cfg = _tool_cfg()
    narrowed = narrow_agent_tool_config(
        cfg, {"tools": {"web_tools": ["web_search", "web_fetch"]}}
    )
    assert "web_tools" not in narrowed.allowed_plugins
    assert "web_tools" not in narrowed.allowed_tool_names_by_alias


# ── narrowing: MCP ────────────────────────────────────────────────────────────


def test_narrow_mcp_server_off():
    cfg = _tool_cfg()
    narrowed = narrow_agent_tool_config(cfg, {"mcp": {"knowledge": True}})
    assert narrowed.mcp_tool_specs == []
    assert "knowledge" not in narrowed.allowed_plugins
    assert "knowledge" not in narrowed.allowed_tool_names_by_alias
    # Python groups are untouched.
    assert "web_tools" in narrowed.allowed_plugins


# ── narrowing: named-service namespaces ───────────────────────────────────────


def test_narrow_namespace_off_recomputes_named_service_allowlist():
    props = _props()
    cfg = _tool_cfg(props)
    assert set(cfg.allowed_tool_names_by_alias["named_services"]) == {
        "provider_about",
        "host_file",
        "list_objects",
    }

    narrowed = narrow_agent_tool_config(
        cfg, {"named_services": {"task": True}}, bundle_props=props, agent_id="main"
    )
    # host_file was granted only by the denied `task` namespace.
    assert set(narrowed.allowed_tool_names_by_alias["named_services"]) == {
        "provider_about",
        "list_objects",
    }


def test_narrow_all_namespaces_off_removes_named_service_alias():
    props = _props()
    cfg = _tool_cfg(props)
    narrowed = narrow_agent_tool_config(
        cfg,
        {"named_services": {"task": True, "mem": True}},
        bundle_props=props,
        agent_id="main",
    )
    assert "named_services" not in narrowed.allowed_plugins
    assert "named_services" not in narrowed.allowed_tool_names_by_alias


def test_namespace_deny_set_excludes_namespace_from_roster_and_dispatch():
    props = _props()
    assert set(connected_named_service_namespaces(props, client_id="main")) == {"task", "mem"}

    set_denied_named_service_namespaces({"task"})
    assert set(connected_named_service_namespaces(props, client_id="main")) == {"mem"}
    assert "task" not in named_service_namespaces(props)
    assert named_service_namespace_client_tools_config(
        props, namespace="task", client_id="main"
    ) == {}
    # mem stays fully wired.
    assert named_service_namespace_client_tools_config(
        props, namespace="mem", client_id="main"
    )

    set_denied_named_service_namespaces(None)
    assert set(connected_named_service_namespaces(props, client_id="main")) == {"task", "mem"}


# ── narrowing: skills ─────────────────────────────────────────────────────────


def test_narrow_skill_config_appends_denials_to_all_consumers_and_star():
    cfg = agent_skill_config_from_bundle_props(_props(), "main")
    narrowed = narrow_agent_skill_config(cfg, ["public.web_search"])

    consumer_cfg = narrowed.agents_config["solver.react.v2.decision.v2.strong"]
    assert consumer_cfg["enabled"] == ["public.*"]
    assert consumer_cfg["disabled"] == ["public.web_search"]
    assert narrowed.agents_config["*"]["disabled"] == ["public.web_search"]
    # Original is untouched (pure narrowing).
    assert "disabled" not in cfg.agents_config["solver.react.v2.decision.v2.strong"]
    assert "*" not in cfg.agents_config


# ── per-tool MCP (Phase 3) ────────────────────────────────────────────────────


def _props_mcp_concrete() -> dict:
    """MCP connection with a concrete allow-list (per-tool toggles, no handshake)."""
    props = _props()
    tools = props["surfaces"]["as_consumer"]["agents"]["main"]["tools"]
    for tool in tools:
        if tool.get("kind") == "mcp":
            tool["allowed"] = ["kb_search", "kb_fetch"]
    return props


def test_catalog_concrete_mcp_allowlist_yields_tool_entries():
    catalog = agent_capabilities_catalog(_props_mcp_concrete(), "main")
    entry = catalog["mcp"][0]
    assert entry["tools"] == ["kb_search", "kb_fetch"]
    assert [t["name"] for t in entry["tool_entries"]] == ["kb_search", "kb_fetch"]

    # Wildcard servers carry no tool_entries until enriched from the cached
    # runtime listing.
    wildcard = agent_capabilities_catalog(_props(), "main")["mcp"][0]
    assert "tool_entries" not in wildcard


def test_clamp_mcp_per_tool_names_against_known_entries():
    catalog = agent_capabilities_catalog(_props_mcp_concrete(), "main")
    clamped = clamp_selection(
        {"mcp": {"knowledge": ["kb_fetch", "not_listed"]}},
        catalog,
    )
    assert clamped == {"mcp": {"knowledge": ["kb_fetch"]}}

    # A wildcard server without known names accepts only the whole-server form.
    wildcard_catalog = agent_capabilities_catalog(_props(), "main")
    assert clamp_selection({"mcp": {"knowledge": ["kb_fetch"]}}, wildcard_catalog) == {}
    assert clamp_selection({"mcp": {"knowledge": True}}, wildcard_catalog) == {"mcp": {"knowledge": True}}


def test_narrow_mcp_per_tool_concrete_allowlist():
    props = _props_mcp_concrete()
    cfg = _tool_cfg(props)
    narrowed = narrow_agent_tool_config(cfg, {"mcp": {"knowledge": ["kb_fetch"]}})
    spec = narrowed.mcp_tool_specs[0]
    assert spec["tools"] == ["kb_search"]
    assert narrowed.allowed_tool_names_by_alias["knowledge"] == ["kb_search"]

    # Denying every listed tool collapses to the whole-server removal.
    all_off = narrow_agent_tool_config(cfg, {"mcp": {"knowledge": ["kb_search", "kb_fetch"]}})
    assert all_off.mcp_tool_specs == []
    assert "knowledge" not in all_off.allowed_plugins


def test_narrow_mcp_per_tool_wildcard_uses_denied_tools():
    cfg = _tool_cfg(_props())
    assert _tool_cfg(_props()).mcp_tool_specs[0]["tools"] == ["*"]
    narrowed = narrow_agent_tool_config(cfg, {"mcp": {"knowledge": ["kb_fetch"]}})
    spec = narrowed.mcp_tool_specs[0]
    # Wildcard allow stays a wildcard (new server tools default ON); the
    # denial rides as the subsystem-applied deny-list.
    assert spec["tools"] == ["*"]
    assert spec["denied_tools"] == ["kb_fetch"]
    assert "knowledge" in narrowed.allowed_plugins


# ── per-user model choice (admin-allowed list) ────────────────────────────────

from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (  # noqa: E402
    USER_MODEL_TARGET_ROLE,
    configured_strong_model,
    match_supported_model,
    react_supported_models,
)

_SUPPORTED = [
    {"model": "claude-sonnet-4-6", "provider": "anthropic", "label": "Sonnet 4.6"},
    {"model": "claude-haiku-4-5-20251001", "provider": "anthropic", "label": "Haiku 4.5"},
]


def _props_with_models(*, agent_level: bool) -> dict:
    props = _props()
    block = {"supported_models": [dict(row) for row in _SUPPORTED]}
    props["react"] = {"main": block} if agent_level else {"default_agent": block}
    props["role_models"] = {
        USER_MODEL_TARGET_ROLE: {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    }
    return props


def test_supported_models_parse_agent_and_default_levels():
    for agent_level in (True, False):
        rows = react_supported_models(_props_with_models(agent_level=agent_level), "main")
        assert [r["model"] for r in rows] == ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
        assert rows[0]["label"] == "Sonnet 4.6"
    # Absent list => the feature stays invisible.
    assert react_supported_models(_props(), "main") == []


def test_supported_models_agent_key_wins_over_default():
    props = _props_with_models(agent_level=False)
    props["react"]["main"] = {"supported_models": [{"model": "claude-opus-4-6", "label": "Opus"}]}
    rows = react_supported_models(props, "main")
    assert [r["model"] for r in rows] == ["claude-opus-4-6"]
    assert rows[0]["provider"] == "anthropic"  # default provider fill


def test_configured_strong_model_resolution():
    props = _props_with_models(agent_level=False)
    assert configured_strong_model(props, "main") == {
        "provider": "anthropic", "model": "claude-sonnet-4-6",
    }
    # The react block's role_models beats the bundle-level prop.
    props["react"]["default_agent"]["role_models"] = {
        USER_MODEL_TARGET_ROLE: {"provider": "anthropic", "model": "claude-opus-4-6"},
    }
    assert configured_strong_model(props, "main")["model"] == "claude-opus-4-6"
    assert configured_strong_model(_props(), "main") is None


def test_match_supported_model_validates_the_pick():
    assert match_supported_model({"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}, _SUPPORTED) == {
        "provider": "anthropic", "model": "claude-haiku-4-5-20251001",
    }
    # Provider omitted -> matches on model id and fills the row's provider.
    assert match_supported_model({"model": "claude-sonnet-4-6"}, _SUPPORTED)["provider"] == "anthropic"
    # Stale/foreign picks resolve to None (=> configured default).
    assert match_supported_model({"model": "claude-3-opus"}, _SUPPORTED) is None
    assert match_supported_model({"provider": "openai", "model": "claude-sonnet-4-6"}, _SUPPORTED) is None
    assert match_supported_model(None, _SUPPORTED) is None
    assert match_supported_model({"model": "claude-sonnet-4-6"}, []) is None


def test_catalog_carries_supported_models_and_default():
    props = _props_with_models(agent_level=False)
    catalog = agent_capabilities_catalog(props, "main")
    assert [r["model"] for r in catalog["supported_models"]] == [
        "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
    ]
    assert catalog["default_model"] == {"provider": "anthropic", "model": "claude-sonnet-4-6"}

    plain = agent_capabilities_catalog(_props(), "main")
    assert plain["supported_models"] == []
    assert plain["default_model"] is None


# ── Named-service realm view (what's inside a namespace + derived claims) ────
# The realm's discovery spec is the declaration surface its own claim
# resolution uses; the catalog scopes the shown claims to the operations the
# consumer configuration allows and NEVER invents granularity a realm did not
# declare.


class _FakeDiscovery:
    def __init__(self, specs_by_namespace):
        self.specs_by_namespace = specs_by_namespace

    async def entries_for_namespace(self, namespace):
        spec = self.specs_by_namespace.get(namespace)
        if spec is None:
            return []

        class _Entry:
            def __init__(self, spec):
                self.spec = spec

        return [_Entry(spec)]


def _spec_for(namespace):
    if namespace == "slack":
        from kdcube_ai_app.apps.chat.sdk.integrations.slack.named_service import (
            slack_named_service_spec,
        )

        return slack_named_service_spec()
    raise AssertionError(namespace)


@pytest.mark.asyncio
async def test_realm_enrichment_scopes_mail_claims_to_allowed_operations():
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
        enrich_catalog_named_service_realms,
    )
    from kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service import (
        MAIL_CONNECTED_ACCOUNT_REQUIREMENTS,
        MAIL_SCHEMA,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
        NamedServiceProviderSpec,
    )

    spec = NamedServiceProviderSpec(
        provider_id="kdcube.mail",
        namespace="mail",
        label="Mail",
        description="Provider-neutral mail namespace over user-connected accounts.",
        metadata={
            "connected_accounts": MAIL_CONNECTED_ACCOUNT_REQUIREMENTS,
            "actions": {
                name: str((meta or {}).get("description") or "")
                for name, meta in (MAIL_SCHEMA.get("actions") or {}).items()
            },
        },
    )

    # Read-only configuration: the send claim must NOT appear.
    catalog = {
        "named_services": [
            {"namespace": "mail", "alias": "named_services",
             "operations": ["provider.about", "object.list", "object.search", "object.get"]},
        ]
    }
    out = await enrich_catalog_named_service_realms(
        catalog, discovery=_FakeDiscovery({"mail": spec}),
    )
    realm = out["named_services"][0]["realm"]
    assert realm["label"] == "Mail"
    requirement = realm["connected_accounts"][0]
    assert requirement["provider_id"] == "google"
    assert requirement["connector_app_id"] == "gmail"
    assert requirement["claims"] == ["gmail:read"]
    # The declared differentiation rides along so consumers can recompute
    # effective claims over a user-narrowed operation set.
    assert requirement["claims_by_operation"]["object.action.send"] == ["gmail:send"]
    # No actions listing without object.action in the allowed set.
    assert realm["actions"] == []
    assert [op["name"] for op in realm["operations"]] == [
        "provider.about", "object.list", "object.search", "object.get",
    ]

    # Full configuration: both claims, and the named actions render with the
    # per-action claims the realm declared.
    catalog = {
        "named_services": [
            {"namespace": "mail", "alias": "named_services",
             "operations": ["object.list", "object.search", "object.get", "object.action"]},
        ]
    }
    out = await enrich_catalog_named_service_realms(
        catalog, discovery=_FakeDiscovery({"mail": spec}),
    )
    realm = out["named_services"][0]["realm"]
    assert realm["connected_accounts"][0]["claims"] == ["gmail:read", "gmail:send"]
    actions = {item["name"]: item for item in realm["actions"]}
    assert "send" in actions and "forward" in actions
    assert actions["send"]["claims"] == ["gmail:send"]
    assert actions["forward"]["claims"] == ["gmail:read", "gmail:send"]
    assert actions["download_attachments"]["claims"] == ["gmail:read"]
    assert actions["send"]["description"]
    # object.action is expanded by the named actions, so its generic row hides.
    assert "object.action" not in [op["name"] for op in realm["operations"]]


@pytest.mark.asyncio
async def test_realm_enrichment_keeps_slack_flat_claim_set():
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
        enrich_catalog_named_service_realms,
    )

    spec = _spec_for("slack")
    catalog = {
        "named_services": [
            {"namespace": "slack", "alias": "named_services",
             "operations": ["object.list", "object.search", "object.get", "object.action"]},
            {"namespace": "task", "alias": "named_services", "operations": ["object.list"]},
        ]
    }
    out = await enrich_catalog_named_service_realms(
        catalog, discovery=_FakeDiscovery({"slack": spec}),
    )
    slack_entry, task_entry = out["named_services"]
    realm = slack_entry["realm"]
    # One declared flat set: shown whole, no invented per-operation split.
    assert realm["connected_accounts"] == [{
        "provider_id": "slack",
        "connector_app_id": "demo",
        "claims": sorted([
            "slack:search", "slack:channels", "slack:history",
            "slack:files:read", "slack:files:write", "slack:post",
            "slack:assistant:search",
        ]),
    }]
    actions = {item["name"] for item in realm["actions"]}
    assert {"post_message", "upload_file", "download_file"} <= actions
    for item in realm["actions"]:
        assert "claims" not in item  # slack declared no per-action claims
    # Unresolvable namespace keeps its plain row (fail-open).
    assert "realm" not in task_entry


@pytest.mark.asyncio
async def test_realm_enrichment_fails_open_on_discovery_errors():
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
        enrich_catalog_named_service_realms,
    )

    class _BrokenDiscovery:
        async def entries_for_namespace(self, namespace):
            raise RuntimeError("discovery down")

    catalog = {"named_services": [{"namespace": "mail", "alias": "named_services", "operations": ["object.list"]}]}
    out = await enrich_catalog_named_service_realms(catalog, discovery=_BrokenDiscovery())
    assert "realm" not in out["named_services"][0]


# ── Per-user namespace narrowing at (namespace, operation/action) level ──────


def _mail_catalog(*, operations=None):
    from kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service import (
        MAIL_CONNECTED_ACCOUNT_REQUIREMENTS,
    )

    ops = operations or ["object.list", "object.search", "object.get", "object.action"]
    return {
        "named_services": [
            {
                "namespace": "mail",
                "alias": "named_services",
                "operations": list(ops),
                "realm": {
                    "label": "Mail",
                    "actions": [
                        {"name": "send"}, {"name": "forward"}, {"name": "download_attachments"},
                    ],
                    "connected_accounts": [dict(req) for req in MAIL_CONNECTED_ACCOUNT_REQUIREMENTS],
                },
            }
        ]
    }


def test_disabled_namespace_maps_split_full_and_entry_denies():
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import disabled_namespace_maps

    fully, per_entry = disabled_namespace_maps({
        "named_services": {
            "slack": True,
            "mail": ["object.search", "object.action.send"],
            "task": [],
        }
    })
    assert fully == {"slack"}
    assert per_entry == {"mail": {"object.search", "object.action.send"}}


def test_clamp_accepts_namespace_entry_lists_within_the_inventory():
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import clamp_selection

    catalog = _mail_catalog()
    out = clamp_selection({
        "named_services": {
            "mail": ["object.search", "object.action.send", "object.action.bogus", "object.upsert"],
            "ghost": ["object.list"],
        }
    }, catalog)
    # Known keys survive: allowed operations + realm actions as
    # object.action.<name>; unknown keys and unknown namespaces are stripped
    # (the user pick narrows within the config's allowed set, never widens).
    assert out["named_services"] == {"mail": ["object.search", "object.action.send"]}


def test_narrow_keeps_grammar_tools_for_entry_denies_but_drops_full_denies():
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import disabled_namespace_maps

    # The full/entry split is what narrow consumes: a per-entry list must
    # NEVER count as a full namespace deny (that was the truthiness trap).
    fully, per_entry = disabled_namespace_maps({"named_services": {"mail": ["object.search"]}})
    assert fully == set()
    assert per_entry == {"mail": {"object.search"}}


def test_namespace_claim_policies_recompute_effective_claims_over_denies():
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import namespace_claim_policies

    catalog = _mail_catalog()

    # No denies: both claims.
    policies = namespace_claim_policies(catalog, {})
    assert policies == [{
        "tool_name": "mail",
        "connected_accounts": [{
            "provider_id": "google", "connector_app_id": "gmail",
            "claims": ["gmail:read", "gmail:send"],
        }],
    }]

    # Denying send + forward drops the send claim: the user is never asked
    # for gmail:send.
    policies = namespace_claim_policies(catalog, {
        "named_services": {"mail": ["object.action.send", "object.action.forward"]},
    })
    assert policies[0]["connected_accounts"][0]["claims"] == ["gmail:read"]

    # Fully denied namespace: no policy at all.
    assert namespace_claim_policies(catalog, {"named_services": {"mail": True}}) == []


def test_namespace_claim_policies_keep_flat_realms_flat():
    from kdcube_ai_app.apps.chat.sdk.integrations.slack.named_service import (
        SLACK_CONNECTED_ACCOUNT_REQUIREMENTS,
    )
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import namespace_claim_policies

    catalog = {
        "named_services": [{
            "namespace": "slack",
            "alias": "named_services",
            "operations": ["object.list", "object.search", "object.action"],
            "realm": {
                "label": "Slack",
                "actions": [{"name": "post_message"}],
                "connected_accounts": [dict(req) for req in SLACK_CONNECTED_ACCOUNT_REQUIREMENTS],
            },
        }]
    }
    # Slack declared one flat set: entry denies keep it whole (honest — the
    # realm declared no per-operation split).
    policies = namespace_claim_policies(catalog, {
        "named_services": {"slack": ["object.action.post_message"]},
    })
    assert policies[0]["connected_accounts"][0]["claims"] == sorted(
        SLACK_CONNECTED_ACCOUNT_REQUIREMENTS[0]["claims"]
    )


# ── The human layer: the realm card speaks the service's own contract ────────


@pytest.mark.asyncio
async def test_realm_card_carries_the_services_own_human_contract():
    """The catalog passes through the realm's self-description in user terms:
    purpose sentence, third-party dependency, object kinds, and per-entry
    human labels/descriptions + via-lines — all from the SAME declaration the
    agent reads, never invented downstream."""
    from kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service import (
        MailNamedServiceProvider,
    )
    from kdcube_ai_app.apps.chat.sdk.integrations.slack.named_service import (
        slack_named_service_spec,
    )
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
        enrich_catalog_named_service_realms,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
        NamedServiceProviderSpec,
    )

    mail_spec_meta = getattr(MailNamedServiceProvider, "__named_service_spec__", None)
    if mail_spec_meta is None:
        # Build from the decorator registration path used at runtime.
        from kdcube_ai_app.apps.chat.sdk.integrations.mail import named_service as mail_ns

        mail_spec_meta = NamedServiceProviderSpec(
            provider_id="kdcube.mail",
            namespace="mail",
            label="Mail",
            description="Provider-neutral mail namespace over user-connected accounts.",
            metadata={
                "connected_accounts": mail_ns.MAIL_CONNECTED_ACCOUNT_REQUIREMENTS,
                "actions": {
                    name: str((meta or {}).get("description") or "")
                    for name, meta in (mail_ns.MAIL_SCHEMA.get("actions") or {}).items()
                },
                "presentation": mail_ns.MAIL_PRESENTATION,
                "object_kinds": {
                    kind: str((meta or {}).get("description") or "")
                    for kind, meta in (mail_ns.MAIL_SCHEMA.get("object_kinds") or {}).items()
                },
            },
        )

    catalog = {
        "named_services": [
            {"namespace": "mail", "alias": "named_services",
             "operations": ["object.list", "object.search", "object.get", "object.action"]},
            {"namespace": "slack", "alias": "named_services",
             "operations": ["object.list", "object.search", "object.get", "object.action"]},
        ]
    }
    out = await enrich_catalog_named_service_realms(
        catalog,
        discovery=_FakeDiscovery({"mail": mail_spec_meta, "slack": slack_named_service_spec()}),
    )
    mail_realm, slack_realm = (e["realm"] for e in out["named_services"])

    # Purpose + third-party, in user terms.
    assert mail_realm["about"] == "Read, search, and send email from the mail accounts you connect."
    assert mail_realm["third_party"] == "Works with your mailbox through your connected Google account."
    assert slack_realm["about"] == "Search, read, and post in the Slack workspaces you connect."
    assert slack_realm["third_party"] == "Works with your Slack workspace through your connected Slack account."

    # Object kinds with their one-liners (from the schema the agent reads).
    slack_objects = {o["name"]: o["description"] for o in slack_realm["objects"]}
    assert "One Slack conversation/channel visible to the connected account." in slack_objects.values()

    # Entries lead with the human name; the grammar token stays as `name`.
    mail_actions = {a["name"]: a for a in mail_realm["actions"]}
    assert mail_actions["send"]["label"] == "Send email"
    assert mail_actions["send"]["description"] == "Send an email from your connected mail account."
    # Third-party transparency per entry, from declared labels.
    assert mail_actions["send"]["via"] == "via your connected Google account · send mail"
    assert mail_actions["forward"]["via"] == "via your connected Google account · read mail, send mail"

    slack_ops = {o["name"]: o for o in slack_realm["operations"]}
    assert slack_ops["object.search"]["label"] == "Search messages"
    assert slack_ops["object.search"]["description"] == "Search messages across channels you can see."
    slack_actions = {a["name"]: a for a in slack_realm["actions"]}
    assert slack_actions["post_message"]["label"] == "Post a message"
