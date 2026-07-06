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
    """Trimmed versatile-shaped inventory for agent `main`."""
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
