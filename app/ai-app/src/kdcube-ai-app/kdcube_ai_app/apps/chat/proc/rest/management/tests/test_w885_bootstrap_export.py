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

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.proc.rest.management import routes, secret_export_routes
from kdcube_ai_app.apps.chat.proc.rest.management.config import HumanSecretExportConfig
from kdcube_ai_app.apps.chat.proc.rest.management.human_approval import (
    SESSION_CONFIRMATION,
    HumanApprovalEvidence,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_contracts import SecretTarget
from kdcube_ai_app.apps.chat.proc.rest.management.secret_export import (
    SECRET_EXPORT_REQUEST_SCHEMA,
    RedisSecretExportStore,
)

# Self-contained (README: a sibling test module imported by bare name does not collect under importlib).
TENANT, PROJECT = "tenant-a", "project-a"


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def eval(self, _script, _keys, key, expected, _ttl, replacement):
        current = self.values.get(key)
        if current is None:
            return 0
        if current != expected:
            return -1
        self.values[key] = replacement
        return 1


class _Approver:
    async def evaluate(self, _request, *, context, phase):
        return HumanApprovalEvidence(subject="human-a", assurance=SESSION_CONFIRMATION, method="test_browser_session",
                                     request_digest=context.request_digest, verified_at=int(time.time()))


class _SecretRuntime:
    def __init__(self) -> None:
        self.reads = []
        self.inventory_targets: tuple[SecretTarget, ...] = ()

    async def inventory(self, *, skip_provider_key=None):
        if skip_provider_key is None:
            return self.inventory_targets
        return tuple(target for target in self.inventory_targets if not skip_provider_key(target.provider_key))

    async def read(self, target):
        self.reads.append(target)
        return {**target.public_dict(), "value": f"canary::{target.provider_key}"}


def _export_client(monkeypatch):
    config = HumanSecretExportConfig(enabled=True, required_assurance=SESSION_CONFIRMATION,
                                     max_evidence_age_seconds=300, transaction_ttl_seconds=180,
                                     consumed_tombstone_seconds=600, max_targets=8, max_total_value_bytes=1024 * 1024)
    redis = _Redis()
    store = RedisSecretExportStore(redis, tenant=TENANT, project=PROJECT,
                                   transaction_ttl_seconds=config.transaction_ttl_seconds,
                                   consumed_tombstone_seconds=config.consumed_tombstone_seconds,
                                   max_targets=config.max_targets)
    runtime = _SecretRuntime()
    monkeypatch.setattr(secret_export_routes, "_configuration", lambda: (SimpleNamespace(), config, TENANT, PROJECT))
    app = FastAPI()
    app.state.secret_export_store = store
    app.state.secret_export_runtime = runtime
    app.state.human_approval_verifier = _Approver()
    app.include_router(routes.router, prefix="/api/integrations")
    return TestClient(app), redis, runtime

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


_PAGES: list[str] = []


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
    _PAGES.append(page.text)
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


def test_the_cli_and_the_runtime_leave_out_the_same_props():
    from kdcube_cli.runtime_secrets_export import EXCLUDED_USER_PROP_PREFIXES

    assert tuple(EXCLUDED_USER_PROP_PREFIXES) == secret_export_routes.BOOTSTRAP_EXCLUDED_USER_PROP_PREFIXES


def test_the_approval_page_says_whether_user_props_are_included(monkeypatch):
    """codex-infra on #381: the person must see the props scope they approve, also for a props-only export."""
    _PAGES.clear()
    _run(monkeypatch, user_data=False)
    assert "User props:</strong> not included" in _PAGES[-1]
    _run(monkeypatch, user_data=True)
    assert "User props:</strong> <strong>included</strong>" in _PAGES[-1]
    assert "every user&#x27;s stored props for every application" in _PAGES[-1] or (
        "every user's stored props for every application" in _PAGES[-1])
    _run(monkeypatch, user_data=True, inventory=(SecretTarget(scope="user", user_id="u", key="remote_mcp.ref"),))
    page = _PAGES[-1]
    assert "Secrets (0)" in page and "User props:</strong> <strong>included</strong>" in page


# --- the bootstrap drops keys it never exports before parsing them (W885, after window 16r) ----------------
# dev-main, 2026-10-17 00:03Z: 1 of 67 provider keys was a Hub-acquired token under a user id with ":", which
# the key contract refuses; inventory parsed every key first, so the whole bootstrap answered
# secret_export_provider_unavailable.

COLON_USER = "service:0123456789abcdef0123456789abcdef0123"
RAW_KEYS = [
    "platform.infra.redis.password",
    "bundles.demo.bundle.secrets.provider.token",
    "users.user-1.bundles.demo.bundle.secrets.github.token",
    f"users.{COLON_USER}.bundles.demo.bundle.secrets.delegated_to_kdcube.credentials.c",
    f"users.{COLON_USER}.secrets.connections.accounts.gmail.tokens",
]


class _Manager:
    provider_type = "secrets-file"

    def __init__(self, keys):
        self.keys = list(keys)
        self.reads: list[str] = []

    async def list_all_secret_keys(self):
        return list(self.keys)

    async def get_secret_strict(self, key):
        self.reads.append(key)
        return f"canary::{key}"


def _real_runtime_client(monkeypatch, keys):
    from kdcube_ai_app.apps.chat.proc.rest.management.secret_runtime import KDCubeSecretRuntime

    client, redis, _fake = _export_client(monkeypatch)
    manager = _Manager(keys)
    client.app.state.secret_export_runtime = KDCubeSecretRuntime(
        SimpleNamespace(), tenant=TENANT, project=PROJECT, manager=manager)
    return client, redis, manager


def _start(client, selection, **extra):
    return client.post("/api/integrations/management/v1/secrets/export/start", json={
        "schema": SECRET_EXPORT_REQUEST_SCHEMA, "callback_uri": "http://127.0.0.1:53123/callback",
        "state": "s" * 43, "code_challenge": "c" * 43, "code_challenge_method": "S256",
        "selection": selection, **extra})


import pytest  # noqa: E402


@pytest.mark.parametrize("user_data", [False, True])
def test_an_acquired_token_under_a_colon_user_id_neither_blocks_nor_leaves_the_bootstrap(monkeypatch, user_data):
    client, redis, manager = _real_runtime_client(monkeypatch, RAW_KEYS)
    response = _start(client, "bootstrap", user_data=user_data)
    assert response.status_code == 200, response.text
    frozen = json.loads(next(iter(redis.values.values())))["request"]["targets"]
    keys = {item["key"] for item in frozen}
    expected = {"platform.infra.redis.password", "provider.token"} | ({"github.token"} if user_data else set())
    assert keys == expected
    assert all(COLON_USER not in json.dumps(item) for item in frozen)
    assert manager.reads == [], "nothing is read at start"


def test_the_all_selection_stays_strict_on_a_key_outside_the_contract(monkeypatch):
    client, redis, _manager = _real_runtime_client(monkeypatch, RAW_KEYS)
    response = _start(client, "all")
    assert response.status_code == 503 and response.json()["error"]["code"] == "secret_export_provider_unavailable"
    assert redis.values == {}


def test_an_ordinary_user_secret_under_a_colon_id_still_refuses_the_bootstrap_with_user_data(monkeypatch):
    """It would be exported, and neither the key contract nor the cloud intake accepts the id."""
    keys = [*RAW_KEYS, f"users.{COLON_USER}.bundles.demo.bundle.secrets.github.token"]
    client, _redis, _manager = _real_runtime_client(monkeypatch, keys)
    assert _start(client, "bootstrap", user_data=True).status_code == 503
    client, _redis, _manager = _real_runtime_client(monkeypatch, keys)
    assert _start(client, "bootstrap", user_data=False).status_code == 200, "without the flag it is never exported"


def test_the_raw_key_rule_matches_the_parsed_bootstrap_filter():
    parsed = [SecretTarget.from_provider_key(key) for key in RAW_KEYS if COLON_USER not in key]
    for user_data in (False, True):
        kept = {t.provider_key for t in secret_export_routes.bootstrap_targets(parsed, user_data=user_data)}
        raw_kept = {t.provider_key for t in parsed
                    if not secret_export_routes.bootstrap_skips_provider_key(t.provider_key, user_data=user_data)}
        assert kept == raw_kept


def test_the_runtime_never_returns_connection_hub_props(monkeypatch):
    """Operator, 2026-10-17: "3. exclude". Connection Hub's delegated_to_kdcube.* props stay in the runtime."""
    import asyncio

    from kdcube_ai_app.infra.props import manager as props_manager

    rows = [{**PROPS[0]}, {"user_id": "user-1", "bundle_id": "connection-hub@1-0",
                           "key": "delegated_to_kdcube.consent_demands", "subsystem": "bundle",
                           "value_json": '"hub-prop-canary"'}]

    calls = []

    class _Con:
        async def fetch(self, sql, *params):
            calls.append((sql, params))
            return rows  # as if the database ignored the filter: the Python filter still holds

    class _Acquire:
        async def __aenter__(self):
            return _Con()

        async def __aexit__(self, *_exc):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    class _Manager:
        _schema = "schema_a"

        async def _pool(self):
            return _Pool()

    monkeypatch.setattr(props_manager, "get_props_manager", lambda: _Manager())
    returned = asyncio.run(secret_export_routes.read_user_props())
    assert returned == PROPS
    sql, params = calls[0]
    assert "starts_with(key, prefix)" in sql and sql.index("WHERE") < sql.index("LIMIT"), "filtered before the limit"
    assert params == (["delegated_to_kdcube."],)
    assert "hub-prop-canary" not in json.dumps(returned)
