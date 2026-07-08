# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Per-user agent capability inventory + selection narrowing.

The bundle config (``surfaces.as_consumer.agents.<id>.{tools,skills}``) is the
INVENTORY an administrator grants an agent. This module enumerates that
inventory for a picker UI (``agent_capabilities_catalog``) and applies a
per-user deny-list selection as a pure narrowing of the resolved runtime
configs (``narrow_agent_tool_config`` / ``narrow_agent_skill_config``).

Selection record shape (deny-list; absent key/entry = enabled):

    {
      "tools": {"<alias>": true | ["<tool_name>", ...]},
      "mcp": {"<server_id>": true | ["<tool_name>", ...]},
      "named_services": {"<namespace>": true},
      "skills": ["<namespace>.<skill_id>", ...]
    }

The user can only remove; nothing outside the configured inventory can ever be
enabled (``clamp_selection``). System tool groups (``io``/``context``) are
locked on and immune to denial.
"""

from __future__ import annotations

import importlib
import pathlib
import re
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.event_identity import normalize_agent_id
from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import AgentSkillConfig
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import (
    _NAMED_SERVICE_OPERATION_TO_TOOL,
    _agent_tool_connections,
    _named_service_tools_for_connection,
    AgentToolConfig,
    DEFAULT_AGENT_ID,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import (
    NAMED_SERVICE_TOOLS_ALIAS,
    NAMED_SERVICE_TOOLS_MODULE,
)

# io_tools carries the ReAct `tool_call` mechanism and ctx_tools the context
# plumbing — always present regardless of the user's pick, else the agent
# cannot act. Config `name:` forms included so denials keyed either way are
# stripped.
SYSTEM_TOOL_ALIASES = frozenset({"io_tools", "ctx_tools", "io", "context"})

# The per-user model pick targets the ReAct strong decision role: it overrides
# what bundle-level `role_models` or the agent's react block configures for it,
# for that user's turns only.
USER_MODEL_TARGET_ROLE = "solver.react.v2.decision.v2.strong"


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_namespace(value: Any) -> str:
    return _norm(value).lower().rstrip(":")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            text = _norm(item)
            if text and text not in out:
                out.append(text)
        return out
    text = _norm(value)
    return [text] if text else []


def _first_para(text: str) -> str:
    return _norm(text).split("\n\n")[0].strip()


def is_system_tool_alias(alias: Any) -> bool:
    return _norm(alias) in SYSTEM_TOOL_ALIASES


# ── per-user model choice (admin-allowed list) ───────────────────────────────


def _react_agent_config_blocks(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
) -> list[Mapping[str, Any]]:
    """React config blocks in agent-key precedence — parity with the
    BaseWorkflow `_react_config_lookup` chain (agent key → `default_agent` →
    `default` → the react root), over both `react` and `config.react` roots."""

    def _get(data: Mapping[str, Any], path: str) -> Any:
        cur: Any = data
        for part in path.split("."):
            if not isinstance(cur, Mapping) or part not in cur:
                return None
            cur = cur[part]
        return cur

    normalized = normalize_agent_id(agent_id)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", normalized).strip("_")
    keys: list[str] = []
    for key in (normalized, safe, "default_agent", "default"):
        if key and key not in keys:
            keys.append(key)

    blocks: list[Mapping[str, Any]] = []
    for root_path in ("react", "config.react"):
        root = _get(bundle_props or {}, root_path)
        if not isinstance(root, Mapping):
            continue
        agents = root.get("agents")
        for key in keys:
            direct = root.get(key)
            if isinstance(direct, Mapping):
                blocks.append(direct)
            if isinstance(agents, Mapping) and isinstance(agents.get(key), Mapping):
                blocks.append(agents[key])
        blocks.append(root)
    return blocks


def react_supported_models(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
) -> list[dict[str, str]]:
    """The admin-allowed model list for this agent's react block.

    Config shape mirrors the economics price-table rows::

        react:
          default_agent:            # or a per-agent key
            supported_models:
              - model: claude-sonnet-4-6
                provider: anthropic
                label: Sonnet 4.6

    Empty/absent list means the per-user model choice stays invisible.
    """
    for block in _react_agent_config_blocks(bundle_props, agent_id):
        raw = block.get("supported_models")
        if not isinstance(raw, list):
            continue
        out: list[dict[str, str]] = []
        for row in raw:
            if not isinstance(row, Mapping):
                continue
            model = _norm(row.get("model"))
            if not model:
                continue
            out.append({
                "model": model,
                "provider": _norm(row.get("provider")) or "anthropic",
                "label": _norm(row.get("label")) or model,
            })
        return out
    return []


def configured_strong_model(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
) -> dict[str, str] | None:
    """The configured default for the strong decision role: the agent react
    block's `role_models` first, else the bundle-level `role_models` prop."""
    for block in _react_agent_config_blocks(bundle_props, agent_id):
        role_models = block.get("role_models")
        if isinstance(role_models, Mapping):
            spec = role_models.get(USER_MODEL_TARGET_ROLE)
            if isinstance(spec, Mapping) and _norm(spec.get("model")):
                return {
                    "provider": _norm(spec.get("provider")) or "anthropic",
                    "model": _norm(spec.get("model")),
                }
    role_models = (bundle_props or {}).get("role_models")
    if isinstance(role_models, Mapping):
        spec = role_models.get(USER_MODEL_TARGET_ROLE)
        if isinstance(spec, Mapping) and _norm(spec.get("model")):
            return {
                "provider": _norm(spec.get("provider")) or "anthropic",
                "model": _norm(spec.get("model")),
            }
    return None


def normalize_model_pick(pick: Any) -> dict[str, str] | None:
    """`{provider, model}` from a stored/submitted pick; None when shapeless."""
    if not isinstance(pick, Mapping):
        return None
    model = _norm(pick.get("model"))
    if not model:
        return None
    return {"provider": _norm(pick.get("provider")), "model": model}


def match_supported_model(
    pick: Any,
    supported: Sequence[Mapping[str, Any]] | None,
) -> dict[str, str] | None:
    """The supported row a pick refers to, or None (stale/foreign pick).

    Matches on model id; when both sides carry a provider it must match too.
    """
    normalized = normalize_model_pick(pick)
    if not normalized:
        return None
    for row in supported or []:
        if not isinstance(row, Mapping):
            continue
        if _norm(row.get("model")) != normalized["model"]:
            continue
        row_provider = _norm(row.get("provider"))
        if normalized["provider"] and row_provider and normalized["provider"] != row_provider:
            continue
        return {
            "provider": row_provider or normalized["provider"] or "anthropic",
            "model": normalized["model"],
        }
    return None


