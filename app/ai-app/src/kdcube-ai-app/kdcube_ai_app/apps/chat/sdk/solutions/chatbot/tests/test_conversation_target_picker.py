from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import bind_bundle_named_service_caller
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


def _catalog():
    return {
        "named_services": [{"namespace": "conv"}],
        "conversation_targets": [
            {"bundle_id": "allowed@1-0"},
            {"bundle_id": "candidate@1-0"},
        ],
    }


@pytest.mark.asyncio
async def test_picker_keeps_descriptor_targets_for_three_state_card_projection():
    owner = SimpleNamespace(
        _named_services_bundle_id=lambda: "own@1-0",
    )
    calls = []

    async def _hub(call):
        calls.append(call)
        raise AssertionError("target discovery must not probe a previous Card")

    with bind_bundle_named_service_caller(_hub):
        catalog = await BaseEntrypoint._attach_conversation_targets(owner, _catalog(), "main")
    assert calls == []
    assert catalog["conversation_targets"] == [
        {"bundle_id": "allowed@1-0"},
        {"bundle_id": "candidate@1-0"},
        {"bundle_id": "own@1-0"},
    ]


@pytest.mark.asyncio
async def test_picker_has_no_targets_without_the_conversation_namespace():
    owner = SimpleNamespace(
        _named_services_bundle_id=lambda: "own@1-0",
    )
    catalog = await BaseEntrypoint._attach_conversation_targets(
        owner,
        {
            "named_services": [],
            "conversation_targets": [{"bundle_id": "candidate@1-0"}],
        },
        "main",
    )
    assert catalog["conversation_targets"] == []
