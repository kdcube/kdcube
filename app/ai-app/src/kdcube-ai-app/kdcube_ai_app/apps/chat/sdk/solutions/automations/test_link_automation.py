# SPDX-License-Identifier: MIT
"""link_automation(reciprocal=False) edits the source only."""
from __future__ import annotations

import time

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.automations.storage import AutomationStorage


def _pair(root):
    store = AutomationStorage(root, user_id="user-a")
    source = store.create_automation(title="Daily summary", description="Summarise yesterday.")
    target = store.create_automation(title="Invoice audit", description="Check invoices.")
    # Let a rewrite land on a later mtime tick than the creation did.
    time.sleep(0.05)
    return store, source, target


def _file_state(store, automation_id):
    """Bytes AND mtime: rewriting an unchanged asset yields identical bytes, so only the mtime shows the write."""
    path = store.root / store.get_automation(automation_id)["path"]
    return path.read_bytes(), path.stat().st_mtime_ns


@pytest.mark.parametrize("relation", ["related", "child", "depends_on", "blocks"])
def test_a_one_way_link_leaves_the_target_untouched(tmp_path, relation):
    store, source, target = _pair(tmp_path)
    before = _file_state(store, target["id"])
    updated_at = store.get_automation(target["id"])["updated_at"]

    store.link_automation(
        automation_id=source["id"], target_automation_id=target["id"], relation=relation, reciprocal=False,
    )

    assert _file_state(store, target["id"]) == before
    assert store.get_automation(target["id"])["updated_at"] == updated_at


def test_a_one_way_link_does_not_reorder_the_listing(tmp_path):
    store, source, target = _pair(tmp_path)
    store.create_automation(title="Backup digest", description="Failed snapshots.")
    time.sleep(0.05)

    def others():
        return [a["title"] for a in store.list_automations() if a["id"] != source["id"]]

    before = others()
    store.link_automation(automation_id=source["id"], target_automation_id=target["id"], reciprocal=False)
    assert others() == before


def test_a_reciprocal_link_still_writes_both_sides(tmp_path):
    store, source, target = _pair(tmp_path)
    store.link_automation(automation_id=source["id"], target_automation_id=target["id"], reciprocal=True)
    linked = store.get_automation(target["id"])
    assert linked["relations"]["related_automation_ids"] == [source["id"]]
    assert linked["metadata"]["related_automations"] == [source["id"]]


@pytest.mark.parametrize("relation,inverse_key,expected", [
    ("child", "parent_automation_id", "source"),
    ("depends_on", "blocks_automation_ids", ["source"]),
    ("blocks", "depends_on_automation_ids", ["source"]),
])
def test_reciprocal_inverse_edges(tmp_path, relation, inverse_key, expected):
    store, source, target = _pair(tmp_path)
    store.link_automation(
        automation_id=source["id"], target_automation_id=target["id"], relation=relation, reciprocal=True,
    )
    got = store.get_automation(target["id"])["relations"][inverse_key]
    want = source["id"] if expected == "source" else [source["id"]]
    assert got == want


@pytest.mark.parametrize("reciprocal", [True, False])
def test_the_source_is_written_and_returned_either_way(tmp_path, reciprocal):
    store, source, target = _pair(tmp_path)
    updated = store.link_automation(
        automation_id=source["id"], target_automation_id=target["id"], reciprocal=reciprocal,
    )
    assert updated["id"] == source["id"]
    assert updated["relations"]["related_automation_ids"] == [target["id"]]
    assert store.get_automation(source["id"])["relations"]["related_automation_ids"] == [target["id"]]
