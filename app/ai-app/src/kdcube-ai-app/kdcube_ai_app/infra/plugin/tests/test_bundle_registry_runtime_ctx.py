from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.plugin import bundle_registry
from kdcube_ai_app.infra.plugin import bundle_refs
from kdcube_ai_app.infra.plugin import git_bundle
from kdcube_ai_app.infra.plugin.bundle_store import BundleEntry, BundlesRegistry


class _FakeRedis:
    def __init__(self, data=None):
        self.data = data or {}

    async def get(self, key):
        return self.data.get(key)


@pytest.mark.asyncio
async def test_load_persisted_registry_from_runtime_ctx_prefers_state_redis_async(monkeypatch):
    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")
    redis = _FakeRedis()
    runtime_ctx = SimpleNamespace(redis_async=redis)

    async def _load_store_registry(redis_client, tenant, project):
        assert redis_client is redis
        assert tenant == "tenant-a"
        assert project == "project-a"
        return BundlesRegistry(
            default_bundle_id="bundle.demo",
            bundles={
                "bundle.demo": BundleEntry(
                    id="bundle.demo",
                    path="/bundles/bundle.demo",
                    module="entrypoint",
                )
            },
        )

    monkeypatch.setattr(bundle_registry, "_load_store_registry", _load_store_registry, raising=False)

    reg = await bundle_registry.load_persisted_registry_from_runtime_ctx(
        runtime_ctx,
        "tenant-a",
        "project-a",
    )

    assert reg is not None
    assert reg.default_bundle_id == "bundle.demo"


@pytest.mark.asyncio
async def test_resolve_default_bundle_id_from_runtime_ctx_falls_back_to_middleware_redis(monkeypatch):
    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")
    redis = _FakeRedis()
    runtime_ctx = SimpleNamespace(middleware=SimpleNamespace(redis=redis))

    async def _load_store_registry(redis_client, tenant, project):
        assert redis_client is redis
        return BundlesRegistry(
            default_bundle_id="bundle.demo",
            bundles={
                "bundle.demo": BundleEntry(
                    id="bundle.demo",
                    path="/bundles/bundle.demo",
                    module="entrypoint",
                )
            },
        )

    monkeypatch.setattr(bundle_registry, "_load_store_registry", _load_store_registry, raising=False)

    resolved = await bundle_registry.resolve_default_bundle_id_from_runtime_ctx(
        runtime_ctx,
        "tenant-a",
        "project-a",
    )

    assert resolved == "bundle.demo"


@pytest.mark.asyncio
async def test_resolve_default_bundle_id_from_runtime_ctx_rejects_missing_default(monkeypatch):
    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")
    runtime_ctx = SimpleNamespace(redis_async=_FakeRedis())

    async def _load_store_registry(redis_client, tenant, project):
        del redis_client, tenant, project
        return BundlesRegistry(
            default_bundle_id="bundle.missing",
            bundles={
                "bundle.demo": BundleEntry(
                    id="bundle.demo",
                    path="/bundles/bundle.demo",
                    module="entrypoint",
                )
            },
        )

    monkeypatch.setattr(bundle_registry, "_load_store_registry", _load_store_registry, raising=False)

    resolved = await bundle_registry.resolve_default_bundle_id_from_runtime_ctx(
        runtime_ctx,
        "tenant-a",
        "project-a",
    )

    assert resolved is None


@pytest.mark.asyncio
async def test_ingress_loads_registry_from_readonly_authority(monkeypatch):
    monkeypatch.setenv("GATEWAY_COMPONENT", "ingress")
    runtime_ctx = SimpleNamespace(redis_async=_FakeRedis())
    expected = BundlesRegistry(
        default_bundle_id="bundle.demo",
        bundles={
            "bundle.demo": BundleEntry(
                id="bundle.demo",
                path="/bundles/bundle.demo",
                module="entrypoint",
            )
        },
    )

    async def _load_store_registry_readonly(tenant, project):
        assert tenant == "tenant-a"
        assert project == "project-a"
        return expected

    async def _load_store_registry(redis_client, tenant, project):
        raise AssertionError("ingress must not load descriptor-backed bundle store through proc path")

    monkeypatch.setattr(bundle_registry, "_load_store_registry_readonly", _load_store_registry_readonly, raising=False)
    monkeypatch.setattr(bundle_registry, "_load_store_registry", _load_store_registry, raising=False)

    reg = await bundle_registry.load_persisted_registry_from_runtime_ctx(
        runtime_ctx,
        "tenant-a",
        "project-a",
    )

    assert reg is expected


