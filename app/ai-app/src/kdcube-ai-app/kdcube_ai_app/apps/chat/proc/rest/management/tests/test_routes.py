from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from kdcube_ai_app.apps.chat.proc.rest.management import routes, secret_export_routes
from kdcube_ai_app.apps.chat.proc.rest.management.config import (
    DelegatedManagementConfig,
    HumanSecretExportConfig,
)
from kdcube_ai_app.apps.chat.proc.rest.management.contracts import (
    RELOAD_OPERATION,
    management_resource,
)
from kdcube_ai_app.apps.chat.proc.rest.management.human_approval import (
    SESSION_CONFIRMATION,
    HumanApprovalChallenge,
    HumanApprovalEvidence,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_contracts import (
    SECRET_DELETE_OPERATION,
    SECRET_METADATA_OPERATION,
    SECRET_READ_OPERATION,
    SECRET_RESOURCE_SELECTOR,
    SECRET_WRITE_OPERATION,
    SecretTarget,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_export import (
    SECRET_EXPORT_REQUEST_SCHEMA,
    RedisSecretExportStore,
)
from kdcube_ai_app.apps.chat.proc.rest.management.service import ManagementResponse

TENANT = "tenant-a"
PROJECT = "project-a"
RESOURCE = management_resource(TENANT, PROJECT)


def _config(*, enabled: bool = True) -> DelegatedManagementConfig:
    return DelegatedManagementConfig(
        enabled=enabled,
        tenant=TENANT,
        project=PROJECT,
        connection_hub_app_id="connection-hub@1-0",
        service_id="kdcube-management",
        service_secret_ref="connections.management.signing_secret",
        admission_url="http://connection-hub.test/admission",
    )


def _client(monkeypatch, *, enabled: bool = True) -> TestClient:
    config = _config(enabled=enabled)
    monkeypatch.setattr(
        routes,
        "_configuration",
        lambda: (SimpleNamespace(INSTANCE_ID="proc-test"), config, RESOURCE),
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/integrations")
    return TestClient(app)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        del ex
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


class _HumanApprovalVerifier:
    def __init__(self) -> None:
        self.phases = []

    async def evaluate(self, _request, *, context, phase):
        self.phases.append(phase)
        assert context.required_assurance == SESSION_CONFIRMATION
        return HumanApprovalEvidence(
            subject="human-a",
            assurance=SESSION_CONFIRMATION,
            method="test_browser_session",
            request_digest=context.request_digest,
            verified_at=int(time.time()),
        )


class _HumanApprovalChallengeVerifier:
    def __init__(self) -> None:
        self.contexts = []
        self.phases = []

    async def evaluate(self, _request, *, context, phase):
        assert phase in {"present", "commit"}
        self.contexts.append(context)
        self.phases.append(phase)
        return HumanApprovalChallenge(
            authorization_url="https://identity.example/step-up?state=opaque",
            method="test_step_up",
        )


class _SecretRuntime:
    def __init__(self) -> None:
        self.reads = []
        self.inventory_targets: tuple[SecretTarget, ...] = ()

    async def inventory(self):
        return self.inventory_targets

    async def read(self, target):
        self.reads.append(target)
        return {**target.public_dict(), "value": f"canary::{target.provider_key}"}


def _export_client(
    monkeypatch,
    *,
    verifier=None,
) -> tuple[TestClient, _Redis, _SecretRuntime]:
    config = HumanSecretExportConfig(
        enabled=True,
        required_assurance=SESSION_CONFIRMATION,
        max_evidence_age_seconds=300,
        transaction_ttl_seconds=180,
        consumed_tombstone_seconds=600,
        max_targets=8,
        max_total_value_bytes=1024 * 1024,
    )
    redis = _Redis()
    store = RedisSecretExportStore(
        redis,
        tenant=TENANT,
        project=PROJECT,
        transaction_ttl_seconds=config.transaction_ttl_seconds,
        consumed_tombstone_seconds=config.consumed_tombstone_seconds,
        max_targets=config.max_targets,
    )
    runtime = _SecretRuntime()
    monkeypatch.setattr(
        secret_export_routes,
        "_configuration",
        lambda: (SimpleNamespace(), config, TENANT, PROJECT),
    )
    app = FastAPI()
    app.state.secret_export_store = store
    app.state.secret_export_runtime = runtime
    app.state.human_approval_verifier = verifier or _HumanApprovalVerifier()
    app.include_router(routes.router, prefix="/api/integrations")
    return TestClient(app), redis, runtime


PUBLIC_HOST = "demo.kdcube.tech"
PUBLIC_AUTHORIZATION_SERVER = (
    f"https://{PUBLIC_HOST}/api/integrations/bundles/tenant-a/project-a/"
    "connection-hub@1-0/public/oauth"
)


def _published_authorization_server(monkeypatch, headers: dict[str, str]) -> str:
    response = _client(monkeypatch).get(
        "/api/integrations/management/v1/.well-known/oauth-protected-resource",
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()["authorization_servers"][0]


def test_metadata_publishes_the_public_origin_behind_a_terminating_proxy(
    monkeypatch,
) -> None:
    """The document travels to callers that never see the internal hop.

    The proc is reached over plain http on an internal name; the published
    authorization server must be the origin the browser used, taken from the
    forwarded provenance the deployment's proxy sets.
    """
    assert _published_authorization_server(
        monkeypatch,
        {"host": PUBLIC_HOST, "x-forwarded-proto": "https"},
    ) == PUBLIC_AUTHORIZATION_SERVER


def test_metadata_reads_rfc_7239_forwarded_when_it_is_the_only_provenance(
    monkeypatch,
) -> None:
    """A proxy that speaks RFC 7239 instead of X-Forwarded-* must not leave the
    internal host in a document external callers act on."""
    assert _published_authorization_server(
        monkeypatch,
        {"host": "chat-proc:8020", "forwarded": f"proto=https;host={PUBLIC_HOST}"},
    ) == PUBLIC_AUTHORIZATION_SERVER


def test_metadata_does_not_promise_https_on_a_deployment_reached_over_http(
    monkeypatch,
) -> None:
    """A local deployment answers on http; a document claiming https sends its
    callers to a port that never completes a TLS handshake."""
    assert _published_authorization_server(
        monkeypatch, {"host": "localhost:8020"}
    ).startswith("http://localhost:8020/")


def test_metadata_publishes_resource_authorization_server_and_operations(
    monkeypatch,
) -> None:
    response = _client(monkeypatch).get(
        "/api/integrations/management/v1/.well-known/oauth-protected-resource"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource"] == RESOURCE
    assert payload["authorization_servers"] == [
        (
            "http://testserver/api/integrations/bundles/tenant-a/project-a/"
            "connection-hub@1-0/public/oauth"
        )
    ]
    assert payload["bearer_methods_supported"] == ["header"]
    assert payload["kdcube_management_resources"] == [
        RESOURCE,
        SECRET_RESOURCE_SELECTOR,
    ]
    assert set(payload["kdcube_management_operations"]) == {
        "kdcube.management.deployment.inspect",
        "kdcube.management.application.surfaces.read",
        RELOAD_OPERATION,
        SECRET_METADATA_OPERATION,
        SECRET_READ_OPERATION,
        SECRET_WRITE_OPERATION,
        SECRET_DELETE_OPERATION,
    }


def test_human_approval_and_secret_export_routes_are_mounted_once() -> None:
    expected = {
        ("GET", "/human-approval/oidc/callback"),
        ("POST", "/human-approval/oidc/callback"),
        ("GET", "/human-approval/webauthn"),
        ("POST", "/human-approval/webauthn/complete"),
        ("GET", "/human-approval/passkeys/register"),
        ("POST", "/human-approval/passkeys/register/complete"),
        ("POST", "/secrets/export/start"),
        ("GET", "/secrets/export/authorize"),
        ("POST", "/secrets/export/authorize"),
        ("POST", "/secrets/export/exchange"),
    }
    actual = []
    for route in routes.router.routes:
        for method in getattr(route, "methods", set()):
            suffix = next(
                (path for _verb, path in expected if route.path.endswith(path)),
                "",
            )
            if suffix:
                actual.append((method, suffix))
    assert sorted(actual) == sorted(expected)


def test_metadata_uses_public_https_origin_behind_tunnel(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/integrations/management/v1/.well-known/oauth-protected-resource",
        headers={
            "Host": "runtime.example.test",
            "X-Forwarded-Host": "public.example.test",
            "X-Forwarded-Proto": "http",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["authorization_servers"] == [
        (
            "https://public.example.test/api/integrations/bundles/tenant-a/project-a/"
            "connection-hub@1-0/public/oauth"
        )
    ]


def test_metadata_preserves_explicit_local_http_origin(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/integrations/management/v1/.well-known/oauth-protected-resource",
        headers={"Host": "localhost:5173", "X-Forwarded-Proto": "http"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["authorization_servers"] == [
        (
            "http://localhost:5173/api/integrations/bundles/tenant-a/project-a/"
            "connection-hub@1-0/public/oauth"
        )
    ]


def test_missing_bearer_points_to_protected_resource_metadata(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/integrations/management/v1/deployment",
        headers={"Idempotency-Key": "inspect-1"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "delegated_bearer_missing"
    assert response.headers["www-authenticate"] == (
        'Bearer resource_metadata="http://testserver/api/integrations/'
        'management/v1/.well-known/oauth-protected-resource"'
    )


def test_invalid_idempotency_and_request_shapes_fail_before_service(monkeypatch) -> None:
    client = _client(monkeypatch)
    headers = {"Authorization": "Bearer opaque-token"}

    invalid_key = client.get(
        "/api/integrations/management/v1/deployment",
        headers={**headers, "Idempotency-Key": "contains space"},
    )
    inspect_body = client.request(
        "GET",
        "/api/integrations/management/v1/deployment",
        headers={**headers, "Idempotency-Key": "inspect-1"},
        content=b"{}",
    )
    invalid_app = client.post(
        "/api/integrations/management/v1/applications/%2A/reload",
        headers={**headers, "Idempotency-Key": "reload-1"},
        json={},
    )
    invalid_reload = client.post(
        "/api/integrations/management/v1/applications/app-a@1-0/reload",
        headers={**headers, "Idempotency-Key": "reload-1"},
        json={"all": True},
    )

    assert invalid_key.status_code == 400
    assert invalid_key.json()["error"]["code"] == "idempotency_key_invalid"
    assert inspect_body.status_code == 400
    assert inspect_body.json()["error"]["code"] == "request_body_not_allowed"
    assert invalid_app.status_code == 400
    assert invalid_app.json()["error"]["code"] == "application_id_invalid"
    assert invalid_reload.status_code == 400
    assert invalid_reload.json()["error"]["code"] == "reload_request_invalid"


def test_reload_route_passes_one_exact_application_and_empty_body(
    monkeypatch,
) -> None:
    client = _client(monkeypatch)
    calls: list[dict[str, Any]] = []

    class _Service:
        async def execute(self, **kwargs: Any) -> ManagementResponse:
            calls.append(kwargs)
            return ManagementResponse(
                200,
                {
                    "ok": True,
                    "operation": kwargs["operation"],
                    "application_id": kwargs["application_id"],
                },
            )

    async def _service(_request):
        return _Service(), RESOURCE

    monkeypatch.setattr(routes, "_service", _service)
    response = client.post(
        "/api/integrations/management/v1/applications/app-a@1-0/reload",
        headers={
            "Authorization": "Bearer opaque-token",
            "Idempotency-Key": "reload-1",
        },
        json={},
    )

    assert response.status_code == 200
    assert calls == [
        {
            "operation": RELOAD_OPERATION,
            "delegated_bearer": "opaque-token",
            "invocation_id": "reload-1",
            "application_id": "app-a@1-0",
            "body": {},
            "resource": RESOURCE,
            "approval_context": None,
            "secret_target": None,
        }
    ]


def test_secret_write_passes_exact_resource_and_value_only_to_service(
    monkeypatch,
) -> None:
    client = _client(monkeypatch)
    calls: list[dict[str, Any]] = []

    class _Service:
        async def execute(self, **kwargs: Any) -> ManagementResponse:
            calls.append(kwargs)
            return ManagementResponse(
                200,
                {
                    "ok": True,
                    "operation": kwargs["operation"],
                    "result": {"state": "stored"},
                },
            )

    async def _service(_request):
        return _Service(), RESOURCE

    monkeypatch.setattr(routes, "_service", _service)
    response = client.post(
        "/api/integrations/management/v1/secrets/value/write",
        headers={
            "Authorization": "Bearer opaque-token",
            "Idempotency-Key": "secret-write-1",
        },
        json={
            "scope": "bundle",
            "bundle_id": "workspace@1-0",
            "key": "provider.api_key",
            "value": "secret-canary",
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert calls[0]["operation"] == SECRET_WRITE_OPERATION
    assert calls[0]["resource"] == (
        "urn:kdcube:management:secret:tenant-a:project-a:bundle:"
        "workspace@1-0:provider.api_key"
    )
    assert calls[0]["body"] == {
        "scope": "bundle",
        "bundle_id": "workspace@1-0",
        "key": "provider.api_key",
        "value": "secret-canary",
    }
    assert calls[0]["approval_context"] == {
        "secret_scope": "bundle",
        "bundle_id": "workspace@1-0",
        "secret_key": "provider.api_key",
    }
    assert calls[0]["secret_target"].provider_key == (
        "bundles.workspace@1-0.secrets.provider.api_key"
    )
    assert "secret-canary" not in response.text


def test_secret_routes_reject_scope_escape_before_admission(monkeypatch) -> None:
    client = _client(monkeypatch)

    async def _must_not_build_service(_request):
        raise AssertionError("service must not be built")

    monkeypatch.setattr(routes, "_service", _must_not_build_service)
    response = client.post(
        "/api/integrations/management/v1/secrets/value/read",
        headers={
            "Authorization": "Bearer opaque-token",
            "Idempotency-Key": "secret-read-1",
        },
        json={
            "scope": "platform",
            "key": "bundles.workspace.secrets.api_key",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "secret_request_invalid"
    assert response.headers["cache-control"] == "no-store, private"


def test_human_secret_export_is_exact_pkce_bound_and_one_use(monkeypatch) -> None:
    approval_verifier = _HumanApprovalVerifier()
    client, redis, runtime = _export_client(
        monkeypatch,
        verifier=approval_verifier,
    )
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    start = client.post(
        "/api/integrations/management/v1/secrets/export/start",
        json={
            "schema": SECRET_EXPORT_REQUEST_SCHEMA,
            "callback_uri": "http://127.0.0.1:53123/callback",
            "state": "s" * 43,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "targets": [
                {
                    "scope": "platform",
                    "key": "platform.services.brave.api_key",
                },
                {
                    "scope": "bundle",
                    "bundle_id": "connection-hub@1-0",
                    "key": "connections.oauth_state_secret",
                },
            ],
        },
    )

    assert start.status_code == 200
    start_payload = start.json()
    assert start_payload["required_assurance"] == SESSION_CONFIRMATION
    assert start_payload["authorization_url"].startswith("http://testserver/")
    assert "canary::" not in start.text

    authorization = client.get(start_payload["authorization_url"])
    assert authorization.status_code == 200
    assert "platform.services.brave.api_key" in authorization.text
    assert "connections.oauth_state_secret" in authorization.text
    assert "canary::" not in authorization.text
    csrf_match = re.search(r'name="csrf" value="([A-Za-z0-9_-]+)"', authorization.text)
    assert csrf_match is not None

    approval = client.post(
        "/api/integrations/management/v1/secrets/export/authorize",
        data={
            "transaction": start_payload["transaction_id"],
            "csrf": csrf_match.group(1),
            "decision": "approve",
        },
        follow_redirects=False,
    )
    assert approval.status_code == 302
    callback = urlsplit(approval.headers["location"])
    callback_query = parse_qs(callback.query)
    assert f"{callback.scheme}://{callback.netloc}{callback.path}" == (
        "http://127.0.0.1:53123/callback"
    )
    assert callback_query["state"] == ["s" * 43]
    assert callback_query["iss"] == ["http://testserver"]
    code = callback_query["code"][0]

    result = client.post(
        "/api/integrations/management/v1/secrets/export/exchange",
        json={
            "transaction_id": start_payload["transaction_id"],
            "code": code,
            "code_verifier": verifier,
        },
    )
    assert result.status_code == 200
    result_payload = result.json()
    assert result_payload["request_digest"] == start_payload["request_digest"]
    assert result_payload["target"] == {"tenant": TENANT, "project": PROJECT}
    assert result_payload["approval"]["assurance"] == SESSION_CONFIRMATION
    assert result_payload["approval"]["method"] == "test_browser_session"
    assert isinstance(result_payload["approval"]["verified_at"], int)
    assert [item["scope"] for item in result_payload["values"]] == [
        "bundle",
        "platform",
    ]
    assert len(runtime.reads) == 2
    assert approval_verifier.phases == ["present", "commit"]

    replay = client.post(
        "/api/integrations/management/v1/secrets/export/exchange",
        json={
            "transaction_id": start_payload["transaction_id"],
            "code": code,
            "code_verifier": verifier,
        },
    )
    assert replay.status_code == 403
    assert replay.json()["error"]["code"] == "secret_export_not_approved"
    assert len(runtime.reads) == 2
    records = "\n".join(redis.values.values())
    assert code not in records
    assert "canary::" not in records


def test_human_secret_export_all_freezes_complete_provider_inventory(monkeypatch) -> None:
    client, _redis, runtime = _export_client(monkeypatch)
    runtime.inventory_targets = (
        SecretTarget(
            scope="user",
            user_id="user-1",
            key="personal.token",
        ),
        SecretTarget(
            scope="bundle",
            bundle_id="connection-hub@1-0",
            key="connections.oauth_state_secret",
        ),
        SecretTarget(
            scope="platform",
            key="platform.infra.redis.password",
        ),
    )
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")

    response = client.post(
        "/api/integrations/management/v1/secrets/export/start",
        json={
            "schema": SECRET_EXPORT_REQUEST_SCHEMA,
            "callback_uri": "http://127.0.0.1:53123/callback",
            "state": "s" * 43,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "selection": "all",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["target_count"] == 3
    assert payload["targets"] == []
    frozen_targets = [
        {
            "scope": "bundle",
            "bundle_id": "connection-hub@1-0",
            "key": "connections.oauth_state_secret",
        },
        {
            "scope": "platform",
            "key": "platform.infra.redis.password",
        },
        {
            "scope": "user",
            "user_id": "user-1",
            "key": "personal.token",
        },
    ]
    record = json.loads(next(iter(_redis.values.values())))
    assert record["request"]["targets"] == frozen_targets
    assert "selection" not in record["request"]


def test_human_secret_export_rejects_ambiguous_query_and_oversized_approval(
    monkeypatch,
) -> None:
    client, _redis, _runtime = _export_client(monkeypatch)
    duplicate_query = client.get(
        "/api/integrations/management/v1/secrets/export/authorize?"
        f"transaction={'a' * 43}&transaction={'b' * 43}"
    )
    oversized_approval = client.post(
        "/api/integrations/management/v1/secrets/export/authorize",
        content=b"decision=approve&padding=" + (b"x" * 8192),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert duplicate_query.status_code == 400
    assert "secret_export_transaction_invalid" in duplicate_query.text
    assert oversized_approval.status_code == 400
    assert "secret_export_approval_invalid" in oversized_approval.text


def test_human_secret_export_redirects_get_step_up_and_rejects_post_challenge(
    monkeypatch,
) -> None:
    verifier = _HumanApprovalChallengeVerifier()
    client, redis, runtime = _export_client(monkeypatch, verifier=verifier)
    start = client.post(
        "/api/integrations/management/v1/secrets/export/start",
        json={
            "schema": SECRET_EXPORT_REQUEST_SCHEMA,
            "callback_uri": "http://127.0.0.1:53123/callback",
            "state": "s" * 43,
            "code_challenge": base64.urlsafe_b64encode(
                hashlib.sha256(("v" * 64).encode("ascii")).digest()
            ).decode("ascii").rstrip("="),
            "code_challenge_method": "S256",
            "targets": [
                {
                    "scope": "platform",
                    "key": "platform.services.brave.api_key",
                },
            ],
        },
    )
    transaction_id = start.json()["transaction_id"]

    challenge = client.get(
        start.json()["authorization_url"],
        follow_redirects=False,
    )
    assert challenge.status_code == 302
    assert challenge.headers["location"] == (
        "https://identity.example/step-up?state=opaque"
    )
    assert verifier.contexts[0].request_digest == start.json()["request_digest"]
    assert verifier.contexts[0].transaction_id == transaction_id
    assert verifier.phases == ["present"]

    record = json.loads(next(iter(redis.values.values())))
    approval = client.post(
        "/api/integrations/management/v1/secrets/export/authorize",
        data={
            "transaction": transaction_id,
            "csrf": record["csrf_token"],
            "decision": "approve",
        },
        follow_redirects=False,
    )
    assert approval.status_code == 409
    assert "human_approval_restart_required" in approval.text
    assert json.loads(next(iter(redis.values.values())))["status"] == "pending"
    assert runtime.reads == []
    assert verifier.phases == ["present", "commit"]
