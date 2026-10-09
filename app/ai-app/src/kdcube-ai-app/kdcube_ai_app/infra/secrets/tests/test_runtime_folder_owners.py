"""Owner-scoped, one-file-per-record layout of the file secrets manager's runtime folder.

Records live at ``<runtime root>/<owner bundle id | platform>/<namespace>/<ref>.json``. A create publishes a
complete file under a name that never replaces an existing one, and no lock file is used. A deleted or purged
reference keeps a value-free tombstone so it cannot be minted again. Exercised through the store the Hub uses
(``ephemeral_secret_store``) over a real SecretsFileSecretsManager on a temporary folder. No value is printed.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import time

import pytest
import yaml

from kdcube_ai_app.infra.secrets.ephemeral import ephemeral_secret_store
from kdcube_ai_app.infra.secrets.manager import (
    SecretsFileSecretsManager,
    SecretsManagerConfig,
    SecretsManagerError,
)

HUB = "connection-hub@1-0"
OTHER = "other-bundle@2-1"
NAMESPACE = "oauth-refresh-tokens"
REF = "e" * 32
CANARY = "synthetic-owner-layout-canary"


@pytest.fixture
def layout(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    bundle_yaml = config / "bundles.secrets.yaml"
    bundle_yaml.write_text(yaml.safe_dump({"bundles": {"version": "1", "items": []}}))
    root = config / "secrets"

    def manager():
        return SecretsFileSecretsManager(SecretsManagerConfig(
            provider="secrets-file", component="proc", bundle_secrets_yaml=bundle_yaml.as_uri(),
            runtime_secrets_root=str(root), runtime_secret_namespaces=(NAMESPACE, "card-credentials")))

    return root, manager


def _store(manager, owner, namespace=NAMESPACE):
    return ephemeral_secret_store(namespace=namespace, manager=manager, bundle_id=owner)


def _later(seconds=300):
    return int(time.time()) + seconds


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def _files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) if root.exists() else []


@pytest.mark.asyncio
async def test_a_bundle_record_is_one_private_file_under_its_owner_and_purpose(layout):
    root, manager = layout
    assert await _store(manager(), HUB).create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    record = root / HUB / NAMESPACE / f"{REF}.json"
    assert _files(root) == [f"{HUB}/{NAMESPACE}/{REF}.json"]
    assert _mode(record) == 0o600
    for folder in (root, root / HUB, root / HUB / NAMESPACE):
        assert _mode(folder) == 0o700, folder.name


@pytest.mark.asyncio
async def test_a_bundle_less_record_goes_under_platform(layout):
    root, manager = layout
    assert await _store(manager(), None).create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    assert _files(root) == [f"platform/{NAMESPACE}/{REF}.json"]


@pytest.mark.asyncio
async def test_owners_and_purposes_are_isolated_for_the_same_reference(layout):
    root, manager = layout
    stores = {"hub": _store(manager(), HUB), "other": _store(manager(), OTHER),
              "platform": _store(manager(), None), "hub-cards": _store(manager(), HUB, "card-credentials")}
    for name, store in stores.items():
        assert await store.create(secret_ref=REF, value=f"value-of-{name}", expires_at=_later()) is True
    await stores["hub"].delete(secret_ref=REF)
    assert await stores["hub"].get(secret_ref=REF) is None
    for name in ("other", "platform", "hub-cards"):
        assert await stores[name].get(secret_ref=REF) == f"value-of-{name}"


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["platform", "../escape", "a/b", ".", "..", "", ".hidden", "x" * 129,
                                   "bad owner", "owner\x00nul"])
async def test_an_invalid_owner_is_refused_before_anything_is_written(layout, owner):
    root, manager = layout
    with pytest.raises(SecretsManagerError):
        await _store(manager(), owner).create(secret_ref=REF, value=CANARY, expires_at=_later())
    assert CANARY not in "".join(p.read_text(errors="ignore") for p in root.parent.rglob("*") if p.is_file())
    assert not (root.parent / "escape").exists()


@pytest.mark.asyncio
async def test_a_deleted_reference_keeps_a_value_free_read_only_tombstone_across_a_restart(layout):
    root, manager = layout
    store = _store(manager(), HUB)
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    await store.delete(secret_ref=REF)
    record = root / HUB / NAMESPACE / f"{REF}.json"
    assert _mode(record) == 0o400
    assert CANARY not in record.read_text()
    assert json.loads(record.read_text())["value"] is None
    again = _store(manager(), HUB)  # a new process
    assert await again.create(secret_ref=REF, value=CANARY, expires_at=_later()) is False
    assert await again.get(secret_ref=REF) is None


@pytest.mark.asyncio
async def test_purge_stays_inside_its_owner(layout, monkeypatch):
    root, manager = layout
    now = int(time.time())
    hub, other = _store(manager(), HUB), _store(manager(), OTHER)
    refs = [f"{n:032x}" for n in range(1, 4)]
    for ref in refs:
        assert await hub.create(secret_ref=ref, value=CANARY, expires_at=now + 30)
        assert await other.create(secret_ref=ref, value="other-owner", expires_at=now + 30)
    monkeypatch.setattr(time, "time", lambda: float(now + 60))
    assert await hub.purge_expired(now=now + 60, limit=10) == 3
    monkeypatch.undo()
    remaining = [json.loads((root / OTHER / NAMESPACE / f"{ref}.json").read_text())["value"] for ref in refs]
    assert remaining == ["other-owner"] * 3


@pytest.mark.asyncio
async def test_two_processes_creating_one_owner_reference_yield_one_winner_and_no_lock_file(layout):
    root, manager = layout
    first, second = _store(manager(), HUB), _store(manager(), HUB)
    results = await asyncio.gather(
        first.create(secret_ref=REF, value="value-from-process-a", expires_at=_later()),
        second.create(secret_ref=REF, value="value-from-process-b", expires_at=_later()))
    assert sorted(results) == [False, True]
    winner = "value-from-process-a" if results[0] else "value-from-process-b"
    assert await _store(manager(), HUB).get(secret_ref=REF) == winner
    assert _files(root) == [f"{HUB}/{NAMESPACE}/{REF}.json"]  # no .lock, no leftover temporary file


@pytest.mark.asyncio
async def test_an_owner_folder_with_broad_permissions_is_refused_not_repaired(layout):
    root, manager = layout
    (root / HUB / NAMESPACE).mkdir(parents=True)
    os.chmod(root, 0o700)
    os.chmod(root / HUB, 0o755)
    with pytest.raises(SecretsManagerError):
        await _store(manager(), HUB).create(secret_ref=REF, value=CANARY, expires_at=_later())
    assert _mode(root / HUB) == 0o755
    assert _files(root) == []


@pytest.mark.asyncio
async def test_a_symlinked_owner_folder_is_refused(layout, tmp_path):
    root, manager = layout
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    os.symlink(elsewhere, root / HUB)
    with pytest.raises(SecretsManagerError):
        await _store(manager(), HUB).create(secret_ref=REF, value=CANARY, expires_at=_later())
    assert list(elsewhere.rglob("*")) == []
