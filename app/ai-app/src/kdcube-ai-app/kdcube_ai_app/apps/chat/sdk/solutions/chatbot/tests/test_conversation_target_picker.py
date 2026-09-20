from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.application_resources import (
    application_resource,
)

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import bind_bundle_named_service_caller
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.target_policy import (
    configured_conversation_target_rows,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


def _catalog():
    return {
        "named_services": [{"namespace": "conv"}],
        "conversation_targets": [
            {"bundle_id": "allowed@1-0"},
            {"bundle_id": "candidate@1-0"},
        ],
    }


def _identity():
    return {"tenant": "tenant-a", "project": "project-a"}


def _target(application: str) -> str:
    return application_resource(
        tenant="tenant-a",
        project="project-a",
        application=application,
        agent="*",
    )


@pytest.mark.asyncio
async def test_picker_keeps_descriptor_targets_for_three_state_card_projection():
    owner = SimpleNamespace(
        _named_services_bundle_id=lambda: "own@1-0",
        _agent_selection_identity=_identity,
    )
    calls = []

    async def _hub(call):
        calls.append(call)
        raise AssertionError("target discovery must not probe a previous Card")

    with bind_bundle_named_service_caller(_hub):
        catalog = await BaseEntrypoint._attach_conversation_targets(owner, _catalog(), "main")
    assert calls == []
    assert catalog["conversation_targets"] == [
        {"bundle_id": "allowed@1-0", "resource": _target("allowed@1-0")},
        {"bundle_id": "candidate@1-0", "resource": _target("candidate@1-0")},
        {"bundle_id": "own@1-0", "resource": _target("own@1-0")},
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


def test_typed_descriptor_target_keeps_its_canonical_resource():
    target = application_resource(
        tenant="tenant-a",
        project="project-a",
        application="*",
        agent="*",
    )
    props = {
        "surfaces": {
            "as_consumer": {
                "agents": {
                    "main": {
                        "tools": [
                            {
                                "kind": "named_service",
                                "namespaces": {"conv": {"targets": [target]}},
                            }
                        ]
                    }
                }
            }
        }
    }

    assert configured_conversation_target_rows(props, "main") == (
        {"bundle_id": "*", "resource": target},
    )


@pytest.mark.asyncio
async def test_picker_keeps_typed_wildcard_identity_through_final_catalog():
    target = _target("*")
    owner = SimpleNamespace(
        _named_services_bundle_id=lambda: "own@1-0",
        _agent_selection_identity=_identity,
    )
    catalog = await BaseEntrypoint._attach_conversation_targets(
        owner,
        {
            "named_services": [{"namespace": "conv"}],
            "conversation_targets": [
                {"bundle_id": "*", "resource": target},
            ],
        },
        "main",
    )

    assert {row["resource"] for row in catalog["conversation_targets"]} == {
        target,
        _target("own@1-0"),
    }