# ── cold-cache: selection-change classification + user-held policy ───────────

# Selection-change policy values. The USER pays for the cache, so the USER
# holds the policy; admin config supplies the default and the allowed set.
SELECTION_CHANGE_POLICIES = ("accept", "confirm", "defer_cold", "defer_conversation")
DEFAULT_SELECTION_CHANGE_POLICY = "confirm"

# Delta classes (a selection change belongs to one or both).
SELECTION_CHANGE_MODEL = "model_switch"
SELECTION_CHANGE_CAPABILITY = "capability_toggle"

_DISABLED_REASONS = (
    ("tools", "tool_toggle"),
    ("mcp", "mcp_toggle"),
    ("named_services", "namespace_toggle"),
    ("skills", "skill_toggle"),
)


def selection_snapshot(disabled: Mapping[str, Any] | None, model: Any) -> dict[str, Any]:
    """The canonical APPLIED-selection snapshot persisted per conversation."""
    return {
        "disabled": dict(disabled) if isinstance(disabled, Mapping) else {},
        "model": normalize_model_pick(model),
    }


def _category_norm(disabled: Mapping[str, Any] | None, key: str) -> Any:
    raw = (disabled or {}).get(key) if isinstance(disabled, Mapping) else None
    if key == "skills":
        return sorted(_string_list(raw))
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    for name, value in raw.items():
        name = _norm(name)
        if not name or not value:
            continue
        out[name] = True if value is True else sorted(_string_list(value))
    return out


