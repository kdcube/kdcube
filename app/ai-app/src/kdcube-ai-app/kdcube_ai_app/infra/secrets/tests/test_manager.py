import json
import os
from types import SimpleNamespace

import kdcube_ai_app.infra.secrets.manager as secrets_manager_module
import pytest
from kdcube_ai_app.infra.secrets import (
    AwsSecretsManagerSecretsManager,
    InMemorySecretsManager,
    KDCubeEphemeralSecretStore,
    SecretsFileSecretsManager,
    SecretsManagerConfig,
    SecretsManagerError,
    SecretsManagerWriteError,
    SecretsServiceSecretsManager,
    build_secrets_manager_config,
    build_user_secret_metadata_key,
    get_secrets_manager,
    reset_secrets_manager_cache,
)


class _FakeHttpResponse:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.mark.asyncio
async def test_provider_keys_require_explicit_scope_and_inventory_is_partitioned():
    manager = InMemorySecretsManager()
    with pytest.raises(SecretsManagerError):
        await manager.set_secret("services.fixture.token", "legacy")

    await manager.set_secret("platform.services.fixture.token", "platform")
    await manager.set_secret(
        "bundles.demo@1-0.secrets.fixture.token",
        "bundle",
    )
    await manager.set_secret("users.owner.secrets.fixture.token", "user")

    assert await manager.list_secret_keys("platform.__keys") == [
        "platform.services.fixture.token"
    ]
    assert await manager.list_secret_keys("bundles.__keys") == [
        "bundles.demo@1-0.secrets.fixture.token"
    ]
    assert await manager.list_secret_keys("users.__keys") == [
        "users.owner.secrets.fixture.token"
    ]
    assert await manager.list_all_secret_keys() == [
        "bundles.demo@1-0.secrets.fixture.token",
        "platform.services.fixture.token",
        "users.owner.secrets.fixture.token",
    ]


