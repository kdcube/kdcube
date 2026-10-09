"""Test seam for the secrets tests: the Hub record adapter's per-record lock needs a Redis (W670)."""
from __future__ import annotations

import pytest

from kdcube_ai_app.infra.secrets import ephemeral as ephemeral_module


class FakeRedis:
    """SET NX PX and the compare-and-delete release script, with Redis semantics."""

    def __init__(self):
        self.values = {}

    async def set(self, key, value, *, nx=False, px=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def eval(self, script, numkeys, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


@pytest.fixture(autouse=True)
def record_lock_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(ephemeral_module, "_record_lock_redis", lambda settings: redis)
    return redis