def classify_selection_change(
    prev: Mapping[str, Any] | None,
    curr: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Diff two applied-selection snapshots into cache-relevant delta classes.

    Returns ``{changed, classes, reasons, prev_model, new_model}`` where
    ``classes`` ⊆ {model_switch, capability_toggle} and ``reasons`` names the
    concrete toggle kinds (tool/skill/mcp/namespace) plus model_switch.
    """
    prev = prev if isinstance(prev, Mapping) else {}
    curr = curr if isinstance(curr, Mapping) else {}
    prev_model = normalize_model_pick(prev.get("model"))
    new_model = normalize_model_pick(curr.get("model"))
    reasons: list[str] = []
    classes: list[str] = []
    if prev_model != new_model:
        reasons.append(SELECTION_CHANGE_MODEL)
        classes.append(SELECTION_CHANGE_MODEL)
    prev_disabled = prev.get("disabled")
    curr_disabled = curr.get("disabled")
    for key, reason in _DISABLED_REASONS:
        if _category_norm(prev_disabled, key) != _category_norm(curr_disabled, key):
            reasons.append(reason)
            if SELECTION_CHANGE_CAPABILITY not in classes:
                classes.append(SELECTION_CHANGE_CAPABILITY)
    return {
        "changed": bool(reasons),
        "classes": classes,
        "reasons": reasons,
        "prev_model": prev_model,
        "new_model": new_model,
    }


def normalize_selection_change_policy(value: Any, *, allowed: Sequence[str] | None = None) -> str:
    text = _norm(value).lower()
    pool = [p for p in (allowed or SELECTION_CHANGE_POLICIES) if p in SELECTION_CHANGE_POLICIES]
    if text in pool:
        return text
    if DEFAULT_SELECTION_CHANGE_POLICY in pool:
        return DEFAULT_SELECTION_CHANGE_POLICY
    return pool[0] if pool else DEFAULT_SELECTION_CHANGE_POLICY


def react_selection_change_policy(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
) -> dict[str, Any]:
    """Admin defaults/bounds for the selection-change policy.

    Config (same agent-key chain as the rest of the react block)::

        react:
          default_agent:
            cache:
              selection_change_policy: confirm            # one default for both classes
              # or:
              selection_change_policy:
                model_switch: confirm
                capability_toggle: accept
                allowed: [accept, confirm, defer_cold]

    Returns ``{model_switch, capability_toggle, allowed}`` — the platform
    default is ``confirm`` for both classes with the full set allowed.
    """
    raw: Any = None
    for block in _react_agent_config_blocks(bundle_props, agent_id):
        cache_cfg = block.get("cache")
        if isinstance(cache_cfg, Mapping) and cache_cfg.get("selection_change_policy") is not None:
            raw = cache_cfg.get("selection_change_policy")
            break
    allowed = list(SELECTION_CHANGE_POLICIES)
    model_default = DEFAULT_SELECTION_CHANGE_POLICY
    capability_default = DEFAULT_SELECTION_CHANGE_POLICY
    if isinstance(raw, str):
        model_default = capability_default = normalize_selection_change_policy(raw)
    elif isinstance(raw, Mapping):
        raw_allowed = [p for p in _string_list(raw.get("allowed")) if p in SELECTION_CHANGE_POLICIES]
        if raw_allowed:
            allowed = raw_allowed
        base = raw.get("default")
        if base is not None:
            model_default = capability_default = normalize_selection_change_policy(base, allowed=allowed)
        if raw.get(SELECTION_CHANGE_MODEL) is not None:
            model_default = normalize_selection_change_policy(raw.get(SELECTION_CHANGE_MODEL), allowed=allowed)
        if raw.get(SELECTION_CHANGE_CAPABILITY) is not None:
            capability_default = normalize_selection_change_policy(raw.get(SELECTION_CHANGE_CAPABILITY), allowed=allowed)
    return {
        SELECTION_CHANGE_MODEL: model_default,
        SELECTION_CHANGE_CAPABILITY: capability_default,
        "allowed": allowed,
    }


def effective_selection_change_policy(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
    user_cache_policy: Mapping[str, Any] | None,
) -> dict[str, str]:
    """The user's standing policy over the admin default, clamped to the
    admin-allowed set: the user pays for the cache, so the user decides."""
    admin = react_selection_change_policy(bundle_props, agent_id)
    allowed = admin["allowed"]
    out: dict[str, str] = {}
    for klass in (SELECTION_CHANGE_MODEL, SELECTION_CHANGE_CAPABILITY):
        user_value = (user_cache_policy or {}).get(klass) if isinstance(user_cache_policy, Mapping) else None
        if _norm(user_value).lower() in allowed:
            out[klass] = _norm(user_value).lower()
        else:
            out[klass] = admin[klass]
    return out


def clamp_cache_policy(
    policy: Mapping[str, Any] | None,
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
) -> dict[str, str]:
    """Write-side clamp for the user's standing `cache_policy` patch: only the
    two known classes, only admin-allowed values; everything else drops."""
    admin = react_selection_change_policy(bundle_props, agent_id)
    allowed = admin["allowed"]
    out: dict[str, str] = {}
    if isinstance(policy, Mapping):
        for klass in (SELECTION_CHANGE_MODEL, SELECTION_CHANGE_CAPABILITY):
            value = _norm(policy.get(klass)).lower()
            if value in allowed:
                out[klass] = value
    return out


# ── catalog (the pickable inventory) ─────────────────────────────────────────


def _module_tool_docs(module_name: str) -> dict[str, str]:
    """``{tool_name: first-paragraph description}`` via light introspection.

    Mirrors the tool manager's own extraction (`ToolSubsystem._introspect_module`):
    a tool's doc is its ``list_tools()`` meta ``description``, else the callable's
    SK/``description`` attribute, else its ``__doc__``.
    """
    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return {}
    owner = getattr(mod, "tools", mod)
    reg: Mapping[str, Any] = {}
    if hasattr(mod, "list_tools"):
        try:
            reg = mod.list_tools() or {}
        except Exception:
            reg = {}
    docs: dict[str, str] = {}
    names = list(reg.keys()) if isinstance(reg, Mapping) and reg else [
        name for name in dir(owner) if not name.startswith("_")
    ]
    for fn_name in names:
        meta = reg.get(fn_name) if isinstance(reg, Mapping) else None
        fn = (meta.get("callable") if isinstance(meta, Mapping) else None) or getattr(owner, fn_name, None)
        desc = _norm(meta.get("description")) if isinstance(meta, Mapping) else ""
        if not desc and fn is not None:
            desc = (
                getattr(fn, "__kernel_function_description__", "")
                or getattr(fn, "description", "")
                or (getattr(fn, "__doc__", "") or "")
            )
        desc = _first_para(str(desc or ""))
        if callable(fn) or (isinstance(meta, Mapping) and meta):
            docs[fn_name] = desc
    return docs


def _module_tool_names(module_name: str) -> list[str] | None:
    """Concrete tool names published by a module, or None when unknowable."""
    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return None
    if hasattr(mod, "list_tools"):
        try:
            reg = mod.list_tools() or {}
            if isinstance(reg, Mapping):
                return [str(k) for k in reg.keys()]
        except Exception:
            return None
    owner = getattr(mod, "tools", mod)
    names = [name for name in dir(owner) if not name.startswith("_") and callable(getattr(owner, name, None))]
    return names or None


def agent_capabilities_catalog(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
    *,
    bundle_root: str | pathlib.Path | None = None,
    default_agent_id: str = DEFAULT_AGENT_ID,
) -> dict[str, Any]:
    """The pickable inventory for one agent, ready for a selection UI.

    Categories match the selection record: python tool groups (with per-tool
    names + descriptions), MCP entries per server, named-service namespaces,
    and skills expanded to concrete entries with front-matter.
    """
    tools_out: list[dict[str, Any]] = []
    mcp_out: list[dict[str, Any]] = []
    namespaces_out: list[dict[str, Any]] = []

    for connection in _agent_tool_connections(
        bundle_props,
        agent_id=agent_id,
        default_agent_id=default_agent_id,
    ):
        kind = str(connection.get("kind") or "python").strip().lower()
        alias = _norm(connection.get("alias") or connection.get("name"))

        if kind == "python":
            if not alias:
                continue
            allowed = _string_list(connection.get("allowed"))
            module = _norm(connection.get("module"))
            docs: dict[str, str] = _module_tool_docs(module) if module else {}
            names = allowed or (list(docs.keys()) if docs else [])
            tools_out.append({
                "alias": alias,
                "name": _norm(connection.get("name")) or alias,
                "kind": "python",
                "system": is_system_tool_alias(alias) or is_system_tool_alias(connection.get("name")),
                "tools": [
                    {"name": tool_name, "description": docs.get(tool_name, "")}
                    for tool_name in names
                ],
            })
            continue

        if kind == "mcp":
            server_id = _norm(
                connection.get("server_id") or connection.get("server") or connection.get("name")
            )
            if not server_id:
                continue
            allowed = _string_list(connection.get("allowed") or connection.get("tools")) or ["*"]
            entry: dict[str, Any] = {
                "server_id": server_id,
                "alias": alias or f"mcp_{server_id}",
                "name": _norm(connection.get("name")) or server_id,
                "tools": allowed,
            }
            # Concrete configured names give per-tool toggles with no handshake.
            # Wildcard servers get `tool_entries` best-effort via the cached
            # runtime listing (enrich_catalog_mcp_tools); absent entries keep
            # the server-level toggle only.
            if "*" not in allowed:
                entry["tool_entries"] = [{"name": name, "description": ""} for name in allowed]
            mcp_out.append(entry)
            continue

        if kind == "named_service":
            raw_namespaces = connection.get("namespaces")
            if not isinstance(raw_namespaces, Mapping):
                continue
            ns_alias = alias or NAMED_SERVICE_TOOLS_ALIAS
            for namespace, namespace_cfg in raw_namespaces.items():
                ns = _norm_namespace(namespace)
                if not ns or not isinstance(namespace_cfg, Mapping):
                    continue
                operations = _string_list(
                    namespace_cfg.get("allowed")
                    or namespace_cfg.get("allowed_operations")
                    or namespace_cfg.get("operations")
                )
                namespaces_out.append({
                    "namespace": ns,
                    "alias": ns_alias,
                    "operations": operations,
                    "tools": [
                        _NAMED_SERVICE_OPERATION_TO_TOOL[op]
                        for op in operations
                        if op in _NAMED_SERVICE_OPERATION_TO_TOOL
                    ],
                })
            continue

    skills_out = _catalog_skills(
        bundle_props,
        agent_id,
        bundle_root=bundle_root,
        default_agent_id=default_agent_id,
    )

    return {
        "agent": _norm(agent_id) or default_agent_id,
        "tools": tools_out,
        "mcp": mcp_out,
        "named_services": namespaces_out,
        "skills": skills_out,
        # Per-user model choice: the admin-allowed list (empty = the feature
        # stays invisible) and the configured default for the strong decision
        # role the pick overrides.
        "supported_models": react_supported_models(bundle_props, agent_id),
        "default_model": configured_strong_model(bundle_props, agent_id),
    }


# One-line descriptions for the generic named-service grammar, shown when a
# namespace row expands in the picker UI.
_NAMED_SERVICE_OPERATION_DESCRIPTIONS = {
    "provider.about": "What this service is and how to work it.",
    "provider.capabilities": "Provider-declared operations and behaviors.",
    "object.list": "List the namespace's objects (accounts, folders, channels...).",
    "object.search": "Search objects by query and filters.",
    "object.get": "Read one object by ref.",
    "object.schema": "Object shapes and refs for this namespace.",
    "object.upsert": "Create or update one object.",
    "object.action": "Run a named, bounded provider action.",
    "object.host_file": "Host a file into the namespace.",
    "object.delete": "Delete one object.",
}


def _operation_key_allowed(op_key: str, allowed_operations: Sequence[str]) -> bool:
    """`object.action.send` counts allowed when `object.action` is allowed."""
    key = str(op_key or "").strip()
    for allowed in allowed_operations or ():
        base = str(allowed or "").strip()
        if not base:
            continue
        if key == base or key.startswith(base + "."):
            return True
    return False


def _realm_requirement_effective_claims(
    requirement: Mapping[str, Any],
    allowed_operations: Sequence[str],
) -> list[str]:
    """The provider claims the ALLOWED operations actually need.

    A realm that differentiates declares `claims_by_operation`; the effective
    set is the union over allowed operation keys. A realm that declares one
    flat `claims` set shows that set — the catalog never invents granularity.
    """
    by_operation = requirement.get("claims_by_operation")
    if isinstance(by_operation, Mapping) and by_operation:
        claims: list[str] = []
        for op_key, op_claims in by_operation.items():
            if not _operation_key_allowed(str(op_key), allowed_operations):
                continue
            for claim in _string_list(op_claims):
                if claim not in claims:
                    claims.append(claim)
        return sorted(claims)
    return sorted(set(_string_list(requirement.get("claims"))))


def _realm_payload_from_spec(spec: Any, allowed_operations: Sequence[str]) -> dict[str, Any] | None:
    """The picker-facing view of one realm behind a configured namespace.

    Sourced from the provider's discovery spec — the same declaration surface
    its claim resolution uses: label/description, the named actions with
    their one-line descriptions, and the connected-account requirements
    scoped to the operations this configuration allows.
    """
    if spec is None:
        return None
    metadata = getattr(spec, "metadata", None)
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    allowed = [str(op or "").strip() for op in (allowed_operations or ()) if str(op or "").strip()]

    requirements_out: list[dict[str, Any]] = []
    raw_requirements = metadata.get("connected_accounts")
    by_operation_union: dict[str, list[str]] = {}
    if isinstance(raw_requirements, (list, tuple)):
        for raw in raw_requirements:
            if not isinstance(raw, Mapping):
                continue
            provider_id = _norm(raw.get("provider_id"))
            if not provider_id:
                continue
            effective = _realm_requirement_effective_claims(raw, allowed)
            if not effective:
                continue
            requirement_out: dict[str, Any] = {
                "provider_id": provider_id,
                "connector_app_id": _norm(raw.get("connector_app_id")),
                "claims": effective,
            }
            declared_by_op = raw.get("claims_by_operation")
            if isinstance(declared_by_op, Mapping) and declared_by_op:
                requirement_out["claims_by_operation"] = {
                    str(op_key): _string_list(op_claims)
                    for op_key, op_claims in declared_by_op.items()
                }
            requirements_out.append(requirement_out)
            if isinstance(declared_by_op, Mapping):
                for op_key, op_claims in declared_by_op.items():
                    merged = by_operation_union.setdefault(str(op_key), [])
                    for claim in _string_list(op_claims):
                        if claim not in merged:
                            merged.append(claim)

    # Human layer of the realm's self-description (the same contract the
    # agent reads): labels + user-terms descriptions per operation/action,
    # the purpose sentence, and the third-party dependency sentence. Only
    # declared text renders — a missing description is a realm defect fixed
    # at the source, never invented here.
    presentation = metadata.get("presentation")
    presentation = dict(presentation) if isinstance(presentation, Mapping) else {}
    presented_operations = presentation.get("operations")
    presented_operations = presented_operations if isinstance(presented_operations, Mapping) else {}
    presented_actions = presentation.get("actions")
    presented_actions = presented_actions if isinstance(presented_actions, Mapping) else {}

    # Per-entry third-party line from the declared provider/claim labels:
    # "via your connected Google account · send mail".
    def _via_line(claims: list[str] | None) -> str:
        if not claims or not isinstance(raw_requirements, (list, tuple)):
            return ""
        for raw in raw_requirements:
            if not isinstance(raw, Mapping):
                continue
            provider_label = _norm(raw.get("provider_label"))
            claim_labels = raw.get("claim_labels")
            claim_labels = claim_labels if isinstance(claim_labels, Mapping) else {}
            named = [str(claim_labels.get(claim) or "").strip() for claim in claims]
            named = [item for item in named if item]
            if provider_label and named:
                return f"via your connected {provider_label} account · {', '.join(named)}"
            if provider_label:
                return f"via your connected {provider_label} account"
        return ""

    actions_out: list[dict[str, Any]] = []
    if _operation_key_allowed("object.action", allowed):
        raw_actions = metadata.get("actions")
        if isinstance(raw_actions, Mapping):
            for name in sorted(raw_actions):
                presented = presented_actions.get(name)
                presented = presented if isinstance(presented, Mapping) else {}
                entry: dict[str, Any] = {
                    "name": str(name),
                    "description": _first_para(
                        str(presented.get("description") or raw_actions.get(name) or "")
                    ),
                }
                label_text = _norm(presented.get("label"))
                if label_text:
                    entry["label"] = label_text
                claims = by_operation_union.get(f"object.action.{name}")
                if claims:
                    entry["claims"] = sorted(claims)
                    via = _via_line(entry["claims"])
                    if via:
                        entry["via"] = via
                actions_out.append(entry)

    operations_out: list[dict[str, Any]] = []
    for op in allowed:
        if op == "object.action" and actions_out:
            continue  # the named actions expand this operation
        presented = presented_operations.get(op)
        presented = presented if isinstance(presented, Mapping) else {}
        entry = {
            "name": op,
            "description": _first_para(
                str(presented.get("description") or _NAMED_SERVICE_OPERATION_DESCRIPTIONS.get(op, ""))
            ),
        }
        label_text = _norm(presented.get("label"))
        if label_text:
            entry["label"] = label_text
        claims = by_operation_union.get(op)
        if claims:
            entry["claims"] = sorted(claims)
            via = _via_line(entry["claims"])
            if via:
                entry["via"] = via
        operations_out.append(entry)

    objects_out: list[dict[str, Any]] = []
    raw_object_kinds = metadata.get("object_kinds")
    if isinstance(raw_object_kinds, Mapping):
        for kind in sorted(raw_object_kinds):
            objects_out.append({
                "name": str(kind),
                "description": _first_para(str(raw_object_kinds.get(kind) or "")),
            })

    label = _norm(getattr(spec, "label", ""))
    description = _first_para(str(getattr(spec, "description", "") or ""))
    about = _first_para(str(presentation.get("about") or "")) or description
    third_party = _first_para(str(presentation.get("third_party") or ""))
    if not (label or description or requirements_out or actions_out):
        return None
    payload: dict[str, Any] = {
        "label": label,
        "description": description,
        "about": about,
        "operations": operations_out,
        "actions": actions_out,
    }
    if third_party:
        payload["third_party"] = third_party
    if objects_out:
        payload["objects"] = objects_out
    if requirements_out:
        payload["connected_accounts"] = requirements_out
    return payload


async def enrich_catalog_named_service_realms(
    catalog: dict[str, Any],
    *,
    discovery: Any = None,
    tenant: str = "",
    project: str = "",
) -> dict[str, Any]:
    """Attach each configured namespace's realm view to the catalog.

    Resolution goes through named-service discovery (the registry every
    provider publishes its spec to); a namespace with no resolvable realm
    keeps its plain row. Fail-open throughout — a menu render asks nothing.
    """
    entries = [e for e in (catalog.get("named_services") or []) if isinstance(e, dict)]
    if not entries:
        return catalog
    try:
        if discovery is None:
            from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import (
                RedisNamedServiceDiscovery,
                _redis_client_from_settings,
            )

            if not tenant or not project:
                return catalog
            discovery = RedisNamedServiceDiscovery(
                _redis_client_from_settings(), tenant=tenant, project=project,
            )
        for entry in entries:
            namespace = _norm_namespace(entry.get("namespace"))
            if not namespace:
                continue
            try:
                found = await discovery.entries_for_namespace(namespace)
            except Exception:
                continue
            spec = next(
                (item.spec for item in (found or []) if getattr(item, "spec", None) is not None),
                None,
            )
            realm = _realm_payload_from_spec(spec, _string_list(entry.get("operations")))
            if realm:
                entry["realm"] = realm
    except Exception:
        pass
    return catalog


def namespace_claim_policies(
    catalog: Mapping[str, Any] | None,
    disabled: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Namespace-named claim policies for coverage, over the NARROWED set.

    Effective claims recompute against the operations/actions the user kept:
    a realm that differentiates (`claims_by_operation`) loses the claims whose
    every carrying operation is denied — a user who denied `object.action.send`
    is never asked for the send claim. A realm with one flat claim set keeps
    it whole (honest: the realm declared no per-operation split) unless the
    namespace itself is fully denied. Returns `{tool_name, connected_accounts}`
    config dicts ready for ``ToolClaimPolicy.from_config``.
    """
    fully_denied, per_entry_denied = disabled_namespace_maps(disabled)
    out: list[dict[str, Any]] = []
    for entry in (catalog or {}).get("named_services") or []:
        if not isinstance(entry, Mapping):
            continue
        namespace = _norm_namespace(entry.get("namespace"))
        realm = entry.get("realm") if isinstance(entry.get("realm"), Mapping) else {}
        requirements = realm.get("connected_accounts") or []
        if not namespace or not requirements or namespace in fully_denied:
            continue
        denied_keys = per_entry_denied.get(namespace) or set()
        allowed_ops = [
            op for op in _string_list(entry.get("operations"))
            if op not in denied_keys
        ]
        effective_requirements: list[dict[str, Any]] = []
        for raw in requirements:
            if not isinstance(raw, Mapping):
                continue
            by_operation = raw.get("claims_by_operation")
            if isinstance(by_operation, Mapping) and by_operation:
                claims: list[str] = []
                for op_key, op_claims in by_operation.items():
                    key = str(op_key)
                    if key in denied_keys:
                        continue
                    if not _operation_key_allowed(key, allowed_ops):
                        continue
                    for claim in _string_list(op_claims):
                        if claim not in claims:
                            claims.append(claim)
                claims = sorted(claims)
            else:
                claims = sorted(set(_string_list(raw.get("claims"))))
            if not claims:
                continue
            effective_requirements.append({
                "provider_id": _norm(raw.get("provider_id")),
                "connector_app_id": _norm(raw.get("connector_app_id")),
                "claims": claims,
            })
        if effective_requirements:
            out.append({
                "tool_name": namespace,
                "connected_accounts": effective_requirements,
            })
    return out


def _mcp_services_config_from_props(bundle_props: Mapping[str, Any] | None) -> Any:
    """MCP services config as the runtime resolves it (parity with
    BaseWorkflow._resolve_mcp_services_config, trimmed to the mapping forms)."""
    props = bundle_props or {}

    def _get(path: str) -> Any:
        cur: Any = props
        for part in path.split("."):
            if not isinstance(cur, Mapping) or part not in cur:
                return None
            cur = cur[part]
        return cur

    for base in ("surfaces.as_consumer.mcp", "mcp"):
        raw = _get(f"{base}.services")
        if isinstance(raw, Mapping) and raw:
            return dict(raw)
        if isinstance(raw, str) and raw.strip():
            return raw
        block = _get(base)
        if isinstance(block, Mapping):
            if isinstance(block.get("mcpServers"), Mapping) and block.get("mcpServers"):
                return {"mcpServers": dict(block["mcpServers"])}
            if isinstance(block.get("servers"), Mapping) and block.get("servers"):
                return {"servers": dict(block["servers"])}
    raw = _get("mcp_services")
    if isinstance(raw, Mapping) and raw:
        return dict(raw)
    return None


async def enrich_catalog_mcp_tools(
    catalog: dict[str, Any],
    bundle_props: Mapping[str, Any] | None,
    *,
    bundle_id: str = "",
    timeout_seconds: float = 2.5,
) -> dict[str, Any]:
    """Best-effort per-tool listings for wildcard MCP servers (in place).

    Uses the runtime MCP subsystem's redis-cached `list_tools` (a cache hit is
    a plain read; a miss does one short live listing bounded by
    ``timeout_seconds``). Any failure leaves the server without
    ``tool_entries`` — the picker then offers the server-level toggle only.
    """
    pending = [
        entry for entry in catalog.get("mcp") or []
        if isinstance(entry, dict) and not entry.get("tool_entries")
    ]
    if not pending:
        return catalog
    try:
        import asyncio

        from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_tools_subsystem import (
            MCPToolsSubsystem,
        )

        services_config = _mcp_services_config_from_props(bundle_props)
        # One single-server listing per pending entry so results attribute to
        # their server; each is individually bounded and individually optional.
        for entry in pending:
            try:
                subsystem = MCPToolsSubsystem(
                    bundle_id=str(bundle_id or "default"),
                    mcp_tool_specs=[
                        {"mcp": {"server_id": entry["server_id"], "alias": entry.get("alias"), "tools": ["*"]}}
                    ],
                    services_config=services_config,
                )
                tools = await asyncio.wait_for(
                    subsystem.list_tools(),
                    timeout=max(0.1, timeout_seconds),
                )
            except Exception:
                continue
            listed = [
                {
                    "name": _norm(getattr(tool, "id", "") or getattr(tool, "name", "")),
                    "description": _first_para(str(getattr(tool, "description", "") or "")),
                }
                for tool in tools
                if _norm(getattr(tool, "id", "") or getattr(tool, "name", ""))
            ]
            if listed:
                entry["tool_entries"] = listed
    except Exception:
        # Graceful fallback: server-level toggles only.
        pass
    return catalog


def _skill_enabled_patterns(skill_config: AgentSkillConfig) -> list[str]:
    patterns: list[str] = []
    for cfg in (skill_config.agents_config or {}).values():
        for pat in _string_list((cfg or {}).get("enabled")):
            if pat not in patterns:
                patterns.append(pat)
    return patterns


def _catalog_skills(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
    *,
    bundle_root: str | pathlib.Path | None,
    default_agent_id: str,
) -> list[dict[str, Any]]:
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import (
            agent_skill_config_from_bundle_props,
        )
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem

        skill_config = agent_skill_config_from_bundle_props(
            bundle_props,
            agent_id,
            bundle_root=bundle_root,
            default_agent_id=default_agent_id,
        )
        if skill_config.custom_skills_root == "":
            # Skills surface explicitly disabled for this agent.
            custom_root = None
        else:
            custom_root = skill_config.custom_skills_root
        subsystem = SkillsSubsystem(
            descriptor={
                "custom_skills_root": str(custom_root) if custom_root else None,
                "agents_config": dict(skill_config.agents_config or {}),
            },
            bundle_root=pathlib.Path(bundle_root) if bundle_root else None,
        )
        return subsystem.picker_catalog(_skill_enabled_patterns(skill_config))
    except Exception:
        return []


# ── selection clamp (write-side guard) ───────────────────────────────────────


def clamp_selection(
    disabled: Mapping[str, Any] | None,
    catalog: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Sanitize a deny-list so it never references anything outside the live
    inventory; system tool aliases are stripped (locked on)."""
    catalog = catalog or {}
    disabled = disabled or {}

    tool_names_by_alias: dict[str, set[str]] = {}
    system_aliases: set[str] = set(SYSTEM_TOOL_ALIASES)
    for group in catalog.get("tools") or []:
        alias = _norm((group or {}).get("alias"))
        if not alias:
            continue
        if bool((group or {}).get("system")):
            system_aliases.add(alias)
        tool_names_by_alias[alias] = {
            _norm(t.get("name")) for t in (group.get("tools") or []) if _norm(t.get("name"))
        }
    mcp_servers = {_norm(e.get("server_id")) for e in (catalog.get("mcp") or []) if _norm(e.get("server_id"))}
    mcp_tool_names: dict[str, set[str]] = {
        _norm(e.get("server_id")): {
            _norm(t.get("name")) for t in (e.get("tool_entries") or []) if _norm(t.get("name"))
        }
        for e in (catalog.get("mcp") or [])
        if _norm(e.get("server_id"))
    }
    namespaces = {
        _norm_namespace(e.get("namespace"))
        for e in (catalog.get("named_services") or [])
        if _norm_namespace(e.get("namespace"))
    }
    skill_ids = {_norm(s.get("id")) for s in (catalog.get("skills") or []) if _norm(s.get("id"))}

    out_tools: dict[str, Any] = {}
    raw_tools = disabled.get("tools")
    if isinstance(raw_tools, Mapping):
        for alias, value in raw_tools.items():
            alias = _norm(alias)
            if not alias or alias in system_aliases or alias not in tool_names_by_alias:
                continue
            if value is True:
                out_tools[alias] = True
                continue
            names = [n for n in _string_list(value) if n in tool_names_by_alias[alias]]
            if names:
                out_tools[alias] = names

    out_mcp: dict[str, Any] = {}
    raw_mcp = disabled.get("mcp")
    if isinstance(raw_mcp, Mapping):
        for server_id, value in raw_mcp.items():
            server_id = _norm(server_id)
            if not server_id or server_id not in mcp_servers:
                continue
            if value is True:
                out_mcp[server_id] = True
                continue
            # Per-tool MCP denial: only names the inventory actually lists
            # (config allow-list or the cached listing). No known names =>
            # only the server-level toggle exists.
            known = mcp_tool_names.get(server_id) or set()
            names = [n for n in _string_list(value) if n in known]
            if names:
                out_mcp[server_id] = names

    # Known deny keys per namespace: the configured operations plus the
    # realm's named actions as `object.action.<name>` — the same key grammar
    # the runtime dispatch enforces.
    namespace_entry_keys: dict[str, set[str]] = {}
    for e in catalog.get("named_services") or []:
        ns = _norm_namespace(e.get("namespace"))
        if not ns:
            continue
        keys = set(_string_list(e.get("operations")))
        realm = e.get("realm") if isinstance(e.get("realm"), Mapping) else {}
        for action in realm.get("actions") or []:
            name = _norm((action or {}).get("name")) if isinstance(action, Mapping) else ""
            if name:
                keys.add(f"object.action.{name}")
        namespace_entry_keys[ns] = keys

    out_namespaces: dict[str, Any] = {}
    raw_namespaces = disabled.get("named_services")
    if isinstance(raw_namespaces, Mapping):
        for namespace, value in raw_namespaces.items():
            namespace = _norm_namespace(namespace)
            if not namespace or namespace not in namespaces:
                continue
            if value is True:
                out_namespaces[namespace] = True
                continue
            known = namespace_entry_keys.get(namespace) or set()
            keys = [k for k in _string_list(value) if k in known]
            if keys:
                out_namespaces[namespace] = keys

    out_skills: list[str] = []
    for skill_id in _string_list(disabled.get("skills")):
        if skill_id in skill_ids and skill_id not in out_skills:
            out_skills.append(skill_id)

    out: dict[str, Any] = {}
    if out_tools:
        out["tools"] = out_tools
    if out_mcp:
        out["mcp"] = out_mcp
    if out_namespaces:
        out["named_services"] = out_namespaces
    if out_skills:
        out["skills"] = out_skills
    return out


# ── narrowing (read-side application; effective = configured − disabled) ─────


def disabled_namespace_maps(
    disabled: Mapping[str, Any] | None,
) -> tuple[set[str], dict[str, set[str]]]:
    """Split the named_services deny map into fully-denied namespaces and
    per-entry (operation / `object.action.<name>`) denials."""
    fully: set[str] = set()
    per_entry: dict[str, set[str]] = {}
    raw = (disabled or {}).get("named_services")
    if isinstance(raw, Mapping):
        for namespace, value in raw.items():
            ns = _norm_namespace(namespace)
            if not ns:
                continue
            if value is True:
                fully.add(ns)
            else:
                keys = set(_string_list(value))
                if keys:
                    per_entry[ns] = keys
    return fully, per_entry


def _disabled_tool_maps(disabled: Mapping[str, Any] | None) -> tuple[set[str], dict[str, set[str]]]:
    fully: set[str] = set()
    per_tool: dict[str, set[str]] = {}
    raw = (disabled or {}).get("tools")
    if isinstance(raw, Mapping):
        for alias, value in raw.items():
            alias = _norm(alias)
            if not alias or alias in SYSTEM_TOOL_ALIASES:
                continue
            if value is True:
                fully.add(alias)
            else:
                names = set(_string_list(value))
                if names:
                    per_tool[alias] = names
    return fully, per_tool


def _disabled_mcp_maps(disabled: Mapping[str, Any] | None) -> tuple[set[str], dict[str, set[str]]]:
    """Split the mcp deny map into fully-denied servers and per-tool denials."""
    fully: set[str] = set()
    per_tool: dict[str, set[str]] = {}
    raw = (disabled or {}).get("mcp")
    if isinstance(raw, Mapping):
        for server_id, value in raw.items():
            server_id = _norm(server_id)
            if not server_id:
                continue
            if value is True:
                fully.add(server_id)
            else:
                names = set(_string_list(value))
                if names:
                    per_tool[server_id] = names
    return fully, per_tool


def _disabled_flag_set(disabled: Mapping[str, Any] | None, key: str, *, namespace: bool = False) -> set[str]:
    raw = (disabled or {}).get(key)
    out: set[str] = set()
    if isinstance(raw, Mapping):
        for name, value in raw.items():
            text = _norm_namespace(name) if namespace else _norm(name)
            if text and value:
                out.add(text)
    return out


def _materialize_alias_tool_names(cfg: AgentToolConfig, alias: str) -> list[str] | None:
    """Expand a None (wildcard) configured allowed list to concrete tool names."""
    for spec in cfg.tool_specs:
        if _norm(spec.get("alias")) != alias:
            continue
        module = _norm(spec.get("module"))
        if module:
            return _module_tool_names(module)
        return None
    return None


def _named_service_aliases(cfg: AgentToolConfig) -> set[str]:
    aliases: set[str] = set()
    for spec in cfg.tool_specs:
        if _norm(spec.get("module")) == NAMED_SERVICE_TOOLS_MODULE:
            alias = _norm(spec.get("alias"))
            if alias:
                aliases.add(alias)
    return aliases


def _recomputed_named_service_tools(
    bundle_props: Mapping[str, Any] | None,
    *,
    agent_id: str | None,
    default_agent_id: str,
    denied_namespaces: set[str],
) -> dict[str, list[str]]:
    """``{alias: [tool names]}`` union over the ENABLED namespaces only."""
    out: dict[str, list[str]] = {}
    for connection in _agent_tool_connections(
        bundle_props,
        agent_id=agent_id,
        default_agent_id=default_agent_id,
    ):
        if str(connection.get("kind") or "python").strip().lower() != "named_service":
            continue
        alias = _norm(connection.get("alias") or connection.get("name")) or NAMED_SERVICE_TOOLS_ALIAS
        raw_namespaces = connection.get("namespaces")
        if not isinstance(raw_namespaces, Mapping):
            continue
        enabled_only = {
            ns: ns_cfg
            for ns, ns_cfg in raw_namespaces.items()
            if _norm_namespace(ns) not in denied_namespaces
        }
        tools = _named_service_tools_for_connection({"namespaces": enabled_only})
        bucket = out.setdefault(alias, [])
        for tool_name in tools:
            if tool_name not in bucket:
                bucket.append(tool_name)
    return out


def _prune_tool_id_keys(mapping: Mapping[str, Any], removed_aliases: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tool_id, value in mapping.items():
        alias = str(tool_id).split(".", 1)[0]
        mcp_alias = ""
        if str(tool_id).startswith("mcp."):
            parts = str(tool_id).split(".", 2)
            mcp_alias = parts[1] if len(parts) > 1 else ""
        if alias in removed_aliases or (mcp_alias and mcp_alias in removed_aliases):
            continue
        out[tool_id] = value
    return out


def narrow_agent_tool_config(
    cfg: AgentToolConfig,
    disabled: Mapping[str, Any] | None,
    *,
    bundle_props: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    default_agent_id: str = DEFAULT_AGENT_ID,
) -> AgentToolConfig:
    """Return a narrowed copy of ``cfg`` (effective = configured − disabled).

    Pure: never widens, never mutates ``cfg``. System tool aliases are immune.
    ``bundle_props``/``agent_id`` are needed only to recompute the
    named-service tool allowlist over the enabled namespaces.
    """
    if not disabled:
        return cfg

    fully_disabled, per_tool_disabled = _disabled_tool_maps(disabled)
    denied_servers, mcp_per_tool_disabled = _disabled_mcp_maps(disabled)
    # Only FULL namespace denials remove tools from the grammar; per-entry
    # denials are enforced at dispatch (a per-entry list is NOT a full deny).
    denied_namespaces, _ = disabled_namespace_maps(disabled)

    removed_aliases: set[str] = set(fully_disabled)
    allowed_map: dict[str, list[str] | None] = {
        alias: (list(names) if names is not None else None)
        for alias, names in cfg.allowed_tool_names_by_alias.items()
    }
    mcp_denied_tool_ids: set[str] = set()

    # MCP: drop denied servers whole; subtract per-tool denials — from the
    # spec's concrete allow-list when configured, else via the spec's
    # `denied_tools` deny-list the MCP subsystem applies after listing (a
    # wildcard allow stays a wildcard: new server tools default ON).
    new_mcp_specs: list[dict[str, Any]] = []
    for spec in cfg.mcp_tool_specs:
        server_id = _norm(spec.get("server_id"))
        alias = _norm(spec.get("alias")) or f"mcp_{server_id}"
        if server_id in denied_servers:
            removed_aliases.add(alias)
            continue
        new_spec = dict(spec)
        denied_names = mcp_per_tool_disabled.get(server_id) or set()
        if denied_names:
            configured = _string_list(new_spec.get("tools"))
            if configured and "*" not in configured:
                effective = [name for name in configured if name not in denied_names]
                if not effective:
                    removed_aliases.add(alias)
                    continue
                new_spec["tools"] = effective
            else:
                existing = set(_string_list(new_spec.get("denied_tools")))
                new_spec["denied_tools"] = sorted(existing | denied_names)
            current = allowed_map.get(alias)
            if isinstance(current, list) and "*" not in current:
                effective = [name for name in current if name not in denied_names]
                if effective:
                    allowed_map[alias] = effective
            for name in denied_names:
                mcp_denied_tool_ids.add(f"{alias}.{name}")
                mcp_denied_tool_ids.add(f"mcp.{alias}.{name}")
        new_mcp_specs.append(new_spec)

    # Named service: recompute the tool allowlist over enabled namespaces only.
    ns_aliases = _named_service_aliases(cfg)
    if denied_namespaces and ns_aliases:
        recomputed = _recomputed_named_service_tools(
            bundle_props,
            agent_id=agent_id,
            default_agent_id=default_agent_id,
            denied_namespaces=denied_namespaces,
        ) if bundle_props is not None else {}
        for alias in ns_aliases:
            if alias in removed_aliases:
                continue
            if bundle_props is None:
                # Cannot recompute per-namespace tools without the inventory;
                # fail open for this alias (dispatch-time namespace deny still
                # applies via the runtime deny-set hook).
                continue
            tools = recomputed.get(alias) or []
            if tools:
                allowed_map[alias] = tools
            else:
                removed_aliases.add(alias)

    # Python per-tool denials (materialize wildcard entries first).
    for alias, denied_names in per_tool_disabled.items():
        if alias in removed_aliases or alias not in allowed_map:
            continue
        configured = allowed_map.get(alias)
        if configured is None:
            configured = _materialize_alias_tool_names(cfg, alias)
            if configured is None:
                # Unknowable wildcard: fail open for this alias.
                continue
        effective = [name for name in configured if name not in denied_names]
        if effective:
            allowed_map[alias] = effective
        else:
            removed_aliases.add(alias)

    new_tool_specs = [
        dict(spec) for spec in cfg.tool_specs if _norm(spec.get("alias")) not in removed_aliases
    ]
    new_allowed_plugins = [alias for alias in cfg.allowed_plugins if alias not in removed_aliases]
    new_allowed_map = {
        alias: (list(names) if names is not None else None)
        for alias, names in allowed_map.items()
        if alias not in removed_aliases
    }

    # Drop runtime/traits/claim policies for removed aliases and denied tools,
    # so e.g. connected-account preflight never demands consent for a tool the
    # user turned off.
    denied_tool_ids = {
        f"{alias}.{name}" for alias, names in per_tool_disabled.items() for name in names
    } | mcp_denied_tool_ids
    new_tool_runtime = {
        tool_id: mode
        for tool_id, mode in _prune_tool_id_keys(cfg.tool_runtime, removed_aliases).items()
        if tool_id not in denied_tool_ids
    }
    new_tool_traits = {
        tool_id: dict(traits)
        for tool_id, traits in _prune_tool_id_keys(cfg.tool_traits, removed_aliases).items()
        if tool_id not in denied_tool_ids
    }
    new_claim_policies = []
    for policy in cfg.tool_claim_policies:
        tool_name = _norm(getattr(policy, "tool_name", ""))
        alias = tool_name.split(".", 1)[0]
        mcp_alias = tool_name.split(".", 2)[1] if tool_name.startswith("mcp.") and tool_name.count(".") >= 2 else ""
        if alias in removed_aliases or (mcp_alias and mcp_alias in removed_aliases):
            continue
        if tool_name in denied_tool_ids:
            continue
        new_claim_policies.append(policy)

    return AgentToolConfig(
        tool_specs=new_tool_specs,
        mcp_tool_specs=new_mcp_specs,
        tool_runtime=new_tool_runtime,
        tool_traits=new_tool_traits,
        allowed_plugins=new_allowed_plugins,
        allowed_tool_names_by_alias=new_allowed_map,
        tool_claim_policies=new_claim_policies,
    )


def narrow_agent_skill_config(
    cfg: AgentSkillConfig,
    disabled_skills: Sequence[str] | None,
) -> AgentSkillConfig:
    """Return a copy of ``cfg`` with the denied skill ids appended to every
    consumer's disabled list, plus the ``"*"`` catch-all consumer so agents
    without per-consumer entries still honour the denial."""
    denied = _string_list(disabled_skills)
    if not denied:
        return cfg
    agents_config: dict[str, dict[str, Any]] = {
        consumer: dict(entry or {}) for consumer, entry in (cfg.agents_config or {}).items()
    }
    for consumer in [*agents_config.keys(), "*"]:
        entry = agents_config.setdefault(consumer, {})
        merged = _string_list(entry.get("disabled"))
        for skill_id in denied:
            if skill_id not in merged:
                merged.append(skill_id)
        entry["disabled"] = merged
    return AgentSkillConfig(
        custom_skills_root=cfg.custom_skills_root,
        agents_config=agents_config,
    )


def selection_deltas(disabled: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compact, log-friendly summary of what a selection turns off."""
    fully, per_tool = _disabled_tool_maps(disabled)
    return {
        "tools_off": sorted(fully),
        "tool_names_off": {alias: sorted(names) for alias, names in per_tool.items()},
        "mcp_off": sorted(_disabled_flag_set(disabled, "mcp")),
        "named_services_off": sorted(_disabled_flag_set(disabled, "named_services", namespace=True)),
        "skills_off": _string_list((disabled or {}).get("skills")),
    }


# Public name for the light per-module tool-doc introspection so app bundles
# (e.g. user-automation's picker) reuse it instead of re-implementing.
module_tool_docs = _module_tool_docs

__all__ = [
    "DEFAULT_SELECTION_CHANGE_POLICY",
    "SELECTION_CHANGE_CAPABILITY",
    "SELECTION_CHANGE_MODEL",
    "SELECTION_CHANGE_POLICIES",
    "SYSTEM_TOOL_ALIASES",
    "USER_MODEL_TARGET_ROLE",
    "agent_capabilities_catalog",
    "clamp_cache_policy",
    "clamp_selection",
    "classify_selection_change",
    "configured_strong_model",
    "effective_selection_change_policy",
    "enrich_catalog_mcp_tools",
    "is_system_tool_alias",
    "match_supported_model",
    "module_tool_docs",
    "narrow_agent_skill_config",
    "narrow_agent_tool_config",
    "normalize_model_pick",
    "react_selection_change_policy",
    "react_supported_models",
    "selection_deltas",
    "selection_snapshot",
]