class _FakeSecretsHttpClient:
    def __init__(self, response: _FakeHttpResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _respond(self, method: str, url: str, **kwargs):
        self.requests.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response

    async def get(self, url: str, **kwargs):
        return await self._respond("GET", url, **kwargs)

    async def post(self, url: str, **kwargs):
        return await self._respond("POST", url, **kwargs)

    async def delete(self, url: str, **kwargs):
        return await self._respond("DELETE", url, **kwargs)


class _FakeHttpxModule:
    def __init__(self, client: _FakeSecretsHttpClient):
        self.client = client

    def AsyncClient(self, *, timeout: float):
        assert timeout > 0
        return self.client


class _QueuedSecretsHttpClient(_FakeSecretsHttpClient):
    def __init__(self, responses: list[_FakeHttpResponse]):
        super().__init__()
        self.responses = list(responses)

    async def _respond(self, method: str, url: str, **kwargs):
        self.requests.append((method, url, kwargs))
        assert self.responses, f"unexpected {method} {url}"
        return self.responses.pop(0)


class _FakeAwsClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeAwsSecretsClient:
    def __init__(self, initial: dict[str, str] | None = None):
        self.data = dict(initial or {})
        self.tags: dict[str, list[dict[str, str]]] = {}
        self.version_tokens: dict[str, str] = {}
        self.create_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.page_size: int | None = None

    async def get_secret_value(self, *, SecretId: str):
        if SecretId not in self.data:
            raise _FakeAwsClientError("ResourceNotFoundException")
        return {"SecretString": self.data[SecretId]}

    async def put_secret_value(self, *, SecretId: str, SecretString: str):
        if SecretId not in self.data:
            raise _FakeAwsClientError("ResourceNotFoundException")
        self.data[SecretId] = SecretString
        return {"ARN": SecretId}

    async def create_secret(
        self,
        *,
        Name: str,
        SecretString: str,
        Tags=None,
        ClientRequestToken: str | None = None,
    ):
        self.create_calls.append(
            {
                "Name": Name,
                "SecretString": SecretString,
                "Tags": list(Tags or []),
                "ClientRequestToken": ClientRequestToken,
            }
        )
        if Name in self.data:
            if (
                ClientRequestToken
                and self.version_tokens.get(Name) == ClientRequestToken
            ):
                if self.data[Name] == SecretString:
                    return {"ARN": Name, "VersionId": ClientRequestToken}
                raise _FakeAwsClientError("InvalidRequestException")
            raise _FakeAwsClientError("ResourceExistsException")
        self.data[Name] = SecretString
        self.tags[Name] = list(Tags or [])
        if ClientRequestToken:
            self.version_tokens[Name] = ClientRequestToken
        return {"ARN": Name, "VersionId": ClientRequestToken}

    async def delete_secret(self, *, SecretId: str, ForceDeleteWithoutRecovery: bool):
        self.delete_calls.append(
            {
                "SecretId": SecretId,
                "ForceDeleteWithoutRecovery": ForceDeleteWithoutRecovery,
            }
        )
        if SecretId not in self.data:
            raise _FakeAwsClientError("ResourceNotFoundException")
        self.data.pop(SecretId, None)
        self.tags.pop(SecretId, None)
        return {"ARN": SecretId}

    async def list_secrets(self, **request):
        self.list_calls.append(dict(request))
        prefixes = [
            str(value)
            for item in request.get("Filters") or []
            if item.get("Key") == "name"
            for value in item.get("Values") or []
        ]
        names = sorted(
            name
            for name in self.data
            if not prefixes or any(name.startswith(prefix) for prefix in prefixes)
        )
        start = int(request.get("NextToken") or 0)
        page_size = self.page_size or max(1, len(names))
        end = min(len(names), start + page_size)
        response = {
            "SecretList": [
                {"Name": name, "Tags": list(self.tags.get(name) or [])}
                for name in names[start:end]
            ]
        }
        if end < len(names):
            response["NextToken"] = str(end)
        return response


class _LostAwsCreateResponseClient(_FakeAwsSecretsClient):
    def __init__(self):
        super().__init__()
        self._lose_next_create_response = True

    async def create_secret(self, **request):
        response = await super().create_secret(**request)
        if self._lose_next_create_response:
            self._lose_next_create_response = False
            raise RuntimeError("create response was lost")
        return response


class _FakeAwsClientContext:
    def __init__(self, client: _FakeAwsSecretsClient):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeAwsSession:
    def __init__(self, client: _FakeAwsSecretsClient):
        self._client = client

    def client(self, *_args, **_kwargs):
        return _FakeAwsClientContext(self._client)


def test_build_secrets_manager_config_uses_env_and_ignores_gateway_json(monkeypatch):
    monkeypatch.setenv("GATEWAY_CONFIG_JSON", '{"secrets":{"provider":"in-memory"}}')
    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")
    monkeypatch.setenv("SECRETS_PROVIDER", "local")
    monkeypatch.setenv("SECRETS_URL", "http://kdcube-secrets:7777")
    monkeypatch.setenv("SECRETS_TOKEN", "proc-read")
    monkeypatch.setenv("SECRETS_ADMIN_TOKEN", "proc-admin")
    reset_secrets_manager_cache()

    config = build_secrets_manager_config()

    assert config.provider == "secrets-service"
    assert config.component == "proc"
    assert config.url == "http://kdcube-secrets:7777"
    assert config.token == "proc-read"
    assert config.admin_token == "proc-admin"


def _secrets_service_manager() -> SecretsServiceSecretsManager:
    return SecretsServiceSecretsManager(
        SecretsManagerConfig(
            provider="secrets-service",
            component="proc",
            url="http://kdcube-secrets:7777",
            token="read-token",
            admin_token="admin-token",
        )
    )


@pytest.mark.asyncio
async def test_secrets_service_accepts_host_vault_generation(monkeypatch):
    client = _FakeSecretsHttpClient(
        response=_FakeHttpResponse(200, {"status": "ok", "generation": 4})
    )
    monkeypatch.setattr(
        secrets_manager_module,
        "_get_httpx",
        lambda: _FakeHttpxModule(client),
    )

    await _secrets_service_manager().set_secret("platform.services.fixture.token", "secret-value")

    assert client.requests == [
        (
            "POST",
            "http://kdcube-secrets:7777/set",
            {
                "json": {"key": "platform.services.fixture.token", "value": "secret-value"},
                "headers": {"X-KDCUBE-ADMIN-TOKEN": "admin-token"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_secrets_service_create_is_atomic_and_reports_collision(monkeypatch):
    client = _QueuedSecretsHttpClient(
        [
            _FakeHttpResponse(200, {"status": "ok", "generation": 1}),
            _FakeHttpResponse(409, {"detail": "generation conflict"}),
        ]
    )
    monkeypatch.setattr(
        secrets_manager_module,
        "_get_httpx",
        lambda: _FakeHttpxModule(client),
    )
    store = KDCubeEphemeralSecretStore(
        _secrets_service_manager(), namespace="resident-secrets"
    )
    secret_ref = "a" * 32

    assert await store.create(
        secret_ref=secret_ref,
        value="original",
        expires_at=20,
    )
    assert not await store.create(
        secret_ref=secret_ref,
        value="replacement",
        expires_at=30,
    )
    assert [request[2]["json"] for request in client.requests] == [
        {
            "key": f"platform.runtime.resident-secrets.{secret_ref}",
            "value": "original",
            "expected_generation": 0,
        },
        {
            "key": f"platform.runtime.resident-secrets.{secret_ref}",
            "value": "replacement",
            "expected_generation": 0,
        },
    ]


@pytest.mark.asyncio
async def test_secrets_service_create_transport_failure_is_outcome_unknown_and_safe(
    monkeypatch,
):
    canary = "must-not-escape-create"
    client = _FakeSecretsHttpClient(error=RuntimeError(canary))
    monkeypatch.setattr(
        secrets_manager_module,
        "_get_httpx",
        lambda: _FakeHttpxModule(client),
    )

    with pytest.raises(SecretsManagerWriteError) as captured:
        await _secrets_service_manager().create_ephemeral_secret(
            namespace="resident-secrets",
            secret_ref="b" * 32,
            value=canary,
            expires_at=20,
        )

    assert str(captured.value) == "secrets-service create request outcome is unknown"
    assert canary not in str(captured.value)
    assert "platform.runtime" not in str(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ok"},
        {"status": "ok", "generation": 2},
    ],
)
async def test_secrets_service_create_requires_generation_one(
    monkeypatch,
    payload,
):
    client = _FakeSecretsHttpClient(response=_FakeHttpResponse(200, payload))
    monkeypatch.setattr(
        secrets_manager_module,
        "_get_httpx",
        lambda: _FakeHttpxModule(client),
    )

    with pytest.raises(
        SecretsManagerWriteError,
        match="does not prove ownership; outcome is unknown",
    ):
        await _secrets_service_manager().create_ephemeral_secret(
            namespace="resident-secrets",
            secret_ref="c" * 32,
            value="secret",
            expires_at=20,
        )


@pytest.mark.asyncio
async def test_secrets_service_does_not_send_inventory_mutations(monkeypatch):
    client = _FakeSecretsHttpClient(response=_FakeHttpResponse(500, {}))
    monkeypatch.setattr(
        secrets_manager_module,
        "_get_httpx",
        lambda: _FakeHttpxModule(client),
    )
    manager = _secrets_service_manager()
    metadata_key = "bundles.fixture@1-0.secrets.__keys"

    await manager.set_secret(metadata_key, '["must-not-be-stored"]')
    await manager.delete_secret(metadata_key)

    assert client.requests == []


@pytest.mark.asyncio
async def test_secrets_service_inventory_is_provider_derived_and_scope_checked(
    monkeypatch,
):
    metadata_key = "bundles.fixture@1-0.secrets.__keys"
    expected = "bundles.fixture@1-0.secrets.provider.token"
    client = _FakeSecretsHttpClient(
        response=_FakeHttpResponse(200, {"value": json.dumps([expected])})
    )
    monkeypatch.setattr(
        secrets_manager_module,
        "_get_httpx",
        lambda: _FakeHttpxModule(client),
    )

    manager = _secrets_service_manager()

    assert await manager.list_secret_keys(metadata_key) == [expected]
    assert client.requests[0][0] == "GET"

    client.response = _FakeHttpResponse(
        200,
        {"value": json.dumps(["bundles.other@1-0.secrets.provider.token"])},
    )
    with pytest.raises(SecretsManagerError, match="inventory response is invalid"):
        await manager.list_secret_keys(metadata_key)


@pytest.mark.asyncio
async def test_secrets_service_conflict_is_fixed_and_secret_safe(monkeypatch):
    canary = "must-not-escape-conflict"
    client = _FakeSecretsHttpClient(
        response=_FakeHttpResponse(409, {"detail": canary})
    )
    monkeypatch.setattr(
        secrets_manager_module,
        "_get_httpx",
        lambda: _FakeHttpxModule(client),
    )

    with pytest.raises(SecretsManagerWriteError) as captured:
        await _secrets_service_manager().set_secret("users.private.secrets.token", canary)

    assert str(captured.value) == "secrets-service set conflict"
    assert canary not in str(captured.value)
    assert "users.private.secrets.token" not in str(captured.value)


@pytest.mark.asyncio
async def test_secrets_service_transport_failure_is_fixed_and_secret_safe(monkeypatch):
    canary = "must-not-escape-transport"
    client = _FakeSecretsHttpClient(error=RuntimeError(canary))
    monkeypatch.setattr(
        secrets_manager_module,
        "_get_httpx",
        lambda: _FakeHttpxModule(client),
    )

    with pytest.raises(SecretsManagerWriteError) as captured:
        await _secrets_service_manager().set_secret("platform.services.fixture.token", canary)

    assert str(captured.value) == "secrets-service set request failed"
    assert canary not in str(captured.value)


@pytest.mark.asyncio
async def test_secrets_service_unavailable_read_fails_closed_without_key_or_body(
    monkeypatch,
    caplog,
):
    canary = "must-not-escape-read"
    client = _FakeSecretsHttpClient(
        response=_FakeHttpResponse(503, {"detail": canary})
    )
    monkeypatch.setattr(
        secrets_manager_module,
        "_get_httpx",
        lambda: _FakeHttpxModule(client),
    )

    value = await _secrets_service_manager().get_secret("users.private.secrets.token")

    with pytest.raises(SecretsManagerError) as captured:
        await _secrets_service_manager().get_secret_strict("users.private.secrets.token")

    assert value is None
    assert str(captured.value) == "Secrets service read failed"
    assert canary not in str(captured.value)
    assert canary not in caplog.text
    assert "users.private.secrets.token" not in caplog.text


def test_aws_sm_secret_path_uses_grouped_documents():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="proc",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )

    assert manager._secret_id("platform.services.openai.api_key") == "kdcube/demo/demo-march/platform/secrets"
    assert (
        manager._secret_id("bundles.react@2026-03-15.secrets.openai.api_key")
        == "kdcube/demo/demo-march/bundles/react@2026-03-15/secrets"
    )
    assert (
        manager._secret_id("users.user-1.bundles.rms@06-04-26-156.secrets.anthropic.api_key")
        == "kdcube/demo/demo-march/users/user-1/bundles/rms@06-04-26-156/secrets"
    )
    assert manager._inventory_secret_id() == "kdcube/demo/demo-march/inventory"


@pytest.mark.asyncio
async def test_aws_sm_manager_reads_grouped_documents_and_virtual_metadata():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(provider="aws-sm", component="proc", aws_sm_prefix="kdcube/demo/demo-march")
    )
    client = _FakeAwsSecretsClient(
        {
            "kdcube/demo/demo-march/inventory": json.dumps(
                {
                    "schema": "kdcube.aws_secret_inventory.v1",
                    "keys": [
                        "platform.services.openai.api_key",
                        "bundles.user-mgmt@1-0.secrets.user_management.cognito_user_pool_id",
                        "bundles.user-mgmt@1-0.secrets.user_management.dry_run",
                        "users.user-1.bundles.user-mgmt@1-0.secrets.google.refresh_token",
                    ],
                }
            ),
            "kdcube/demo/demo-march/platform/secrets": json.dumps(
                {"services": {"openai": {"api_key": "sk-openai"}}}
            ),
            "kdcube/demo/demo-march/bundles/user-mgmt@1-0/secrets": json.dumps(
                {"user_management": {"cognito_user_pool_id": "pool-123", "dry_run": "false"}}
            ),
            "kdcube/demo/demo-march/users/user-1/bundles/user-mgmt@1-0/secrets": json.dumps(
                {"google": {"refresh_token": "rt-user"}}
            ),
        }
    )
    manager._session = _FakeAwsSession(client)

    assert await manager.get_secret("platform.services.openai.api_key") == "sk-openai"
    assert (
        await manager.get_secret("bundles.user-mgmt@1-0.secrets.user_management.cognito_user_pool_id") == "pool-123"
    )
    assert (
        await manager.get_secret("users.user-1.bundles.user-mgmt@1-0.secrets.google.refresh_token") == "rt-user"
    )
    assert json.loads(await manager.get_secret("bundles.user-mgmt@1-0.secrets.__keys") or "[]") == [
        "bundles.user-mgmt@1-0.secrets.user_management.cognito_user_pool_id",
        "bundles.user-mgmt@1-0.secrets.user_management.dry_run",
    ]


@pytest.mark.asyncio
async def test_aws_inventory_is_complete_and_provider_owned() -> None:
    metadata_key = "bundles.user-mgmt@1-0.secrets.__keys"
    grouped_key = "bundles.user-mgmt@1-0.secrets.provider.grouped"
    user_key = "users.user-1.secrets.provider.token"
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="proc",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )
    client = _FakeAwsSecretsClient(
        {
            "kdcube/demo/demo-march/inventory": json.dumps(
                {
                    "schema": "kdcube.aws_secret_inventory.v1",
                    "keys": [grouped_key, user_key],
                }
            ),
            "kdcube/demo/demo-march/bundles/user-mgmt@1-0/secrets": json.dumps(
                {"provider": {"grouped": "grouped-value"}}
            ),
            "kdcube/demo/demo-march/users/user-1/secrets": json.dumps(
                {"provider": {"token": "user-value"}}
            ),
        }
    )
    manager._session = _FakeAwsSession(client)

    assert await manager.list_secret_keys(metadata_key) == [grouped_key]
    assert json.loads(await manager.get_secret(metadata_key) or "[]") == [grouped_key]
    assert await manager.list_all_secret_keys() == [grouped_key, user_key]


@pytest.mark.asyncio
async def test_aws_inventory_rejects_invalid_or_duplicate_keys() -> None:
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="proc",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )
    client = _FakeAwsSecretsClient(
        {
            "kdcube/demo/demo-march/bundles/user-mgmt@1-0/secrets/__keys": json.dumps(
                {
                    "schema": "kdcube.aws_secret_inventory.v1",
                    "keys": [
                        "bundles.other@1-0.secrets.provider.token",
                        "bundles.other@1-0.secrets.provider.token",
                    ],
                }
            )
        }
    )
    manager._session = _FakeAwsSession(client)

    client.data["kdcube/demo/demo-march/inventory"] = client.data.pop(
        "kdcube/demo/demo-march/bundles/user-mgmt@1-0/secrets/__keys"
    )
    with pytest.raises(SecretsManagerError, match="inventory response is invalid"):
        await manager.list_all_secret_keys()


