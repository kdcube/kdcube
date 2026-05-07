from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

try:
    from .async_storage import AsyncTaskStorage
except ImportError:
    from kdcube_ai_app.apps.chat.sdk.solutions.tasks.async_storage import AsyncTaskStorage

try:
    from .common import bind_integrations, error, log_tool_error, log_tool_start, log_tool_success, ok, scope
except ImportError:
    from kdcube_ai_app.apps.chat.sdk.solutions.tasks.common import bind_integrations, error, log_tool_error, log_tool_start, log_tool_success, ok, scope


def _linked_task_ids(task: Dict[str, Any]) -> List[str]:
    relations = task.get("relations") if isinstance(task.get("relations"), dict) else {}
    ids: List[str] = []
    for key in ("parent_task_id", "child_task_ids", "depends_on_task_ids", "blocks_task_ids", "related_task_ids"):
        value = relations.get(key)
        if isinstance(value, list):
            ids.extend(str(item).strip() for item in value if str(item).strip())
        elif value:
            ids.append(str(value).strip())
    out: List[str] = []
    seen: set[str] = set()
    for item in ids:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _job_context(sc: Dict[str, Any]) -> Dict[str, Any]:
    raw = sc.get("bundle_call_context") if isinstance(sc, dict) else {}
    context = dict(raw or {}) if isinstance(raw, dict) else {}
    if str(context.get("kind") or "") == "background_job":
        payload = context.get("payload") if isinstance(context.get("payload"), dict) else {}
        merged = dict(payload)
        merged.update({key: value for key, value in context.items() if key != "payload"})
        return merged
    return context


def _context_value(context: Dict[str, Any], key: str) -> str:
    value = context.get(key)
    if isinstance(value, dict):
        return ""
    return str(value or "").strip()


def _scope_for_tool(tool_name: str, **params: Any) -> Dict[str, Any]:
    sc = scope()
    log_tool_start(tool_name, sc, **params)
    return sc


def _tool_failed(tool_name: str, code: str, exc: Exception, sc: Dict[str, Any] | None = None, **params: Any) -> Dict[str, Any]:
    log_tool_error(tool_name, exc, sc, **params)
    return error(code, str(exc))


