from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.bundle_urls import bundle_operation_url


def test_bundle_operation_url_builds_encoded_operation_path():
    url = bundle_operation_url(
        tenant="tenant a",
        project="project/a",
        bundle_id="task-tracker@1-0",
        operation="issue_attachment_download",
        query={
            "object_ref": "task:issue:attachment:ISSUE-1/attachments/a1/v000001/evidence.md",
            "mime": "text/markdown",
        },
    )

    assert url.startswith(
        "/api/integrations/bundles/tenant%20a/project%2Fa/task-tracker%401-0/operations/issue_attachment_download?"
    )
    assert "object_ref=task%3Aissue%3Aattachment%3AISSUE-1%2Fattachments%2Fa1%2Fv000001%2Fevidence.md" in url
    assert "mime=text%2Fmarkdown" in url


def test_bundle_operation_url_supports_public_route_and_base_url():
    assert bundle_operation_url(
        tenant="tenant",
        project="project",
        bundle_id="bundle@1",
        route="public",
        operation="download",
        base_url="https://demo.kdcube.tech/",
    ) == "https://demo.kdcube.tech/api/integrations/bundles/tenant/project/bundle%401/public/download"


def test_bundle_operation_url_can_omit_missing_optional_url():
    assert bundle_operation_url(
        tenant="",
        project="project",
        bundle_id="bundle@1",
        operation="download",
    ) == ""


def test_bundle_operation_url_can_require_complete_route_parts():
    with pytest.raises(ValueError, match="missing tenant"):
        bundle_operation_url(
            tenant="",
            project="project",
            bundle_id="bundle@1",
            operation="download",
            strict=True,
        )
