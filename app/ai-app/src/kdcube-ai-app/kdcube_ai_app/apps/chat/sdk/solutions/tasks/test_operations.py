from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.tasks import operations
from kdcube_ai_app.apps.chat.sdk.solutions.tasks.storage import TaskStorage


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
    operations.configure_task_operations(
        storage_root_or_error=lambda _entrypoint: str(tmp_path),
        target_user_id=lambda _entrypoint, user_id=None, fingerprint=None: user_id or fingerprint or "user-a",
        bundle_id="bundle@1-0",
    )


def test_storage_for_prefers_entrypoint_scope_methods_over_module_configuration(tmp_path):
    wrong_root = tmp_path / "wrong"
    right_root = tmp_path / "right"
    operations.configure_task_operations(
        storage_root_or_error=lambda _entrypoint: str(wrong_root),
        target_user_id=lambda _entrypoint, user_id=None, fingerprint=None: "wrong-user",
        bundle_id="bundle@1-0",
    )

    class Entrypoint:
        def task_storage_root(self):
            return str(right_root)

        def target_task_user_id(self, *, user_id=None, fingerprint=None):
            return user_id or fingerprint or "right-user"

    storage, target_user = operations.storage_for(Entrypoint(), user_id="user-a")

    assert storage.root == right_root
    assert target_user == "user-a"


@pytest.mark.asyncio
async def test_run_task_execution_uses_entrypoint_virtual_executor(tmp_path):
    _configure(tmp_path)
    storage = TaskStorage(tmp_path, user_id="user-a")
    task = storage.create_task(title="Personal news", description="Prepare my AI news brief.")
    calls = []

    class Entrypoint:
        config = SimpleNamespace(
            tenant="demo-tenant",
            project="demo-project",
            ai_bundle_spec=SimpleNamespace(id="bundle@1-0"),
        )
        settings = SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project")

        async def execute_task_job(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "success",
                "answer": "News issue generated.",
                "metadata": {"runner": "news_pipeline"},
                "artifacts": [{"filename": "issue.html", "mime_type": "text/html"}],
            }

    result = await operations.run_task_execution(
        Entrypoint(),
        task_id=task["id"],
        trigger="manual",
        source={"surface": "test"},
        user_id="user-a",
    )

    assert result["ok"] is True
    assert result["answer"] == "News issue generated."
    assert len(calls) == 1
    assert calls[0]["task"]["id"] == task["id"]
    assert calls[0]["storage"].user_id == "user-a"
    assert calls[0]["bundle_call_context"]["kind"] == "task_execution"

    execution = result["execution"]
    assert execution["status"] == "success"
    assert execution["summary"] == "News issue generated."
    assert execution["result"]["answer"] == "News issue generated."
    assert execution["metadata"]["runner"] == "news_pipeline"
    assert execution["artifacts"][0]["filename"] == "issue.html"


@pytest.mark.asyncio
async def test_scheduled_execution_skips_task_deleted_after_enqueue(tmp_path):
    _configure(tmp_path)
    storage = TaskStorage(tmp_path, user_id="user-a")
    task = storage.create_task(title="Inbox monitor", schedule_cron="*/10 * * * *")
    execution = storage.create_execution(
        task_id=task["id"],
        status="queued",
        trigger="scheduled",
        source={"due_slot": "2026-05-08T08:49:00+00:00"},
        conversation_id="task_job_1",
    )
    storage.delete_task(task_id=task["id"])

    result = await operations.run_task_execution(
        _entrypoint(),
        task_id=task["id"],
        trigger="scheduled",
        source={"due_slot": "2026-05-08T08:49:00+00:00"},
        execution_id=execution["id"],
        user_id="user-a",
    )

    updated = storage.get_execution(execution_id=execution["id"], task_id=task["id"])
    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "task_not_runnable"
    assert result["task"]["status"] == "deleted"
    assert updated["status"] == "cancelled"
    assert "task status is deleted" in updated["summary"]


@pytest.mark.asyncio
async def test_scheduled_execution_skips_disabled_task_at_pickup(tmp_path):
    _configure(tmp_path)
    storage = TaskStorage(tmp_path, user_id="user-a")
    task = storage.create_task(title="Inbox monitor", schedule_cron="*/10 * * * *")
    storage.set_task_status(task_id=task["id"], status="disabled")
    execution = storage.create_execution(task_id=task["id"], status="queued", trigger="scheduled")

    result = await operations.run_task_execution(
        _entrypoint(),
        task_id=task["id"],
        trigger="Scheduled",
        source={},
        execution_id=execution["id"],
        user_id="user-a",
    )

    updated = storage.get_execution(execution_id=execution["id"], task_id=task["id"])
    assert result["ok"] is True
    assert result["skipped"] is True
    assert updated["status"] == "cancelled"
    assert "task status is disabled" in updated["summary"]
