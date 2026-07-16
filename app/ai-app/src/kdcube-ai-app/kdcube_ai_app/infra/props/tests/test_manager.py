# SPDX-License-Identifier: MIT

import json

import pytest

from kdcube_ai_app.infra.props.manager import UserPropsManager
from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema


class _FakeConnection:
    def __init__(self):
        self.rows = {}

    async def execute(self, sql, *args):
        if "INSERT INTO" in sql:
            user_id, bundle_id, key, value_json = args
            self.rows[(str(user_id), str(bundle_id), str(key))] = json.loads(value_json)
            return "INSERT 0 1"
        if "DELETE FROM" in sql:
            user_id, bundle_id, key = args
            self.rows.pop((str(user_id), str(bundle_id), str(key)), None)
            return "DELETE 1"
        raise AssertionError(f"Unexpected SQL in fake connection: {sql}")

    async def fetchval(self, sql, *args):
        if "SELECT value_json" in sql:
            user_id, bundle_id, key = args
            value = self.rows.get((str(user_id), str(bundle_id), str(key)))
            return None if value is None else json.dumps(value)
        raise AssertionError(f"Unexpected SQL in fake connection: {sql}")

    async def fetch(self, sql, *args):
        if "SELECT key, value_json" in sql:
            user_id, bundle_id = args
            out = []
            for (stored_user, stored_bundle, key), value in sorted(self.rows.items()):
                if stored_user == str(user_id) and stored_bundle == str(bundle_id):
                    out.append({"key": key, "value_json": json.dumps(value)})
            return out
        raise AssertionError(f"Unexpected SQL in fake connection: {sql}")


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self):
        self.connection = _FakeConnection()

    def acquire(self):
        return _Acquire(self.connection)


@pytest.mark.asyncio
async def test_user_props_manager_roundtrip():
    pool = _FakePool()
    mgr = UserPropsManager(
        tenant="tenant-a",
        project="project-a",
        pg_pool=pool,
    )

    await mgr.set_user_prop(
        user_id="user-1",
        bundle_id="bundle.demo",
        key="preferences.theme",
        value={"mode": "dark"},
    )

    assert await mgr.get_user_prop(
        user_id="user-1",
        bundle_id="bundle.demo",
        key="preferences.theme",
    ) == {"mode": "dark"}
    assert await mgr.list_user_props(
        user_id="user-1",
        bundle_id="bundle.demo",
    ) == {"preferences.theme": {"mode": "dark"}}

    for key, value in {
        "preferences.labels": ["one", "two"],
        "preferences.locale": "de-DE",
        "preferences.literal": "false",
        "preferences.limit": 5,
        "preferences.enabled": False,
    }.items():
        await mgr.set_user_prop(
            user_id="user-1",
            bundle_id="bundle.demo",
            key=key,
            value=value,
        )
        assert await mgr.get_user_prop(
            user_id="user-1",
            bundle_id="bundle.demo",
            key=key,
        ) == value

    await mgr.delete_user_prop(
        user_id="user-1",
        bundle_id="bundle.demo",
        key="preferences.theme",
    )

    assert await mgr.get_user_prop(
        user_id="user-1",
        bundle_id="bundle.demo",
        key="preferences.theme",
    ) is None


def test_user_props_manager_uses_project_schema():
    mgr = UserPropsManager(
        tenant="tenant-a",
        project="project-a",
        pg_pool=_FakePool(),
    )

    assert mgr._schema == project_schema("tenant-a", "project-a")
