"""The per-agent tool inventory + per-user narrowing (platform/tool_pick.py).

Exercises the runtime half of the tool picker offline: given the admin-declared
connection list (the ceiling) and a user's saved deny-map, bind EXACTLY the
declared, user-enabled tools — a tool the admin omits is never bound (hard off),
a declared tool the user opts out of is dropped, and run_python appears only when
the code-exec connection is declared.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _tool_pick():
    _name, module = load_dynamic_module_for_path(BUNDLE_ROOT / "platform" / "tool_pick.py")
    return module


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _registry():
    return {"calc": _FakeTool("calc"), "unit_convert": _FakeTool("unit_convert"), "kb_search": _FakeTool("kb_search")}


def _run_python_factory():
    return _FakeTool("run_python")


def _names(tools):
    return [t.name for t in tools]


_PLAIN_CONNS = [
    {"name": "calc", "kind": "python", "alias": "calc", "allowed": ["calc"]},
    {"name": "unit_convert", "kind": "python", "alias": "unit_convert", "allowed": ["unit_convert"]},
    {"name": "kb_search", "kind": "python", "alias": "kb_search", "allowed": ["kb_search"]},
]
_EXEC_CONN = {"name": "code_exec", "kind": "python", "alias": "code_exec", "allowed": ["run_python"], "code_exec": {"timeout_s": 90}}


def _bind(mod, connections, disabled):
    return _names(mod.select_bound_tools(
        connections, disabled, plain_registry=_registry(), run_python_factory=_run_python_factory,
    ))


def test_admin_ceiling_binds_only_declared_tools() -> None:
    mod = _tool_pick()
    # Only calc + kb_search declared -> unit_convert (undeclared) is never bound.
    conns = [_PLAIN_CONNS[0], _PLAIN_CONNS[2]]
    assert _bind(mod, conns, {}) == ["calc", "kb_search"]


def test_code_exec_binds_run_python_only_when_declared() -> None:
    mod = _tool_pick()
    assert "run_python" not in _bind(mod, _PLAIN_CONNS, {})               # no exec connection
    assert "run_python" in _bind(mod, _PLAIN_CONNS + [_EXEC_CONN], {})    # declared
    assert mod.code_exec_connection(_PLAIN_CONNS) is None
    assert mod.code_exec_connection(_PLAIN_CONNS + [_EXEC_CONN]) is not None


def test_user_opt_out_drops_a_declared_tool() -> None:
    mod = _tool_pick()
    conns = _PLAIN_CONNS + [_EXEC_CONN]
    # Whole-alias opt-out (true) and per-tool opt-out both drop the tool.
    assert _bind(mod, conns, {"code_exec": True}) == ["calc", "unit_convert", "kb_search"]
    assert "unit_convert" not in _bind(mod, conns, {"unit_convert": ["unit_convert"]})


def test_opt_out_cannot_widen_beyond_ceiling() -> None:
    mod = _tool_pick()
    # A deny-map referencing an UNDECLARED alias is a no-op (never widens/introduces).
    assert _bind(mod, [_PLAIN_CONNS[0]], {"kb_search": True}) == ["calc"]


def test_bound_order_follows_declaration() -> None:
    mod = _tool_pick()
    conns = [_PLAIN_CONNS[2], _PLAIN_CONNS[0], _EXEC_CONN]  # kb_search, calc, exec
    assert _bind(mod, conns, {}) == ["kb_search", "calc", "run_python"]
