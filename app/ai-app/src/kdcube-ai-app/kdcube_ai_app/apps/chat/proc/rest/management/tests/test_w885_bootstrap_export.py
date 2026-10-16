# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""W885: the browser-approved export's `bootstrap` selection, for a configuration bootstrap from any store.

Operator, 2026-10-16: "this must be simply equal and the same code for any export" and "user props support is
must". The server filters the inventory before it freezes the manifest (no user secret without user_data, never
a token Connection Hub acquires), and with user_data the approved exchange also returns the user props.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

from kdcube_ai_app.apps.chat.proc.rest.management import secret_export_routes
from kdcube_ai_app.apps.chat.proc.rest.management.secret_contracts import SecretTarget
from kdcube_ai_app.apps.chat.proc.rest.management.secret_export import SECRET_EXPORT_REQUEST_SCHEMA

from test_routes import _export_client

PROPS = [{"user_id": "user-1", "bundle_id": "demo.bundle", "key": "theme", "subsystem": "bundle",
          "value_json": '{"dark": true, "n": 0, "x": null}'}]
INVENTORY = (
    SecretTarget(scope="platform", key="platform.infra.redis.password"),
    SecretTarget(scope="bundle", bundle_id="connection-hub@1-0", key="connections.oauth_state_secret"),
    SecretTarget(scope="user", user_id="user-1", bundle_id="demo.bundle", key="github.token"),
    SecretTarget(scope="user", user_id="user-1", key="connections.accounts.gmail.tokens"),
    SecretTarget(scope="user", user_id="user-1", bundle_id="demo.bundle", key="delegated_to_kdcube.grant"),
    SecretTarget(scope="user", user_id="user-1", bundle_id="demo.bundle", key="remote_mcp.ref"),
    SecretTarget(scope="user", user_id="user-1", bundle_id="demo.bundle", key="linkedin.accounts.a.tokens"),
)


def _run(monkeypatch, *, user_data: bool, inventory=INVENTORY):
    client, redis, runtime = _export_client(monkeypatch)
    runtime.inventory_targets = inventory

    async def props():
        return [dict(row) for row in PROPS]

    monkeypatch.setattr(secret_export_routes, "read_user_props", props)
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
    start = client.post("/api/integrations/management/v1/secrets/export/start", json={
        "schema": SECRET_EXPORT_REQUEST_SCHEMA, "callback_uri": "http://127.0.0.1:53123/callback",
        "state": "s" * 43, "code_challenge": challenge, "code_challenge_method": "S256",
        "selection": "bootstrap", "user_data": user_data})
    assert start.status_code == 200, start.text
    started = start.json()
    record = json.loads(next(iter(redis.values.values())))
    page = client.get(started["authorization_url"])
    csrf = re.search(r'name="csrf" value="([A-Za-z0-9_-]+)"', page.text).group(1)
    approval = client.post("/api/integrations/management/v1/secrets/export/authorize", follow_redirects=False,
                           data={"transaction": started["transaction_id"], "csrf": csrf, "decision": "approve"})
    code = parse_qs(urlsplit(approval.headers["location"]).query)["code"][0]
    result = client.post("/api/integrations/management/v1/secrets/export/exchange", json={
        "transaction_id": started["transaction_id"], "code": code, "code_verifier": verifier})
    assert result.status_code == 200, result.text
    return started, record, result.json(), runtime


def test_without_user_data_only_platform_and_app_secrets_are_frozen_read_and_returned(monkeypatch):
    started, record, result, runtime = _run(monkeypatch, user_data=False)
    assert started["targets"] == [] and started["target_count"] == 2
    assert {item["scope"] for item in record["request"]["targets"]} == {"platform", "bundle"}
    assert "user_props" not in record["request"] and "user_props" not in result
    assert {target.scope for target in runtime.reads} == {"platform", "bundle"}, "nothing left out is read"


def test_with_user_data_user_secrets_and_props_come_but_never_acquired_tokens(monkeypatch):
    started, record, result, runtime = _run(monkeypatch, user_data=True)
    keys = {item["key"] for item in record["request"]["targets"]}
    assert keys == {"platform.infra.redis.password", "connections.oauth_state_secret", "github.token"}
    assert record["request"]["user_props"] is True, "the props are bound into the approved request"
    assert result["user_props"] == PROPS
    assert all(not target.key.startswith(secret_export_routes.BOOTSTRAP_EXCLUDED_USER_PREFIXES)
               for target in runtime.reads)


def test_props_only_bootstrap_is_allowed(monkeypatch):
    started, record, result, _runtime = _run(
        monkeypatch, user_data=True, inventory=(SecretTarget(scope="user", user_id="u", key="remote_mcp.ref"),))
    assert started["target_count"] == 0 and result["values"] == [] and result["user_props"] == PROPS


def test_user_data_is_refused_outside_the_bootstrap_selection(monkeypatch):
    client, _redis, runtime = _export_client(monkeypatch)
    runtime.inventory_targets = INVENTORY
    response = client.post("/api/integrations/management/v1/secrets/export/start", json={
        "schema": SECRET_EXPORT_REQUEST_SCHEMA, "callback_uri": "http://127.0.0.1:53123/callback",
        "state": "s" * 43, "code_challenge": "c" * 43, "code_challenge_method": "S256",
        "selection": "all", "user_data": True})
    assert response.status_code == 400


def test_the_cli_keeps_the_same_acquired_token_prefixes():
    from kdcube_cli.runtime_secrets_export import EXCLUDED_USER_PREFIXES

    assert tuple(EXCLUDED_USER_PREFIXES) == secret_export_routes.BOOTSTRAP_EXCLUDED_USER_PREFIXES