@pytest.mark.asyncio
async def test_ingress_falls_back_to_redis_cache_when_readonly_authority_is_empty(monkeypatch):
    from kdcube_ai_app.infra.plugin.bundle_store import redis_key

    monkeypatch.setenv("GATEWAY_COMPONENT", "ingress")
    raw = BundlesRegistry(
        default_bundle_id="bundle.demo",
        bundles={
            "bundle.demo": BundleEntry(
                id="bundle.demo",
                path="/bundles/bundle.demo",
                module="entrypoint",
            )
        },
    ).model_dump_json()
    redis = _FakeRedis({redis_key("tenant-a", "project-a"): raw})
    runtime_ctx = SimpleNamespace(redis_async=redis)

    async def _load_store_registry_readonly(tenant, project):
        del tenant, project
        return None

    async def _load_store_registry(redis_client, tenant, project):
        raise AssertionError("ingress must not load descriptor-backed bundle store")

    monkeypatch.setattr(bundle_registry, "_load_store_registry_readonly", _load_store_registry_readonly, raising=False)
    monkeypatch.setattr(bundle_registry, "_load_store_registry", _load_store_registry, raising=False)

    reg = await bundle_registry.load_persisted_registry_from_runtime_ctx(
        runtime_ctx,
        "tenant-a",
        "project-a",
    )

    assert reg is not None
    assert reg.default_bundle_id == "bundle.demo"


@pytest.mark.asyncio
async def test_ingress_rejects_missing_registry_cache_without_descriptor_load(monkeypatch):
    monkeypatch.setenv("GATEWAY_COMPONENT", "ingress")
    runtime_ctx = SimpleNamespace(redis_async=_FakeRedis())

    async def _load_store_registry_readonly(tenant, project):
        del tenant, project
        return None

    async def _load_store_registry(redis_client, tenant, project):
        raise AssertionError("ingress must not load descriptor-backed bundle store")

    monkeypatch.setattr(bundle_registry, "_load_store_registry_readonly", _load_store_registry_readonly, raising=False)
    monkeypatch.setattr(bundle_registry, "_load_store_registry", _load_store_registry, raising=False)

    reg = await bundle_registry.load_persisted_registry_from_runtime_ctx(
        runtime_ctx,
        "tenant-a",
        "project-a",
    )

    assert reg is None


@pytest.mark.asyncio
async def test_apply_git_resolution_warns_once_for_missing_local_path_bundle(monkeypatch, caplog, tmp_path):
    bundle_registry._MISSING_PATH_WARNED.clear()
    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")

    reg = {
        "demo.local": {
            "id": "demo.local",
            "path": str(tmp_path / "missing-local-bundle"),
            "module": "entrypoint",
        }
    }

    with caplog.at_level("WARNING"):
        await bundle_registry._apply_git_resolution(reg, source="test")
        await bundle_registry._apply_git_resolution(reg, source="test")

    matches = [r for r in caplog.records if "local-path bundle" in r.message]
    assert len(matches) == 1
    assert "demo.local" in matches[0].message