@pytest.mark.asyncio
async def test_aws_sm_manager_does_not_read_legacy_aggregate_or_leafs():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(provider="aws-sm", component="proc", aws_sm_prefix="kdcube/demo/demo-march")
    )
    client = _FakeAwsSecretsClient(
        {
            "kdcube/demo/demo-march/bundles/secrets": json.dumps(
                {
                    "user-mgmt@1-0": {
                        "user_management": {
                            "cognito_user_pool_id": "pool-from-blob",
                            "sheets_key": "sheet-from-blob",
                        }
                    }
                }
            ),
            "kdcube/demo/demo-march/services/openai/api_key": "sk-legacy-openai",
        }
    )
    manager._session = _FakeAwsSession(client)

    assert await manager.get_secret(
        "bundles.user-mgmt@1-0.secrets.user_management.cognito_user_pool_id"
    ) is None
    assert await manager.get_secret(
        "bundles.user-mgmt@1-0.secrets.user_management.sheets_key"
    ) is None
    assert await manager.get_secret("platform.services.openai.api_key") is None


@pytest.mark.asyncio
async def test_aws_sm_manager_writes_and_deletes_grouped_bundle_documents():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(provider="aws-sm", component="proc", aws_sm_prefix="kdcube/demo/demo-march")
    )
    client = _FakeAwsSecretsClient()
    manager._session = _FakeAwsSession(client)

    await manager.set_secret("bundles.user-mgmt@1-0.secrets.user_management.cognito_user_pool_id", "pool-123")
    await manager.set_secret("bundles.user-mgmt@1-0.secrets.user_management.sheets_key", "sheet-1")
    await manager.set_secret("bundles.user-mgmt@1-0.secrets.__keys", json.dumps(["ignored"]))

    stored = json.loads(client.data["kdcube/demo/demo-march/bundles/user-mgmt@1-0/secrets"])
    assert stored == {
        "user_management": {
            "cognito_user_pool_id": "pool-123",
            "sheets_key": "sheet-1",
        }
    }
    inventory = json.loads(client.data["kdcube/demo/demo-march/inventory"])
    assert inventory == {
        "keys": [
            "bundles.user-mgmt@1-0.secrets.user_management.cognito_user_pool_id",
            "bundles.user-mgmt@1-0.secrets.user_management.sheets_key",
        ],
        "schema": "kdcube.aws_secret_inventory.v1",
    }

    await manager.delete_secret("bundles.user-mgmt@1-0.secrets.user_management.sheets_key")
    stored = json.loads(client.data["kdcube/demo/demo-march/bundles/user-mgmt@1-0/secrets"])
    assert stored == {"user_management": {"cognito_user_pool_id": "pool-123"}}

    await manager.delete_secret("bundles.user-mgmt@1-0.secrets.user_management.cognito_user_pool_id")
    assert "kdcube/demo/demo-march/bundles/user-mgmt@1-0/secrets" not in client.data
    assert json.loads(client.data["kdcube/demo/demo-march/inventory"])["keys"] == []


