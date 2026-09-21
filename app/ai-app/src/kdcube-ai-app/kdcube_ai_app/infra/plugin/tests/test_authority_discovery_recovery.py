# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import json

import pytest

from connection_hub.authority_discovery import RedisAuthorityDiscovery
from connection_hub.authority_registry import AuthorityProviderSpec

from kdcube_ai_app.infra.plugin import authority_discovery
from kdcube_ai_app.infra.plugin.authority_discovery import (
    list_authority_providers,
    reconcile_authority_discovery,
)
from kdcube_ai_app.infra.plugin.bundle_loader import (
    AuthorityProviderDeclarationSpec,
    BundleInterfaceManifest,
)
from kdcube_ai_app.infra.plugin.bundle_store import BundleEntry, BundlesRegistry


class _Pipeline:
    def __init__(self, redis: "_Redis") -> None:
        self.redis = redis
        self.operations: list[tuple[str, tuple[object, ...]]] = []

    def info(self, section: str) -> "_Pipeline":
        self.operations.append(("info", (section,)))
        return self

    def get(self, key: str) -> "_Pipeline":
        self.operations.append(("get", (key,)))
        return self

    def mget(self, keys: list[str]) -> "_Pipeline":
        self.operations.append(("mget", (keys,)))
        return self

    def set(self, key: str, value: str) -> "_Pipeline":
        self.operations.append(("set", (key, value)))
        return self

    async def execute(self) -> list[object]:
        results: list[object] = []
        for operation, arguments in self.operations:
            if operation == "info":
                results.append({"run_id": self.redis.run_id})
            elif operation == "get":
                [key] = arguments
                results.append(self.redis.values.get(str(key)))
            elif operation == "mget":
                [keys] = arguments
                results.append([self.redis.values.get(str(key)) for key in keys])
            elif operation == "set":
                key, value = arguments
                self.redis.values[str(key)] = str(value)
                results.append(True)
        return results


class _Redis:
    def __init__(self) -> None:
        self.run_id = "run-a"
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def info(self, section: str) -> dict[str, str]:
        assert section == "server"
        return {"run_id": self.run_id}

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        if nx and key in self.values:
            return False
        if ex is not None:
            assert ex > 0
        self.values[key] = value
        return True

    def pipeline(self, *, transaction: bool) -> _Pipeline:
        assert transaction is True
        return _Pipeline(self)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def srem(self, key: str, value: str) -> int:
        existed = value in self.sets.get(key, set())
        self.sets.setdefault(key, set()).discard(value)
        return int(existed)

    async def delete(self, key: str) -> int:
        existed = key in self.values or key in self.sets
        self.values.pop(key, None)
        self.sets.pop(key, None)
        return int(existed)

    async def eval(self, script: str, numkeys: int, *arguments: str) -> int:
        if numkeys == 1:
            key, token = arguments
            if self.values.get(key) == token:
                self.values.pop(key, None)
                return 1
            return 0
        keys = list(arguments[:numkeys])
        argv = list(arguments[numkeys:])
        if "allowed_keys" in script:
            all_key, bundle_key, publication_key, *_record_keys = keys
            bundle_id, _ttl, raw_count, *publication_arguments = argv
            count = int(raw_count)
            allowed = set(keys[3:])
            if not self.sets.get(bundle_key, set()).issubset(allowed):
                return -1
            for record_key in list(self.sets.get(bundle_key, set())):
                raw = self.values.get(record_key)
                record = json.loads(raw) if raw else {}
                if record.get("bundle_id") == bundle_id:
                    self.values.pop(record_key, None)
                    self.sets.setdefault(all_key, set()).discard(record_key)
            self.sets.pop(bundle_key, None)
            for index in range(count):
                argument_index = index * 2
                key_index = int(publication_arguments[argument_index]) - 1
                record_key = keys[key_index]
                raw = publication_arguments[argument_index + 1]
                self.values[record_key] = raw
                self.sets.setdefault(all_key, set()).add(record_key)
                self.sets.setdefault(bundle_key, set()).add(record_key)
            self.values[publication_key] = publication_arguments[count * 2]
            return count

        all_key, record_key = keys
        member, observed = argv
        current = self.values.get(record_key)
        if current is None or current == observed:
            self.sets.setdefault(all_key, set()).discard(member)
            self.values.pop(record_key, None)
            return 1
        return 0


