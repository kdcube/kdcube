# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.solutions.react.git_workspace import (
    _runtime_ctx_for_conversation,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import (
    workspace_lineage_segments,
    workspace_version_ref,
)


class _CapturingContextClient:
    def __init__(self) -> None:
        self.calls = []

    async def materialize_turn(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {"turn_log": {}}


@pytest.mark.asyncio
async def test_cross_conversation_turn_lookup_keeps_runtime_bound_user():
    client = _CapturingContextClient()
    runtime = RuntimeCtx(
        tenant="tenant-a",
        project="project-a",
        user_id="user-a",
        conversation_id="conversation-a",
        turn_id="turn-current",
    )
    browser = ContextBrowser(ctx_client=client, runtime_ctx=runtime)

    await browser.get_turn_log(
        turn_id="turn-requested",
        conversation_id="conversation-requested",
    )

    assert len(client.calls) == 1
    assert client.calls[0]["user_id"] == "user-a"
    assert client.calls[0]["conversation_id"] == "conversation-requested"
    assert client.calls[0]["turn_id"] == "turn-requested"


def test_cross_conversation_git_lineage_keeps_tenant_project_and_user():
    runtime = RuntimeCtx(
        tenant="tenant-a",
        project="project-a",
        user_id="user-a",
        conversation_id="conversation-a",
        turn_id="turn-current",
    )

    scoped = _runtime_ctx_for_conversation(runtime, "conversation-requested")

    assert workspace_lineage_segments(scoped) == {
        "tenant": "tenant-a",
        "project": "project-a",
        "user_id": "user-a",
        "conversation_id": "conversation-requested",
    }
    assert workspace_version_ref(scoped, "turn-requested") == (
        "refs/kdcube/tenant-a/project-a/user-a/conversation-requested/"
        "versions/turn-requested"
    )
