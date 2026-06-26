from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.automations import operations
from kdcube_ai_app.apps.chat.sdk.solutions.automations.storage import AutomationStorage


def _entrypoint() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            tenant="demo-tenant",
            project="demo-project",
            ai_bundle_spec=SimpleNamespace(id="bundle@1-0"),
        ),
        settings=SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project"),
    )


def _configure(tmp_path) -> None:
    operations.configure_automation_operations(
        storage_root_or_error=lambda _entrypoint: str(tmp_path),
        target_user_id=lambda _entrypoint, user_id=None, fingerprint=None: user_id or fingerprint or "user-a",
        bundle_id="bundle@1-0",
    )


def test_storage_for_prefers_entrypoint_scope_methods_over_module_configuration(tmp_path):
    wrong_root = tmp_path / "wrong"
    right_root = tmp_path / "right"
    operations.configure_automation_operations(
        storage_root_or_error=lambda _entrypoint: str(wrong_root),
        target_user_id=lambda _entrypoint, user_id=None, fingerprint=None: "wrong-user",
        bundle_id="bundle@1-0",
    )

    class Entrypoint:
        def automation_storage_root(self):
            return str(right_root)

        def target_automation_user_id(self, *, user_id=None, fingerprint=None):
            return user_id or fingerprint or "right-user"

    storage, target_user = operations.storage_for(Entrypoint(), user_id="user-a")

    assert storage.root == right_root
    assert target_user == "user-a"


@pytest.mark.asyncio
async def test_run_automation_execution_uses_entrypoint_virtual_executor(tmp_path):
    _configure(tmp_path)
    storage = AutomationStorage(tmp_path, user_id="user-a")
    automation = storage.create_automation(title="Personal news", description="Prepare my AI news brief.")
    calls = []

    class Entrypoint:
        config = SimpleNamespace(
            tenant="demo-tenant",
            project="demo-project",
            ai_bundle_spec=SimpleNamespace(id="bundle@1-0"),
        )
        settings = SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project")

        async def execute_automation_job(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "success",
                "answer": "News issue generated.",
                "metadata": {"runner": "news_pipeline"},
                "artifacts": [{"filename": "issue.html", "mime_type": "text/html"}],
            }

    result = await operations.run_automation_execution(
        Entrypoint(),
        automation_id=automation["id"],
        trigger="manual",
        source={"surface": "test"},
        user_id="user-a",
    )

    assert result["ok"] is True
    assert result["answer"] == "News issue generated."
    assert len(calls) == 1
    assert calls[0]["automation"]["id"] == automation["id"]
    assert calls[0]["storage"].user_id == "user-a"
    assert calls[0]["bundle_call_context"]["kind"] == "automation_execution"

    execution = result["execution"]
    assert execution["status"] == "success"
    assert execution["summary"] == "News issue generated."
    assert execution["result"]["answer"] == "News issue generated."
    assert execution["metadata"]["runner"] == "news_pipeline"
    assert execution["artifacts"][0]["filename"] == "issue.html"


@pytest.mark.asyncio
async def test_scheduled_execution_skips_automation_deleted_after_enqueue(tmp_path):
    _configure(tmp_path)
    storage = AutomationStorage(tmp_path, user_id="user-a")
    automation = storage.create_automation(title="Inbox monitor", schedule_cron="*/10 * * * *")
    execution = storage.create_execution(
        automation_id=automation["id"],
        status="queued",
        trigger="scheduled",
        source={"due_slot": "2026-05-08T08:49:00+00:00"},
        conversation_id="automation_job_1",
    )
    storage.delete_automation(automation_id=automation["id"])

    result = await operations.run_automation_execution(
        _entrypoint(),
        automation_id=automation["id"],
        trigger="scheduled",
        source={"due_slot": "2026-05-08T08:49:00+00:00"},
        execution_id=execution["id"],
        user_id="user-a",
    )

    updated = storage.get_execution(execution_id=execution["id"], automation_id=automation["id"])
    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "automation_not_runnable"
    assert result["automation"]["status"] == "deleted"
    assert updated["status"] == "cancelled"
    assert "automation status is deleted" in updated["summary"]


@pytest.mark.asyncio
async def test_scheduled_execution_skips_disabled_automation_at_pickup(tmp_path):
    _configure(tmp_path)
    storage = AutomationStorage(tmp_path, user_id="user-a")
    automation = storage.create_automation(title="Inbox monitor", schedule_cron="*/10 * * * *")
    storage.set_automation_status(automation_id=automation["id"], status="disabled")
    execution = storage.create_execution(automation_id=automation["id"], status="queued", trigger="scheduled")

    result = await operations.run_automation_execution(
        _entrypoint(),
        automation_id=automation["id"],
        trigger="Scheduled",
        source={},
        execution_id=execution["id"],
        user_id="user-a",
    )

    updated = storage.get_execution(execution_id=execution["id"], automation_id=automation["id"])
    assert result["ok"] is True
    assert result["skipped"] is True
    assert updated["status"] == "cancelled"
    assert "automation status is disabled" in updated["summary"]


@pytest.mark.asyncio
async def test_scheduled_execution_runs_one_shot_disabled_after_enqueue(tmp_path):
    _configure(tmp_path)
    storage = AutomationStorage(tmp_path, user_id="user-a")
    automation = storage.create_automation(
        title="One-shot monitor",
        schedule_cron="*/10 * * * *",
        recurring=False,
    )
    due_slot = "2026-05-08T08:50:00+00:00"
    execution = storage.create_execution(
        automation_id=automation["id"],
        status="queued",
        trigger="scheduled",
        source={"due_slot": due_slot},
        conversation_id="automation_job_1",
    )
    storage.update_automation(
        automation_id=automation["id"],
        status="disabled",
        metadata_patch={"one_shot_completed_due_slot": due_slot},
        revision_mode="in_place",
    )
    calls = []

    class Entrypoint:
        config = SimpleNamespace(
            tenant="demo-tenant",
            project="demo-project",
            ai_bundle_spec=SimpleNamespace(id="bundle@1-0"),
        )
        settings = SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project")

        async def execute_automation_job(self, **kwargs):
            calls.append(kwargs)
            return {"status": "success", "answer": "One-shot execution completed."}

    result = await operations.run_automation_execution(
        Entrypoint(),
        automation_id=automation["id"],
        trigger="scheduled",
        source={"due_slot": due_slot},
        execution_id=execution["id"],
        user_id="user-a",
    )

    updated = storage.get_execution(execution_id=execution["id"], automation_id=automation["id"])
    assert result["ok"] is True
    assert result.get("skipped") is not True
    assert updated["status"] == "success"
    assert updated["summary"] == "One-shot execution completed."
    assert len(calls) == 1
