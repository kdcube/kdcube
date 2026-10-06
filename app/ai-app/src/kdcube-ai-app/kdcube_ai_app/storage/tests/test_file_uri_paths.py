import pytest

import kdcube_ai_app.storage.storage as storage
from kdcube_ai_app.storage.uri import local_file_uri_path


@pytest.mark.parametrize("name", ["author@home", "with space", "literal%40", "100%", "café"])
@pytest.mark.parametrize("use_uri", [True, False], ids=["file-uri", "raw-path"])
def test_local_backend_uses_the_physical_path(tmp_path, name, use_uri):
    root = tmp_path / name
    locator = root.as_uri() if use_uri else str(root)
    backend = storage.create_storage_backend(locator)
    assert backend.base_path == root.resolve()
    backend.write_text("synthetic.txt", "synthetic value")
    assert (root / "synthetic.txt").read_text() == "synthetic value"
    (root / "synthetic.txt").unlink()
    assert not backend.exists("synthetic.txt")
    assert sorted(path.name for path in tmp_path.iterdir()) == [name]


def test_s3_factory_keeps_encoded_object_prefix(monkeypatch):
    captured = {}

    def backend(**kwargs):
        captured.update(kwargs)
        return captured

    monkeypatch.setattr(storage, "S3StorageBackend", backend)
    assert storage.create_storage_backend("s3://fixture-bucket/author%40home/with%20space/literal%2540") == {
        "bucket_name": "fixture-bucket",
        "prefix": "author%40home/with%20space/literal%2540",
    }


def test_file_uri_decoder_decodes_once_and_preserves_plus():
    assert local_file_uri_path("file:///fixture@home/with%20space/literal%252F/plus+name") == (
        "/fixture@home/with space/literal%2F/plus+name"
    )


@pytest.mark.parametrize("locator", ["/fixture/literal%40", "s3://fixture/literal%40"])
def test_file_uri_decoder_rejects_other_locator_families(locator):
    with pytest.raises(ValueError, match="Expected a local file URI"):
        local_file_uri_path(locator)
