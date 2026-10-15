"""W109: an S3 failure keeps its kind, so an outage is never read as a deleted object.

Only a missing key is FileNotFoundError. A missing bucket, a refused credential,
a throttle, a timeout and a dropped connection raise StorageUnavailableError,
carrying the original exception as its cause and saying whether a retry can help.
"""
import asyncio

import pytest

botocore_exceptions = pytest.importorskip("botocore.exceptions")
ClientError = botocore_exceptions.ClientError

import kdcube_ai_app.storage.storage as storage  # noqa: E402
from kdcube_ai_app.storage.storage import S3StorageBackend, StorageUnavailableError  # noqa: E402


def _client_error(code: str, status: int, operation: str = "GetObject") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "synthetic"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


MISSING_KEY = [
    _client_error("NoSuchKey", 404),
    _client_error("404", 404, "HeadObject"),  # head_object reports absence as a bare 404
]

UNAVAILABLE = [
    # (failure, retryable)
    (_client_error("NoSuchBucket", 404), False),
    (_client_error("AccessDenied", 403), False),
    (_client_error("ExpiredToken", 400), False),
    (_client_error("InvalidAccessKeyId", 403), False),
    (_client_error("SlowDown", 503), True),
    (_client_error("ThrottlingException", 400), True),
    (_client_error("RequestTimeout", 400), True),
    (_client_error("InternalError", 500), True),
    (botocore_exceptions.EndpointConnectionError(endpoint_url="https://s3.example"), True),
    (botocore_exceptions.ConnectTimeoutError(endpoint_url="https://s3.example"), True),
    (botocore_exceptions.ReadTimeoutError(endpoint_url="https://s3.example"), True),
    (botocore_exceptions.NoCredentialsError(), False),
]


class _SyncClient:
    def __init__(self, failure):
        self.failure = failure

    def get_object(self, **kwargs):
        raise self.failure

    def head_object(self, **kwargs):
        raise self.failure


class _AsyncClient:
    def __init__(self, failure):
        self.failure = failure

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_object(self, **kwargs):
        raise self.failure

    async def head_object(self, **kwargs):
        raise self.failure


def _backend(monkeypatch, failure) -> S3StorageBackend:
    monkeypatch.setattr(S3StorageBackend, "_create_s3_client", lambda self, kwargs: _SyncClient(failure))
    backend = S3StorageBackend("synthetic-bucket", skip_bucket_check=True)

    async def _async_client(self):
        return _AsyncClient(failure)

    monkeypatch.setattr(S3StorageBackend, "_get_async_client", _async_client)
    return backend


async def _drain(iterator):
    return [chunk async for chunk in iterator]


def _all_seven(backend):
    """Every site W109 names: read, size and modified time (sync and async), and the streaming read."""
    return [
        ("read_bytes", lambda: backend.read_bytes("a/b.json")),
        ("get_size", lambda: backend.get_size("a/b.json")),
        ("get_modified_time", lambda: backend.get_modified_time("a/b.json")),
        ("read_bytes_a", lambda: asyncio.run(backend.read_bytes_a("a/b.json"))),
        ("get_size_a", lambda: asyncio.run(backend.get_size_a("a/b.json"))),
        ("get_modified_time_a", lambda: asyncio.run(backend.get_modified_time_a("a/b.json"))),
        ("iter_bytes_a", lambda: asyncio.run(_drain(backend.iter_bytes_a("a/b.json")))),
    ]


@pytest.mark.parametrize("failure", MISSING_KEY, ids=lambda f: f.response["Error"]["Code"])
def test_only_a_missing_key_is_reported_as_a_missing_file(monkeypatch, failure):
    for name, call in _all_seven(_backend(monkeypatch, failure)):
        with pytest.raises(FileNotFoundError) as raised:
            call()
        assert not isinstance(raised.value, StorageUnavailableError), name
        assert raised.value.__cause__ is failure, name


def _failure_id(value) -> str:
    if isinstance(value, bool):
        return "retryable" if value else "final"
    response = getattr(value, "response", None)
    return response["Error"]["Code"] if response else type(value).__name__


@pytest.mark.parametrize("failure,retryable", UNAVAILABLE, ids=_failure_id)
def test_an_unreachable_store_is_never_reported_as_a_missing_file(monkeypatch, failure, retryable):
    for name, call in _all_seven(_backend(monkeypatch, failure)):
        with pytest.raises(StorageUnavailableError) as raised:
            call()
        assert not isinstance(raised.value, FileNotFoundError), name
        assert raised.value.__cause__ is failure, name  # the original survives as the cause, not as text
        assert raised.value.retryable is retryable, name
        assert raised.value.path == "a/b.json", name


def test_a_missing_bucket_is_a_configuration_failure_not_absence(monkeypatch):
    backend = _backend(monkeypatch, _client_error("NoSuchBucket", 404))
    with pytest.raises(StorageUnavailableError) as raised:
        backend.read_bytes("a/b.json")
    assert raised.value.retryable is False
    assert raised.value.code == "NoSuchBucket"


def test_an_unclassified_failure_raises_as_itself(monkeypatch):
    failure = ValueError("synthetic bug in a caller-supplied body")
    for name, call in _all_seven(_backend(monkeypatch, failure)):
        with pytest.raises(ValueError) as raised:
            call()
        assert raised.value is failure, name


class _StreamingBody:
    """A body that yields one chunk, then fails the way the transport fails mid-stream."""

    def __init__(self, failure):
        self.failure, self.sent = failure, False

    async def read(self, size=-1):
        if not self.sent:
            self.sent = True
            return b"first-chunk"
        raise self.failure


def _mid_stream_failures():
    failures = [botocore_exceptions.ResponseStreamingError(error="synthetic connection reset")]
    try:  # optional: without aiobotocore only this one case is dropped, never the module
        from aiobotocore.response import AioReadTimeoutError
    except ImportError:
        return failures
    return failures + [AioReadTimeoutError(endpoint_url="https://s3.example", error="t")]


@pytest.mark.parametrize("failure", _mid_stream_failures(), ids=lambda f: type(f).__name__)
def test_a_failure_in_the_middle_of_a_stream_is_unavailable_and_retryable(monkeypatch, failure):
    backend = _backend(monkeypatch, failure)

    class _MidStreamClient(_AsyncClient):
        async def get_object(self, **kwargs):
            return {"Body": _StreamingBody(failure)}

    async def _async_client(self):
        return _MidStreamClient(failure)

    monkeypatch.setattr(S3StorageBackend, "_get_async_client", _async_client)
    chunks = []

    async def _consume():
        async for chunk in backend.iter_bytes_a("a/b.json"):
            chunks.append(chunk)

    with pytest.raises(StorageUnavailableError) as raised:
        asyncio.run(_consume())
    assert chunks == [b"first-chunk"]  # the failure came after data had already been delivered
    assert raised.value.retryable is True
    assert raised.value.__cause__ is failure


def test_the_unavailable_error_is_an_os_error_callers_already_handle():
    assert issubclass(StorageUnavailableError, OSError)
    assert not issubclass(StorageUnavailableError, FileNotFoundError)
    assert storage.StorageUnavailableError is StorageUnavailableError
