# SPDX-License-Identifier: MIT
from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import build_logical_artifact_path
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.resolver import (
    canonicalize_event_ref_for_context,
    resolve_event_ref_action,
)
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore


@pytest.mark.asyncio
async def test_resolve_event_ref_action_downloads_canonical_fi_artifact(tmp_path):
    store = ConversationStore(storage_uri=tmp_path.as_uri())
    await store.put_artifact_file(
        tenant="tenant",
        project="project",
        user="user",
        fingerprint=None,
        conversation_id="conversation",
        turn_id="turn_1",
        relpath="turn_1/outputs/problem.md",
        data=b"# Problem\n",
        mime="text/markdown",
    )
    ref = build_logical_artifact_path(
        turn_id="turn_1",
        namespace="outputs",
        relpath="problem.md",
        conversation_id="conversation",
    )

    result = await resolve_event_ref_action(
        {"object_ref": ref, "action": "download"},
        tenant="tenant",
        project="project",
        user_id="user",
        storage_path=tmp_path.as_uri(),
    )

    assert result["ok"] is True
    assert result["resolver"] == "react.event_ref"
    assert result["object_ref"] == ref
    assert result["filename"] == "problem.md"
    assert result["mime"] == "text/markdown"
    assert "content_base64" not in result
    assert result["download_url"] == (
        "/api/cb/resources/tenant/project/conv/user/conversation/turn/turn_1/attachment/turn_1/outputs/problem.md/download"
    )


@pytest.mark.asyncio
async def test_resolve_event_ref_action_reports_unknown_namespace():
    result = await resolve_event_ref_action(
        {"object_ref": "mem:mem_1", "action": "download"},
        tenant="tenant",
        project="project",
        user_id="user",
    )

    assert result["ok"] is False
    assert result["namespace"] == "mem"
    assert result["error"] == "event_ref_resolver_not_registered"


@pytest.mark.asyncio
async def test_resolve_event_ref_action_can_require_cross_conversation_fi_refs():
    ref = "fi:turn_2026-06-07.outputs/problem.md"
    result = await resolve_event_ref_action(
        {"object_ref": ref, "action": "download"},
        tenant="tenant",
        project="project",
        user_id="user",
        require_embedded_conversation=True,
    )

    assert result["ok"] is False
    assert result["object_ref"] == ref
    assert result["error"] == "fi_ref_requires_embedded_conversation"


@pytest.mark.asyncio
async def test_resolve_event_ref_action_allows_current_turn_fi_refs_when_not_required():
    ref = "fi:turn_2026-06-07.outputs/problem.md"
    result = await resolve_event_ref_action(
        {"object_ref": ref, "action": "capabilities"},
        tenant="tenant",
        project="project",
        user_id="user",
    )

    assert result["ok"] is True
    assert result["object_ref"] == ref
    assert result["capabilities"]["download"] is True
    assert result["default_open_effect_action"] == "download"


def test_canonicalize_event_ref_for_context_adds_conversation_to_current_turn_fi_refs():
    assert canonicalize_event_ref_for_context(
        "fi:turn_2026-06-07.outputs/problem.md",
        conversation_id="abc",
    ) == "fi:conv_abc.turn_2026-06-07.outputs/problem.md"


def test_canonicalize_event_ref_for_context_preserves_existing_cross_conversation_fi_refs():
    ref = "fi:conv_other.turn_2026-06-07.outputs/problem.md"
    assert canonicalize_event_ref_for_context(ref, conversation_id="abc") == ref
