from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.automations.async_storage import AsyncAutomationStorage, list_automation_user_ids
from kdcube_ai_app.apps.chat.sdk.solutions.automations.execution_artifacts import (
    artifact_ref_for_execution_artifact,
    downloadable_execution_artifacts,
    execution_artifacts,
    execution_completed_at,
    execution_for_agent,
    execution_id_from_artifact_ref,
    materialize_execution_artifact_for_current_turn,
    read_execution_artifact_for_download,
)
from kdcube_ai_app.apps.chat.sdk.solutions.automations.executions_storage import AutomationExecutionsStorage
from kdcube_ai_app.apps.chat.sdk.solutions.automations.storage import AUTOMATION_STATUSES, AutomationStorage
from kdcube_ai_app.apps.chat.sdk.solutions.automations.due import WORK_KIND_AUTOMATION_EXECUTION_DUE, configure_due_automations, enqueue_due_automations, handle_job
from kdcube_ai_app.apps.chat.sdk.solutions.automations.operations import WORK_KIND_AUTOMATION_RUN_NOW, configure_automation_operations
from kdcube_ai_app.apps.chat.sdk.solutions.automations.system_jobs import enqueue_job_as_identity
from kdcube_ai_app.apps.chat.sdk.solutions.automations.common import (
    extract_automation_execution_context,
    extract_automation_execution_context_from_scope,
)

__all__ = [
    "AsyncAutomationStorage",
    "AUTOMATION_STATUSES",
    "AutomationExecutionsStorage",
    "AutomationStorage",
    "WORK_KIND_AUTOMATION_EXECUTION_DUE",
    "WORK_KIND_AUTOMATION_RUN_NOW",
    "artifact_ref_for_execution_artifact",
    "configure_due_automations",
    "configure_automation_operations",
    "downloadable_execution_artifacts",
    "enqueue_due_automations",
    "enqueue_job_as_identity",
    "execution_artifacts",
    "execution_completed_at",
    "execution_for_agent",
    "execution_id_from_artifact_ref",
    "extract_automation_execution_context",
    "extract_automation_execution_context_from_scope",
    "handle_job",
    "list_automation_user_ids",
    "materialize_execution_artifact_for_current_turn",
    "read_execution_artifact_for_download",
]
