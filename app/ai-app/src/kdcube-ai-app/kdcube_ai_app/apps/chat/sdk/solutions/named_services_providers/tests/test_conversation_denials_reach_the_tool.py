# SPDX-License-Identifier: MIT
"""A conversation's named-service denials reach the tool.

The tool subsystem loads ``named_services_providers/tools.py`` as a dynamic copy
(``runtime/tool_subsystem.py::_load_tools_module``, synthetic package
``dynpkg_<hash>``). A relative import inside that copy loaded a second
``client_tools`` whose deny set nobody wrote, so ``_operation_allowed`` allowed
every operation the conversation had turned off. The denials now travel in the
bundle call context, and the SDK imports by absolute name.
"""

from __future__ import annotations

import contextvars
import importlib.util
import json
import sys

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path
from kdcube_ai_app.apps.chat.sdk.runtime.snapshot import _encode_value
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import client_tools

TOOLS_MODULE = "kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.tools"


@pytest.fixture
def dynamic_tools():
    """``tools.py`` loaded the way the tool subsystem loads it."""
    before = set(sys.modules)
    path = importlib.util.find_spec(TOOLS_MODULE).origin
    name, module = load_dynamic_module_for_path(path)
    assert name.startswith("dynpkg_") or name.startswith("dyn_")
    yield module
    for key in set(sys.modules) - before:
        if key.startswith(("dynpkg_", "dyn_")):
            sys.modules.pop(key, None)


def _in_fresh_context(fn):
    return contextvars.Context().run(fn)


def test_the_dynamic_copy_reads_the_denials_the_turn_wrote(dynamic_tools):
    def turn():
        client_tools.set_denied_named_service_entries({"docs": ["object.schema"]})
        return (
            dynamic_tools._operation_allowed("docs", "object.schema"),
            dynamic_tools.denied_named_service_entries("docs"),
        )

    allowed, seen = _in_fresh_context(turn)
    assert seen == frozenset({"object.schema"})
    assert allowed is False


def test_the_dynamic_copy_shares_the_canonical_client_tools(dynamic_tools):
    """No second generation of the shared-state module (relative import)."""
    assert dynamic_tools.denied_named_service_entries is client_tools.denied_named_service_entries
    assert (
        dynamic_tools.named_service_namespace_client_tools_config
        is client_tools.named_service_namespace_client_tools_config
    )
    assert not any(key.endswith(".client_tools") and key.startswith("dynpkg_") for key in sys.modules)


_PROPS = {
    "tools": {
        "agents": {
            client_tools.DEFAULT_AGENT_TOOLS_ID: [
                {"kind": "named_service", "namespaces": {"docs": {"allowed": ["provider.about", "object.schema"]}}}
            ]
        }
    }
}


def test_a_namespace_denial_hides_the_namespace_from_the_copy(dynamic_tools):
    def config(denied):
        def turn():
            client_tools.set_denied_named_service_namespaces(denied)
            return dynamic_tools.named_service_namespace_client_tools_config(
                _PROPS, namespace="docs", client_id=client_tools.DEFAULT_AGENT_TOOLS_ID
            )

        return _in_fresh_context(turn)

    assert config(None), "the namespace has a tool surface while it is not denied"
    assert config(["docs"]) == {}


def test_the_denials_survive_the_portable_round_trip():
    """A subprocess or ISO runtime restores them with the comm context."""

    def host():
        client_tools.set_denied_named_service_namespaces(["mail"])
        client_tools.set_denied_named_service_entries({"docs": ["object.schema", "object.get"]})
        return json.loads(json.dumps(comm_ctx.snapshot_ctxvars(), default=str))

    snapshot = _in_fresh_context(host)

    def child():
        comm_ctx.restore_ctxvars(snapshot)
        return (
            client_tools.denied_named_service_namespaces(),
            client_tools.denied_named_service_entries("docs"),
        )

    namespaces, entries = _in_fresh_context(child)
    assert namespaces == frozenset({"mail"})
    assert entries == frozenset({"object.schema", "object.get"})


def test_clearing_one_deny_set_keeps_the_other_and_the_rest_of_the_call_context():
    def turn():
        comm_ctx.update_current_bundle_call_context({"role_models": {"solver": "m"}})
        client_tools.set_denied_named_service_namespaces(["mail"])
        client_tools.set_denied_named_service_entries({"docs": ["object.schema"]})
        client_tools.set_denied_named_service_entries(None)
        return (
            client_tools.denied_named_service_namespaces(),
            client_tools.denied_named_service_entries("docs"),
            comm_ctx.get_current_bundle_call_context().get("role_models"),
        )

    namespaces, entries, role_models = _in_fresh_context(turn)
    assert namespaces == frozenset({"mail"})
    assert entries == frozenset()
    assert role_models == {"solver": "m"}


def test_one_turn_never_sees_another_turns_denials():
    def turn_a():
        client_tools.set_denied_named_service_entries({"docs": ["object.schema"]})
        return client_tools.denied_named_service_entries("docs")

    def turn_b():
        return client_tools.denied_named_service_entries("docs")

    assert _in_fresh_context(turn_a) == frozenset({"object.schema"})
    assert _in_fresh_context(turn_b) == frozenset()


def test_the_generic_snapshot_keeps_tuples_and_sets():
    """An agent's per-account claim scope used to lose its claims ({provider: {}})."""
    assert _encode_value({"google": {"acct-1": ("docs:read", "docs:write")}}) == (
        True,
        {"google": {"acct-1": ["docs:read", "docs:write"]}},
    )
    assert _encode_value(frozenset({"b", "a"})) == (True, ["a", "b"])
