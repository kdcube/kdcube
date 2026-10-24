# SPDX-License-Identifier: MIT

"""One Redis database per pytest-xdist worker for the OAuth tests.

Several OAuth tests share one fixed tenant/project namespace on the real Redis
named by ``REDIS_URL`` (``helpers.bind_delegated_card_persistence`` clears that
namespace before it binds a test app). Under ``pytest -n 4`` one worker's clear
removed another worker's live delegated-card keys mid-test, so 6 to 8 tests
failed with different names on each run (503s, a missing refresh token,
``card_transition_not_claimed``); serial runs passed. Each xdist worker now
selects its own database on the same disposable Redis, so no namespace is
shared across workers. A serial run is unchanged.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import pytest

_REDIS_URL_VARIABLES = ("REDIS_URL", "KDCUBE_TEST_REDIS_URL")
# A default Redis server has databases 0-15.
_REDIS_DATABASES = 16


def _xdist_worker_index() -> int | None:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    if not worker.startswith("gw") or not worker[2:].isdigit():
        return None
    return int(worker[2:])


def worker_redis_url(url: str, index: int) -> str:
    """``url`` with its database moved by ``index + 1`` (the base database stays the serial run's)."""
    parts = urlsplit(url)
    path = parts.path.strip("/")
    base = int(path) if path.isdigit() else 0
    database = base + index + 1
    if database >= _REDIS_DATABASES:
        raise pytest.UsageError(
            f"xdist worker gw{index} needs Redis database {database}; "
            f"run with at most {_REDIS_DATABASES - base - 1} workers"
        )
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture(autouse=True, scope="session")
def _xdist_worker_redis_database():
    index = _xdist_worker_index()
    if index is None:
        yield
        return
    with pytest.MonkeyPatch.context() as patch:
        for name in _REDIS_URL_VARIABLES:
            url = os.environ.get(name, "")
            if url:
                patch.setenv(name, worker_redis_url(url, index))
        yield
