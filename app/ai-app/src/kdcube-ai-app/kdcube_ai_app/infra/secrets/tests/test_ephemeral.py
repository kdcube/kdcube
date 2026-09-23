from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.secrets.ephemeral import ephemeral_secret_store
from kdcube_ai_app.infra.secrets.manager import SecretsManagerError


def _settings(*, backend: str) -> SimpleNamespace:
    return SimpleNamespace(
        GATEWAY_COMPONENT="proc",
        TENANT="demo-tenant",
        PROJECT="demo-project",
        SECRETS_PROVIDER="secrets-file",
        SECRETS_SERVICE_BACKEND=backend,
        SECRETS_URL=None,
        SECRETS_TOKEN=None,
        SECRETS_ADMIN_TOKEN=None,
        SECRETS_AWS_SM_PREFIX=None,
        SECRETS_SM_PREFIX=None,
        GLOBAL_SECRETS_YAML="file:///config/secrets.yaml",
        BUNDLE_SECRETS_YAML="file:///config/bundles.secrets.yaml",
        REDIS_URL=None,
    )


def test_host_vault_shadow_uses_broker_for_runtime_secret_custody() -> None:
    store = ephemeral_secret_store(
        namespace="resident-card-credentials",
        settings=_settings(backend="host-vault"),
    )

    assert store._manager.provider_type == "secrets-service"
    assert store._manager._url == "http://kdcube-secrets:7777"
    assert store._manager.can_write() is True


def test_ephemeral_sidecar_does_not_become_durable_runtime_custody() -> None:
    with pytest.raises(SecretsManagerError, match="require the host vault"):
        ephemeral_secret_store(
            namespace="resident-card-credentials",
            settings=_settings(backend="ephemeral"),
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
