import os
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from kdcube_ai_app.infra.secrets.manager import (
    SecretsFileSecretsManager,
    SecretsManagerConfig,
    _load_yaml_mapping_from_storage,
    _storage_backend_and_key_from_uri,
    _write_yaml_mapping_to_storage,
)


@pytest.mark.parametrize("name", ["author@home", "with space", "literal%40", "100%", "café"])
@pytest.mark.parametrize("use_uri", [True, False], ids=["file-uri", "raw-path"])
def test_yaml_read_write_and_backend_round_trip_target_one_physical_file(tmp_path, name, use_uri):
    target = tmp_path / name / f"{name}.yaml"
    locator = target.as_uri() if use_uri else str(target)
    payload = {"fixture": "synthetic value"}
    _write_yaml_mapping_to_storage(locator, payload)
    assert target.is_file()
    assert _load_yaml_mapping_from_storage(locator) == payload
    backend_uri, key = _storage_backend_and_key_from_uri(locator)
    assert Path(unquote(urlparse(backend_uri).path)) == target.parent.resolve()
    assert key == target.name
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in tmp_path.iterdir()) == [name]


@pytest.mark.parametrize("name", ["author@home", "with space", "literal%40", "100%", "café"])
def test_yaml_loader_reads_an_existing_physical_file_without_encoded_sibling(tmp_path, name):
    target = tmp_path / name / f"{name}.yaml"
    target.parent.mkdir()
    target.write_text("fixture: synthetic value\n")
    assert _load_yaml_mapping_from_storage(target.as_uri()) == {"fixture": "synthetic value"}
    assert sorted(path.name for path in tmp_path.iterdir()) == [name]


@pytest.mark.asyncio
@pytest.mark.parametrize("store", ["bundle", "global"])
@pytest.mark.parametrize("name", ["author@home", "with space", "literal%40", "100%"])
async def test_actual_file_provider_write_read_and_removal_use_uri_path(tmp_path, store, name):
    target = tmp_path / name / f"{name}.yaml"
    manager = SecretsFileSecretsManager(SecretsManagerConfig(
        provider="secrets-file", tenant="fixture-tenant", project="fixture-project", component="proc",
        redis_url=None,
        bundle_secrets_yaml=target.as_uri() if store == "bundle" else None,
        global_secrets_yaml=target.as_uri() if store == "global" else None,
    ))
    key = "bundles.fixture@1-0.secrets.value" if store == "bundle" else "platform.fixture.value"
    if store == "bundle":
        # W670: app secrets live beside the descriptor, in <descriptor folder>/secrets/<bundle>/<key>.json,
        # so the decoded folder of the yaml URI is where the record must land.
        target.parent.mkdir()
        await manager.set_many({key: "synthetic value"})
        target = tmp_path / name / "secrets" / "fixture@1-0" / "value.json"
    else:
        await manager.set_many({key: "synthetic value"})
    assert target.is_file()
    assert await manager.get_secret(key) == "synthetic value"
    # Strict physical removal is the original consumer's unavailable witness.
    target.unlink()
    assert await manager.get_secret(key) is None
    assert sorted(path.name for path in tmp_path.iterdir()) == [name]


def test_s3_secrets_locator_keeps_encoded_prefix_and_key():
    assert _storage_backend_and_key_from_uri("s3://fixture-bucket/author%40home/with%20space/literal%2540.yaml") == (
        "s3://fixture-bucket/author%40home/with%20space", "literal%2540.yaml",
    )


@pytest.mark.parametrize("name", ["author@home", "with space", "literal%40"])
def test_missing_physical_store_never_reads_or_moves_legacy_encoded_sibling(tmp_path, name):
    target = tmp_path / name / "synthetic.yaml"
    encoded_path = Path(urlparse(target.as_uri()).path)
    assert encoded_path != target
    # When tmp_path itself needs encoding (a workspace folder like "agent@host"), the encoded sibling's
    # ancestors lie outside tmp_path: create only the missing ones and remove exactly those afterwards.
    created = [path for path in (encoded_path.parent, *encoded_path.parent.parents) if not path.exists()]
    encoded_path.parent.mkdir(parents=True)
    try:
        encoded_path.write_text("fixture: synthetic misplaced value\n")
        assert _load_yaml_mapping_from_storage(target.as_uri(), missing_ok=True) == {}
        assert not target.exists()
        assert encoded_path.read_text() == "fixture: synthetic misplaced value\n"
    finally:
        if created:
            shutil.rmtree(created[-1])