@pytest.mark.asyncio
async def test_aws_inventory_absence_and_interrupted_write_fail_closed():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="proc",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )
    client = _FakeAwsSecretsClient()
    manager._session = _FakeAwsSession(client)

    with pytest.raises(SecretsManagerError, match="not initialized"):
        await manager.list_all_secret_keys()

    client.data["kdcube/demo/demo-march/inventory"] = json.dumps(
        {
            "schema": "kdcube.aws_secret_inventory.v1",
            "keys": ["platform.services.fixture.token"],
        }
    )
    assert await manager.list_all_secret_keys() == [
        "platform.services.fixture.token"
    ]
    assert await manager.get_secret_strict("platform.services.fixture.token") is None


@pytest.mark.asyncio
async def test_aws_sm_strict_read_and_write_fail_without_clobbering(caplog):
    marker = "aws-provider-secret-marker"

    class _UnavailableAwsClient(_FakeAwsSecretsClient):
        async def get_secret_value(self, *, SecretId: str):
            raise RuntimeError(marker)

    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="proc",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )
    client = _UnavailableAwsClient(
        {
            "kdcube/demo/demo-march/platform/secrets": json.dumps(
                {"services": {"existing": {"token": "preserve-me"}}}
            )
        }
    )
    manager._session = _FakeAwsSession(client)

    assert await manager.get_secret("platform.services.new.token") is None
    with pytest.raises(SecretsManagerError) as read_error:
        await manager.get_secret_strict("platform.services.new.token")
    with pytest.raises(SecretsManagerError) as write_error:
        await manager.set_secret("platform.services.new.token", marker)

    assert json.loads(
        client.data["kdcube/demo/demo-march/platform/secrets"]
    ) == {"services": {"existing": {"token": "preserve-me"}}}
    assert str(read_error.value) == "AWS Secrets Manager read failed"
    assert str(write_error.value) == "AWS Secrets Manager read failed"
    assert marker not in caplog.text
    assert marker not in str(read_error.value)
    assert marker not in str(write_error.value)


