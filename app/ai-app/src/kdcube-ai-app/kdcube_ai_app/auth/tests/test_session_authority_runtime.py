from __future__ import annotations

from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.authority_config import (
    DurableAuthorityConfig,
)
from kdcube_ai_app.auth.bundle.sessions import BundleSessionAuthority
from kdcube_ai_app.auth.session_authority_runtime import (
    KDCUBE_SESSION_AUTHORITY_FAMILIES,
    KDCUBE_BUNDLE_SESSION_AUTHORITY_FAMILIES,
    SessionAuthorityUnavailable,
    activate_session_authority_stores,
    bundle_session_store_for,
    configure_session_authority,
    prepare_configured_session_authority,
    reset_session_authority_runtime_for_tests,
)
from kdcube_ai_app.auth.sessions import SessionManager


@pytest.fixture(autouse=True)
def _clean_runtime_registry():
    reset_session_authority_runtime_for_tests()
    yield
    reset_session_authority_runtime_for_tests()


def _config(
    backend: str = "postgresql",
    generation_id: str = "durable-authority-v1",
) -> DurableAuthorityConfig:
    return DurableAuthorityConfig.from_mapping(
        {"backend": backend, "generation_id": generation_id},
        field_path="auth.sessions.authority",
    )


class _BundleStore:
    def __init__(self) -> None:
        self.reads: list[str] = []

    async def get_user(self, sub: str):
        self.reads.append(sub)
        return {"sub": sub, "roles": [], "permissions": []}


class _PlatformStore:
    def __init__(self) -> None:
        self.reads: list[str] = []

    async def get_session_by_id(self, session_id: str):
        self.reads.append(session_id)
        return None


@pytest.mark.asyncio
async def test_precreated_consumers_switch_together_after_activation() -> None:
    bundle_authority = BundleSessionAuthority()
    platform_manager = SessionManager(
        "redis://unused",
        tenant="tenant-a",
        project="project-a",
    )
    config = _config()
    configure_session_authority(
        tenant="tenant-a",
        project="project-a",
        config=config,
    )

    with pytest.raises(
        SessionAuthorityUnavailable,
        match="session_authority_generation_not_prepared",
    ):
        await bundle_authority.get_user("user-a")
    with pytest.raises(
        SessionAuthorityUnavailable,
        match="session_authority_generation_not_prepared",
    ):
        await platform_manager.get_session_by_id("session-a")

    bundle_store = _BundleStore()
    platform_store = _PlatformStore()
    activate_session_authority_stores(
        tenant="tenant-a",
        project="project-a",
        config=config,
        bundle_store=bundle_store,
        platform_store=platform_store,
    )

    user = await bundle_authority.get_user("user-a")
    assert user is not None and user.sub == "user-a"
    assert await platform_manager.get_session_by_id("session-a") is None
    assert bundle_store.reads == ["user-a"]
    assert platform_store.reads == ["session-a"]


@pytest.mark.asyncio
async def test_explicit_stores_override_an_unprepared_process_binding() -> None:
    config = _config()
    configure_session_authority(
        tenant="tenant-a",
        project="project-a",
        config=config,
    )
    bundle_store = _BundleStore()
    platform_store = _PlatformStore()
    bundle_authority = BundleSessionAuthority(
        tenant="tenant-a",
        project="project-a",
        authority_store=bundle_store,
    )
    platform_manager = SessionManager(
        "redis://unused",
        tenant="tenant-a",
        project="project-a",
        authority_store=platform_store,
    )

    assert (await bundle_authority.get_user("user-a")).sub == "user-a"
    assert await platform_manager.get_session_by_id("session-a") is None


@pytest.mark.asyncio
async def test_redis_migration_source_remains_an_explicit_compatibility_mode() -> None:
    config = _config(
        backend="redis-migration-source",
        generation_id="",
    )
    snapshot = configure_session_authority(
        tenant="tenant-a",
        project="project-a",
        config=config,
    )
    manager = SessionManager(
        "redis://unused",
        tenant="tenant-a",
        project="project-a",
    )

    assert snapshot.ready is True
    assert manager.authority_store is None


def test_postgresql_process_without_binding_does_not_fall_back_to_redis(
    monkeypatch,
) -> None:
    from kdcube_ai_app.apps.chat.sdk import config as config_module

    monkeypatch.setattr(config_module, "get_settings", _settings)

    with pytest.raises(
        SessionAuthorityUnavailable,
        match="session_authority_postgresql_store_not_bound",
    ):
        bundle_session_store_for(tenant="tenant-a", project="project-a")


class _PreparedStore:
    def __init__(self, events: list[object], name: str) -> None:
        self.events = events
        self.name = name

    async def ensure_schema(self) -> None:
        self.events.append(("schema", self.name))


class _Cutovers(_PreparedStore):
    def __init__(
        self,
        events: list[object],
        *,
        failure: Exception | None = None,
    ) -> None:
        super().__init__(events, "cutovers")
        self.failure = failure

    async def require_activated(
        self,
        generation_id: str,
        *,
        required_families: tuple[str, ...],
    ) -> object:
        self.events.append(
            ("receipt", generation_id, required_families)
        )
        if self.failure is not None:
            raise self.failure
        return object()