@pytest.mark.asyncio
async def test_apply_git_resolution_materializes_stale_existing_git_path(monkeypatch, tmp_path):
    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")
    monkeypatch.setattr(
        bundle_registry,
        "get_settings",
        lambda: SimpleNamespace(
            PLATFORM=SimpleNamespace(
                APPLICATIONS=SimpleNamespace(
                    GIT=SimpleNamespace(
                        BUNDLE_GIT_RESOLUTION_ENABLED=True,
                        BUNDLE_GIT_ATOMIC=True,
                        BUNDLE_GIT_ALWAYS_PULL=False,
                    )
                )
            )
        ),
    )
    bundle_root = tmp_path / "repo-old" / "bundle"
    bundle_root.mkdir(parents=True)
    materialized_root = tmp_path / "repo-new"
    materialized_bundle = materialized_root / "bundle"
    ensure_calls = []

    async def _git_bundle_cache_status(**kwargs):
        assert kwargs["bundle_id"] == "demo.git"
        return SimpleNamespace(
            current=False,
            reason="git_ref_mismatch",
            paths=SimpleNamespace(bundle_root=bundle_root),
            marker={"commit": "old"},
        )

    async def _ensure_git_bundle(**kwargs):
        ensure_calls.append(kwargs)
        materialized_bundle.mkdir(parents=True)
        return SimpleNamespace(
            repo_root=materialized_root,
            bundle_root=materialized_bundle,
        )

    async def _cleanup_old_git_bundles_async(**kwargs):
        return None

    async def _fake_create_subprocess_exec(*args, **kwargs):
        class _Proc:
            returncode = 0

            async def communicate(self):
                return (b"new-commit\n", b"")

        return _Proc()

    monkeypatch.setattr(git_bundle, "git_bundle_cache_status", _git_bundle_cache_status)
    monkeypatch.setattr(git_bundle, "ensure_git_bundle", _ensure_git_bundle)
    monkeypatch.setattr(git_bundle, "cleanup_old_git_bundles_async", _cleanup_old_git_bundles_async)
    monkeypatch.setattr(git_bundle, "resolve_managed_bundles_root", lambda: tmp_path)
    monkeypatch.setattr(bundle_refs, "get_local_active_paths", lambda: set())
    monkeypatch.setattr(bundle_registry.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    resolved = await bundle_registry._apply_git_resolution(
        {
            "demo.git": {
                "id": "demo.git",
                "repo": "https://example.invalid/repo.git",
                "ref": "new-ref",
                "subdir": "bundle",
                "path": str(bundle_root),
                "module": "entrypoint",
            }
        },
        source="test",
    )

    assert [call["bundle_id"] for call in ensure_calls] == ["demo.git"]
    assert resolved["demo.git"]["path"] == str(materialized_bundle)
    assert resolved["demo.git"]["git_commit"] == "new-commit"


@pytest.mark.asyncio
async def test_apply_git_resolution_skips_only_current_git_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")
    monkeypatch.setattr(
        bundle_registry,
        "get_settings",
        lambda: SimpleNamespace(
            PLATFORM=SimpleNamespace(
                APPLICATIONS=SimpleNamespace(
                    GIT=SimpleNamespace(
                        BUNDLE_GIT_RESOLUTION_ENABLED=True,
                        BUNDLE_GIT_ATOMIC=True,
                        BUNDLE_GIT_ALWAYS_PULL=False,
                    )
                )
            )
        ),
    )
    bundle_root = tmp_path / "repo-current" / "bundle"
    bundle_root.mkdir(parents=True)
    ensure_calls = []

    async def _git_bundle_cache_status(**kwargs):
        return SimpleNamespace(
            current=True,
            reason="current",
            paths=SimpleNamespace(bundle_root=bundle_root),
            marker={"commit": "current-commit"},
        )

    async def _ensure_git_bundle(**kwargs):
        ensure_calls.append(kwargs)

    monkeypatch.setattr(git_bundle, "git_bundle_cache_status", _git_bundle_cache_status)
    monkeypatch.setattr(git_bundle, "ensure_git_bundle", _ensure_git_bundle)
    monkeypatch.setattr(git_bundle, "resolve_managed_bundles_root", lambda: tmp_path)

    resolved = await bundle_registry._apply_git_resolution(
        {
            "demo.git": {
                "id": "demo.git",
                "repo": "https://example.invalid/repo.git",
                "ref": "current-ref",
                "subdir": "bundle",
                "path": str(bundle_root),
                "module": "entrypoint",
            }
        },
        source="test",
    )

    assert ensure_calls == []
    assert resolved["demo.git"]["path"] == str(bundle_root)
    assert resolved["demo.git"]["git_commit"] == "current-commit"


def test_resolve_bundle_warns_once_for_missing_local_path_bundle(monkeypatch, caplog, tmp_path):
    bundle_registry._MISSING_PATH_WARNED.clear()
    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")
    missing = str(tmp_path / "missing-local-bundle")
    bundle_registry.set_registry(
        {"demo.local": {"path": missing, "module": "entrypoint"}},
        "demo.local",
        resolve_git=False,
        source="test",
    )

    with caplog.at_level("WARNING"):
        spec = bundle_registry.resolve_bundle("demo.local")
        spec_again = bundle_registry.resolve_bundle("demo.local")

    assert spec is not None
    assert spec.path == missing
    matches = [r for r in caplog.records if "local-path bundle" in r.message]
    assert len(matches) == 1
    assert "demo.local" in matches[0].message