@pytest.mark.asyncio
async def test_aws_sm_manager_cross_replica_writes_use_distributed_doc_lock(monkeypatch):
    from kdcube_ai_app.infra import namespaces
    from kdcube_ai_app.infra.redis import client as redis_client

    fake_redis = _FakeRedis()
    shared_client = _FakeAwsSecretsClient()
    monkeypatch.setattr(redis_client, "get_async_redis_client", lambda *args, **kwargs: fake_redis)

    cfg = SecretsManagerConfig(
        provider="aws-sm",
        component="proc",
        tenant="demo",
        project="demo-project",
        redis_url="redis://fake",
        aws_sm_prefix="kdcube/demo/demo-project",
    )
    manager_a = AwsSecretsManagerSecretsManager(cfg)
    manager_b = AwsSecretsManagerSecretsManager(cfg)
    manager_a._session = _FakeAwsSession(shared_client)
    manager_b._session = _FakeAwsSession(shared_client)

    secret_id = "kdcube/demo/demo-project/bundles/user-mgmt@1-0/secrets"
    lock_key = namespaces.CONFIG.BUNDLES.SECRETS_AWS_SM_LOCK_FMT.format(
        tenant="demo",
        project="demo-project",
        doc=secret_id.replace("/", ":"),
    )

    await manager_a.set_many(
        {
            "bundles.user-mgmt@1-0.secrets.user_management.cognito_user_pool_id": "pool-123",
            "bundles.user-mgmt@1-0.secrets.user_management.sheets_key": "sheet-1",
        }
    )

    assert lock_key not in fake_redis.data
    assert (
        await manager_b.get_secret("bundles.user-mgmt@1-0.secrets.user_management.cognito_user_pool_id")
        == "pool-123"
    )
    assert await manager_b.get_secret("bundles.user-mgmt@1-0.secrets.user_management.sheets_key") == "sheet-1"


def test_build_secrets_manager_config_defaults_prefix_from_tenant_and_project(monkeypatch):
    reset_secrets_manager_cache()

    config = build_secrets_manager_config(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
        )
    )

    assert config.tenant == "demo"
    assert config.project == "demo-march"
    assert config.aws_sm_prefix == "kdcube/demo/demo-march"


def test_build_secrets_manager_config_prefers_explicit_prefix_from_settings():
    reset_secrets_manager_cache()

    config = build_secrets_manager_config(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
            SECRETS_SM_PREFIX="kdcube/custom/prefix",
        )
    )

    assert config.tenant == "demo"
    assert config.project == "demo-march"
    assert config.aws_sm_prefix == "kdcube/custom/prefix"


def test_build_secrets_manager_config_uses_secrets_file_when_yaml_is_configured(monkeypatch):
    reset_secrets_manager_cache()
    monkeypatch.delenv("SECRETS_PROVIDER", raising=False)
    monkeypatch.setenv("GLOBAL_SECRETS_YAML", "file:///tmp/global-secrets.yaml")

    config = build_secrets_manager_config(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
        )
    )

    assert config.provider == "secrets-file"
    assert config.global_secrets_yaml == "file:///tmp/global-secrets.yaml"


@pytest.mark.asyncio
async def test_secrets_file_manager_reads_global_and_bundle_yaml(tmp_path, monkeypatch):
    global_file = tmp_path / "secrets.yaml"
    bundle_file = tmp_path / "bundles.secrets.yaml"
    global_file.write_text(
        "platform:\n"
        "  services:\n"
        "    openai:\n"
        "      api_key: sk-global\n"
        "    anthropic:\n"
        "      claude_code_key: sk-claude-code",
        encoding="utf-8",
    )
    bundle_file.write_text(
        "bundles:\n"
        "  version: '1'\n"
        "  items:\n"
        "    - id: 'kdcube.copilot@2026-04-03-19-05'\n"
        "      secrets:\n"
        "        telegram:\n"
        "          webhook_secret: tg-secret",
        encoding="utf-8",
    )

    monkeypatch.setenv("SECRETS_PROVIDER", "secrets-file")
    monkeypatch.setenv("GLOBAL_SECRETS_YAML", global_file.resolve().as_uri())
    monkeypatch.setenv("BUNDLE_SECRETS_YAML", bundle_file.resolve().as_uri())
    reset_secrets_manager_cache()

    manager = get_secrets_manager(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
            SECRETS_PROVIDER="secrets-file",
            GLOBAL_SECRETS_YAML=global_file.resolve().as_uri(),
            BUNDLE_SECRETS_YAML=bundle_file.resolve().as_uri(),
        )
    )

    assert await manager.get_secret("platform.services.openai.api_key") == "sk-global"
    assert await manager.get_secret("platform.services.anthropic.claude_code_key") == "sk-claude-code"
    assert (
        await manager.get_secret(
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret"
        )
        == "tg-secret"
    )
    assert json.loads(
        await manager.get_secret("bundles.kdcube.copilot@2026-04-03-19-05.secrets.__keys") or "[]"
    ) == [
        "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret"
    ]


