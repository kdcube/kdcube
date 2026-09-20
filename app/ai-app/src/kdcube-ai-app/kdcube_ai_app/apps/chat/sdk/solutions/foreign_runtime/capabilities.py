# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── capabilities.py ── the per-turn, per-agent model-pick seam ──
#
# The chat component's Capabilities widget lets a user pick the answer model for a
# conversation. The pickable inventory + the saved selection are platform-owned
# (BaseEntrypoint serves `agent_capabilities` / `agent_selection_update`, backed by
# the UserAgentSelectionStore). An app declares the generic `simple_model_pick`
# provider PER AGENT in config (`surfaces.as_consumer.agents.<agent_id>`), so the
# widget is active for each agent with zero adapter code.
#
# What stays app-side is the ONE thing that is framework-specific: HOW a saved pick
# is applied at runtime. A foreign-runtime agent does not run the ReAct node, so it
# cannot reuse ReAct's `runtime_ctx.agent_role_models` seam. This module resolves the
# pick for the ACTIVE (dispatched) agent in the two shapes a wrapped runtime needs:
#
#   - `resolve_turn_role_models` — for a runtime that calls THROUGH the KDCube model
#     router: the app binds the result onto `bundle_call_context.role_models` around
#     the run and the router overlays it on that agent's answer role
#     (`<agent>.answer`), so the chosen model is used for that turn only.
#   - `resolve_turn_model_pick` — for a runtime configured with a MODEL NAME (a CLI
#     agent such as Claude Code): no role to rebase, so the pick is returned as
#     itself, and `None` when the user picked nothing — leaving the app's own
#     deployment default in charge of that case.
#
# The picker saves MORE than a model. Its deny map has one entry per pickable
# CATEGORY, and the three dictionary ones are what a tool-bearing agent narrows by
# (the shapes the platform clamps and stores — see
# `solutions/user_settings/agent_selection.py`):
#
#   disabled.tools          {<tool group alias>: true | [tool names]}
#   disabled.mcp            {<server_id>: true | [tool names]}
#   disabled.named_services {<namespace>: true | [operation | object.action.<name>]}
#
# `resolve_turn_selection_disabled` reads the whole block in ONE store round trip;
# `disabled_category` slices it. The per-category wrappers below stay for callers
# that need exactly one thing.
#
# Model and presentation preferences fail open to app defaults. Capability
# authority is different: every turn resolves the live Connection Hub Card
# projection, and an unavailable projection closes selectable capabilities.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

# The deny-map categories a wrapped runtime narrows by. Names are the platform's
# stored keys, not this module's invention.
DISABLED_TOOLS = "tools"
DISABLED_MCP = "mcp"
DISABLED_NAMED_SERVICES = "named_services"

from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities import (
    resolve_capability_provider,
)
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities._config import (
    agent_config_block,
)


@dataclass
class _CapabilityTurnCtx:
    """The minimal per-turn identity the capabilities provider reads to LOAD the
    saved pick and REBASE the answer role.

    The generic ``simple_model_pick`` provider inspects exactly these attributes on
    the ``runtime_ctx`` it is given: the pg pool + identity to key the selection
    store, and ``agent_role_models`` which it rebases in place with the validated
    pick. This holder carries only those fields — a value object for one turn, not
    the ReAct workflow runtime context.
    """

    pg_pool: Any = None
    tenant: str = "default"
    project: str = "default"
    user_id: str = ""
    bundle_id: str = ""
    agent_id: str = "default"
    conversation_id: str = ""
    agent_role_models: Dict[str, Dict[str, str]] = field(default_factory=dict)


