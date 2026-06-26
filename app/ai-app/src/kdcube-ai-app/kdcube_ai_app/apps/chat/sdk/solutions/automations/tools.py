from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Dict, Iterable

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

try:
    from .async_storage import AsyncAutomationStorage
    from .execution_artifacts import (
        execution_artifacts,
        execution_completed_at,
        execution_id_from_artifact_ref,
        execution_for_agent,
        materialize_execution_artifact_for_current_turn,
    )
except ImportError:
    from kdcube_ai_app.apps.chat.sdk.solutions.automations.async_storage import AsyncAutomationStorage
    from kdcube_ai_app.apps.chat.sdk.solutions.automations.execution_artifacts import (
        execution_artifacts,
        execution_completed_at,
        execution_id_from_artifact_ref,
        execution_for_agent,
        materialize_execution_artifact_for_current_turn,
    )

try:
    from .common import bind_integrations, error, log_tool_error, log_tool_start, log_tool_success, ok, scope
except ImportError:
    from kdcube_ai_app.apps.chat.sdk.solutions.automations.common import bind_integrations, error, log_tool_error, log_tool_start, log_tool_success, ok, scope


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


class AutomationTools:
    @kernel_function(
        name="create_automation",
        description=(
            "Create a durable executable automation asset for the current user. Use for user requests to create, track, "
            "schedule, or manage actionable work. This is not user memory."
        ),
    )
    async def create_automation(
        self,
        title: Annotated[str, "Short human-readable automation title."],
        description: Annotated[str, "Automation instructions, context, and expected outcome."] = "",
        schedule_cron: Annotated[str, "Optional cron expression for scheduled automations; leave empty for an unscheduled automation."] = "",
        timezone: Annotated[str, "Timezone for schedule_cron, default UTC."] = "UTC",
        recurring: Annotated[
            bool,
            "True for recurring scheduled automations. False for one-shot scheduled automations that should disable themselves after the first due run.",
        ] = True,
        labels: Annotated[str, "Optional comma-separated automation labels."] = "",
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool(
                "automations.create_automation",
                title=title,
                description_chars=len(description or ""),
                has_schedule=bool(str(schedule_cron or "").strip()),
                recurring=recurring,
                labels=labels,
            )
            storage = AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"])
            automation = await storage.create_automation(
                title=title,
                description=description,
                schedule_cron=schedule_cron,
                timezone_name=timezone,
                recurring=recurring,
                labels=labels,
                source="chat",
                conversation_id=sc.get("conversation_id"),
            )
            log_tool_success("automations.create_automation", sc, automation_id=automation.get("id"), status=automation.get("status"))
            return ok(automation)
        except Exception as exc:
            return _tool_failed("automations.create_automation", "create_automation_failed", exc, sc)

    @kernel_function(
        name="list_automations",
        description="List or search current user's durable executable automation assets.",
    )
    async def list_automations(
        self,
        status: Annotated[str, "Optional status filter: enabled, disabled, archived, or deleted."] = "",
        query: Annotated[str, "Optional lexical search over automation title/body/labels/relations."] = "",
        limit: Annotated[int, "Maximum number of automations to return."] = 20,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("automations.list_automations", status=status, query=query, limit=limit)
            automations = await AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"]).list_automations(
                status=status,
                query=query,
                limit=limit,
            )
            log_tool_success("automations.list_automations", sc, count=len(automations))
            return ok({"automations": automations, "count": len(automations)})
        except Exception as exc:
            return _tool_failed("automations.list_automations", "list_automations_failed", exc, sc)

    @kernel_function(
        name="search_automations",
        description=(
            "Search current user's executable automation assets using the generated SQLite lexical index. "
            "Use before editing or deleting automations so the right automation is selected and duplicates are avoided."
        ),
    )
    async def search_automations(
        self,
        query: Annotated[str, "Search text for title, description, labels, relation ids, or conversation id."] = "",
        status: Annotated[str, "Optional status filter: enabled, disabled, archived, or deleted."] = "",
        limit: Annotated[int, "Maximum number of automations to return."] = 10,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("automations.search_automations", query=query, status=status, limit=limit)
            automations = await AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"]).search_automations(
                query=query,
                status=status,
                limit=limit,
            )
            log_tool_success("automations.search_automations", sc, count=len(automations))
            return ok({"automations": automations, "count": len(automations)})
        except Exception as exc:
            return _tool_failed("automations.search_automations", "search_automations_failed", exc, sc)

    @kernel_function(
        name="get_automation",
        description="Load one automation definition with recent execution history before updating, deleting, or discussing it.",
    )
    async def get_automation(
        self,
        automation_id: Annotated[str, "Existing automation id."],
        execution_limit: Annotated[int, "Maximum recent executions to include."] = 5,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("automations.get_automation", automation_id=automation_id, execution_limit=execution_limit)
            storage = AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"])
            automation = await storage.get_automation(automation_id)
            if automation:
                automation = (await storage.attach_execution_history([automation], execution_limit=execution_limit))[0]
            log_tool_success("automations.get_automation", sc, automation_id=automation_id, found=automation is not None)
            return ok({"automation": automation})
        except Exception as exc:
            return _tool_failed("automations.get_automation", "get_automation_failed", exc, sc, automation_id=automation_id)

    @kernel_function(
        name="update_automation",
        description=(
            "Update a automation definition after first identifying the exact automation. "
            "Title, description, schedule, labels, context, and relation edits archive the previous automation "
            "and create a new revision with a new automation id; status-only changes update in place."
        ),
    )
    async def update_automation(
        self,
        automation_id: Annotated[str, "Existing automation id."],
        title: Annotated[str, "Optional new title; leave empty to keep current title."] = "",
        description: Annotated[str, "Optional new executable instructions; leave empty to keep current description."] = "",
        status: Annotated[str, "Optional status: enabled, disabled, archived, or deleted."] = "",
        schedule_cron: Annotated[str, "Optional cron expression; leave empty to keep current schedule."] = "",
        timezone: Annotated[str, "Optional timezone; leave empty to keep current timezone."] = "",
        recurring: Annotated[
            bool | None,
            "Optional recurrence flag. Set false for a one-shot scheduled automation; omit to keep current value.",
        ] = None,
        labels: Annotated[str, "Optional comma-separated replacement labels; leave empty to keep current labels."] = "",
        revision_mode: Annotated[
            str,
            (
                "Automation revision behavior: auto, in_place, or archive_and_create. "
                "auto archives the old automation and creates a new automation id for semantic changes "
                "such as title, description, schedule, or labels, while status-only changes update in place. "
                "Use archive_and_create when the automation meaning changes and prior run state must not be reused. "
                "Use in_place only for typo/formatting/minor metadata corrections where existing execution/state history remains valid."
            ),
        ] = "auto",
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool(
                "automations.update_automation",
                automation_id=automation_id,
                title_changed=bool(title),
                description_changed=bool(description),
                status=status,
                has_schedule=bool(schedule_cron),
                recurring=recurring,
                labels_changed=bool(labels),
                revision_mode=revision_mode,
            )
            automation = await AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"]).update_automation(
                automation_id=automation_id,
                title=title or None,
                description=description or None,
                status=status or None,
                schedule_cron=schedule_cron or None,
                timezone_name=timezone or None,
                recurring=recurring,
                labels=labels or None,
                revision_mode=revision_mode,
            )
            log_tool_success("automations.update_automation", sc, automation_id=automation.get("id"), status=automation.get("status"))
            return ok(automation)
        except Exception as exc:
            return _tool_failed("automations.update_automation", "update_automation_failed", exc, sc, automation_id=automation_id)

    @kernel_function(
        name="delete_automation",
        description="Delete a automation. Defaults to soft delete by setting status=deleted.",
    )
    async def delete_automation(
        self,
        automation_id: Annotated[str, "Existing automation id."],
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("automations.delete_automation", automation_id=automation_id, hard=False)
            automation = await AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"]).delete_automation(
                automation_id=automation_id,
                hard=False,
            )
            log_tool_success("automations.delete_automation", sc, automation_id=automation_id, deleted=automation is not None)
            return ok({"deleted": automation is not None, "automation": automation})
        except Exception as exc:
            return _tool_failed("automations.delete_automation", "delete_automation_failed", exc, sc, automation_id=automation_id)

    @kernel_function(
        name="set_automation_status",
        description="Set a automation status to enabled, disabled, archived, or deleted.",
    )
    async def set_automation_status(
        self,
        automation_id: Annotated[str, "Existing automation id."],
        status: Annotated[str, "New status: enabled, disabled, archived, or deleted."],
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("automations.set_automation_status", automation_id=automation_id, status=status)
            automation = await AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"]).set_automation_status(
                automation_id=automation_id,
                status=status,
            )
            log_tool_success("automations.set_automation_status", sc, automation_id=automation.get("id"), status=automation.get("status"))
            return ok(automation)
        except Exception as exc:
            return _tool_failed("automations.set_automation_status", "set_automation_status_failed", exc, sc, automation_id=automation_id, status=status)

    @kernel_function(
        name="link_automation",
        description=(
            "Create a relationship between two existing automations. Use for automation chains such as "
            "'after this is finished, do that', dependencies, blockers, and related follow-up automations."
        ),
    )
    async def link_automation(
        self,
        automation_id: Annotated[str, "Source automation id."],
        target_automation_id: Annotated[str, "Target automation id."],
        relation: Annotated[str, "Relation: related, child, depends_on, or blocks."] = "related",
        reciprocal: Annotated[bool, "Whether to update the reciprocal relation on the target automation."] = True,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool(
                "automations.link_automation",
                automation_id=automation_id,
                target_automation_id=target_automation_id,
                relation=relation,
                reciprocal=reciprocal,
            )
            automation = await AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"]).link_automation(
                automation_id=automation_id,
                target_automation_id=target_automation_id,
                relation=relation,
                reciprocal=reciprocal,
            )
            log_tool_success("automations.link_automation", sc, automation_id=automation.get("id"), relation=relation)
            return ok(automation)
        except Exception as exc:
            return _tool_failed("automations.link_automation", "link_automation_failed", exc, sc, automation_id=automation_id, target_automation_id=target_automation_id)

    @kernel_function(
        name="list_automation_executions",
        description="List stored execution results for a automation, or across all current user's automations.",
    )
    async def list_automation_executions(
        self,
        automation_id: Annotated[str, "Optional automation id; leave empty to list executions across all automations."] = "",
        status: Annotated[str, "Optional execution status: queued, running, success, failed, or cancelled."] = "",
        limit: Annotated[int, "Maximum execution records to return."] = 20,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("automations.list_automation_executions", automation_id=automation_id, status=status, limit=limit)
            executions = await AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"]).list_executions(
                automation_id=automation_id,
                status=status,
                limit=limit,
            )
            log_tool_success("automations.list_automation_executions", sc, count=len(executions))
            return ok({"executions": executions, "count": len(executions)})
        except Exception as exc:
            return _tool_failed("automations.list_automation_executions", "list_automation_executions_failed", exc, sc, automation_id=automation_id)

    @kernel_function(
        name="search_automation_executions",
        description=(
            "Search stored automation execution results and artifact metadata. Use when the user asks what happened "
            "during prior runs, needs a generated file from an execution, or wants to inspect failures."
        ),
    )
    async def search_automation_executions(
        self,
        query: Annotated[str, "Search text across execution summary, error, log excerpt, automation title, and artifacts."] = "",
        automation_id: Annotated[str, "Optional automation id to restrict search."] = "",
        status: Annotated[str, "Optional execution status: queued, running, success, failed, or cancelled."] = "",
        limit: Annotated[int, "Maximum execution records to return."] = 20,
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("automations.search_automation_executions", query=query, automation_id=automation_id, status=status, limit=limit)
            storage = AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"])
            executions = await storage.search_executions(
                query=query,
                automation_id=automation_id,
                status=status,
                limit=limit,
            )
            log_tool_success("automations.search_automation_executions", sc, count=len(executions))
            return ok({"executions": executions, "count": len(executions)})
        except Exception as exc:
            return _tool_failed("automations.search_automation_executions", "search_automation_executions_failed", exc, sc, automation_id=automation_id)

    @kernel_function(
        name="search_recent_outputs",
        description=(
            "Search recent automation/job outputs, execution summaries, and artifact metadata for the current user. "
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
                "automations.search_recent_outputs",
                query=query,
                completed_after_iso=completed_after_iso,
                limit=limit,
            )
            storage = AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"])
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
            log_tool_success("automations.search_recent_outputs", sc, count=len(outputs), scanned=len(executions))
            return ok({"outputs": outputs, "count": len(outputs)})
        except Exception as exc:
            return _tool_failed("automations.search_recent_outputs", "search_recent_outputs_failed", exc, sc)

    @kernel_function(
        name="get_automation_execution",
        description=(
            "Load one prior automation/job execution after its execution id was discovered from list/search outputs. "
            "Use this to inspect the exact result, conversation id, turn id, and artifact metadata before "
            "answering follow-up questions about a generated report or file."
        ),
    )
    async def get_automation_execution(
        self,
        execution_id: Annotated[str, "Execution id returned by list_automation_executions, search_automation_executions, or search_recent_outputs."],
        automation_id: Annotated[str, "Optional automation id returned with the execution; leave empty if unknown."] = "",
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("automations.get_automation_execution", execution_id=execution_id, automation_id=automation_id)
            execution = await AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"]).get_execution(
                execution_id=execution_id,
                automation_id=automation_id,
            )
            if execution is None:
                log_tool_success("automations.get_automation_execution", sc, execution_id=execution_id, found=False)
                return error("execution_not_found", f"Execution {execution_id!r} was not found")
            log_tool_success("automations.get_automation_execution", sc, execution_id=execution_id, found=True)
            return ok({"execution": await execution_for_agent(execution, sc=sc)})
        except Exception as exc:
            return _tool_failed("automations.get_automation_execution", "get_automation_execution_failed", exc, sc, execution_id=execution_id)

    @kernel_function(
        name="materialize_execution_artifact",
        description=(
            "Copy one artifact from a prior automation/job execution into the current React turn outputs so it can be "
            "read, inspected, edited, or used by code. Use only after search_recent_outputs or get_automation_execution "
            "returned an artifact_ref for the selected artifact."
        ),
    )
    async def materialize_execution_artifact(
        self,
        artifact_ref: Annotated[
            str,
            "Opaque artifact_ref returned by search_recent_outputs or get_automation_execution for the selected artifact.",
        ],
    ) -> Dict[str, Any]:
        sc = None
        try:
            sc = _scope_for_tool("automations.materialize_execution_artifact", artifact_ref=artifact_ref)
            execution_id = execution_id_from_artifact_ref(artifact_ref)
            execution = await AsyncAutomationStorage(sc["storage_root"], user_id=sc["user_id"]).get_execution(
                execution_id=execution_id,
            )
            if execution is None:
                log_tool_success("automations.materialize_execution_artifact", sc, artifact_ref=artifact_ref, found=False)
                return error("execution_not_found", f"Execution {execution_id!r} was not found")
            payload = await materialize_execution_artifact_for_current_turn(
                artifact_ref=artifact_ref,
                execution=execution,
                sc=sc,
            )
            log_tool_success(
                "automations.materialize_execution_artifact",
                sc,
                artifact_ref=artifact_ref,
                logical_path=payload.get("logical_path") if isinstance(payload, dict) else "",
            )
            return ok(payload)
        except Exception as exc:
            return _tool_failed("automations.materialize_execution_artifact", "materialize_execution_artifact_failed", exc, sc)


kernel = sk.Kernel()
tools = AutomationTools()
kernel.add_plugin(tools, "automations")