@pytest.mark.asyncio
async def test_secrets_file_manager_writes_global_and_bundle_yaml(tmp_path, monkeypatch):
    global_file = tmp_path / "secrets.yaml"
    bundle_file = tmp_path / "bundles.secrets.yaml"

    monkeypatch.setenv("SECRETS_PROVIDER", "secrets-file")
    monkeypatch.setenv("GLOBAL_SECRETS_YAML", global_file.resolve().as_uri())
    monkeypatch.setenv("BUNDLE_SECRETS_YAML", bundle_file.resolve().as_uri())
    reset_secrets_manager_cache()

    manager = get_secrets_manager(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
            SECRETS_PROVIDER="secrets-file",
            GLOBAL_SECRETS_YAML=global_file.resolve().as_uri(),
            BUNDLE_SECRETS_YAML=bundle_file.resolve().as_uri(),
        )
    )

    assert manager.can_write() is True

    await manager.set_secret("platform.services.openai.api_key", "sk-new")
    await manager.set_many(
        {
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret": "tg-secret",
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.docs.token": "docs-secret",
        }
    )

    assert await manager.get_secret("platform.services.openai.api_key") == "sk-new"
    assert (
        await manager.get_secret(
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret"
        )
        == "tg-secret"
    )
    assert json.loads(
        await manager.get_secret("bundles.kdcube.copilot@2026-04-03-19-05.secrets.__keys") or "[]"
    ) == [
        "bundles.kdcube.copilot@2026-04-03-19-05.secrets.docs.token",
        "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret",
    ]

    assert "sk-new" in global_file.read_text(encoding="utf-8")
    bundle_text = bundle_file.read_text(encoding="utf-8")
    assert "kdcube.copilot@2026-04-03-19-05" in bundle_text
    assert "tg-secret" in bundle_text
    assert "docs-secret" in bundle_text
    if os.name == "posix":
        assert global_file.stat().st_mode & 0o777 == 0o600
        assert bundle_file.stat().st_mode & 0o777 == 0o600

    await manager.delete_secret("platform.services.openai.api_key")
    await manager.delete_many(
        [
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret",
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.docs.token",
        ]
    )

    assert await manager.get_secret("platform.services.openai.api_key") is None
    assert (
        await manager.get_secret(
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret"
        )
        is None
    )
    assert await manager.get_secret("bundles.kdcube.copilot@2026-04-03-19-05.secrets.__keys") is None


@pytest.mark.asyncio
async def test_secrets_file_manager_preserves_nonempty_exact_strings_and_skips_placeholders(
    tmp_path,
):
    global_file = tmp_path / "secrets.yaml"
    manager = SecretsFileSecretsManager(
        SecretsManagerConfig(
            provider="secrets-file",
            component="proc",
            global_secrets_yaml=global_file.resolve().as_uri(),
        )
    )

    await manager.set_many(
        {
            "platform.services.fixture.whitespace": "  exact value  ",
            "platform.services.fixture.empty": "",
        }
    )

    assert await manager.get_secret_strict("platform.services.fixture.whitespace") == (
        "  exact value  "
    )
    assert await manager.get_secret_strict("platform.services.fixture.empty") is None


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes are required")
async def test_secrets_file_manager_atomic_rewrite_repairs_public_mode(tmp_path, monkeypatch):
    global_file = tmp_path / "secrets.yaml"
    global_file.write_text("platform:\n  services: {}\n", encoding="utf-8")
    global_file.chmod(0o644)

    monkeypatch.setenv("SECRETS_PROVIDER", "secrets-file")
    monkeypatch.setenv("GLOBAL_SECRETS_YAML", global_file.resolve().as_uri())
    reset_secrets_manager_cache()

    manager = get_secrets_manager(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
            SECRETS_PROVIDER="secrets-file",
            GLOBAL_SECRETS_YAML=global_file.resolve().as_uri(),
            BUNDLE_SECRETS_YAML=None,
        )
    )

    await manager.set_secret("platform.services.demo.token", "first")
    assert global_file.stat().st_mode & 0o777 == 0o600

    await manager.set_secret("platform.services.demo.token", "second")
    assert global_file.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".secrets.yaml.tmp-*")) == []


@pytest.mark.asyncio
async def test_secrets_file_manager_reads_and_writes_user_bundle_secrets(tmp_path, monkeypatch):
    global_file = tmp_path / "secrets.yaml"
    expected_key = (
        "users.user-1.bundles.rms@06-04-26-156.secrets.anthropic.api_key"
    )
    monkeypatch.setenv("SECRETS_PROVIDER", "secrets-file")
    monkeypatch.setenv("GLOBAL_SECRETS_YAML", global_file.resolve().as_uri())
    reset_secrets_manager_cache()

    manager = get_secrets_manager(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
            SECRETS_PROVIDER="secrets-file",
            GLOBAL_SECRETS_YAML=global_file.resolve().as_uri(),
        )
    )

    await manager.set_user_secret(
        user_id="user-1",
        bundle_id="rms@06-04-26-156",
        key="anthropic.api_key",
        value="sk-user",
    )

    assert (
        await manager.get_user_secret(
            user_id="user-1",
            bundle_id="rms@06-04-26-156",
            key="anthropic.api_key",
        )
        == "sk-user"
    )
    text = global_file.read_text(encoding="utf-8")
    assert "users:" in text
    assert "user-1:" in text
    assert "rms@06-04-26-156" in text
    assert "sk-user" in text
    assert await manager.list_user_secret_keys(
        user_id="user-1",
        bundle_id="rms@06-04-26-156",
    ) == [expected_key]

    metadata_key = build_user_secret_metadata_key(
        user_id="user-1",
        bundle_id="rms@06-04-26-156",
    )
    await manager.set_secret(metadata_key, '["users.user-1.stale"]')
    assert metadata_key not in global_file.read_text(encoding="utf-8")

    await manager.delete_user_secret(
        user_id="user-1",
        bundle_id="rms@06-04-26-156",
        key="anthropic.api_key",
    )
    assert await manager.list_user_secret_keys(
        user_id="user-1",
        bundle_id="rms@06-04-26-156",
    ) == []


