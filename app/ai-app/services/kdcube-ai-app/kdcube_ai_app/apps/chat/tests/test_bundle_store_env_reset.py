import fnmatch
import json

import pytest

from kdcube_ai_app.infra.plugin import bundle_store


class _FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, *args, **kwargs):
        self.data[key] = value
        return True

    async def delete(self, key):
        self.data.pop(key, None)
        return 1

    async def keys(self, pattern):
        return [key for key in self.data.keys() if fnmatch.fnmatch(key, pattern)]


@pytest.mark.asyncio
async def test_reset_registry_from_env_removes_stale_bundle_props(monkeypatch):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    bundle_id = "demo.bundle"
    props_key = bundle_store._props_key(tenant=tenant, project=project, bundle_id=bundle_id)

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))

    monkeypatch.setenv(
        "AGENTIC_BUNDLES_JSON",
        json.dumps(
            {
                "default_bundle_id": bundle_id,
                "bundles": {
                    bundle_id: {
                        "id": bundle_id,
                        "path": "/bundles/demo.bundle",
                        "module": "entrypoint",
                        "config": {
                            "role_models": {
                                "solver.react.v2.decision.v2.strong": {
                                    "provider": "anthropic",
                                    "model": "claude-sonnet-4-6",
                                }
                            }
                        },
                    }
                },
            }
        ),
    )

    await bundle_store.reset_registry_from_env(redis, tenant=tenant, project=project)

    assert json.loads(redis.data[props_key]) == {
        "role_models": {
            "solver.react.v2.decision.v2.strong": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            }
        }
    }

    monkeypatch.setenv(
        "AGENTIC_BUNDLES_JSON",
        json.dumps(
            {
                "default_bundle_id": bundle_id,
                "bundles": {
                    bundle_id: {
                        "id": bundle_id,
                        "path": "/bundles/demo.bundle",
                        "module": "entrypoint",
                    }
                },
            }
        ),
    )

    await bundle_store.reset_registry_from_env(redis, tenant=tenant, project=project)

    assert await redis.get(props_key) is None
