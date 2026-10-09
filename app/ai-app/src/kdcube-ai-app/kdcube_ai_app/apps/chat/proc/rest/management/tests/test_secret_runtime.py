from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from kdcube_ai_app.apps.chat.proc.rest.management.secret_contracts import (
    BUNDLE_SCOPE,
    MAX_SECRET_VALUE_BYTES,
    PLATFORM_SCOPE,
    SecretTarget,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_runtime import (
    KDCubeSecretRuntime,
    ManagementSecretNotFound,
    ManagementSecretsProviderReadOnly,
    ManagementSecretsProviderUnavailable,
)
from starlette.requests import Request


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def set(self, key: str, value: str, **_kwargs: Any) -> bool:
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.values.pop(key, None) is not None else 0

    async def publish(self, channel: str, value: str) -> int:
        self.published.append((channel, value))
        return 1


class _Manager:
    provider_type = "fixture"

    def __init__(self, *, writable: bool = True) -> None:
        self.data: dict[str, str] = {}
        self.writable = writable

    async def get_secret(self, key: str) -> str | None:
        return self.data.get(key)

    async def get_secret_strict(self, key: str) -> str | None:
        return await self.get_secret(key)

    def can_write(self) -> bool:
        return self.writable

    async def set_secret(self, key: str, value: str) -> None:
        self.data[key] = value

    async def delete_secret(self, key: str) -> None:
        self.data.pop(key, None)

    async def set_many(self, values: dict[str, str]) -> None:
        self.data.update(values)

    async def list_all_secret_keys(self) -> list[str]:
        return sorted(self.data)


def _request(redis: _Redis) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(redis_async=redis))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("runtime.example", 443),
            "app": app,
        }
    )


@pytest.mark.asyncio
async def test_bundle_secret_lifecycle_invalidates_derived_inventory_cache() -> None:
    redis = _Redis()
    manager = _Manager()
    runtime = KDCubeSecretRuntime(
        _request(redis),
        tenant="tenant-a",
        project="project-a",
        manager=manager,
    )
    target = SecretTarget(
        scope=BUNDLE_SCOPE,
        bundle_id="workspace@1-0",
        key="provider.api_key",
    )
    redis.values[
        "kdcube:config:bundles:secrets:tenant-a:project-a:workspace@1-0"
    ] = json.dumps([target.provider_key])

    created = await runtime.write(
        target,
        value="secret-canary",
        caller_profile="devops-agent",
    )
    metadata = await runtime.metadata(target)
    disclosed = await runtime.read(target)
    deleted = await runtime.delete(target, caller_profile="devops-agent")

    metadata_key = "bundles.workspace@1-0.secrets.__keys"
    assert created == {
        "scope": "bundle",
        "bundle_id": "workspace@1-0",
        "key": "provider.api_key",
        "created": True,
        "provider": "fixture",
        "state": "stored",
    }
    assert metadata["exists"] is True
    assert disclosed["value"] == "secret-canary"
    assert deleted["existed"] is True
    assert target.provider_key not in manager.data
    assert metadata_key not in manager.data
    assert redis.values == {}
    assert redis.published
    assert "secret-canary" not in str(created)
    assert "secret-canary" not in str(metadata)
    assert "secret-canary" not in str(deleted)
    assert "secret-canary" not in str(redis.values)
    assert "secret-canary" not in str(redis.published)


@pytest.mark.asyncio
async def test_bundle_write_does_not_mutate_legacy_inventory_record() -> None:
    manager = _Manager()
    metadata_key = "bundles.workspace@1-0.secrets.__keys"
    manager.data[metadata_key] = json.dumps(
        ["bundles.workspace@1-0.secrets.existing.token"]
    )
    runtime = KDCubeSecretRuntime(
        _request(_Redis()),
        tenant="tenant-a",
        project="project-a",
        manager=manager,
    )

    await runtime.write(
        SecretTarget(
            scope=BUNDLE_SCOPE,
            bundle_id="workspace@1-0",
            key="new.token",
        ),
        value="new-value",
        caller_profile="devops-agent",
    )

    assert json.loads(manager.data[metadata_key]) == [
        "bundles.workspace@1-0.secrets.existing.token"
    ]
    assert manager.data["bundles.workspace@1-0.secrets.new.token"] == "new-value"


@pytest.mark.asyncio
async def test_platform_secret_does_not_enter_bundle_metadata() -> None:
    manager = _Manager()
    runtime = KDCubeSecretRuntime(
        _request(_Redis()),
        tenant="tenant-a",
        project="project-a",
        manager=manager,
    )
    target = SecretTarget(
        scope=PLATFORM_SCOPE,
        key="platform.services.brave.api_key",
    )

    await runtime.write(
        target,
        value="platform-canary",
        caller_profile="devops-agent",
    )

    assert manager.data == {
        "platform.services.brave.api_key": "platform-canary"
    }


@pytest.mark.asyncio
async def test_missing_secret_and_read_only_provider_fail_closed() -> None:
    manager = _Manager(writable=False)
    runtime = KDCubeSecretRuntime(
        _request(_Redis()),
        tenant="tenant-a",
        project="project-a",
        manager=manager,
    )
    target = SecretTarget(
        scope=BUNDLE_SCOPE,
        bundle_id="workspace@1-0",
        key="provider.api_key",
    )

    with pytest.raises(ManagementSecretNotFound):
        await runtime.read(target)
    with pytest.raises(ManagementSecretsProviderReadOnly):
        await runtime.write(
            target,
            value="must-not-write",
            caller_profile="devops-agent",
        )
    assert manager.data == {}


