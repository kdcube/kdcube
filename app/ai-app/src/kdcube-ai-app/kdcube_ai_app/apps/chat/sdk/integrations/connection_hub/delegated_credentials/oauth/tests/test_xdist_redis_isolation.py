# SPDX-License-Identifier: MIT

"""W931: each pytest-xdist worker selects its own Redis database (see conftest.py)."""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.conftest import (
    worker_redis_url,
)


@pytest.mark.parametrize(
    ("url", "index", "expected"),
    [
        ("redis://127.0.0.1:55001/0", 0, "redis://127.0.0.1:55001/1"),
        ("redis://127.0.0.1:55001/0", 3, "redis://127.0.0.1:55001/4"),
        ("redis://127.0.0.1:55001", 1, "redis://127.0.0.1:55001/2"),
        ("redis://127.0.0.1:55001/2?protocol=3", 0, "redis://127.0.0.1:55001/3?protocol=3"),
    ],
)
def test_each_worker_gets_its_own_database_on_the_same_server(url, index, expected):
    assert worker_redis_url(url, index) == expected


def test_workers_never_share_a_database():
    urls = {worker_redis_url("redis://127.0.0.1:55001/0", index) for index in range(8)}
    assert len(urls) == 8 and "redis://127.0.0.1:55001/0" not in urls


def test_more_workers_than_databases_is_refused_not_wrapped():
    with pytest.raises(pytest.UsageError, match="at most 15 workers"):
        worker_redis_url("redis://127.0.0.1:55001/0", 15)
