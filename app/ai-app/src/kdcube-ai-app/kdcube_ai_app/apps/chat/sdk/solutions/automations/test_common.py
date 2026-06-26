from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.automations.common import (
    extract_automation_execution_context,
    extract_automation_execution_context_from_scope,
)


def test_extract_automation_execution_context_keeps_direct_execution_shape():
    context = {
        "kind": "automation_execution",
        "automation_id": "automation-a",
        "execution_id": "execution-a",
        "source": {"surface": "manual"},
    }

    assert extract_automation_execution_context(context) == context


def test_extract_automation_execution_context_flattens_background_job_payload():
    context = {
        "kind": "background_job",
        "job_id": "job-a",
        "work_kind": "automation.execution.due",
        "payload": {
            "automation_id": "automation-a",
            "execution_id": "execution-a",
        },
    }

    assert extract_automation_execution_context(context) == {
        "kind": "background_job",
        "job_id": "job-a",
        "work_kind": "automation.execution.due",
        "automation_id": "automation-a",
        "execution_id": "execution-a",
    }


def test_extract_automation_execution_context_from_tool_scope():
    scope = {
        "bundle_call_context": {
            "kind": "background_job",
            "job_id": "job-a",
            "payload": {"automation_id": "automation-a", "execution_id": "execution-a"},
        }
    }

    assert extract_automation_execution_context_from_scope(scope)["automation_id"] == "automation-a"
