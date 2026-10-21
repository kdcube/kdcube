"""The site catalog converges on the current view during rolling replacement.

Every publication replaces the stored catalog. A process whose allow-list
predates the assembly descriptor must not publish, and an unchanged catalog
is not published again.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.sites.registry import compile_application_site_catalog
from kdcube_ai_app.apps.chat.sdk.solutions.sites.runtime import (
    ApplicationSiteCatalogRuntime,
    load_application_site_catalog,
    publish_application_site_catalog,
    refresh_application_site_catalog,
)
from kdcube_ai_app.apps.chat.sdk.solutions.sites.tests.test_runtime import FakeRedis
from kdcube_ai_app.infra.plugin import bundle_store

TENANT = "tenant-a"
PROJECT = "project-a"
BOARD = "board@1"
LANDING = "landing@1"


def _apps(*bundle_ids: str) -> dict[str, dict[str, str]]:
    return {
        bundle_id: {"id": bundle_id, "path": f"/managed/{bundle_id}", "module": "entrypoint"}
        for bundle_id in bundle_ids
    }


@pytest.fixture
def view(monkeypatch):
    """The process view: props by app and whether its allow-list is current."""

    state = SimpleNamespace(
        current=True,
        props={
            BOARD: {"ui": {"main_view": {"site": {"enabled": True, "alias": "board"}}}},
            LANDING: {
                "ui": {"main_view": {"site": {"enabled": True, "alias": "landing", "default": True}}},
            },
        },
    )

    async def _get_bundle_props(_redis, *, tenant, project, bundle_id):
        return state.props.get(bundle_id, {})

    monkeypatch.setattr(bundle_store, "get_bundle_props", _get_bundle_props)
    # raising=False keeps this test meaningful against code without the guard.
    monkeypatch.setattr(bundle_store, "allowed_bundle_ids_are_current", lambda: state.current, raising=False)
    return state


async def _refresh(redis, applications, runtime=None):
    return await refresh_application_site_catalog(
        redis, tenant=TENANT, project=PROJECT, applications=applications, runtime=runtime,
    )


async def _stored(redis):
    return await load_application_site_catalog(redis, tenant=TENANT, project=PROJECT)


@pytest.mark.asyncio
async def test_older_allowlist_process_cannot_publish_last_during_rolling_overlap(view) -> None:
    redis = FakeRedis()
    new_task = await _refresh(redis, _apps(BOARD, LANDING))
    assert {site.alias for site in new_task.sites} == {"board", "landing"}

    # The older task's allow-list predates the landing app, so its registry lacks it.
    view.current = False
    old_runtime = ApplicationSiteCatalogRuntime()
    for _ in range(3):
        kept = await _refresh(redis, _apps(BOARD), runtime=old_runtime)

    stored = await _stored(redis)
    assert stored.revision == new_task.revision
    assert {site.alias for site in stored.sites} == {"board", "landing"}
    assert kept == stored
    assert old_runtime.snapshot() == stored
    assert len(redis.messages) == 1


@pytest.mark.asyncio
async def test_current_process_replaces_an_older_peer_catalog_when_it_refreshes(view) -> None:
    # A catalog published by an older peer before it stopped (one site).
    redis = FakeRedis()
    older = compile_application_site_catalog(
        tenant=TENANT,
        project=PROJECT,
        sites=[
            site
            for site in (await _refresh(FakeRedis(), _apps(BOARD, LANDING))).sites
            if site.application_id == BOARD
        ],
    )
    await publish_application_site_catalog(redis, older)

    # App readiness or the periodic reconcile refreshes from the current view.
    runtime = ApplicationSiteCatalogRuntime()
    refreshed = await _refresh(redis, _apps(BOARD, LANDING), runtime=runtime)

    stored = await _stored(redis)
    assert {site.alias for site in stored.sites} == {"board", "landing"}
    assert stored.generation == 2
    assert runtime.snapshot() == refreshed == stored


@pytest.mark.asyncio
async def test_unchanged_catalog_is_not_published_again(view) -> None:
    redis = FakeRedis()
    first = await _refresh(redis, _apps(BOARD, LANDING))
    second = await _refresh(redis, _apps(BOARD, LANDING))

    assert second == first
    assert second.generation == 1
    assert len(redis.messages) == 1


@pytest.mark.asyncio
async def test_older_allowlist_without_a_stored_catalog_returns_unpublished_projection(view) -> None:
    view.current = False
    redis = FakeRedis()
    runtime = ApplicationSiteCatalogRuntime()

    catalog = await _refresh(redis, _apps(BOARD), runtime=runtime)

    assert [site.alias for site in catalog.sites] == ["board"]
    assert await _stored(redis) is None
    assert redis.messages == []
    assert runtime.snapshot() is None


@pytest.mark.parametrize(
    ("loaded", "current", "expected"),
    [
        (["a", "b"], ["b", "a"], True),
        (None, None, True),
        (["a"], ["a", "b"], False),
        (None, ["a"], False),
        (["a"], None, False),
    ],
)
def test_allowlist_is_current_compares_the_descriptor_now(monkeypatch, loaded, current, expected) -> None:
    settings = SimpleNamespace(
        PLATFORM=SimpleNamespace(APPLICATIONS=SimpleNamespace(ALLOWED_BUNDLE_IDS=loaded)),
        current_assembly_bundle_allowlist=lambda: current,
    )
    monkeypatch.setattr(bundle_store, "get_settings", lambda: settings)
    assert bundle_store.allowed_bundle_ids_are_current() is expected


def test_invalid_descriptor_allowlist_counts_as_not_current(monkeypatch) -> None:
    def _invalid():
        raise ValueError("platform.services.proc.bundles.allowed_bundle_ids must be null or a list")

    settings = SimpleNamespace(
        PLATFORM=SimpleNamespace(APPLICATIONS=SimpleNamespace(ALLOWED_BUNDLE_IDS=["a"])),
        current_assembly_bundle_allowlist=_invalid,
    )
    monkeypatch.setattr(bundle_store, "get_settings", lambda: settings)
    assert bundle_store.allowed_bundle_ids_are_current() is False


def test_current_assembly_allowlist_reads_this_component_path() -> None:
    from kdcube_ai_app.apps.chat.sdk.config import Settings

    seen: list[str] = []
    settings = SimpleNamespace(
        GATEWAY_COMPONENT=" Proc ",
        _assembly_bundle_allowlist=lambda path: seen.append(path) or ["a"],
    )
    assert Settings.current_assembly_bundle_allowlist(settings) == ["a"]
    assert seen == ["platform.services.proc.bundles.allowed_bundle_ids"]