async def _load_selection(
    entrypoint: Any, state: Dict[str, Any], agent_id: str
) -> Dict[str, Any]:
    """THIS (user, conversation, agent)'s saved selection record, or ``{}``.

    The ONE store read every resolver in this module shares. Identity keys are
    resolved exactly as the ``agent_capabilities`` / ``agent_selection_update``
    wire ops resolve them, so the LOAD key here matches the SAVE key there and a
    pick under conversation A never leaks into conversation B (the record key
    carries the conversation). Raises nothing of its own — the callers decide
    what an absence means for them."""
    if getattr(entrypoint, "pg_pool", None) is None:
        return {}
    identity = entrypoint._agent_selection_identity()
    store = entrypoint._agent_selection_store(identity)
    conversation_id = str(
        state.get("conversation_id") or state.get("session_id") or ""
    ).strip()
    selection = await store.get_selection(
        user_id=str(identity.get("user_id") or "anonymous"),
        bundle_id=str(identity.get("bundle_id") or ""),
        agent_id=agent_id,
        conversation_id=conversation_id,
        materialize=bool(conversation_id),
    )
    return dict(selection or {})


def disabled_category(disabled: Mapping[str, Any] | None, category: str) -> Dict[str, Any]:
    """One category out of a resolved deny map, always as a dict.

    Pure shaping over the block ``resolve_turn_selection_disabled`` returns, so a
    lane that narrows by several categories pays for ONE store read and slices it
    here. A missing or malformed category reads as "nothing denied"."""
    raw = (disabled or {}).get(category)
    return dict(raw) if isinstance(raw, Mapping) else {}


def declared_python_tool_enabled(
    connections: Any,
    disabled_tools: Optional[Mapping[str, Any]],
    tool_name: str,
) -> bool:
    """Whether one declared ``kind: python`` tool survives user narrowing.

    This is the framework-neutral form of the ordinary agent-tool inventory
    rule: the descriptor connection list is the admin ceiling and
    ``disabled.tools`` is the conversation user's subtraction from it.
    """
    wanted = str(tool_name or "").strip()
    if not wanted or not isinstance(connections, list):
        return False
    denied = disabled_tools if isinstance(disabled_tools, Mapping) else {}
    for raw in connections:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("kind") or "python").strip().lower() != "python":
            continue
        alias = str(raw.get("alias") or raw.get("name") or "").strip()
        allowed = raw.get("allowed")
        names = (
            [str(value).strip() for value in allowed if str(value).strip()]
            if isinstance(allowed, list) and allowed
            else ([alias] if alias else [])
        )
        if wanted not in names:
            continue
        disabled_spec = denied.get(alias)
        if disabled_spec is True:
            return False
        if isinstance(disabled_spec, list):
            return wanted not in {
                str(value).strip() for value in disabled_spec if str(value).strip()
            }
        return True
    return False


async def resolve_turn_role_models(
    entrypoint: Any, state: Dict[str, Any], agent_id: str
) -> Dict[str, Dict[str, str]]:
    """Resolve THIS (user, conversation)'s model pick for the ACTIVE ``agent_id``
    into a ``role_models`` overlay for the turn.

    Returns ``{"<agent>.answer": {"provider", "model"}}`` when the user has a stored
    pick for this conversation under this agent, or ``{}`` when they picked nothing
    (the model router's configured default then routes the turn). The provider is
    resolved from ``surfaces.as_consumer.agents.<agent_id>``, so a pick for one
    agent rebases ONLY that agent's ``<agent>.answer`` role — co-hosted agents
    never cross-apply. The identity keys are resolved exactly as the
    ``agent_capabilities`` wire op resolves them, so the LOAD key here matches the
    SAVE key there — and a pick under conversation A never leaks to conversation B
    (the store key includes the conversation id).

    Fails open: any error yields ``{}`` so the turn always runs.
    """
    try:
        provider = resolve_capability_provider(entrypoint.bundle_props, agent_id)
        if provider is None:
            return {}
        # Reuse the base entrypoint's own identity resolution so the store key
        # (tenant/project/user_id/bundle_id) is byte-identical to the wire op's.
        identity = entrypoint._agent_selection_identity()
        conversation_id = str(
            state.get("conversation_id") or state.get("session_id") or ""
        ).strip()
        holder = _CapabilityTurnCtx(
            pg_pool=getattr(entrypoint, "pg_pool", None),
            tenant=str(identity.get("tenant") or "default"),
            project=str(identity.get("project") or "default"),
            user_id=str(identity.get("user_id") or "anonymous"),
            bundle_id=str(identity.get("bundle_id") or ""),
            agent_id=agent_id,
            conversation_id=conversation_id,
            agent_role_models={},
        )
        # selection=None -> the provider loads the saved pick from the store keyed
        # by the holder identity, validates it against the admin-allowed list, and
        # rebases holder.agent_role_models. No tool/skill narrowing applies here
        # (these agents have no pickable tools), so tool_config/skill_config are None.
        await provider.apply_selection(
            tool_config=None,
            skill_config=None,
            runtime_ctx=holder,
        )
        return dict(holder.agent_role_models or {})
    except Exception:
        return {}


