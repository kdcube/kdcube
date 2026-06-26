from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.automations.storage import AutomationStorage


def test_automation_storage_default_list_excludes_archived_and_deleted(tmp_path):
    storage = AutomationStorage(tmp_path, user_id="user-a")
    enabled = storage.create_automation(title="Enabled automation")
    disabled = storage.create_automation(title="Disabled automation")
    archived = storage.create_automation(title="Archived automation")
    deleted = storage.create_automation(title="Deleted automation")

    storage.set_automation_status(automation_id=disabled["id"], status="disabled")
    storage.set_automation_status(automation_id=archived["id"], status="archived")
    storage.set_automation_status(automation_id=deleted["id"], status="deleted")

    default_ids = {automation["id"] for automation in storage.list_automations()}

    assert enabled["id"] in default_ids
    assert disabled["id"] in default_ids
    assert archived["id"] not in default_ids
    assert deleted["id"] not in default_ids
    assert storage.list_automations(status="deleted")[0]["id"] == deleted["id"]
