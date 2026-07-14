# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── platform/tool_pick.py ── the per-agent tool inventory + per-user narrowing ──
#
# lg-react's tools are DECLARED as a connection list under
# `surfaces.as_consumer.agents.<id>.tools` — the SAME standard KDCube shape the
# capabilities catalog reads (like the workspace `main` agent). That declaration is
# the ADMIN CEILING and drives the picker natively: the `agent_capabilities` op
# lists the tools, the widget renders per-tool toggles, and a user's opt-outs are
# stored as a deny-map (`disabled.tools.<alias>: true | [names]`) by the platform.
#
# This module is the RUNTIME half (approach B — "reuse the declaration"): it reads
# the declared ceiling and, given the user's saved deny-map, binds EXACTLY the
# bundle's own LangChain @tool objects the admin allowed AND the user left enabled.
# No tool-subsystem load here — the tool manager can be adopted later for anything
# the declaration alone can't express (per-tool runtime, SK discovery); for now the
# declaration is enough to make the tools pickable.
#
# Ceiling semantics: a tool the admin does NOT declare is never bound (hard off);
# a declared tool is on by default and the user can opt out per conversation.

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

# The alias the code-execution connection is declared under (its tool is run_python).
CODE_EXEC_ALIAS = "code_exec"
RUN_PYTHON_TOOL = "run_python"


def agent_tool_connections(ep: Any, agent_id: str) -> List[Dict[str, Any]]:
    """The declared tool connection list for one agent (empty when none/malformed)."""
    try:
        raw = ep.bundle_prop(f"surfaces.as_consumer.agents.{agent_id}.tools", []) or []
    except Exception:
        raw = []
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def _conn_alias(conn: Mapping[str, Any]) -> str:
    return str(conn.get("alias") or conn.get("name") or "").strip()


def _conn_tool_names(conn: Mapping[str, Any]) -> List[str]:
    """The tool names a connection exposes: its `allowed` list, else its alias."""
    allowed = conn.get("allowed")
    if isinstance(allowed, list) and allowed:
        return [str(x).strip() for x in allowed if str(x).strip()]
    alias = _conn_alias(conn)
    return [alias] if alias else []


def python_tool_allowlist(connections: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """alias -> [tool names] for every `kind: python` connection (the admin ceiling).
    Ordered as declared, so the bound tool order is stable and operator-controlled."""
    out: Dict[str, List[str]] = {}
    for conn in connections:
        if str(conn.get("kind") or "python").strip().lower() != "python":
            continue
        alias = _conn_alias(conn)
        if alias:
            out[alias] = _conn_tool_names(conn)
    return out


def code_exec_connection(connections: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The declared code-execution connection (alias `code_exec` or one exposing
    `run_python`), or None when the admin did not declare it (code exec hard off)."""
    for conn in connections:
        if _conn_alias(conn) == CODE_EXEC_ALIAS or RUN_PYTHON_TOOL in _conn_tool_names(conn):
            return conn
    return None


def disabled_tool_names(
    allowlist: Dict[str, List[str]], disabled_map: Optional[Mapping[str, Any]]
) -> set:
    """Flatten the platform deny-map (`{alias: true | [names]}`) to a set of tool
    names, resolved against the ceiling so an alias->true disables all its tools."""
    out: set = set()
    for alias, spec in (disabled_map or {}).items():
        names = allowlist.get(str(alias))
        if names is None:
            continue
        if spec is True:
            out.update(names)
        elif isinstance(spec, list):
            out.update(str(x).strip() for x in spec if str(x).strip())
    return out


def select_bound_tools(
    connections: List[Dict[str, Any]],
    disabled_map: Optional[Mapping[str, Any]],
    *,
    plain_registry: Mapping[str, Any],
    run_python_factory: Callable[[], Any],
) -> List[Any]:
    """Bind EXACTLY the declared, user-enabled tools (the picker's runtime half).

    For each `kind: python` connection, in declared order, bind each of its tool
    names that the user has not opted out of: a name in the plain registry binds
    that @tool; `run_python` binds a fresh code-exec tool. A tool the admin did not
    declare is never built (hard ceiling); a user-disabled tool is skipped."""
    allowlist = python_tool_allowlist(connections)
    disabled = disabled_tool_names(allowlist, disabled_map)
    bound: List[Any] = []
    for names in allowlist.values():
        for name in names:
            if name in disabled:
                continue
            if name in plain_registry:
                bound.append(plain_registry[name])
            elif name == RUN_PYTHON_TOOL:
                bound.append(run_python_factory())
    return bound