async def resolve_turn_model_pick(
    entrypoint: Any, state: Dict[str, Any], agent_id: str
) -> Optional[Dict[str, Any]]:
    """Resolve THIS (user, conversation)'s model pick for ``agent_id`` as a bare
    ``{"provider", "model"}`` pair — or ``None`` when the user picked nothing.

    The companion of ``resolve_turn_role_models`` for a wrapped runtime that
    takes a MODEL NAME rather than a router role: a CLI-driven agent (Claude
    Code) is configured with a model id, so there is no ``<agent>.answer``
    channel to rebase. This returns the pick itself and leaves the fallback to
    the app — which is the whole difference: the generic provider substitutes
    its configured ``default`` when nothing is picked, so its answer can never
    say "the user chose nothing". An app whose own deployment property carries
    the default needs that distinction, and gets it here.

    The pick is clamped to the agent's admin-declared list
    (``surfaces.as_consumer.agents.<id>.capabilities.models.supported``) — the
    SAME list the wire op clamps against on write, so a stale pick left over
    from a narrowed list resolves to ``None`` and the deployment default routes
    the turn. That declared list is the ONE ceiling; an app needs no second
    allowlist of its own.

    Identity keys are resolved exactly as the model pick / the wire op resolve
    them, so the LOAD key here matches the SAVE key there and conversation A
    never leaks into conversation B.

    Fails open: any absence or error yields ``None`` (the app's own default
    routes the turn).
    """
    try:
        if getattr(entrypoint, "pg_pool", None) is None:
            return None
        block = agent_config_block(getattr(entrypoint, "bundle_props", None), agent_id)
        models_cfg = block.get("capabilities") or {}
        models_cfg = models_cfg.get("models") if isinstance(models_cfg, Mapping) else {}
        supported = (models_cfg or {}).get("supported") if isinstance(models_cfg, Mapping) else None
        if not supported:
            return None
        selection = await _load_selection(entrypoint, state, agent_id)
        # Lazily imported: agent_inventory is a large module and this seam is
        # loaded by every foreign-runtime app.
        from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
            match_supported_model,
        )

        matched = match_supported_model((selection or {}).get("model"), supported)
        return dict(matched) if matched else None
    except Exception:
        return None


async def resolve_turn_selection_disabled(
    entrypoint: Any, state: Dict[str, Any], agent_id: str
) -> Dict[str, Any]:
    """THIS (user, conversation)'s WHOLE deny map for the ACTIVE ``agent_id``.

    The result is projected live from the descriptor Control Card intersected
    with the user's agent Card. PostgreSQL remains the migration seed for a Card
    that does not exist yet; after bootstrap it is not capability authority.

    What the admin declared under ``surfaces.as_consumer.agents.<id>`` is the
    CEILING; this is the user's subtraction from it. The effective set is always
    ceiling minus deny map — a selection can never widen an inventory, and a key
    the picker could not have offered simply matches nothing.

    Fails closed: an unavailable Card projection returns a deny map covering the
    current selectable catalog. Platform system tools remain outside this
    user-selectable boundary."""
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control import (
        agent_capability_identity,
        deny_all_capabilities,
        disabled_from_projection,
        sync_agent_capability_projection,
    )
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
        agent_capabilities_catalog,
    )

    identity = agent_capability_identity(entrypoint)
    catalog_builder = getattr(entrypoint, "_agent_capabilities_catalog", None)
    if callable(catalog_builder):
        catalog = catalog_builder(agent_id)
    else:
        catalog = agent_capabilities_catalog(
            getattr(entrypoint, "bundle_props", None),
            agent_id,
        )
    initial_disabled = None
    if getattr(entrypoint, "pg_pool", None) is not None:
        try:
            store_factory = getattr(entrypoint, "_agent_selection_store", None)
            if callable(store_factory):
                store = store_factory(identity)
            else:
                from kdcube_ai_app.apps.chat.sdk.solutions.user_settings import (
                    UserAgentSelectionStore,
                )

                store = UserAgentSelectionStore(
                    pg_pool=entrypoint.pg_pool,
                    tenant=str(identity.get("tenant") or "default"),
                    project=str(identity.get("project") or "default"),
                )
            initial_disabled = await store.get_legacy_capability_seed(
                user_id=str(identity.get("user_id") or "anonymous"),
                bundle_id=str(identity.get("bundle_id") or ""),
                agent_id=agent_id,
            )
        except Exception:
            initial_disabled = None
    try:
        result = await sync_agent_capability_projection(
            entrypoint,
            catalog=catalog,
            agent_id=agent_id,
            initial_disabled=initial_disabled,
        )
        return disabled_from_projection(catalog, result["projection"])
    except Exception:
        return deny_all_capabilities(
            catalog=catalog,
            tenant=str(identity.get("tenant") or ""),
            project=str(identity.get("project") or ""),
            application=str(identity.get("bundle_id") or ""),
            agent_id=agent_id,
        )


