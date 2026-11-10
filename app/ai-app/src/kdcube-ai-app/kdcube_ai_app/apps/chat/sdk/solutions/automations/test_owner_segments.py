# SPDX-License-Identifier: MIT
"""Automation stores are keyed injectively by user id, and survive the upgrade."""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.automations import owner_segments as seg
from kdcube_ai_app.apps.chat.sdk.solutions.automations.async_storage import list_automation_user_ids
from kdcube_ai_app.apps.chat.sdk.solutions.automations.storage import AutomationStorage


COLON_ID = "tenant:user:42"
DASH_ID = "tenant-user-42"


def _dirs(root, subtree="automations"):
    base = root / subtree
    return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []


def _write_legacy_store(root, user_id, *, titles=("Morning brief",), executions=1):
    """A store exactly as the pre-upgrade code laid it out on disk."""
    legacy = seg.legacy_segment(user_id)
    automations = root / "automations" / legacy
    automations.mkdir(parents=True)
    for idx, title in enumerate(titles):
        (automations / f"automation_{idx}.md").write_text(
            f"---\nid: automation_{idx}\ntitle: {title}\nstatus: enabled\nowner_user_id: '{user_id}'\n---\n{title}\n",
            encoding="utf-8",
        )
    for idx in range(executions):
        run = root / "automation_executions" / legacy / "automation_0" / f"exec_{idx}.json"
        run.parent.mkdir(parents=True, exist_ok=True)
        run.write_text(json.dumps({"id": f"exec_{idx}", "automation_id": "automation_0", "owner_user_id": user_id, "status": "success"}), encoding="utf-8")
    index = root / "indexes" / "automations" / legacy
    index.mkdir(parents=True)
    (index / "automations.sqlite").write_bytes(b"stale")
    return legacy


# --- the segment function ----------------------------------------------------

@pytest.mark.parametrize("user_id", [
    COLON_ID, DASH_ID, "anonymous", "u", "a.b", "a_b", "-lead", "trail-", ".hidden", ".", "..",
    "user@example.com", "tenant:user:42 ", "ünïcödé", "a/b", "a~b", "~", "x" * 150,
])
def test_the_segment_round_trips_and_is_a_safe_single_component(user_id):
    name = seg.owner_segment(user_id)
    assert seg.owner_from_segment(name) == user_id
    assert "/" not in name and name not in {".", ".."} and not name.startswith(".")
    assert len(name) <= 255
    assert re.fullmatch(r"[A-Za-z0-9_.~-]+", name)


def test_ids_the_legacy_scheme_collapsed_get_distinct_segments():
    assert seg.legacy_segment(COLON_ID) == seg.legacy_segment(DASH_ID)
    assert seg.owner_segment(COLON_ID) != seg.owner_segment(DASH_ID)


def test_plain_ids_keep_their_legacy_directory_name():
    for user_id in (DASH_ID, "anonymous", "user_1", "a.b"):
        assert seg.owner_segment(user_id) == seg.legacy_segment(user_id) == user_id


def test_a_crafted_plain_id_cannot_name_an_encoded_segment():
    """Encoded names contain '~', which no plain id can, so no id other than the owner maps to them."""
    encoded = seg.owner_segment(COLON_ID)
    assert "~" in encoded
    assert seg.owner_segment(encoded) != encoded


def test_names_no_id_maps_to_are_rejected():
    assert seg.owner_from_segment("") is None
    assert seg.owner_from_segment("x~not!base32") is None
    assert seg.owner_from_segment("other-prefix~" + seg.owner_segment(COLON_ID).rsplit("~", 1)[1]) is None
    assert seg.owner_from_segment(".hidden") is None


# --- isolation ---------------------------------------------------------------

def test_two_colliding_ids_do_not_share_a_store(tmp_path):
    colon = AutomationStorage(tmp_path, user_id=COLON_ID)
    dash = AutomationStorage(tmp_path, user_id=DASH_ID)
    created = colon.create_automation(title="Private nightly report")
    dash.create_automation(title="Someone else")

    assert colon.automations_dir != dash.automations_dir
    assert colon.executions.executions_dir != dash.executions.executions_dir
    assert [a["id"] for a in dash.list_automations()] != [created["id"]]
    assert created["id"] not in {a["id"] for a in dash.list_automations()}
    assert dash.get_automation(created["id"]) is None
    assert dash.search_automations(query="nightly") == []