def _settings(backend="postgresql", platform_backend=None) -> SimpleNamespace:
    authority = SimpleNamespace(
        BACKEND=backend,
        GENERATION_ID="durable-authority-v1" if backend == "postgresql" else "",
    )
    sessions = SimpleNamespace(AUTHORITY=authority)
    if platform_backend is not None:
        sessions.PLATFORM = SimpleNamespace(BACKEND=platform_backend)
    return SimpleNamespace(
        TENANT="tenant-a",
        PROJECT="project-a",
        AUTH=SimpleNamespace(
            SESSIONS=sessions
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_backend", [None, "authority", "redis"])
async def test_prepare_publishes_stores_only_after_the_exact_receipt(
    monkeypatch,
    platform_backend,
) -> None:
    import kdcube_ai_app.auth.bundle.session_store as bundle_store_module
    import kdcube_ai_app.auth.platform_session_store as platform_store_module
    import kdcube_ai_app.auth.session_authority_runtime as runtime

    events: list[object] = []
    bundle_store = _PreparedStore(events, "bundle")
    platform_store = _PreparedStore(events, "platform")
    cutovers = _Cutovers(events)
    monkeypatch.setattr(
        bundle_store_module,
        "PostgresBundleSessionStore",
        lambda **_kwargs: bundle_store,
    )
    monkeypatch.setattr(
        platform_store_module,
        "PostgresPlatformSessionStore",
        lambda **_kwargs: platform_store,
    )
    monkeypatch.setattr(
        runtime,
        "PostgresAuthorityCutoverStore",
        lambda **_kwargs: cutovers,
    )

    snapshot = await prepare_configured_session_authority(
        pg_pool=object(),
        settings=_settings(platform_backend=platform_backend),
        redis=object(),
    )

    assert snapshot.ready is True
    mixed = platform_backend == "redis"
    assert events == [("schema", "bundle")] + ([] if mixed else [("schema", "platform")]) + [
        ("schema", "cutovers"),
        (
            "receipt",
            "durable-authority-v1",
            KDCUBE_BUNDLE_SESSION_AUTHORITY_FAMILIES if mixed else KDCUBE_SESSION_AUTHORITY_FAMILIES,
        ),
    ]
    manager = SessionManager("redis://unused", tenant="tenant-a", project="project-a")
    assert (manager.authority_store is None) == mixed
    assert bundle_session_store_for(tenant="tenant-a", project="project-a") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_backend", ["authority", "redis"])
async def test_prepare_keeps_postgresql_fail_closed_when_receipt_is_missing(
    monkeypatch,
    platform_backend,
) -> None:
    import kdcube_ai_app.auth.bundle.session_store as bundle_store_module
    import kdcube_ai_app.auth.platform_session_store as platform_store_module
    import kdcube_ai_app.auth.session_authority_runtime as runtime

    events: list[object] = []
    monkeypatch.setattr(
        bundle_store_module,
        "PostgresBundleSessionStore",
        lambda **_kwargs: _PreparedStore(events, "bundle"),
    )
    monkeypatch.setattr(
        platform_store_module,
        "PostgresPlatformSessionStore",
        lambda **_kwargs: _PreparedStore(events, "platform"),
    )
    monkeypatch.setattr(
        runtime,
        "PostgresAuthorityCutoverStore",
        lambda **_kwargs: _Cutovers(
            events,
            failure=RuntimeError("authority_cutover_receipt_missing"),
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="authority_cutover_receipt_missing",
    ):
        await prepare_configured_session_authority(
            pg_pool=object(),
            settings=_settings(platform_backend=platform_backend),
            redis=object(),
        )

    manager = SessionManager(
        "redis://unused",
        tenant="tenant-a",
        project="project-a",
    )
    with pytest.raises(
        SessionAuthorityUnavailable,
        match="session_authority_generation_not_prepared",
    ):
        _ = manager.authority_store


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_backend", [None, "authority", "redis"])
async def test_prepare_source_mode_needs_no_postgres_or_receipt(platform_backend):
    snapshot = await prepare_configured_session_authority(
        pg_pool=None, settings=_settings("redis-migration-source", platform_backend),
    )
    assert snapshot.ready
    assert snapshot.platform_backend == (platform_backend or "authority")
    assert bundle_session_store_for(tenant="tenant-a", project="project-a") is None


@pytest.mark.parametrize("backend", ["postgresql", "redis-migration-source"])
def test_platform_selection_cannot_change_in_process(backend):
    config = _config(backend=backend, generation_id="durable-authority-v1" if backend == "postgresql" else "")
    configure_session_authority(tenant="tenant-a", project="project-a", config=config)
    with pytest.raises(SessionAuthorityUnavailable, match="configuration_changed_in_process"):
        configure_session_authority(
            tenant="tenant-a", project="project-a", config=config, platform_backend="redis",
        )


@pytest.mark.parametrize("platform_backend", ["typo", "postgresql", None, True])
def test_configure_rejects_invalid_platform_backend(platform_backend):
    with pytest.raises(ValueError, match="auth.sessions.platform.backend"):
        configure_session_authority(
            tenant="tenant-a", project="project-a", config=_config(),
            platform_backend=platform_backend,
        )
