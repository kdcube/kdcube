# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Original pair custody through the common service client and encrypted vault.

PG, service routes, enrollment ACLs and encrypted disk records are real. ASGI
and native transports are in-process; physical persistent-volume classification
and signing/root keys are synthetic fixtures, not installed/live qualification.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.infra.secrets import runtime_contract, runtime_http, runtime_vault
from kdcube_ai_app.infra.secrets.host_vault import audit, broker, identity, keys, protocol, service, storage
from kdcube_ai_app.infra.secrets.issuance import issuance_secret_custody
from kdcube_ai_app.infra.secrets.tests.test_runtime_http import service_adapter
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_pair_provider import OriginalCredentialPairProvider
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_issuer import HmacOriginalRefreshSigner
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_store import PostgresOriginalRefreshStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_original_pair_provider import paired


@pytest.fixture
def encrypted_service(store, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "persistent_filesystem", lambda root: True)
    monkeypatch.setattr(keys, "persistent_filesystem", lambda root: True)
    ca = identity.HostIssuingCA.generate()
    trust_path = tmp_path / "trust.json"
    registry = identity.TrustRegistry(trust_path, ca=ca)
    namespace = protocol.SecretNamespace(store.tenant, store.project, "kdcube-runtime")
    ticket = registry.mint_ticket(deployment_id="synthetic-original-pair", namespaces=[namespace.path])
    deployment = identity.DeploymentKey.generate()
    cert, enrollment = registry.enroll(ticket_id=ticket.ticket_id, csr_pem=deployment.csr())
    key_path, data_path = tmp_path / "root-keys", tmp_path / "encrypted-records"
    root_keys = keys.FileRootKeyProvider(key_path)
    root_keys.rotate()
    r = SimpleNamespace(operations=[], key_path=key_path, data_path=data_path,
                        registry=registry, fingerprint=enrollment.fingerprint)

    def restart():
        r.registry = identity.TrustRegistry(trust_path, ca=ca)
        r.disk = storage.FileDurableSecretStore(data_path, keys.FileRootKeyProvider(key_path))
        r.vault = service.HostVaultService(store=r.disk, registry=r.registry, audit=audit.MemoryAuditSink())

    restart()

    class Transport(broker.VaultTransport):
        def call(self, request):
            r.operations.append(request.operation)
            return r.vault.handle(request.to_wire(), peer_cert_pem=cert)

    native = broker.SecretsBroker(transport=Transport(), tenant=store.tenant, project=store.project)
    policy = runtime_contract.RuntimeScopePolicy(json.dumps({
        "schema": runtime_contract.POLICY_SCHEMA,
        "read": {hashlib.sha256(b"synthetic-reader").hexdigest(): ["custody"]},
        "write": {hashlib.sha256(b"synthetic-writer").hexdigest(): ["custody"]},
    }))
    app = FastAPI()
    runtime_http.install_runtime_routes(app, policy=policy,
        store_factory=lambda namespace: runtime_vault.RuntimeVaultStore(
            broker=native, application="kdcube-runtime", namespace=namespace,
            authorized_namespaces=("custody",)))
    r.restart = restart
    r.manager = lambda: service_adapter(app, monkeypatch)
    return r


def _provider(paired, store, native, *, forbid_signing=False, namespace="custody"):
    manager = native.manager()
    create = manager.create_ephemeral_secret

    async def counted_create(**kwargs):
        made = await create(**kwargs)
        paired.creates += bool(made)
        return made

    manager.create_ephemeral_secret = counted_create
    custody = issuance_secret_custody(namespace=namespace, manager=manager)

    async def no_signing_key():
        pytest.fail("original recovery resolved a refresh signing key")

    signer = (HmacOriginalRefreshSigner(store.tenant, store.project, no_signing_key)
              if forbid_signing else paired.signer)
    provider = OriginalCredentialPairProvider(
        refresh_store=PostgresOriginalRefreshStore(pg_pool=store._pool,
            tenant=store.tenant, project=store.project),
        custody=custody, custody_namespace=namespace, refresh_signer=signer,
        card_kind="automation", refresh_ttl_seconds=180 * 86400,
        authority_factory=paired.factory)
    return provider, custody


def _disk_hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*.json")}


@pytest.mark.asyncio
async def test_original_pair_survives_encrypted_store_and_service_client_reconstruction(
    store, paired, encrypted_service,
):
    native = encrypted_service
    provider, custody = _provider(paired, store, native)
    first = await provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    original = {slot: await custody.get(secret_ref=value.receipt.secret_ref)
                for slot, value in first.items()}
    assert all(original.values())
    assert all(hashlib.sha256(original[slot].encode()).hexdigest() == value.receipt.bearer_sha256
               for slot, value in first.items())
    encrypted_bytes = b"\n".join(path.read_bytes() for path in native.data_path.rglob("*.json"))
    assert encrypted_bytes and all(value.encode() not in encrypted_bytes for value in original.values())
    before = _disk_hashes(native.data_path)
    writes_before = native.operations.count(protocol.Operation.SET)
    assert writes_before == 2 and paired.creates == 2 and paired.signs == 1

    # Rebuild the store, root-key reader, trust registry, service and manager;
    # retain only the original durable rows/files and enrolled fixture identity.
    native.restart()
    paired.no_prepare = True
    fresh, recovered_custody = _provider(paired, store, native, forbid_signing=True)
    again = await fresh.read_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert again == first
    for slot, value in again.items():
        recovered = await recovered_custody.get(secret_ref=value.receipt.secret_ref)
        assert recovered is not None
        assert hashlib.sha256(recovered.encode()).hexdigest() == value.receipt.bearer_sha256
        assert recovered == original[slot]
    assert _disk_hashes(native.data_path) == before
    assert native.operations.count(protocol.Operation.SET) == writes_before
    assert paired.creates == 2 and paired.signs == 1 and await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_ungranted_service_namespace_refuses_before_original_preparation(
    store, paired, encrypted_service,
):
    provider, _ = _provider(paired, store, encrypted_service, namespace="ungranted")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_not_durable$"):
        await provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert await counts(store) == (0, 0, 0) and paired.creates == paired.signs == 0
    assert encrypted_service.operations == []


@pytest.mark.asyncio
async def test_revoked_native_enrollment_closes_custody_without_remint(
    store, paired, encrypted_service,
):
    native = encrypted_service
    provider, custody = _provider(paired, store, native)
    first = await provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    native.registry.revoke(native.fingerprint)
    before = _disk_hashes(native.data_path)
    writes_before = native.operations.count(protocol.Operation.SET)
    for value in first.values():
        with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unavailable$"):
            await custody.get(secret_ref=value.receipt.secret_ref)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unavailable$"):
        await provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert _disk_hashes(native.data_path) == before
    assert native.operations.count(protocol.Operation.SET) == writes_before
    assert paired.creates == 2 and paired.signs == 1 and await counts(store) == (1, 1, 0)
