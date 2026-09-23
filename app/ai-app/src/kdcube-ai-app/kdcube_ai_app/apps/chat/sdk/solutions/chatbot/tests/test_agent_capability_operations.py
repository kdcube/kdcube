# SPDX-License-Identifier: MIT

from __future__ import annotations

import copy
import json
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
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def log(self, message: Any, level: Any, **_kwargs: Any) -> None:
        self.records.append((str(message), str(level)))


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


class _BrokenPreferenceStore:
    async def ensure_schema(self) -> None:
        raise RuntimeError("preference store unavailable")


class _CapabilityStore:
    def __init__(
        self,
        events: list[str],
        projection: dict[str, Any],
        *,
        write_error: str = "",
    ) -> None:
        self.events = events
        self.base_projection = projection
        self.base_card_revision = 0
        self.projection = projection
        self.write_error = write_error
        self.set_calls: list[dict[str, Any]] = []

    async def get_selection(self, **kwargs: Any):
        if not self.base_card_revision:
            self.base_card_revision = int(kwargs.get("base_card_revision") or 0)
        return {
            "schema_version": 2,
            "base_projection": self.base_projection,
            "base_card_revision": self.base_card_revision,
            "projection": self.projection,
        }

    async def set_projection(self, **kwargs: Any):
        self.events.append("conversation-write")
        self.set_calls.append(dict(kwargs))
        if self.write_error:
            raise RuntimeError(self.write_error)
        self.projection = kwargs["projection"]
        return {
            "schema_version": 2,
            "base_projection": self.base_projection,
            "base_card_revision": self.base_card_revision,
            "projection": self.projection,
        }


