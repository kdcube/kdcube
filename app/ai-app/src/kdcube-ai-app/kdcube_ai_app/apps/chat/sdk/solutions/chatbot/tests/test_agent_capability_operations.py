# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control as capability_control
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control import (
    descriptor_capability_payload,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


CATALOG = {
    "agent": "main",
    "tools": [
        {
            "alias": "web",
            "name": "Web",
            "system": False,
            "tools": [{"name": "search", "description": "Search."}],
        }
    ],
    "mcp": [],
    "named_services": [],
    "resources": [],
    "skills": [],
    "conversation_targets": [],
    "delegated_resource_families": [],
    "subagents": None,
}


class _Logger:
    def log(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _Store:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.set_calls: list[dict[str, Any]] = []

    async def ensure_schema(self) -> None:
        return None

    async def get_legacy_capability_seed(self, **_kwargs: Any):
        return None

    async def get_selection(self, **_kwargs: Any):
        return {"schema_version": 1, "disabled": {}, "model": None}

    async def set_selection(self, **kwargs: Any):
        self.events.append("preference-write")
        self.set_calls.append(dict(kwargs))
        return {
            "schema_version": 1,
            "disabled": {},
            "model": kwargs.get("model"),
        }


def _owner(*, pg_pool: Any, store: _Store | None = None) -> SimpleNamespace:
    async def _catalog(_agent_id: str, *, conversation_id: str = ""):
        return dict(CATALOG)

    return SimpleNamespace(
        pg_pool=pg_pool,
        bundle_props={},
        logger=_Logger(),
        _agent_selection_payload=lambda data, kwargs: BaseEntrypoint._agent_selection_payload(
            data, kwargs
        ),
        _agent_selection_agent_id=lambda payload: "main",
        _agent_selection_identity=lambda: {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-a",
            "bundle_id": "workspace@1-0",
        },
        _agent_capabilities_catalog_enriched=_catalog,
        _agent_selection_store=(lambda _identity: store),
    )


def _authority() -> dict[str, Any]:
    return descriptor_capability_payload(
        bundle_props={},
        catalog=CATALOG,
        tenant="tenant-a",
        project="project-a",
        application="workspace@1-0",
        agent_id="main",
    )["capability_authority"]


@pytest.mark.asyncio
async def test_capability_only_update_does_not_require_postgres(monkeypatch) -> None:
    authority = _authority()
    calls: list[dict[str, Any]] = []

    async def _sync(_entrypoint: Any, **kwargs: Any):
        calls.append(dict(kwargs))
        projection = kwargs.get("selected_capabilities") or authority
        return {
            "authority": authority,
            "projection": projection,
            "selection": projection,
            "states": {},
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    owner = _owner(pg_pool=None)

    result = await BaseEntrypoint.agent_selection_update(
        owner,
        data={
            "data": {
                "agent": "main",
                "disabled": {"tools": {"web": True}},
                "apply": "next_conversation",
            }
        },
    )

    assert result["ok"] is True
    assert result["selection"]["disabled"] == {"tools": {"web": True}}
    assert len(calls) == 2
    assert calls[1]["replace_selection"] is True
    selected = calls[1]["selected_capabilities"]["capabilities"]
    assert selected["tool_groups"] == []
    assert selected["tools"] == []


@pytest.mark.asyncio
async def test_capability_read_marks_rows_not_permitted_when_card_is_unavailable(
    monkeypatch,
) -> None:
    async def _sync(_entrypoint: Any, **_kwargs: Any):
        raise RuntimeError("card unavailable")

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    owner = _owner(pg_pool=None)

    result = await BaseEntrypoint.agent_capabilities(
        owner,
        data={"data": {"agent": "main"}},
    )

    assert result["ok"] is True
    assert result["selection"]["capability_source"] == "unavailable"
    assert result["selection"]["disabled"] == {"tools": {"web": True}}
    assert result["capabilities"]["tools"][0]["authority_state"] == (
        "not_allowed"
    )
    assert result["capabilities"]["tools"][0]["tools"][0][
        "authority_state"
    ] == "not_allowed"


@pytest.mark.asyncio
async def test_mixed_update_reports_preference_commit_before_card_failure(monkeypatch) -> None:
    authority = _authority()
    events: list[str] = []
    store = _Store(events)
    sync_count = 0

    async def _sync(_entrypoint: Any, **kwargs: Any):
        nonlocal sync_count
        sync_count += 1
        if sync_count == 1:
            events.append("card-read")
            return {
                "authority": authority,
                "projection": authority,
                "selection": authority,
                "states": {},
            }
        events.append("card-write")
        raise RuntimeError("card write unavailable")

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    owner = _owner(pg_pool=object(), store=store)

    result = await BaseEntrypoint.agent_selection_update(
        owner,
        data={
            "data": {
                "agent": "main",
                "disabled": {"tools": {"web": True}},
                "model": {"provider": "test", "model": "small"},
                "apply": "when_cold",
            }
        },
    )

    assert result["ok"] is False
    assert result["error"] == "card write unavailable"
    assert result["preference_update_applied"] is True
    assert events == ["card-read", "preference-write", "card-write"]
    assert store.set_calls[0]["patch"] is None
    assert store.set_calls[0]["apply"] == "when_cold"
