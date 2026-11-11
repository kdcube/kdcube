# SPDX-License-Identifier: MIT
"""create_automation describes work to run, and routes defect reports elsewhere."""
from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.automations.tools import AutomationTools


def _description() -> str:
    return AutomationTools.create_automation.__kernel_function_description__


def test_the_description_does_not_offer_to_track_work():
    text = _description().lower()
    assert "track" not in text
    assert "actionable work" not in text


def test_the_description_routes_defect_reports_away_and_asks_when_ambiguous():
    text = _description().lower()
    assert "file this as a bug" in text and "save that as a task to fix" in text
    assert "defect report" in text
    assert "ask which one is meant" in text


def test_the_description_still_names_scheduled_and_on_demand_work():
    text = _description().lower()
    assert "on a schedule" in text and "on demand" in text
