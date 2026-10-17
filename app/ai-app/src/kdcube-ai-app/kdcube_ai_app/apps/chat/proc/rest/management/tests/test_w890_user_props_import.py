# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""W890: the delegated user props import (operator, option A: "5. A agree").

Insert, never overwrite, report equal and different rows; Connection Hub's own props are never imported; the
request digest is keyed because the body holds values; the result and the ledger hold counts only.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.proc.rest.management import routes
from kdcube_ai_app.apps.chat.proc.rest.management.admission import AdmissionDecision
from kdcube_ai_app.apps.chat.proc.rest.management.config import DelegatedManagementConfig
from kdcube_ai_app.apps.chat.proc.rest.management.contracts import management_request_digest, management_resource
from kdcube_ai_app.apps.chat.proc.rest.management.effect_ledger import (
    ACTION_EXECUTE,
    ACTION_REPLAY,
    EffectReservation,
)
from kdcube_ai_app.apps.chat.proc.rest.management.service import DelegatedManagementService, ManagementResponse
from kdcube_ai_app.apps.chat.proc.rest.management.user_props_contracts import (
    MAX_USER_PROPS_PER_REQUEST,
    USER_PROPS_IMPORT_OPERATION,
    USER_PROPS_IMPORT_SCHEMA,
    USER_PROPS_RESOURCE_SELECTOR,
    UserPropsRequestInvalid,
    import_body,
    parse_user_props_import,
)
from kdcube_ai_app.apps.chat.proc.rest.management.user_props_runtime import KDCubeUserPropsRuntime

# Self-contained (README: a sibling test module imported by bare name does not collect under importlib).
TENANT, PROJECT = "tenant-a", "project-a"
PROPS_RESOURCE = "urn:kdcube:management:user-props:tenant-a:project-a"
DIGEST_SECRET = "request-digest-test-secret-at-least-32-bytes"


def _row(key="theme", value='{"dark": true}', user="user-1", bundle="demo.bundle"):
    return {"user_id": user, "bundle_id": bundle, "key": key, "subsystem": "bundle", "value_json": value}


def _payload(*rows):
    return {"schema": USER_PROPS_IMPORT_SCHEMA, "rows": list(rows)}


# --- contract ------------------------------------------------------------------------------------------------

def test_rows_are_parsed_exactly_and_values_canonicalized():
    rows = parse_user_props_import(_payload(_row(value='{"b": 1, "a": [1, 2]}')))
    assert rows[0].value_json == '{"a":[1,2],"b":1}'
    assert import_body(rows)["rows"][0]["key"] == "theme"


@pytest.mark.parametrize("payload", [
    _payload(_row(key="delegated_to_kdcube.account_index")),
    _payload(_row(), _row()),
    _payload(_row(value="not json")),
    _payload(_row(value="null")),
    _payload({**_row(), "extra": "x"}),
    _payload(),
    {"schema": "other", "rows": [_row()]},
    _payload(*[_row(key=f"k{i}") for i in range(MAX_USER_PROPS_PER_REQUEST + 1)]),
])
def test_an_invalid_request_is_refused_whole(payload):
    with pytest.raises(UserPropsRequestInvalid):
        parse_user_props_import(payload)


def test_the_runtime_and_the_export_leave_out_the_same_connection_hub_props():
    from kdcube_ai_app.apps.chat.proc.rest.management import secret_export_routes, user_props_contracts

    assert user_props_contracts.EXCLUDED_USER_PROP_PREFIXES == secret_export_routes.BOOTSTRAP_EXCLUDED_USER_PROP_PREFIXES


# --- route ---------------------------------------------------------------------------------------------------

def _client(monkeypatch, service):
    config = DelegatedManagementConfig(
        enabled=True, tenant=TENANT, project=PROJECT, connection_hub_app_id="connection-hub@1-0",
        service_id="kdcube-management", service_secret_ref="connections.management.signing_secret",
        admission_url="http://connection-hub.test/admission")
    monkeypatch.setattr(routes, "_configuration",
                        lambda: (SimpleNamespace(INSTANCE_ID="proc-test"), config, management_resource(TENANT, PROJECT)))

    async def build(_request):
        if service is None:
            raise AssertionError("the service must not be built")
        return service, management_resource(TENANT, PROJECT)

    monkeypatch.setattr(routes, "_service", build)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/integrations")
    return TestClient(app)


def _post(client, payload):
    return client.post("/api/integrations/management/v1/user-props/import", json=payload,
                       headers={"Authorization": "Bearer opaque-token", "Idempotency-Key": "props-import-1"})


def test_the_route_passes_the_exact_rows_resource_and_counts_only_context(monkeypatch):
    calls: list[dict[str, Any]] = []

    class _Service:
        async def execute(self, **kwargs):
            calls.append(kwargs)
            return ManagementResponse(200, {"ok": True, "result": {"inserted": 2, "equal": 0, "different": 0}})

    response = _post(_client(monkeypatch, _Service()),
                     _payload(_row(key="theme"), _row(key="lang", value='"en"', user="user-2")))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store, private"
    call = calls[0]
    assert call["operation"] == USER_PROPS_IMPORT_OPERATION and call["resource"] == PROPS_RESOURCE
    assert call["approval_context"] == {"user_props_rows": "2", "user_props_users": "2",
                                        "user_props_applications": "1"}
    assert [row.key for row in call["user_props"]] == ["theme", "lang"]
    assert call["body"] == import_body(call["user_props"])