class JobTaskTools:
    @kernel_function(
        name="get_current_task",
        description=(
            "Load the task definition that this job is executing. Optionally includes explicitly linked task "
            "definitions for bounded context. This tool is read-only."
        ),
    )
    async def get_current_task(
        self,
        include_linked: Annotated[bool, "Whether to include explicitly linked task definitions."] = True,
        execution_limit: Annotated[int, "Recent execution records to include for the current task."] = 3,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("task_job.get_current_task", include_linked=include_linked, execution_limit=execution_limit)
            context = _job_context(sc)
            task_id = _context_value(context, "task_id")
            if not task_id:
                log_tool_success("task_job.get_current_task", sc, task_id="", found=False)
                return error("job_context_missing_task_id", "Current task id is missing from bundle call context.")
            storage = AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"])
            task = await storage.get_task(task_id)
            if not task:
                log_tool_success("task_job.get_current_task", sc, task_id=task_id, found=False)
                return error("task_not_found", f"Task {task_id!r} was not found")
            task = (await storage.attach_execution_history([task], execution_limit=execution_limit))[0]
            linked = []
            if include_linked:
                for linked_id in _linked_task_ids(task):
                    linked_task = await storage.get_task(linked_id)
                    if linked_task:
                        linked.append(linked_task)
            log_tool_success("task_job.get_current_task", sc, task_id=task_id, linked_count=len(linked))
            return ok({"task": task, "linked_tasks": linked})
        except Exception as exc:
            return _tool_failed("task_job.get_current_task", "get_current_task_failed", exc, sc)

    @kernel_function(
        name="search_task_executions",
        description="Read-only search over prior execution records for this user and task family.",
    )
    async def search_task_executions(
        self,
        query: Annotated[str, "Search text across summaries, logs, errors, and artifacts."] = "",
        status: Annotated[str, "Optional status filter."] = "",
        include_linked: Annotated[bool, "Whether to include explicitly linked task executions."] = False,
        limit: Annotated[int, "Maximum execution records to return."] = 10,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool(
                "task_job.search_task_executions",
                query=query,
                status=status,
                include_linked=include_linked,
                limit=limit,
            )
            context = _job_context(sc)
            task_id = _context_value(context, "task_id")
            if not task_id:
                log_tool_success("task_job.search_task_executions", sc, task_id="", count=0)
                return error("job_context_missing_task_id", "Current task id is missing from bundle call context.")
            storage = AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"])
            task_ids = [task_id]
            if include_linked:
                task = await storage.get_task(task_id)
                if task:
                    task_ids.extend(_linked_task_ids(task))
            executions = []
            seen: set[str] = set()
            per_task_limit = max(1, int(limit or 10))
            for current_task_id in task_ids:
                for item in await storage.search_executions(
                    query=query,
                    task_id=current_task_id,
                    status=status,
                    limit=per_task_limit,
                ):
                    execution_id = str(item.get("id") or "")
                    if execution_id and execution_id not in seen:
                        seen.add(execution_id)
                        executions.append(item)
            executions.sort(
                key=lambda item: str(
                    item.get("started_at")
                    or item.get("finished_at")
                    or item.get("updated_at")
                    or item.get("created_at")
                    or ""
                ),
                reverse=True,
            )
            executions = executions[:per_task_limit]
            log_tool_success("task_job.search_task_executions", sc, task_id=task_id, count=len(executions))
            return ok({"executions": executions, "count": len(executions)})
        except Exception as exc:
            return _tool_failed("task_job.search_task_executions", "search_task_executions_failed", exc, sc)

    @kernel_function(
        name="update_execution_journal",
        description=(
            "Update the current execution journal with substantial progress or final outcome. Use when status, "
            "user-facing summary, durable logs, structured result, or produced artifacts must not be lost."
        ),
    )
    async def update_execution_journal(
        self,
        status: Annotated[str, "Execution status: queued, running, success, failed, or cancelled."] = "running",
        summary: Annotated[str, "Short user-facing summary of the current progress or final outcome."] = "",
        log_excerpt: Annotated[str, "Compact log line or excerpt worth keeping."] = "",
        result_json: Annotated[str, "Optional compact JSON object with structured result data."] = "",
        artifacts_json: Annotated[
            str,
            "Optional JSON list of important artifacts. Include logical_path, filename, mime_type, hosted_uri, and description when known.",
        ] = "",
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool(
                "task_job.update_execution_journal",
                status=status,
                summary_chars=len(summary or ""),
                log_chars=len(log_excerpt or ""),
                has_result=bool(str(result_json or "").strip()),
                has_artifacts=bool(str(artifacts_json or "").strip()),
            )
            context = _job_context(sc)
            task_id = _context_value(context, "task_id")
            execution_id = _context_value(context, "execution_id")
            if not task_id or not execution_id:
                log_tool_success(
                    "task_job.update_execution_journal",
                    sc,
                    task_id=task_id,
                    execution_id=execution_id,
                    updated=False,
                )
                return error(
                    "job_context_missing_execution_identity",
                    "Current task id or execution id is missing from bundle call context.",
                )
            result: Dict[str, Any] = {}
            if result_json.strip():
                parsed = json.loads(result_json)
                result = parsed if isinstance(parsed, dict) else {"value": parsed}
            artifacts = []
            if artifacts_json.strip():
                parsed_artifacts = json.loads(artifacts_json)
                artifacts = parsed_artifacts if isinstance(parsed_artifacts, list) else []
            execution = await AsyncTaskStorage(sc["storage_root"], user_id=sc["user_id"]).update_execution(
                execution_id=execution_id,
                task_id=task_id,
                status=status or None,
                conversation_id=sc.get("conversation_id") or None,
                turn_id=sc.get("turn_id") or None,
                summary=summary,
                result=result,
                log_excerpt=log_excerpt,
                artifacts=artifacts,
                append_artifacts=True,
            )
            log_tool_success(
                "task_job.update_execution_journal",
                sc,
                task_id=task_id,
                execution_id=execution.get("id"),
                status=execution.get("status"),
                artifact_count=execution.get("artifact_count"),
            )
            return ok(execution)
        except Exception as exc:
            return _tool_failed("task_job.update_execution_journal", "update_execution_journal_failed", exc, sc)


kernel = sk.Kernel()
tools = JobTaskTools()
kernel.add_plugin(tools, "task_job")
