# SPDX-License-Identifier: MIT
"""Automation and execution search answer the same query with or without the index."""
from __future__ import annotations

import sqlite3

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.automations import storage as storage_mod
from kdcube_ai_app.apps.chat.sdk.solutions.automations.executions_storage import AutomationExecutionsStorage
from kdcube_ai_app.apps.chat.sdk.solutions.automations.storage import AutomationStorage

USER = "user-a"


def _seeded(root):
    store = AutomationStorage(root, user_id=USER)
    store.create_automation(
        title="Morning summary report",
        description="Collect overnight metrics before the standup.",
        labels="briefing", conversation_id="conv-brief",
    )
    store.create_automation(
        title="Invoice audit",
        description="Compare billed hours against the ledger.",
        labels="finance", conversation_id="conv-ledger",
    )
    store.create_automation(
        title="Backup digest",
        description="Report every failed snapshot from the night.",
        labels="ops", conversation_id="conv-ops",
    )
    return store


def _break(monkeypatch, cls):
    def _boom(self):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(cls, "ensure_search_index", _boom)


#: Multi-term and out-of-order queries a single-substring scan cannot match,
#: plus a conversation-id query (a column the old fallback did not search).
DISCRIMINATING = ["summary audit", "morning report", "standup metrics", "conv-ledger"]
CONTROLS = ["summary", "summ", "snapshot", "nothing-matches-this"]


@pytest.mark.parametrize("query", DISCRIMINATING + CONTROLS)
def test_the_fallback_answers_the_same_query_as_the_index(tmp_path, monkeypatch, query):
    store = _seeded(tmp_path)
    indexed = sorted(a["title"] for a in store.search_automations(query=query))
    if query in DISCRIMINATING:
        assert indexed, "a discriminating query must match something through the index"
    _break(monkeypatch, AutomationStorage)
    degraded = sorted(a["title"] for a in store.search_automations(query=query))
    assert degraded == indexed


def test_an_empty_query_returns_everything_on_both_paths(tmp_path, monkeypatch):
    store = _seeded(tmp_path)
    assert len(store.search_automations(query="")) == 3
    _break(monkeypatch, AutomationStorage)
    assert len(store.search_automations(query="")) == 3


def test_the_index_holds_exactly_the_columns_the_fallback_searches(tmp_path):
    store = _seeded(tmp_path)
    conn = sqlite3.connect(str(store.ensure_search_index()))
    try:
        rows = conn.execute(
            "SELECT id, title, body, labels, relations, conversation_id FROM automations_fts"
        ).fetchall()
    finally:
        conn.close()
    by_id = {row[0]: tuple(row) for row in rows}
    for path in store.automations_dir.glob("*.md"):
        asset = store._read_asset(path)
        assert storage_mod._fts_columns(asset) == by_id[asset["id"]]


def test_a_broken_index_is_logged_and_has_no_score(tmp_path, monkeypatch, caplog):
    store = _seeded(tmp_path)
    assert "score" in store.search_automations(query="ledger")[0]
    _break(monkeypatch, AutomationStorage)
    with caplog.at_level("WARNING"):
        got = store.search_automations(query="ledger")
    assert [a["title"] for a in got] == ["Invoice audit"]
    assert "score" not in got[0]
    assert "search index unavailable" in caplog.text and "RuntimeError" in caplog.text


# --- executions ---------------------------------------------------------------

def _seeded_executions(root, count=1):
    store = _seeded(root)
    automation = store.list_automations(query="invoice")[0]
    for idx in range(count):
        store.create_execution(
            automation_id=automation["id"],
            status="success",
            summary=f"Ledger reconciled for week {idx}" if idx == 0 else f"Routine pass {idx}",
        )
    return store


@pytest.mark.parametrize("query", ["reconciled ledger", "week ledger", "ledger", "nothing-matches-this"])
def test_the_execution_fallback_answers_the_same_query_as_the_index(tmp_path, monkeypatch, query):
    store = _seeded_executions(tmp_path, count=2)
    indexed = sorted(e["id"] for e in store.search_executions(query=query))
    _break(monkeypatch, AutomationExecutionsStorage)
    degraded = sorted(e["id"] for e in store.search_executions(query=query))
    assert degraded == indexed


def test_the_execution_fallback_matches_before_it_limits(tmp_path, monkeypatch):
    """The oldest record is the only match; the fallback used to look at the newest ``limit`` only."""
    store = _seeded_executions(tmp_path, count=4)
    assert len(store.search_executions(query="reconciled", limit=2)) == 1
    _break(monkeypatch, AutomationExecutionsStorage)
    assert len(store.search_executions(query="reconciled", limit=2)) == 1