@pytest.mark.asyncio
async def test_inventory_and_lifecycle_include_predeclared_bundle_secrets() -> None:
    manager = _Manager()
    target = SecretTarget(
        scope=BUNDLE_SCOPE,
        bundle_id="future.bundle@1-0",
        key="provider.api_key",
    )
    manager.data[target.provider_key] = "portable-canary"
    runtime = KDCubeSecretRuntime(
        _request(_Redis()),
        tenant="tenant-a",
        project="project-a",
        manager=manager,
    )

    assert await runtime.inventory() == (target,)
    assert (await runtime.read(target))["value"] == "portable-canary"
    await runtime.write(
        target,
        value="replacement-canary",
        caller_profile="devops-agent",
    )
    assert manager.data[target.provider_key] == "replacement-canary"


@pytest.mark.asyncio
async def test_provider_exception_text_is_normalized_before_orchestration() -> None:
    marker = "provider-secret-marker"

    class _ExplodingManager(_Manager):
        async def set_secret(self, key: str, value: str) -> None:
            raise RuntimeError(f"provider failed with {value}")

        async def delete_secret(self, key: str) -> None:
            raise RuntimeError(f"provider failed with {marker}")

    runtime = KDCubeSecretRuntime(
        _request(_Redis()),
        tenant="tenant-a",
        project="project-a",
        manager=_ExplodingManager(),
    )
    target = SecretTarget(
        scope=PLATFORM_SCOPE,
        key="platform.services.brave.api_key",
    )

    with pytest.raises(ManagementSecretsProviderUnavailable) as write_error:
        await runtime.write(
            target,
            value=marker,
            caller_profile="devops-agent",
        )
    with pytest.raises(ManagementSecretsProviderUnavailable) as delete_error:
        await runtime.delete(target, caller_profile="devops-agent")

    assert marker not in str(write_error.value)
    assert marker not in str(delete_error.value)


@pytest.mark.asyncio
async def test_strict_provider_read_failure_is_not_reported_as_absence() -> None:
    class _UnavailableManager(_Manager):
        async def get_secret(self, key: str) -> str | None:
            return None

        async def get_secret_strict(self, key: str) -> str | None:
            raise RuntimeError("provider-response-must-not-escape")

    runtime = KDCubeSecretRuntime(
        _request(_Redis()),
        tenant="tenant-a",
        project="project-a",
        manager=_UnavailableManager(),
    )
    target = SecretTarget(
        scope=PLATFORM_SCOPE,
        key="platform.services.brave.api_key",
    )

    with pytest.raises(ManagementSecretsProviderUnavailable) as captured:
        await runtime.metadata(target)

    assert str(captured.value) == (
        "The configured secrets provider could not read the secret"
    )
    assert "provider-response-must-not-escape" not in str(captured.value)


@pytest.mark.asyncio
async def test_management_read_rejects_oversized_existing_value() -> None:
    manager = _Manager()
    target = SecretTarget(
        scope=PLATFORM_SCOPE,
        key="platform.services.fixture.api_key",
    )
    manager.data[target.provider_key] = "x" * (MAX_SECRET_VALUE_BYTES + 1)
    runtime = KDCubeSecretRuntime(
        _request(_Redis()),
        tenant="tenant-a",
        project="project-a",
        manager=manager,
    )

    assert (await runtime.metadata(target))["exists"] is True
    with pytest.raises(ManagementSecretsProviderUnavailable):
        await runtime.read(target)


@pytest.mark.asyncio
async def test_a_platform_secret_change_is_broadcast_and_cleared_in_proc_and_ingress() -> None:
    """W675 review (claude-ops): platform.* writes were never broadcast, so non-file backends kept a cached
    platform value in every process for the TTL. They now publish bundles.secrets.update (scope platform,
    empty bundle id, the exact key), which the proc and ingress listeners turn into an exact cache clear."""
    from kdcube_ai_app.apps.chat.sdk import config_cache
    from kdcube_ai_app.infra.secrets.projections import apply_bundle_secret_update

    redis = _Redis()
    runtime = KDCubeSecretRuntime(_request(redis), tenant="tenant-a", project="project-a", manager=_Manager())
    target = SecretTarget(scope=PLATFORM_SCOPE, key="platform.services.stripe.secret_key")
    for operation in ("write", "delete"):
        config_cache.clear_secret_cache()
        mine = ("provider", "tenant-a", "project-a", target.provider_key)
        other = ("provider", "tenant-a", "project-a", "platform.services.openai.api_key")
        config_cache.set_secret_cache(mine, "stale")
        config_cache.set_secret_cache(other, "kept")
        redis.published.clear()
        if operation == "write":
            await runtime.write(target, value="secret-canary", caller_profile="devops-agent")
        else:
            await runtime.delete(target, caller_profile="devops-agent")
        assert len(redis.published) == 1
        channel, data = redis.published[0]
        event = json.loads(data)
        assert channel == "kdcube:config:bundles:secrets:update:tenant-a:project-a"
        assert (event["scope"], event["bundle_id"], event["keys"]) == ("platform", "", [target.provider_key])
        assert "secret-canary" not in data
        assert redis.values == {}  # no per-bundle inventory projection for a platform key
        # Ingress handler (the processor's listener performs the same clear).
        assert apply_bundle_secret_update(data, tenant="tenant-a", project="project-a") == 1
        assert config_cache.get_secret_cache(mine) == (False, None)
        assert config_cache.get_secret_cache(other) == (True, "kept")
    config_cache.clear_secret_cache()