@pytest.mark.asyncio
async def test_in_memory_manager_reads_and_writes_user_bundle_secrets():
    manager = InMemorySecretsManager()
    expected_key = (
        "users.user-1.bundles.task-and-memo-app@1-0.secrets."
        "email.accounts.google_1.tokens"
    )

    await manager.set_user_secret(
        user_id="user-1",
        bundle_id="task-and-memo-app@1-0",
        key="email.accounts.google_1.tokens",
        value='{"access_token":"secret"}',
    )

    assert (
        await manager.get_user_secret(
            user_id="user-1",
            bundle_id="task-and-memo-app@1-0",
            key="email.accounts.google_1.tokens",
        )
        == '{"access_token":"secret"}'
    )
    assert await manager.list_user_secret_keys(
        user_id="user-1",
        bundle_id="task-and-memo-app@1-0",
    ) == [expected_key]

    await manager.delete_user_secret(
        user_id="user-1",
        bundle_id="task-and-memo-app@1-0",
        key="email.accounts.google_1.tokens",
    )
    assert (
        await manager.get_user_secret(
            user_id="user-1",
            bundle_id="task-and-memo-app@1-0",
            key="email.accounts.google_1.tokens",
        )
        is None
    )


class _FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def eval(self, _script, _keys_count, key, token):
        if self.data.get(key) == token:
            self.data.pop(key, None)
            return 1
        return 0


class _FakeAsyncRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def eval(self, _script, _keys_count, key, token):
        if self.data.get(key) == token:
            self.data.pop(key, None)
            return 1
        return 0


@pytest.mark.asyncio
async def test_secrets_file_manager_cross_replica_reads_current_yaml(tmp_path, monkeypatch):
    from kdcube_ai_app.infra import namespaces
    from kdcube_ai_app.infra.redis import client as redis_client

    global_file = tmp_path / "secrets.yaml"
    global_file.write_text(
        "platform:\n  services:\n    openai:\n      api_key: sk-old\n",
        encoding="utf-8",
    )

    fake_redis = _FakeAsyncRedis()
    monkeypatch.setattr(redis_client, "get_async_redis_client", lambda *args, **kwargs: fake_redis)

    cfg = SecretsManagerConfig(
        provider="secrets-file",
        component="proc",
        tenant="demo",
        project="demo-project",
        redis_url="redis://fake",
        global_secrets_yaml=global_file.resolve().as_uri(),
    )

    manager_a = SecretsFileSecretsManager(cfg)
    manager_b = SecretsFileSecretsManager(cfg)

    lock_key = namespaces.CONFIG.BUNDLES.SECRETS_FILE_LOCK_FMT.format(
        tenant="demo",
        project="demo-project",
    )

    assert await manager_a.get_secret("platform.services.openai.api_key") == "sk-old"
    assert await manager_b.get_secret("platform.services.openai.api_key") == "sk-old"

    await manager_a.set_secret("platform.services.openai.api_key", "sk-new")

    assert lock_key not in fake_redis.data
    assert await manager_b.get_secret("platform.services.openai.api_key") == "sk-new"


@pytest.mark.asyncio
async def test_ephemeral_store_uses_host_vault_inventory_and_purges_expired_records():
    manager = InMemorySecretsManager()
    store = KDCubeEphemeralSecretStore(manager, namespace="login-attempts")
    expired_ref = "a" * 32
    live_ref = "b" * 32

    await store.set(
        secret_ref=expired_ref,
        value=json.dumps({"expires_at": 10, "secret": "expired"}),
        expires_at=10,
    )
    await store.set(
        secret_ref=live_ref,
        value=json.dumps({"expires_at": 30, "secret": "live"}),
        expires_at=30,
    )

    assert await store.purge_expired(now=20, limit=100) == 1
    assert await store.get(secret_ref=expired_ref) is None
    assert json.loads(await store.get(secret_ref=live_ref) or "{}") == {
        "expires_at": 30,
        "secret": "live",
    }


@pytest.mark.asyncio
async def test_in_memory_ephemeral_create_preserves_existing_record():
    store = KDCubeEphemeralSecretStore(
        InMemorySecretsManager(), namespace="resident-secrets"
    )
    secret_ref = "a" * 32

    assert await store.create(
        secret_ref=secret_ref,
        value="original",
        expires_at=20,
    )
    assert not await store.create(
        secret_ref=secret_ref,
        value="replacement",
        expires_at=30,
    )
    assert await store.get(secret_ref=secret_ref) == "original"


@pytest.mark.asyncio
async def test_ephemeral_purge_uses_the_host_vault_broker_inventory(monkeypatch):
    expired_ref = "a" * 32
    live_ref = "b" * 32
    expired_key = f"platform.runtime.login-attempts.{expired_ref}"
    live_key = f"platform.runtime.login-attempts.{live_ref}"
    client = _QueuedSecretsHttpClient(
        [
            _FakeHttpResponse(200, {"value": json.dumps([live_key, expired_key])}),
            _FakeHttpResponse(200, {"value": json.dumps({"expires_at": 10})}),
            _FakeHttpResponse(204, {}),
            _FakeHttpResponse(200, {"value": json.dumps({"expires_at": 30})}),
        ]
    )
    monkeypatch.setattr(
        secrets_manager_module,
        "_get_httpx",
        lambda: _FakeHttpxModule(client),
    )
    store = KDCubeEphemeralSecretStore(
        _secrets_service_manager(), namespace="login-attempts"
    )

    assert await store.purge_expired(now=20, limit=100) == 1
    assert [method for method, _url, _kwargs in client.requests] == [
        "GET",
        "GET",
        "DELETE",
        "GET",
    ]
    assert client.requests[0][1].endswith(
        "/secret/platform.runtime.login-attempts.__keys"
    )
    assert client.requests[1][1].endswith(f"/secret/{expired_key}")
    assert client.requests[2][1].endswith(f"/secret/{expired_key}")
    assert client.requests[3][1].endswith(f"/secret/{live_key}")
    assert client.responses == []


@pytest.mark.asyncio
async def test_ephemeral_purge_is_throttled_per_manager_namespace():
    manager = InMemorySecretsManager()
    store = KDCubeEphemeralSecretStore(manager, namespace="login-attempts")
    await store.set(
        secret_ref="e" * 32,
        value=json.dumps({"expires_at": 10, "secret": "first"}),
        expires_at=10,
    )

    assert await store.purge_expired(now=20, limit=100) == 1
    await store.set(
        secret_ref="f" * 32,
        value=json.dumps({"expires_at": 10, "secret": "second"}),
        expires_at=10,
    )
    assert await store.purge_expired(now=20, limit=100) == 0
    assert await store.get(secret_ref="f" * 32) is not None