async def resolve_turn_disabled_tools(
    entrypoint: Any, state: Dict[str, Any], agent_id: str
) -> Dict[str, Any]:
    """Resolve THIS (user, conversation)'s tool opt-outs for the ACTIVE ``agent_id``.

    Returns the platform deny-map ``{alias: true | [tool_names]}`` the user saved in
    the capabilities widget for this conversation under this agent, or ``{}`` when
    they disabled nothing. The tools themselves are declared as a connection list on
    the agent (the admin ceiling); the app's tool-pick seam turns this deny-map plus
    that ceiling into the bound tool set for the turn.

    This covers the ``kind: python`` groups only — the picker keys MCP servers and
    named-service namespaces in their own categories (``resolve_turn_disabled_mcp``
    / ``resolve_turn_disabled_namespaces``).

    Fails closed through the live Card resolver; an unavailable projection
    denies every selectable tool while platform system tools remain available.
    """
    return disabled_category(
        await resolve_turn_selection_disabled(entrypoint, state, agent_id), DISABLED_TOOLS
    )


async def resolve_turn_disabled_mcp(
    entrypoint: Any, state: Dict[str, Any], agent_id: str
) -> Dict[str, Any]:
    """Resolve THIS (user, conversation)'s MCP opt-outs for the ACTIVE ``agent_id``.

    Returns ``{server_id: true | [tool_names]}`` — ``true`` for a server the user
    turned off whole, a list for the individual tools they turned off under a
    server that stays on. The keys are SERVER IDS (what the picker clamps against
    the catalog's ``mcp[*].server_id``), not connection aliases.

    A ``true`` here is what lets a lane drop the connection BEFORE resolution, so a
    server the user turned off is never dialled and no bearer is ever read for it
    (``mcp_bridge.narrow_mcp_connections``).

    Fails closed through the live Card resolver; an unavailable projection
    denies every selectable server."""
    return disabled_category(
        await resolve_turn_selection_disabled(entrypoint, state, agent_id), DISABLED_MCP
    )


async def resolve_turn_disabled_namespaces(
    entrypoint: Any, state: Dict[str, Any], agent_id: str
) -> Dict[str, Any]:
    """Resolve THIS (user, conversation)'s named-service opt-outs for ``agent_id``.

    Returns ``{namespace: true | [entry keys]}`` — ``true`` for a namespace the user
    turned off whole, a list of operation keys (``object.search``) or named actions
    (``object.action.<name>``) for one they narrowed. The declared side is the
    agent's ``kind: named_service`` roster
    (``namespaces.<ns>.allowed``); ``foreign_runtime.named_services`` turns the two
    into the surviving roster for the turn.

    Fails closed through the live Card resolver; an unavailable projection
    denies every selectable namespace.
    """
    return disabled_category(
        await resolve_turn_selection_disabled(entrypoint, state, agent_id),
        DISABLED_NAMED_SERVICES,
    )
