# SPDX-License-Identifier: MIT

"""The tool catalog only lists operations the provider actually serves.

Surfaced case: `named_services.host_file` listed the mail namespace, but the
mail provider serves no `object.host_file` — the call died with
`named_service_provider_not_found` after being advertised as available.
"""

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import tools as ns_tools


def _entry(namespace: str, operations: dict) -> dict:
    return {"spec": {"namespace": namespace, "operations": operations}}


def test_provider_declared_operations_come_from_the_registry(monkeypatch):
    monkeypatch.setitem(
        ns_tools.REGISTRY,
        "named_service_discovery_entries",
        [_entry("mail", {"object.list": {}, "object.get": {}, "object.action": {}})],
    )
    declared = ns_tools._provider_declared_operations("mail")
    assert declared == {"object.list", "object.get", "object.action"}
    # A namespace with no visible declaration restricts nothing.
    assert ns_tools._provider_declared_operations("unknown-ns") is None


def test_operation_allowed_respects_provider_declaration(monkeypatch):
    monkeypatch.setitem(
        ns_tools.REGISTRY,
        "named_service_discovery_entries",
        [_entry("mail", {"object.list": {}, "object.get": {}})],
    )
    # Served + read-allowed by default policy.
    assert ns_tools._operation_allowed("mail", "object.list") is True
    # The surfaced case: the provider serves no host_file, so the catalog
    # must keep it out regardless of client policy.
    assert ns_tools._operation_allowed("mail", "object.host_file") is False
