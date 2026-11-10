# SPDX-License-Identifier: MIT
"""``completed_after_iso`` filters or refuses; it never silently means "no filter"."""
from __future__ import annotations

import asyncio

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.automations import tools as tools_mod
from kdcube_ai_app.apps.chat.sdk.solutions.automations.storage import AutomationStorage

OLD = {"id": "old", "finished_at": "2026-01-01T00:00:00Z"}
NEW = {"id": "new", "finished_at": "2026-03-01T00:00:00Z"}


@pytest.mark.parametrize("value", ["yesterday", "last week", "2026-13-45", "not a date"])
def test_an_unreadable_timestamp_is_refused(value):
    with pytest.raises(ValueError, match="completed_after_iso"):
        tools_mod._filter_executions_after([OLD, NEW], value)


@pytest.mark.parametrize("value", ["", "   ", None])
def test_an_empty_value_means_no_filter(value):
    assert [e["id"] for e in tools_mod._filter_executions_after([OLD, NEW], value)] == ["old", "new"]


@pytest.mark.parametrize("value", ["2026-02-01T00:00:00Z", "2026-02-01T00:00:00+00:00", "2026-02-01"])
def test_a_readable_timestamp_filters(value):
    assert [e["id"] for e in tools_mod._filter_executions_after([OLD, NEW], value)] == ["new"]


def test_a_stored_record_with_an_unreadable_timestamp_is_skipped_not_raised():
    broken = {"id": "broken", "finished_at": "garbage"}
    got = tools_mod._filter_executions_after([OLD, broken, NEW], "2026-02-01T00:00:00Z")
    assert [e["id"] for e in got] == ["new"]


def test_the_tool_returns_a_dedicated_error_code(tmp_path, monkeypatch):
    store = AutomationStorage(tmp_path, user_id="user-a")
    automation = store.create_automation(title="Weekly report")
    store.create_execution(automation_id=automation["id"], status="success", summary="Report delivered")
    sc = {"storage_root": str(tmp_path), "user_id": "user-a"}
    monkeypatch.setattr(tools_mod, "_scope_for_tool", lambda *_a, **_k: sc)

    refused = asyncio.run(tools_mod.AutomationTools().search_recent_outputs(completed_after_iso="yesterday"))
    assert refused["ok"] is False
    assert refused["error"]["code"] == "invalid_completed_after_iso"

    unfiltered = asyncio.run(tools_mod.AutomationTools().search_recent_outputs(completed_after_iso=""))
    assert unfiltered["ok"] is True and unfiltered["ret"]["count"] == 1
