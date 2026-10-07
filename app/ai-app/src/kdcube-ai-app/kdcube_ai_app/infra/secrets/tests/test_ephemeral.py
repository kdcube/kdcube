from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.secrets.ephemeral import ephemeral_secret_store
from kdcube_ai_app.infra.secrets.manager import SecretsManagerError


def _settings(
    *,
    backend: str,
    admin_token: str | None = "admin-token",
) -> SimpleNamespace:
    return SimpleNamespace(
        GATEWAY_COMPONENT="proc",
        TENANT="demo-tenant",
        PROJECT="demo-project",
        SECRETS_PROVIDER="secrets-file",
        SECRETS_SERVICE_BACKEND=backend,
        SECRETS_URL=None,
        SECRETS_TOKEN=None,
        SECRETS_ADMIN_TOKEN=admin_token,
        SECRETS_AWS_SM_PREFIX=None,
        SECRETS_SM_PREFIX=None,
        GLOBAL_SECRETS_YAML="file:///config/secrets.yaml",
        BUNDLE_SECRETS_YAML="file:///config/bundles.secrets.yaml",
        REDIS_URL=None,
    )


def test_custody_does_not_switch_the_configured_file_provider() -> None:
    store = ephemeral_secret_store(
        namespace="resident-card-credentials",
        settings=_settings(backend="host-vault"),
    )

    assert store._manager.provider_type == "secrets-file"
    assert store._manager.can_write() is True


@pytest.mark.asyncio
async def test_unconfigured_file_runtime_root_does_not_qualify() -> None:
    store = ephemeral_secret_store(
        namespace="resident-card-credentials", settings=_settings(backend="ephemeral"),
    )
    assert await store.qualify_durable_backend() is False


def test_runtime_custody_ignores_backend_environment_fallbacks(monkeypatch) -> None:
    monkeypatch.setenv("SECRETS_SERVICE_BACKEND", "host-vault")
    monkeypatch.setenv("KDCUBE_SECRETS_SERVICE_BACKEND", "host-vault")

    store = ephemeral_secret_store(
        namespace="resident-card-credentials", settings=_settings(backend="ephemeral"),
    )
    assert store.provider_type == "secrets-file"


def test_runtime_custody_requires_the_selected_manager_write_lane() -> None:
    with pytest.raises(
        SecretsManagerError,
        match="not configured for runtime writes",
    ):
        ephemeral_secret_store(
            namespace="resident-card-credentials",
            manager=SimpleNamespace(provider_type="fixture", can_write=lambda: False),
        )


@pytest.mark.asyncio
async def test_writable_probe_exercises_absent_delete_without_creating() -> None:
    calls: list[tuple[str, str]] = []

    class _Manager:
        provider_type = "in-memory"

        def can_write(self) -> bool:
            return True

        async def delete_ephemeral_secret(self, *, namespace, secret_ref) -> None:
            calls.append((namespace, secret_ref))

    store = ephemeral_secret_store(
        namespace="resident-card-credentials",
        manager=_Manager(),
    )

    await store.probe_writable()

    assert len(calls) == 1
    assert calls[0][0] == "resident-card-credentials"
    assert len(calls[0][1]) == 32