def test_a_connection_hub_prop_is_refused_before_the_service_is_built(monkeypatch):
    response = _post(_client(monkeypatch, None), _payload(_row(), _row(key="delegated_to_kdcube.consent_demands")))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "user_props_request_invalid"


def test_the_metadata_publishes_the_import_operation(monkeypatch):
    payload = _client(monkeypatch, None).get(
        "/api/integrations/management/v1/.well-known/oauth-protected-resource").json()
    assert USER_PROPS_RESOURCE_SELECTOR in payload["kdcube_management_resources"]
    assert payload["kdcube_management_operations"][USER_PROPS_IMPORT_OPERATION] == {
        "method": "POST", "path": "/api/integrations/management/v1/user-props/import",
        "resource_selector": USER_PROPS_RESOURCE_SELECTOR}


# --- service -------------------------------------------------------------------------------------------------

class _Admission:
    def __init__(self):
        self.calls = []

    async def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        return AdmissionDecision(200, {"allowed": True, "decision_id": "d-1", "principal": {"client_id": "caller-1"},
                                       "provenance": {"access_id": "access-1", "card_revision": 1}})


class _Ledger:
    def __init__(self):
        self.records = {}

    async def reserve(self, **kwargs):
        key = (kwargs["operation"], kwargs["invocation_id"])
        if key in self.records:
            return EffectReservation(ACTION_REPLAY, record=self.records[key])
        self.records[key] = {"request_digest": kwargs["request_digest"], "audit": dict(kwargs["audit"])}
        return EffectReservation(ACTION_EXECUTE, owner="owner-1", record=self.records[key])

    async def finish(self, **kwargs):
        key = (kwargs["operation"], kwargs["invocation_id"])
        self.records[key] = {**self.records[key], "status_code": kwargs["status_code"],
                             "response": dict(kwargs["response"])}


class _PropsRuntime:
    def __init__(self):
        self.calls = 0

    async def import_rows(self, rows):
        self.calls += 1
        return {"inserted": len(rows), "equal": 0, "different": 0, "received": len(rows), "state": "imported"}


def _service(props_runtime):
    return DelegatedManagementService(
        tenant=TENANT, project=PROJECT, resource=management_resource(TENANT, PROJECT), runtime_instance="proc-1",
        admission=_Admission(), ledger=_Ledger(), runtime=SimpleNamespace(), user_props_runtime=props_runtime,
        request_digest_secret=DIGEST_SECRET)


def test_the_service_keys_the_digest_imports_once_and_keeps_values_out_of_the_ledger():
    rows = parse_user_props_import(_payload(_row(value='"value-canary"')))
    runtime = _PropsRuntime()
    service = _service(runtime)
    kwargs = dict(operation=USER_PROPS_IMPORT_OPERATION, delegated_bearer="opaque", invocation_id="props-1",
                  resource=PROPS_RESOURCE, body=import_body(rows), user_props=rows,
                  approval_context={"user_props_rows": "1"})
    first = asyncio.run(service.execute(**kwargs))
    again = asyncio.run(service.execute(**kwargs))
    assert first.status_code == 200 and first.payload["result"]["inserted"] == 1
    assert again.payload["invocation"]["replay"] is True and runtime.calls == 1, "a retry never imports twice"
    plain = management_request_digest(resource=PROPS_RESOURCE, operation=USER_PROPS_IMPORT_OPERATION,
                                      body=import_body(rows))
    assert service._admission.calls[0]["request_digest"] != plain, "keyed: no offline verifier of the values"
    assert "value-canary" not in str(service._ledger.records) and "value-canary" not in str(first.payload)


# --- runtime (SQL against a fake pool; the statement itself is checked on PostgreSQL in the PR) --------------

def test_the_runtime_sends_one_insert_that_never_updates_and_checks_the_counts():
    seen = {}

    class _Con:
        async def fetchrow(self, sql, *params):
            seen["sql"], seen["params"] = sql, params
            return {"inserted": 1, "equal": 1, "different": 0}

    class _Acquire:
        async def __aenter__(self):
            return _Con()

        async def __aexit__(self, *_exc):
            return False

    class _Manager:
        _schema = "schema_a"

        async def _pool(self):
            return SimpleNamespace(acquire=lambda: _Acquire())

    rows = parse_user_props_import(_payload(_row(key="a"), _row(key="b")))
    result = asyncio.run(KDCubeUserPropsRuntime(manager=_Manager()).import_rows(rows))
    assert result == {"inserted": 1, "equal": 1, "different": 0, "received": 2, "state": "imported"}
    assert "ON CONFLICT (user_id, bundle_id, key) DO NOTHING" in seen["sql"]
    assert "DO UPDATE" not in seen["sql"] and "schema_a.user_bundle_props" in seen["sql"]
    assert seen["params"][2] == ["a", "b"]
