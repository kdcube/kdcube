from __future__ import annotations

from connection_hub.delegated_credentials.cards.credential_handles import (
    PostgresCardCredentialHandleStore,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.cards import (
    credential_handles,
)


def test_postgres_handle_store_binds_metadata_to_host_secret_custody(
    monkeypatch,
) -> None:
    pg_pool = object()
    secret_store = object()
    settings = object()
    captured: dict[str, object] = {}

    def fake_ephemeral_secret_store(*, namespace, settings):
        captured.update(namespace=namespace, settings=settings)
        return secret_store

    monkeypatch.setattr(
        credential_handles,
        "ephemeral_secret_store",
        fake_ephemeral_secret_store,
    )

    store = credential_handles.postgres_card_credential_handle_store(
        pg_pool=pg_pool,
        tenant="tenant-a",
        project="project-a",
        settings=settings,
    )

    assert isinstance(store, PostgresCardCredentialHandleStore)
    assert store._metadata._pool is pg_pool
    assert store._metadata.tenant == "tenant-a"
    assert store._metadata.project == "project-a"
    assert store._resident_secrets._metadata is store._metadata
    assert store._resident_secrets._secrets is secret_store
    assert captured == {
        "namespace": credential_handles.RESIDENT_CARD_SECRET_NAMESPACE,
        "settings": settings,
    }


def test_postgres_handle_store_reuses_preflighted_secret_custody(
    monkeypatch,
) -> None:
    secret_store = object()

    def unexpected_secret_store(*_args, **_kwargs):
        raise AssertionError("preflighted custody must be reused")

    monkeypatch.setattr(
        credential_handles,
        "resident_card_secret_store",
        unexpected_secret_store,
    )

    store = credential_handles.postgres_card_credential_handle_store(
        pg_pool=object(),
        tenant="tenant-a",
        project="project-a",
        secret_store=secret_store,
    )

    assert store._resident_secrets._secrets is secret_store
