from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Dict, Iterable

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

try:
    from .async_storage import AsyncTaskStorage
    from .execution_artifacts import (
        execution_artifacts,
        execution_completed_at,
        execution_id_from_artifact_ref,
        execution_for_agent,
        materialize_execution_artifact_for_current_turn,
    )
except ImportError:
    from kdcube_ai_app.apps.chat.sdk.solutions.tasks.async_storage import AsyncTaskStorage
    from kdcube_ai_app.apps.chat.sdk.solutions.tasks.execution_artifacts import (
        execution_artifacts,
        execution_completed_at,
        execution_id_from_artifact_ref,
        execution_for_agent,
        materialize_execution_artifact_for_current_turn,
    )

try:
    from .common import bind_integrations, error, log_tool_error, log_tool_start, log_tool_success, ok, scope
except ImportError:
    from kdcube_ai_app.apps.chat.sdk.solutions.tasks.common import bind_integrations, error, log_tool_error, log_tool_start, log_tool_success, ok, scope


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _filter_executions_after(executions: Iterable[Dict[str, Any]], completed_after_iso: str) -> list[Dict[str, Any]]:
    after = _parse_iso(completed_after_iso)
    if after is None:
        return list(executions)
    out = []
    for execution in executions:
        completed = _parse_iso(execution_completed_at(execution))
        if completed is not None and completed > after:
            out.append(execution)
    return out


def _scope_for_tool(tool_name: str, **params: Any) -> Dict[str, Any]:
    sc = scope()
    log_tool_start(tool_name, sc, **params)
    return sc


def _tool_failed(tool_name: str, code: str, exc: Exception, sc: Dict[str, Any] | None = None, **params: Any) -> Dict[str, Any]:
    log_tool_error(tool_name, exc, sc, **params)
    return error(code, str(exc))