def test_a_dot_user_id_does_not_resolve_to_a_parent_directory(tmp_path):
    store = AutomationStorage(tmp_path, user_id="..")
    assert store.automations_dir.resolve().parent == (tmp_path / "automations").resolve()
    assert store.executions.executions_dir.resolve().parent == (tmp_path / "automation_executions").resolve()


def test_a_dot_automation_id_stays_inside_the_users_execution_dir(tmp_path):
    store = AutomationStorage(tmp_path, user_id="user-a")
    assert store.executions._execution_dir("..").resolve().parent == store.executions.executions_dir.resolve()


# --- the scheduler scan ------------------------------------------------------

def test_the_scan_returns_owner_ids_and_reopening_creates_nothing(tmp_path):
    AutomationStorage(tmp_path, user_id=COLON_ID).create_automation(title="Daily", schedule_cron="0 8 * * *")
    AutomationStorage(tmp_path, user_id=DASH_ID).create_automation(title="Weekly", schedule_cron="0 8 * * 1")
    before = (_dirs(tmp_path), _dirs(tmp_path, "automation_executions"))

    for _tick in range(3):
        owners = asyncio.run(list_automation_user_ids(tmp_path))
        assert sorted(owners) == sorted([COLON_ID, DASH_ID])
        for owner in owners:
            assert len(AutomationStorage(tmp_path, user_id=owner).list_automations()) == 1

    assert (_dirs(tmp_path), _dirs(tmp_path, "automation_executions")) == before


# --- the upgrade -------------------------------------------------------------

def test_a_legacy_store_is_moved_to_its_owner_on_first_open(tmp_path):
    legacy = _write_legacy_store(tmp_path, COLON_ID)

    store = AutomationStorage(tmp_path, user_id=COLON_ID)

    assert [a["title"] for a in store.list_automations()] == ["Morning brief"]
    assert [e["id"] for e in store.list_executions()] == ["exec_0"]
    assert legacy not in _dirs(tmp_path)
    assert legacy not in _dirs(tmp_path, "automation_executions")
    assert not (tmp_path / "indexes" / "automations" / legacy).exists()
    # the colliding plain id that owns the legacy NAME now sees an empty store
    assert AutomationStorage(tmp_path, user_id=DASH_ID).list_automations() == []


def test_the_scan_finds_a_legacy_store_under_its_owner(tmp_path):
    _write_legacy_store(tmp_path, COLON_ID)

    owners = asyncio.run(list_automation_user_ids(tmp_path))

    assert owners == [COLON_ID]
    assert [a["title"] for a in AutomationStorage(tmp_path, user_id=COLON_ID).list_automations()] == ["Morning brief"]
    assert asyncio.run(list_automation_user_ids(tmp_path)) == [COLON_ID]


def test_a_plain_ids_store_does_not_move(tmp_path):
    legacy = _write_legacy_store(tmp_path, DASH_ID)
    store = AutomationStorage(tmp_path, user_id=DASH_ID)
    assert store.automations_dir.name == legacy
    assert [a["title"] for a in store.list_automations()] == ["Morning brief"]
    # the colliding id does not take it
    other = AutomationStorage(tmp_path, user_id=COLON_ID)
    assert other.list_automations() == []
    assert legacy in _dirs(tmp_path)


def test_a_legacy_store_shared_by_colliding_ids_is_left_in_place(tmp_path, caplog):
    legacy = _write_legacy_store(tmp_path, DASH_ID)
    (tmp_path / "automations" / legacy / "automation_9.md").write_text(
        f"---\nid: automation_9\ntitle: Theirs\nstatus: enabled\nowner_user_id: '{COLON_ID}'\n---\nx\n",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        colon = AutomationStorage(tmp_path, user_id=COLON_ID)
    assert colon.list_automations() == []
    assert legacy in _dirs(tmp_path)
    assert "several owners" in caplog.text
    assert sorted(asyncio.run(list_automation_user_ids(tmp_path))) == sorted([DASH_ID, COLON_ID])
