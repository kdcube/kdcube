"""W937: every delegated door enforces the Hub's entry binding, from the live Card.

A connector Card issued for one entry behind an ``entry_bound`` catalog row is
admitted at that entry and refused at a sibling door that the same row pattern
matches. The live grant record carries the Card's kind and entry, every caller
of the shared boundary passes the active catalog and the configured public
base, the projection shows the entry read-only, and a connector Card's refresh
family never widens to every resource.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect

from connection_hub.delegated_credentials.cards.identity import CARD_KIND_CONNECTOR

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth import surface_guard
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_surface_guard import (
    _Redis,
    _client,
    _live_card,
    _projections_swept,  # noqa: F401 - autouse: projections swept, as after startup
    _pointer_grant,
    _proxy_client,
    _rest_client,
    _rpc_tool_call,
    _store_live_card,
)

BASE = "http://testserver"
PATTERN = f"{BASE}/*"
ENTRY = f"{BASE}/guard"
BOUND = {
    "delegated_credentials": {
        "oauth": {
            "enabled": True,
            "resources": [
                {
                    "resource": PATTERN,
                    "grants": ["records:read"],
                    "entry_bound": True,
                    "tools": {"records_export": {"grants": ["records:read"]}},
                }
            ],
        }
    }
}
REFUSED = "delegated credential is bound to another entry"


def _bound_redis(*, entry: str = ENTRY, kind: str = CARD_KIND_CONNECTOR) -> _Redis:
    redis = _Redis()
    card = _live_card(
        resource_grants={PATTERN: ("records:read",)},
        resource_operations={PATTERN: ("records_export",)},
    )
    _store_live_card(redis, dataclasses.replace(card, card_kind=kind, entry_resource=entry))
    return redis


def _public_base(monkeypatch, value: str = BASE) -> None:
    monkeypatch.setattr(surface_guard, "connection_hub_public_base_url", lambda: value)


def _mcp(monkeypatch, **kwargs):
    return _client(monkeypatch, grant_record=_pointer_grant(), connections=BOUND, **kwargs)


def test_the_mcp_door_admits_the_entry_and_refuses_a_sibling(monkeypatch) -> None:
    _public_base(monkeypatch)
    client = _mcp(monkeypatch, redis=_bound_redis())
    headers = {"Authorization": "Bearer reader"}

    assert client.post("/guard", json=_rpc_tool_call(), headers=headers).status_code == 200
    refused = client.post("/second", json=_rpc_tool_call(), headers=headers)
    assert refused.status_code == 403 and refused.json()["error_description"] == REFUSED


def test_the_rest_door_admits_the_entry_and_refuses_a_sibling(monkeypatch) -> None:
    _public_base(monkeypatch)
    client = _rest_client(monkeypatch, grant_record=_pointer_grant(), connections=BOUND, redis=_bound_redis())
    (guard,) = [route for route in client.app.routes if getattr(route, "path", "") == "/guard"]
    client.app.add_api_route("/second", guard.endpoint, methods=["POST"])
    headers = {"Authorization": "Bearer reader"}

    assert client.post("/guard", headers=headers).status_code == 200
    refused = client.post("/second", headers=headers)
    assert refused.status_code == 403 and refused.json()["error_description"] == REFUSED


def test_an_automation_card_holding_the_bound_row_is_refused_at_the_entry(monkeypatch) -> None:
    _public_base(monkeypatch)
    client = _mcp(monkeypatch, redis=_bound_redis(kind="automation"))
    refused = client.post("/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"})
    assert refused.status_code == 403 and refused.json()["error_description"] == REFUSED


def test_without_a_configured_public_base_the_entry_is_refused(monkeypatch) -> None:
    # The request's own Host or X-Forwarded-Host never stands in for the public base.
    _public_base(monkeypatch, "")
    client = _mcp(monkeypatch, redis=_bound_redis())
    refused = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader", "X-Forwarded-Host": "testserver"},
    )
    assert refused.status_code == 403 and refused.json()["error_description"] == REFUSED


def test_a_forwarded_host_can_name_only_the_host_never_another_entry(monkeypatch) -> None:
    # The edge passes a client's X-Forwarded-Host through, and the guard rebuilds the request
    # origin from it. That can move the host onto the public base, never the path: a sibling
    # door is refused, and so is the entry seen on another host.
    pattern = "*://*/*"
    connections = {
        "delegated_credentials": {
            "oauth": {
                "enabled": True,
                "resources": [
                    {
                        "resource": pattern,
                        "grants": ["records:read"],
                        "entry_bound": True,
                        "tools": {"records_export": {"grants": ["records:read"]}},
                    }
                ],
            }
        }
    }
    redis = _Redis()
    card = _live_card(resource_grants={pattern: ("records:read",)}, resource_operations={pattern: ("records_export",)})
    _store_live_card(
        redis,
        dataclasses.replace(card, card_kind=CARD_KIND_CONNECTOR, entry_resource="https://hub.example/guard"),
    )
    _public_base(monkeypatch, "https://hub.example")
    client = _client(monkeypatch, grant_record=_pointer_grant(), connections=connections, redis=redis)
    spoofed = {"Authorization": "Bearer reader", "X-Forwarded-Host": "hub.example", "X-Forwarded-Proto": "https"}

    assert client.post("/guard", json=_rpc_tool_call(), headers=spoofed).status_code == 200
    sibling = client.post("/second", json=_rpc_tool_call(), headers=spoofed)
    assert sibling.status_code == 403 and sibling.json()["error_description"] == REFUSED
    alias = client.post(
        "/guard",
        json=_rpc_tool_call(),
        headers={"Authorization": "Bearer reader", "X-Forwarded-Host": "alias.example", "X-Forwarded-Proto": "https"},
    )
    assert alias.status_code == 403 and alias.json()["error_description"] == REFUSED


def test_the_proxy_door_refuses_a_card_holding_a_bound_row(monkeypatch) -> None:
    _public_base(monkeypatch)
    client = _proxy_client(monkeypatch, grant_record=_pointer_grant())
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import (
        bind_delegated_catalog,
    )

    bind_delegated_catalog(client.app, BOUND)
    redis = _bound_redis()
    client.app.state.oauth_grant_store = type(client.app.state.oauth_grant_store)(_pointer_grant(), redis=redis)
    refused = client.post("/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"})
    assert refused.status_code == 403 and refused.json()["error_description"] == REFUSED


def test_the_projection_shows_the_entry_read_only(monkeypatch) -> None:
    _public_base(monkeypatch)
    client = _mcp(monkeypatch, redis=_bound_redis(), return_projection=True)
    response = client.post("/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"})
    assert response.status_code == 200
    assert response.json()["projection"]["entry_resource"] == ENTRY


def test_every_boundary_call_passes_the_catalog_and_the_public_base() -> None:
    tree = ast.parse(inspect.getsource(surface_guard))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"authorize_credential_boundary", "authorize_credential_identity_boundary"}
    ]
    assert len(calls) == 5
    for call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert "catalog" in keywords
        if call.func.id == "authorize_credential_boundary":
            public_base = keywords["public_base"]
            assert isinstance(public_base, ast.Call) and public_base.func.id == "connection_hub_public_base_url"


def test_a_connector_refresh_family_never_widens_to_every_resource() -> None:
    family = routes._refresh_family_resources
    assert family({}, entry_resource="", card_kind=CARD_KIND_CONNECTOR) == ()
    assert family({}, entry_resource=ENTRY, card_kind=CARD_KIND_CONNECTOR) == (ENTRY,)
    assert family({"resource": ENTRY}, card_kind=CARD_KIND_CONNECTOR) == (ENTRY,)
    # Unchanged for the whole-Card kinds (#420): a family without resource still covers "*".
    assert family({}, entry_resource="", card_kind="automation") == ("*",)
    assert family({}) == ("*",)