@pytest.mark.asyncio
async def test_aws_ephemeral_store_uses_dedicated_prefix_and_force_deletes():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="ingress",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )
    client = _FakeAwsSecretsClient()
    manager._session = _FakeAwsSession(client)
    store = KDCubeEphemeralSecretStore(manager, namespace="login-attempts")
    expired_ref = "c" * 32
    live_ref = "d" * 32
    expired_id = f"kdcube/demo/demo-march/runtime/login-attempts/{expired_ref}"
    live_id = f"kdcube/demo/demo-march/runtime/login-attempts/{live_ref}"

    await store.set(
        secret_ref=expired_ref,
        value=json.dumps({"expires_at": 10, "secret": "expired"}),
        expires_at=10,
    )
    await store.set(
        secret_ref=live_ref,
        value=json.dumps({"expires_at": 30, "secret": "live"}),
        expires_at=30,
    )

    assert sorted(client.data) == [expired_id, live_id]
    assert client.tags[expired_id] == [
        {"Key": "kdcube:expires-at", "Value": "10"}
    ]
    assert await store.purge_expired(now=20, limit=100) == 1
    assert expired_id not in client.data and live_id in client.data
    assert client.delete_calls == [
        {"SecretId": expired_id, "ForceDeleteWithoutRecovery": True}
    ]

    await store.delete(secret_ref=live_ref)
    assert client.delete_calls[-1] == {
        "SecretId": live_id,
        "ForceDeleteWithoutRecovery": True,
    }


@pytest.mark.asyncio
async def test_aws_ephemeral_create_preserves_existing_record():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="ingress",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )
    client = _FakeAwsSecretsClient()
    manager._session = _FakeAwsSession(client)
    store = KDCubeEphemeralSecretStore(manager, namespace="resident-secrets")
    secret_ref = "e" * 32
    secret_id = f"kdcube/demo/demo-march/runtime/resident-secrets/{secret_ref}"

    await store.set(
        secret_ref=secret_ref,
        value="original",
        expires_at=20,
    )
    assert not await store.create(
        secret_ref=secret_ref,
        value="replacement",
        expires_at=30,
    )
    assert client.data[secret_id] == "original"
    assert client.tags[secret_id] == [
        {"Key": "kdcube:expires-at", "Value": "20"}
    ]


@pytest.mark.asyncio
async def test_aws_ephemeral_create_replays_after_a_lost_success_response():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="ingress",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )
    client = _LostAwsCreateResponseClient()
    manager._session = _FakeAwsSession(client)
    store = KDCubeEphemeralSecretStore(manager, namespace="resident-secrets")
    secret_ref = "f" * 32
    secret_id = f"kdcube/demo/demo-march/runtime/resident-secrets/{secret_ref}"

    with pytest.raises(
        SecretsManagerWriteError,
        match="ephemeral create outcome is unknown",
    ):
        await store.create(
            secret_ref=secret_ref,
            value="resident bearer",
            expires_at=20,
        )

    assert await store.create(
        secret_ref=secret_ref,
        value="resident bearer",
        expires_at=20,
    )
    assert client.data[secret_id] == "resident bearer"
    assert [call["ClientRequestToken"] for call in client.create_calls] == [
        secret_ref,
        secret_ref,
    ]


@pytest.mark.asyncio
async def test_aws_ephemeral_create_uses_canonical_ref_as_idempotency_token():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="ingress",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )
    client = _FakeAwsSecretsClient()
    manager._session = _FakeAwsSession(client)
    store = KDCubeEphemeralSecretStore(manager, namespace="resident-secrets")
    canonical_ref = "f" * 32

    assert await store.create(
        secret_ref=f"  {canonical_ref.upper()}  ",
        value="resident bearer",
        expires_at=20,
    )
    assert client.create_calls[0]["ClientRequestToken"] == canonical_ref


@pytest.mark.asyncio
async def test_aws_ephemeral_purge_scans_at_most_three_pages():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="ingress",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )
    prefix = "kdcube/demo/demo-march/runtime/login-attempts/"
    client = _FakeAwsSecretsClient(
        {f"{prefix}{index:032x}": "value" for index in range(8)}
    )
    client.page_size = 1
    for name in client.data:
        client.tags[name] = [{"Key": "kdcube:expires-at", "Value": "30"}]
    manager._session = _FakeAwsSession(client)
    store = KDCubeEphemeralSecretStore(manager, namespace="login-attempts")

    assert await store.purge_expired(now=20, limit=100) == 0
    assert len(client.list_calls) == 3


@pytest.mark.asyncio
async def test_secrets_file_refuses_all_ephemeral_secret_operations(tmp_path):
    manager = SecretsFileSecretsManager(
        SecretsManagerConfig(
            provider="secrets-file",
            component="proc",
            global_secrets_yaml=(tmp_path / "bundles.secrets.yaml").resolve().as_uri(),
        )
    )
    calls = (
        manager.set_ephemeral_secret(
            namespace="login-attempts",
            secret_ref="a" * 32,
            value="secret",
            expires_at=20,
        ),
        manager.create_ephemeral_secret(
            namespace="login-attempts",
            secret_ref="a" * 32,
            value="secret",
            expires_at=20,
        ),
        manager.get_ephemeral_secret(
            namespace="login-attempts",
            secret_ref="a" * 32,
        ),
        manager.delete_ephemeral_secret(
            namespace="login-attempts",
            secret_ref="a" * 32,
        ),
        manager.purge_expired_ephemeral_secrets(
            namespace="login-attempts",
            now=20,
            limit=100,
        ),
    )
    for call in calls:
        with pytest.raises(SecretsManagerWriteError, match="tracked descriptors"):
            await call