class TaskTools:
    @kernel_function(
        name="create_task",
        description=(
            "Create a durable executable task asset for the current user. Use for user requests to create, track, "
            "schedule, or manage actionable work. This is not user memory."
        ),
    )
    async def create_task(
        self,
        title: Annotated[str, "Short human-readable task title."],
        description: Annotated[str, "Task instructions, context, and expected outcome."] = "",
        schedule_cron: Annotated[str, "Optional cron expression for scheduled tasks; leave empty for an unscheduled task."] = "",
        timezone: Annotated[str, "Timezone for schedule_cron, default UTC."] = "UTC",
        recurring: Annotated[
            bool,
            "True for recurring scheduled tasks. False for one-shot scheduled tasks that should disable themselves after the first due run.",
        ] = True,
        labels: Annotated[str, "Optional comma-separated task labels."] = "",
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool(
                "tasks.create_task",
                title=title,
                description_chars=len(description or ""),
                has_schedule=bool(str(schedule_cron or "").strip()),
                recurring=recurring,
                labels=labels,
            )
            storage = AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"])
            task = await storage.create_task(
                title=title,
                description=description,
                schedule_cron=schedule_cron,
                timezone_name=timezone,
                recurring=recurring,
                labels=labels,
                source="chat",
                conversation_id=sc.get("conversation_id"),
            )
            log_tool_success("tasks.create_task", sc, task_id=task.get("id"), status=task.get("status"))
            return ok(task)
        except Exception as exc:
            return _tool_failed("tasks.create_task", "create_task_failed", exc, sc)

    @kernel_function(
        name="list_tasks",
        description="List or search current user's durable executable task assets.",
    )
    async def list_tasks(
        self,
        status: Annotated[str, "Optional status filter: enabled, disabled, archived, or deleted."] = "",
        query: Annotated[str, "Optional lexical search over task title/body/labels/relations."] = "",
        limit: Annotated[int, "Maximum number of tasks to return."] = 20,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("tasks.list_tasks", status=status, query=query, limit=limit)
            tasks = await AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"]).list_tasks(
                status=status,
                query=query,
                limit=limit,
            )
            log_tool_success("tasks.list_tasks", sc, count=len(tasks))
            return ok({"tasks": tasks, "count": len(tasks)})
        except Exception as exc:
            return _tool_failed("tasks.list_tasks", "list_tasks_failed", exc, sc)

    @kernel_function(
        name="search_tasks",
        description=(
            "Search current user's executable task assets using the generated SQLite lexical index. "
            "Use before editing or deleting tasks so the right task is selected and duplicates are avoided."
        ),
    )
    async def search_tasks(
        self,
        query: Annotated[str, "Search text for title, description, labels, relation ids, or conversation id."] = "",
        status: Annotated[str, "Optional status filter: enabled, disabled, archived, or deleted."] = "",
        limit: Annotated[int, "Maximum number of tasks to return."] = 10,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("tasks.search_tasks", query=query, status=status, limit=limit)
            tasks = await AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"]).search_tasks(
                query=query,
                status=status,
                limit=limit,
            )
            log_tool_success("tasks.search_tasks", sc, count=len(tasks))
            return ok({"tasks": tasks, "count": len(tasks)})
        except Exception as exc:
            return _tool_failed("tasks.search_tasks", "search_tasks_failed", exc, sc)

    @kernel_function(
        name="get_task",
        description="Load one task definition with recent execution history before updating, deleting, or discussing it.",
    )
    async def get_task(
        self,
        task_id: Annotated[str, "Existing task id."],
        execution_limit: Annotated[int, "Maximum recent executions to include."] = 5,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("tasks.get_task", task_id=task_id, execution_limit=execution_limit)
            storage = AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"])
            task = await storage.get_task(task_id)
            if task:
                task = (await storage.attach_execution_history([task], execution_limit=execution_limit))[0]
            log_tool_success("tasks.get_task", sc, task_id=task_id, found=task is not None)
            return ok({"task": task})
        except Exception as exc:
            return _tool_failed("tasks.get_task", "get_task_failed", exc, sc, task_id=task_id)

    @kernel_function(
        name="update_task",
        description=(
            "Update a task definition after first identifying the exact task. "
            "Title, description, schedule, labels, context, and relation edits archive the previous task "
            "and create a new revision with a new task id; status-only changes update in place."
        ),
    )
    async def update_task(
        self,
        task_id: Annotated[str, "Existing task id."],
        title: Annotated[str, "Optional new title; leave empty to keep current title."] = "",
        description: Annotated[str, "Optional new executable instructions; leave empty to keep current description."] = "",
        status: Annotated[str, "Optional status: enabled, disabled, archived, or deleted."] = "",
        schedule_cron: Annotated[str, "Optional cron expression; leave empty to keep current schedule."] = "",
        timezone: Annotated[str, "Optional timezone; leave empty to keep current timezone."] = "",
        recurring: Annotated[
            bool | None,
            "Optional recurrence flag. Set false for a one-shot scheduled task; omit to keep current value.",
        ] = None,
        labels: Annotated[str, "Optional comma-separated replacement labels; leave empty to keep current labels."] = "",
        revision_mode: Annotated[
            str,
            (
                "Task revision behavior: auto, in_place, or archive_and_create. "
                "auto archives the old task and creates a new task id for semantic changes "
                "such as title, description, schedule, or labels, while status-only changes update in place. "
                "Use archive_and_create when the task meaning changes and prior run state must not be reused. "
                "Use in_place only for typo/formatting/minor metadata corrections where existing execution/state history remains valid."
            ),
        ] = "auto",
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool(
                "tasks.update_task",
                task_id=task_id,
                title_changed=bool(title),
                description_changed=bool(description),
                status=status,
                has_schedule=bool(schedule_cron),
                recurring=recurring,
                labels_changed=bool(labels),
                revision_mode=revision_mode,
            )
            task = await AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"]).update_task(
                task_id=task_id,
                title=title or None,
                description=description or None,
                status=status or None,
                schedule_cron=schedule_cron or None,
                timezone_name=timezone or None,
                recurring=recurring,
                labels=labels or None,
                revision_mode=revision_mode,
            )
            log_tool_success("tasks.update_task", sc, task_id=task.get("id"), status=task.get("status"))
            return ok(task)
        except Exception as exc:
            return _tool_failed("tasks.update_task", "update_task_failed", exc, sc, task_id=task_id)

    @kernel_function(
        name="delete_task",
        description="Delete a task. Defaults to soft delete by setting status=deleted.",
    )
    async def delete_task(
        self,
        task_id: Annotated[str, "Existing task id."],
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("tasks.delete_task", task_id=task_id, hard=False)
            task = await AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"]).delete_task(
                task_id=task_id,
                hard=False,
            )
            log_tool_success("tasks.delete_task", sc, task_id=task_id, deleted=task is not None)
            return ok({"deleted": task is not None, "task": task})
        except Exception as exc:
            return _tool_failed("tasks.delete_task", "delete_task_failed", exc, sc, task_id=task_id)

    @kernel_function(
        name="set_task_status",
        description="Set a task status to enabled, disabled, archived, or deleted.",
    )
    async def set_task_status(
        self,
        task_id: Annotated[str, "Existing task id."],
        status: Annotated[str, "New status: enabled, disabled, archived, or deleted."],
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("tasks.set_task_status", task_id=task_id, status=status)
            task = await AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"]).set_task_status(
                task_id=task_id,
                status=status,
            )
            log_tool_success("tasks.set_task_status", sc, task_id=task.get("id"), status=task.get("status"))
            return ok(task)
        except Exception as exc:
            return _tool_failed("tasks.set_task_status", "set_task_status_failed", exc, sc, task_id=task_id, status=status)

    @kernel_function(
        name="link_task",
        description=(
            "Create a relationship between two existing tasks. Use for task chains such as "
            "'after this is finished, do that', dependencies, blockers, and related follow-up tasks."
        ),
    )
    async def link_task(
        self,
        task_id: Annotated[str, "Source task id."],
        target_task_id: Annotated[str, "Target task id."],
        relation: Annotated[str, "Relation: related, child, depends_on, or blocks."] = "related",
        reciprocal: Annotated[bool, "Whether to update the reciprocal relation on the target task."] = True,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool(
                "tasks.link_task",
                task_id=task_id,
                target_task_id=target_task_id,
                relation=relation,
                reciprocal=reciprocal,
            )
            task = await AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"]).link_task(
                task_id=task_id,
                target_task_id=target_task_id,
                relation=relation,
                reciprocal=reciprocal,
            )
            log_tool_success("tasks.link_task", sc, task_id=task.get("id"), relation=relation)
            return ok(task)
        except Exception as exc:
            return _tool_failed("tasks.link_task", "link_task_failed", exc, sc, task_id=task_id, target_task_id=target_task_id)

    @kernel_function(
        name="list_task_executions",
        description="List stored execution results for a task, or across all current user's tasks.",
    )
    async def list_task_executions(
        self,
        task_id: Annotated[str, "Optional task id; leave empty to list executions across all tasks."] = "",
        status: Annotated[str, "Optional execution status: queued, running, success, failed, or cancelled."] = "",
        limit: Annotated[int, "Maximum execution records to return."] = 20,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("tasks.list_task_executions", task_id=task_id, status=status, limit=limit)
            executions = await AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"]).list_executions(
                task_id=task_id,
                status=status,
                limit=limit,
            )
            log_tool_success("tasks.list_task_executions", sc, count=len(executions))
            return ok({"executions": executions, "count": len(executions)})
        except Exception as exc:
            return _tool_failed("tasks.list_task_executions", "list_task_executions_failed", exc, sc, task_id=task_id)

    @kernel_function(
        name="search_task_executions",
        description=(
            "Search stored task execution results and artifact metadata. Use when the user asks what happened "
            "during prior runs, needs a generated file from an execution, or wants to inspect failures."
        ),
    )
    async def search_task_executions(
        self,
        query: Annotated[str, "Search text across execution summary, error, log excerpt, task title, and artifacts."] = "",
        task_id: Annotated[str, "Optional task id to restrict search."] = "",
        status: Annotated[str, "Optional execution status: queued, running, success, failed, or cancelled."] = "",
        limit: Annotated[int, "Maximum execution records to return."] = 20,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("tasks.search_task_executions", query=query, task_id=task_id, status=status, limit=limit)
            storage = AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"])
            executions = await storage.search_executions(
                query=query,
                task_id=task_id,
                status=status,
                limit=limit,
            )
            log_tool_success("tasks.search_task_executions", sc, count=len(executions))
            return ok({"executions": executions, "count": len(executions)})
        except Exception as exc:
            return _tool_failed("tasks.search_task_executions", "search_task_executions_failed", exc, sc, task_id=task_id)

    @kernel_function(
        name="search_recent_outputs",
        description=(
            "Search recent task/job outputs, execution summaries, and artifact metadata for the current user. "
            "Use this when the user refers to a prior delivered report, spreadsheet, file, job result, or says "
            "'it', 'that report', or 'the file you sent' and the item is not in the current chat timeline."
        ),
    )
    async def search_recent_outputs(
        self,
        query: Annotated[str, "Search text for the report/file/job/output the user is referring to. Leave empty for recent outputs."] = "",
        completed_after_iso: Annotated[
            str,
            "Optional ISO timestamp. Use when a visible timeline object may be the referent; search only job outputs completed after that visible object's timestamp.",
        ] = "",
        limit: Annotated[int, "Maximum matching outputs to return."] = 10,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool(
                "tasks.search_recent_outputs",
                query=query,
                completed_after_iso=completed_after_iso,
                limit=limit,
            )
            storage = AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"])
            executions = await storage.search_executions(query=query, limit=max(limit * 3, limit))
            executions = _filter_executions_after(executions, completed_after_iso)
            outputs = []
            for execution in executions:
                artifacts = await execution_artifacts(execution, sc=sc)
                result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
                if not artifacts and not result and not str(execution.get("summary") or "").strip():
                    continue
                outputs.append(await execution_for_agent(execution, sc=sc))
                if len(outputs) >= max(1, int(limit or 10)):
                    break
            log_tool_success("tasks.search_recent_outputs", sc, count=len(outputs), scanned=len(executions))
            return ok({"outputs": outputs, "count": len(outputs)})
        except Exception as exc:
            return _tool_failed("tasks.search_recent_outputs", "search_recent_outputs_failed", exc, sc)

    @kernel_function(
        name="get_task_execution",
        description=(
            "Load one prior task/job execution after its execution id was discovered from list/search outputs. "
            "Use this to inspect the exact result, conversation id, turn id, and artifact metadata before "
            "answering follow-up questions about a generated report or file."
        ),
    )
    async def get_task_execution(
        self,
        execution_id: Annotated[str, "Execution id returned by list_task_executions, search_task_executions, or search_recent_outputs."],
        task_id: Annotated[str, "Optional task id returned with the execution; leave empty if unknown."] = "",
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("tasks.get_task_execution", execution_id=execution_id, task_id=task_id)
            execution = await AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"]).get_execution(
                execution_id=execution_id,
                task_id=task_id,
            )
            if execution is None:
                log_tool_success("tasks.get_task_execution", sc, execution_id=execution_id, found=False)
                return error("execution_not_found", f"Execution {execution_id!r} was not found")
            log_tool_success("tasks.get_task_execution", sc, execution_id=execution_id, found=True)
            return ok({"execution": await execution_for_agent(execution, sc=sc)})
        except Exception as exc:
            return _tool_failed("tasks.get_task_execution", "get_task_execution_failed", exc, sc, execution_id=execution_id)

    @kernel_function(
        name="materialize_execution_artifact",
        description=(
            "Copy one artifact from a prior task/job execution into the current React turn outputs so it can be "
            "read, inspected, edited, or used by code. Use only after search_recent_outputs or get_task_execution "
            "returned an artifact_ref for the selected artifact."
        ),
    )
    async def materialize_execution_artifact(
        self,
        artifact_ref: Annotated[
            str,
            "Opaque artifact_ref returned by search_recent_outputs or get_task_execution for the selected artifact.",
        ],
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("tasks.materialize_execution_artifact", artifact_ref=artifact_ref)
            execution_id = execution_id_from_artifact_ref(artifact_ref)
            execution = await AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"]).get_execution(
                execution_id=execution_id,
            )
            if execution is None:
                log_tool_success("tasks.materialize_execution_artifact", sc, artifact_ref=artifact_ref, found=False)
                return error("execution_not_found", f"Execution {execution_id!r} was not found")
            payload = await materialize_execution_artifact_for_current_turn(
                artifact_ref=artifact_ref,
                execution=execution,
                sc=sc,
            )
            log_tool_success(
                "tasks.materialize_execution_artifact",
                sc,
                artifact_ref=artifact_ref,
                logical_path=payload.get("logical_path") if isinstance(payload, dict) else "",
            )
            return ok(payload)
        except Exception as exc:
            return _tool_failed("tasks.materialize_execution_artifact", "materialize_execution_artifact_failed", exc, sc)


kernel = sk.Kernel()
tools = TaskTools()
kernel.add_plugin(tools, "tasks")