@pytest.mark.asyncio
async def test_source_reconciliations_serialize_durable_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _Redis()
    old_registry = BundlesRegistry(default_bundle_id="old")
    new_registry = BundlesRegistry(default_bundle_id="new")
    durable_registry = old_registry
    first_read_started = asyncio.Event()
    release_first_read = asyncio.Event()
    durable_reads = 0

    async def _registry_from_authority(tenant: str, project: str):
        nonlocal durable_reads
        assert (tenant, project) == ("tenant", "project")
        durable_reads += 1
        snapshot = durable_registry
        if durable_reads == 1:
            first_read_started.set()
            await release_first_read.wait()
        return snapshot

    async def _source(registry: BundlesRegistry):
        label = str(registry.default_bundle_id or "")
        return {
            "custom@1-0": (
                AuthorityProviderSpec(
                    authority_id="custom.identity",
                    bundle_id="custom@1-0",
                    label=label,
                ),
            )
        }

    monkeypatch.setattr(
        authority_discovery,
        "load_registry_from_authority_readonly",
        _registry_from_authority,
    )
    monkeypatch.setattr(authority_discovery, "load_authority_provider_source", _source)

    first = asyncio.create_task(
        reconcile_authority_discovery(
            registry=None,
            tenant="tenant",
            project="project",
            redis=redis,
        )
    )
    await first_read_started.wait()
    durable_registry = new_registry
    second = asyncio.create_task(
        reconcile_authority_discovery(
            registry=None,
            tenant="tenant",
            project="project",
            redis=redis,
        )
    )
    await asyncio.sleep(0)
    assert durable_reads == 1

    release_first_read.set()
    await asyncio.gather(first, second)

    discovery = RedisAuthorityDiscovery(redis, tenant="tenant", project="project")
    providers = await discovery.list_providers()
    assert [item.label for item in providers] == ["new"]
    assert durable_reads == 2


@pytest.mark.asyncio
async def test_one_list_reads_through_to_manifests_after_redis_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _Redis()
    registry = BundlesRegistry(
        default_bundle_id="custom@1-0",
        bundles={
            "custom@1-0": BundleEntry(
                id="custom@1-0",
                path="/unused/custom",
                module="entrypoint",
            )
        },
    )
    manifest = BundleInterfaceManifest(
        bundle_id="custom@1-0",
        authority_providers=(
            AuthorityProviderDeclarationSpec(
                method_name="custom_identity_provider",
                authority_id="custom.identity",
                authenticator_id="custom.identity.oauth",
            ),
        ),
    )
    manifest_reads: list[str] = []
    resolved_sources: list[tuple[str, str]] = []

    async def _resolve(application_id, payload, *, source: str):
        resolved_sources.append((application_id, source))
        return {**payload, "path": "/resolved/custom"}

    def _manifest(spec, *, bundle_id: str):
        assert spec.path == "/resolved/custom"
        manifest_reads.append(bundle_id)
        return manifest

    monkeypatch.setattr(
        authority_discovery,
        "resolve_git_bundle_entry_async",
        _resolve,
    )
    monkeypatch.setattr(authority_discovery, "load_bundle_manifest", _manifest)
    async def _registry_from_authority(tenant: str, project: str):
        assert (tenant, project) == ("tenant", "project")
        return registry

    monkeypatch.setattr(
        authority_discovery,
        "load_registry_from_authority_readonly",
        _registry_from_authority,
    )

    first = await reconcile_authority_discovery(
        registry=registry,
        tenant="tenant",
        project="project",
        redis=redis,
        force=True,
    )
    discovery = RedisAuthorityDiscovery(redis, tenant="tenant", project="project")
    assert first.reconciled is True
    assert [item.authority_id for item in await discovery.list_providers()] == [
        "custom.identity"
    ]

    redis.run_id = "run-after-restart"
    assert await discovery.list_providers() == []

    recovered = await list_authority_providers(
        tenant="tenant",
        project="project",
        redis=redis,
    )

    assert [item.authority_id for item in recovered] == [
        "custom.identity"
    ]
    assert manifest_reads == ["custom@1-0", "custom@1-0"]
    assert resolved_sources == [
        ("custom@1-0", "authority.discovery.reconcile"),
        ("custom@1-0", "authority.discovery.reconcile"),
    ]
