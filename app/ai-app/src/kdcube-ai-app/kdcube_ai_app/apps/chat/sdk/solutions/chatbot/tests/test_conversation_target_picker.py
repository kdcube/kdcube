from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleNamedServiceResult,
    bind_bundle_named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    NamedServiceResponse,
)


def _catalog():
    return {
        "named_services": [{"namespace": "conv"}],
        "conversation_targets": [
            {"bundle_id": "allowed@1-0"},
            {"bundle_id": "ungranted@1-0"},
        ],
    }


@pytest.mark.asyncio
async def test_picker_intersects_configured_targets_with_effective_card():
    owner = SimpleNamespace(
        _named_services_bundle_id=lambda: "own@1-0",
        logger=SimpleNamespace(log=lambda *_args: None),
    )

    async def _hub(call):
        assert call.request["payload"]["client_id"] == "kdcube-agent:own@1-0:main"
        return BundleNamedServiceResult(value=NamedServiceResponse.ok_response(object={
            "granted": True,
            "conversation_targets": ["allowed@1-0", "card-only@1-0"],
        }))

    with bind_bundle_named_service_caller(_hub):
        catalog = await BaseEntrypoint._attach_conversation_targets(owner, _catalog(), "main")
    assert catalog["conversation_targets"] == [
        {"bundle_id": "allowed@1-0"}, {"bundle_id": "own@1-0"},
    ]


@pytest.mark.asyncio
async def test_picker_shows_only_own_target_when_card_probe_fails():
    owner = SimpleNamespace(
        _named_services_bundle_id=lambda: "own@1-0",
        logger=SimpleNamespace(log=lambda *_args: None),
    )

    async def _hub(_call):
        raise RuntimeError("hub offline")

    with bind_bundle_named_service_caller(_hub):
        catalog = await BaseEntrypoint._attach_conversation_targets(owner, _catalog(), "main")
    assert catalog["conversation_targets"] == [{"bundle_id": "own@1-0"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("granted_operation", "expected_probes"),
    [
        ("object.list", ["object.search", "object.list"]),
        ("object.get", ["object.search", "object.list", "object.get"]),
    ],
)
async def test_picker_accepts_targets_from_any_granted_read_operation(
    granted_operation,
    expected_probes,
):
    owner = SimpleNamespace(
        _named_services_bundle_id=lambda: "own@1-0",
        logger=SimpleNamespace(log=lambda *_args: None),
    )
    probes = []

    async def _hub(call):
        operation = call.request["payload"]["operation"]
        probes.append(operation)
        return BundleNamedServiceResult(value=NamedServiceResponse.ok_response(object={
            "granted": operation == granted_operation,
            "conversation_targets": ["allowed@1-0"],
        }))

    with bind_bundle_named_service_caller(_hub):
        catalog = await BaseEntrypoint._attach_conversation_targets(
            owner,
            _catalog(),
            "main",
        )

    assert probes == expected_probes
    assert catalog["conversation_targets"] == [
        {"bundle_id": "allowed@1-0"},
        {"bundle_id": "own@1-0"},
    ]
