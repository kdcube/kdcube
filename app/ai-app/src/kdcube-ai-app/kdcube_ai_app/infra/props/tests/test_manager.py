# SPDX-License-Identifier: MIT

from kdcube_ai_app.infra.props.manager import UserPropsManager
from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema


class _FakeDbMgr:
    def __init__(self):
        self.rows = {}

    def execute_sql(self, sql, data=None, as_dict=True, debug=False, bulk=False):
        if "INSERT INTO" in sql:
            user_id, bundle_id, key, value_json = data
            self.rows[(str(user_id), str(bundle_id), str(key))] = value_json.adapted
            return None
        if "DELETE FROM" in sql:
            user_id, bundle_id, key = data
            self.rows.pop((str(user_id), str(bundle_id), str(key)), None)
            return None
        if "SELECT value_json" in sql:
            user_id, bundle_id, key = data
            value = self.rows.get((str(user_id), str(bundle_id), str(key)))
            return [] if value is None else [{"value_json": value}]
        if "SELECT key, value_json" in sql:
            user_id, bundle_id = data
            out = []
            for (stored_user, stored_bundle, key), value in sorted(self.rows.items()):
                if stored_user == str(user_id) and stored_bundle == str(bundle_id):
                    out.append({"key": key, "value_json": value})
            return out
        raise AssertionError(f"Unexpected SQL in fake db manager: {sql}")


def test_user_props_manager_roundtrip():
    dbmgr = _FakeDbMgr()
    mgr = UserPropsManager(
        tenant="tenant-a",
        project="project-a",
        dbmgr=dbmgr,
    )

    mgr.set_user_prop(
        user_id="user-1",
        bundle_id="bundle.demo",
        key="preferences.theme",
        value={"mode": "dark"},
    )

    assert mgr.get_user_prop(
        user_id="user-1",
        bundle_id="bundle.demo",
        key="preferences.theme",
    ) == {"mode": "dark"}
    assert mgr.list_user_props(
        user_id="user-1",
        bundle_id="bundle.demo",
    ) == {"preferences.theme": {"mode": "dark"}}

    mgr.delete_user_prop(
        user_id="user-1",
        bundle_id="bundle.demo",
        key="preferences.theme",
    )

    assert mgr.get_user_prop(
        user_id="user-1",
        bundle_id="bundle.demo",
        key="preferences.theme",
    ) is None


def test_user_props_manager_uses_project_schema():
    dbmgr = _FakeDbMgr()
    mgr = UserPropsManager(
        tenant="tenant-a",
        project="project-a",
        dbmgr=dbmgr,
    )

    assert mgr._schema == project_schema("tenant-a", "project-a")
