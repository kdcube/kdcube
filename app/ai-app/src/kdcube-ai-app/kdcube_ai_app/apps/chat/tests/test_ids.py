# SPDX-License-Identifier: MIT

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from kdcube_ai_app.apps.chat.ids import new_exec_id, new_turn_id, timestamped_id


def test_timestamped_id_uses_utc_timestamp_and_short_suffix():
    berlin_time = datetime(2026, 5, 6, 14, 52, 43, tzinfo=ZoneInfo("Europe/Berlin"))

    value = timestamped_id("turn", now=berlin_time)

    assert re.fullmatch(r"turn_20260506125243_[0-9a-f]{4}", value)


def test_runtime_id_helpers_use_expected_prefixes():
    now = datetime(2026, 5, 6, 12, 52, 43, tzinfo=timezone.utc)

    turn_id = new_turn_id(now=now)
    assert turn_id == "turn_2026-05-06-12-52-43-000"
    assert not turn_id.startswith("turn_turn_")
    assert re.fullmatch(r"exec_20260506125243_[0-9a-f]{4}", new_exec_id(now=now))
