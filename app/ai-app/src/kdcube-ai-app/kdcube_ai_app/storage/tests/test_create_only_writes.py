"""W210: a create-only write refuses an existing key instead of replacing it.

Every built-in backend makes the refusal atomic: the local file system opens with
O_EXCL, S3 sends If-None-Match: *, the in-memory store checks and writes under
its lock. The original bytes survive the refused write.
"""
import asyncio

import pytest

from kdcube_ai_app.storage.storage import InMemoryStorageBackend, LocalFileSystemBackend, S3StorageBackend


def _local(tmp_path):
    return LocalFileSystemBackend(str(tmp_path))


def _memory(tmp_path):
    return InMemoryStorageBackend()


@pytest.mark.parametrize("make", [_local, _memory], ids=["local", "memory"])
def test_a_create_only_write_refuses_an_existing_key_and_keeps_its_bytes(tmp_path, make):
    backend = make(tmp_path)
    asyncio.run(backend.create_bytes_a("a/b.png", b"first"))

    with pytest.raises(FileExistsError):
        asyncio.run(backend.create_bytes_a("a/b.png", b"second"))

    assert asyncio.run(backend.read_bytes_a("a/b.png")) == b"first"


@pytest.mark.parametrize("make", [_local, _memory], ids=["local", "memory"])
def test_a_create_only_write_of_a_new_key_stores_it(tmp_path, make):
    backend = make(tmp_path)
    asyncio.run(backend.create_bytes_a("a/new.png", b"bytes"))
    assert asyncio.run(backend.read_bytes_a("a/new.png")) == b"bytes"


def test_s3_create_only_sends_if_none_match_and_maps_the_precondition_to_file_exists(monkeypatch):
    botocore_exceptions = pytest.importorskip("botocore.exceptions")
    sent = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def put_object(self, **kwargs):
            sent.append(kwargs)
            if len(sent) > 1:
                raise botocore_exceptions.ClientError(
                    {"Error": {"Code": "PreconditionFailed", "Message": "At least one of the pre-conditions"},
                     "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")

    async def _client(self):
        return _Client()

    monkeypatch.setattr(S3StorageBackend, "_create_s3_client", lambda self, kwargs: object())
    monkeypatch.setattr(S3StorageBackend, "_get_async_client", _client)
    backend = S3StorageBackend("synthetic-bucket", skip_bucket_check=True)

    asyncio.run(backend.create_bytes_a("a/b.png", b"first"))
    with pytest.raises(FileExistsError):
        asyncio.run(backend.create_bytes_a("a/b.png", b"second"))

    assert all(call["IfNoneMatch"] == "*" for call in sent)
    assert asyncio.run(_plain_write_has_no_condition(backend, sent)) is True


async def _plain_write_has_no_condition(backend, sent):
    sent.clear()
    await backend.write_bytes_a("a/c.png", b"x")
    return "IfNoneMatch" not in sent[0]