def _owner(
    *,
    pg_pool: Any,
    store: Any = None,
    capability_store: _CapabilityStore | None = None,
    logger: _Logger | None = None,
) -> SimpleNamespace:
    async def _catalog(_agent_id: str, *, conversation_id: str = ""):
        return dict(CATALOG)

    return SimpleNamespace(
        pg_pool=pg_pool,
        bundle_props={},
        logger=logger or _Logger(),
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
        _agent_capability_selection_store=(lambda _identity: capability_store),
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
async def test_capability_update_writes_only_the_conversation_projection(monkeypatch) -> None:
    authority = _authority()
    calls: list[dict[str, Any]] = []
    events: list[str] = []
    store = _Store(events)
    capability_store = _CapabilityStore(events, authority)

    async def _sync(_entrypoint: Any, **kwargs: Any):
        calls.append(dict(kwargs))
        return {
            "authority": authority,
            "projection": authority,
            "selection": authority,
            "states": {},
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    owner = _owner(
        pg_pool=object(),
        store=store,
        capability_store=capability_store,
    )

    result = await BaseEntrypoint.agent_selection_update(
        owner,
        data={
            "data": {
                "agent": "main",
                "conversation_id": "conv-a",
                "disabled": {"tools": {"web": True}},
                "apply": "next_conversation",
            }
        },
    )

    assert result["ok"] is True
    assert result["selection"]["disabled"] == {"tools": {"web": True}}
    assert result["selection"]["capability_source"] == "conversation"
    assert len(calls) == 1
    assert "replace_selection" not in calls[0]
    selected = capability_store.set_calls[0]["projection"]["capabilities"]
    assert selected["tool_groups"] == []
    assert selected["tools"] == []
    assert events == ["conversation-write"]


@pytest.mark.asyncio
async def test_conversation_can_select_an_option_missing_from_agent_card_default(
    monkeypatch,
) -> None:
    authority = _authority()
    agent_default = capability_control.selected_capabilities_from_disabled(
        authority=authority,
        catalog=CATALOG,
        disabled={"tools": {"web": True}},
    )
    events: list[str] = []
    capability_store = _CapabilityStore(events, agent_default)

    async def _sync(_entrypoint: Any, **_kwargs: Any):
        return {
            "authority": authority,
            "projection": agent_default,
            "selection": agent_default,
            "states": {},
            "card": {"access_id": "agent-main", "card_revision": 7},
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    result = await BaseEntrypoint.agent_selection_update(
        _owner(
            pg_pool=object(),
            store=_Store(events),
            capability_store=capability_store,
        ),
        data={
            "data": {
                "agent": "main",
                "conversation_id": "conv-a",
                "disabled": {"tools": {"web": False}},
            }
        },
    )

    assert result["ok"] is True
    assert result["selection"]["disabled"] == {}
    assert result["selection"]["conversation_base_disabled"] == {
        "tools": {"web": True}
    }
    assert result["selection"]["agent_base_disabled"] == {
        "tools": {"web": True}
    }
    assert capability_control.disabled_from_projection(
        CATALOG,
        capability_store.set_calls[0]["projection"],
    ) == {}
    assert events == ["conversation-write"]


@pytest.mark.asyncio
async def test_capability_only_update_does_not_depend_on_preference_storage(monkeypatch) -> None:
    authority = _authority()
    events: list[str] = []
    capability_store = _CapabilityStore(events, authority)

    async def _sync(_entrypoint: Any, **_kwargs: Any):
        return {
            "authority": authority,
            "projection": authority,
            "selection": authority,
            "states": {},
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    result = await BaseEntrypoint.agent_selection_update(
        _owner(
            pg_pool=object(),
            store=_BrokenPreferenceStore(),
            capability_store=capability_store,
        ),
        data={
            "data": {
                "agent": "main",
                "conversation_id": "conv-a",
                "disabled": {"tools": {"web": True}},
            }
        },
    )

    assert result["ok"] is True
    assert events == ["conversation-write"]


@pytest.mark.asyncio
async def test_model_preference_is_editable_without_a_conversation(monkeypatch) -> None:
    authority = _authority()
    events: list[str] = []
    store = _Store(events)

    async def _sync(_entrypoint: Any, **_kwargs: Any):
        return {
            "authority": authority,
            "projection": authority,
            "selection": authority,
            "states": {},
            "card": {"access_id": "agent-main", "card_revision": 7},
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    result = await BaseEntrypoint.agent_selection_update(
        _owner(pg_pool=object(), store=store),
        data={
            "data": {
                "agent": "main",
                "model": {"provider": "test", "model": "small"},
            }
        },
    )

    assert result["ok"] is True
    assert result["selection"]["model"] == {
        "provider": "test",
        "model": "small",
    }
    assert result["selection"]["scope"] == {
        "kind": "agent_base",
        "conversation_id": "",
        "capabilities_editable": True,
        "agent_card_revision": 7,
    }
    assert events == ["preference-write"]


@pytest.mark.asyncio
async def test_conversation_scope_names_the_inherited_agent_card_revision(
    monkeypatch,
) -> None:
    authority = _authority()
    capability_store = _CapabilityStore([], authority)

    async def _sync(_entrypoint: Any, **_kwargs: Any):
        return {
            "authority": authority,
            "projection": authority,
            "selection": authority,
            "states": {},
            "card": {"access_id": "agent-main", "card_revision": 7},
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    result = await BaseEntrypoint.agent_capabilities(
        _owner(
            pg_pool=object(),
            store=_Store([]),
            capability_store=capability_store,
        ),
        data={"data": {"agent": "main", "conversation_id": "conv-a"}},
    )

    assert result["ok"] is True
    assert result["selection"]["scope"] == {
        "kind": "conversation",
        "conversation_id": "conv-a",
        "capabilities_editable": True,
        "agent_card_revision": 7,
    }


@pytest.mark.asyncio
async def test_capability_read_surfaces_saved_values_missing_from_control(
    monkeypatch,
) -> None:
    authority = _authority()
    selection = copy.deepcopy(authority)
    selection["capabilities"]["tools"].append("web/removed")

    async def _sync(_entrypoint: Any, **_kwargs: Any):
        return {
            "authority": authority,
            "projection": authority,
            "selection": selection,
            "states": {},
            "card": {"access_id": "agent-main", "card_revision": 7},
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    result = await BaseEntrypoint.agent_capabilities(
        _owner(pg_pool=None),
        data={"data": {"agent": "main", "caller_surface": "capabilities_widget"}},
    )

    assert result["ok"] is True
    assert result["capabilities"]["missing_capabilities"] == [
        {
            "category": "tools",
            "capability": "web/removed",
            "reason": "missing_from_control_card",
        }
    ]


@pytest.mark.asyncio
async def test_capability_read_logs_caller_surface_and_resolved_scope(
    monkeypatch,
) -> None:
    authority = _authority()

    async def _sync(_entrypoint: Any, **_kwargs: Any):
        return {
            "authority": authority,
            "projection": authority,
            "selection": authority,
            "states": {},
            "card": {"access_id": "agent-main", "card_revision": 7},
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    logger = _Logger()
    await BaseEntrypoint.agent_capabilities(
        _owner(pg_pool=None, logger=logger),
        data={
            "data": {
                "agent": "main",
                "caller_surface": "capabilities_widget",
            }
        },
    )
    await BaseEntrypoint.agent_capabilities(
        _owner(
            pg_pool=object(),
            store=_Store([]),
            capability_store=_CapabilityStore([], authority),
            logger=logger,
        ),
        data={
            "data": {
                "agent": "main",
                "caller_surface": "chat_composer",
                "conversation_id": "conv-a",
            }
        },
    )

    scope_events = [
        json.loads(message.split("request_scope ", 1)[1])
        for message, level in logger.records
        if level == "INFO" and "request_scope " in message
    ]
    assert scope_events == [
        {
            "caller_surface": "capabilities_widget",
            "capabilities_editable": True,
            "conversation_id": None,
            "expected_capabilities_editable": True,
            "expected_scope_kind": "agent_base",
            "outcome": "ok",
            "scope_kind": "agent_base",
        },
        {
            "caller_surface": "chat_composer",
            "capabilities_editable": True,
            "conversation_id": "conv-a",
            "expected_capabilities_editable": True,
            "expected_scope_kind": "conversation",
            "outcome": "ok",
            "scope_kind": "conversation",
        },
    ]


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
async def test_conversation_read_fails_closed_without_snapshot_storage(monkeypatch) -> None:
    authority = _authority()

    async def _sync(_entrypoint: Any, **_kwargs: Any):
        return {
            "authority": authority,
            "projection": authority,
            "selection": authority,
            "states": {},
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    result = await BaseEntrypoint.agent_capabilities(
        _owner(pg_pool=None),
        data={"data": {"agent": "main", "conversation_id": "conv-a"}},
    )

    assert result["ok"] is True
    assert result["selection"]["capability_source"] == (
        "conversation_selection_unavailable"
    )
    assert result["selection"]["disabled"] == {"tools": {"web": True}}
    assert result["selection"]["scope"] == {
        "kind": "conversation",
        "conversation_id": "conv-a",
        "capabilities_editable": False,
    }


@pytest.mark.asyncio
async def test_mixed_update_reports_preference_commit_before_conversation_failure(monkeypatch) -> None:
    authority = _authority()
    events: list[str] = []
    store = _Store(events)
    capability_store = _CapabilityStore(
        events,
        authority,
        write_error="conversation write unavailable",
    )

    async def _sync(_entrypoint: Any, **kwargs: Any):
        events.append("card-read")
        return {
            "authority": authority,
            "projection": authority,
            "selection": authority,
            "states": {},
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    owner = _owner(
        pg_pool=object(),
        store=store,
        capability_store=capability_store,
    )

    result = await BaseEntrypoint.agent_selection_update(
        owner,
        data={
            "data": {
                "agent": "main",
                "conversation_id": "conv-a",
                "disabled": {"tools": {"web": True}},
                "model": {"provider": "test", "model": "small"},
                "apply": "when_cold",
            }
        },
    )

    assert result["ok"] is False
    assert result["error"] == "conversation write unavailable"
    assert result["preference_update_applied"] is True
    assert result["capability_update_applied"] is False
    assert events == ["card-read", "preference-write", "conversation-write"]
    assert store.set_calls[0]["patch"] is None
    assert store.set_calls[0]["apply"] == "when_cold"


@pytest.mark.asyncio
async def test_capability_update_without_conversation_replaces_agent_card_defaults(
    monkeypatch,
) -> None:
    authority = _authority()
    unselected = capability_control.selected_capabilities_from_disabled(
        authority=authority,
        catalog=CATALOG,
        disabled={"tools": {"web": True}},
    )
    calls: list[dict[str, Any]] = []

    async def _sync(_entrypoint: Any, **kwargs: Any):
        calls.append(dict(kwargs))
        projection = kwargs.get("selected_capabilities", unselected)
        return {
            "authority": authority,
            "projection": projection,
            "selection": projection,
            "states": {},
            "card": {
                "access_id": "agent-main",
                "card_revision": 8 if kwargs.get("replace_selection") else 7,
            },
        }

    monkeypatch.setattr(capability_control, "sync_agent_capability_projection", _sync)
    result = await BaseEntrypoint.agent_selection_update(
        _owner(pg_pool=None),
        data={
            "data": {
                "agent": "main",
                "caller_surface": "capabilities_widget",
                "disabled": {"tools": {"web": False}},
            }
        },
    )

    assert result["ok"] is True
    assert result["selection"]["disabled"] == {}
    assert result["selection"]["agent_base_disabled"] == {}
    assert result["selection"]["capability_source"] == "connection_hub_card"
    assert result["selection"]["scope"] == {
        "kind": "agent_base",
        "conversation_id": "",
        "capabilities_editable": True,
        "agent_card_revision": 8,
    }
    assert len(calls) == 2
    assert "replace_selection" not in calls[0]
    assert calls[1]["replace_selection"] is True
    assert capability_control.disabled_from_projection(
        CATALOG,
        calls[1]["selected_capabilities"],
    ) == {}
