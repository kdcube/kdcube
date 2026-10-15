"""A nested peer operation call logs where its time goes (W389).

The calling app waits on the whole nested call: config secrets, bundle spec, the
target entrypoint instance, request preparation and the handler itself. One
line names each phase in milliseconds, plus the target app id, its operation
alias and a fixed outcome. A direct (non-peer) call logs nothing.
"""

from __future__ import annotations

import asyncio
import logging
import re

import pytest
from fastapi import HTTPException

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.apps.chat.tests.test_integrations_operations_auth import (
    _Workflow,
    _dispatch,
    _patch_bundle_runtime,
    _session,
)

LINE = re.compile(
    r"^\[call_bundle_op\.peer\] bundle=(?P<bundle>\S*) operation=(?P<operation>\S+) "
    r"secrets_ms=(?P<secrets>-?[0-9.]+) spec_ms=(?P<spec>-?[0-9.]+) instance_ms=(?P<instance>-?[0-9.]+) "
    r"prep_ms=(?P<prep>-?[0-9.]+) handler_ms=(?P<handler>-?[0-9.]+) total_ms=(?P<total>[0-9.]+) "
    r"outcome=(?P<outcome>[a-z0-9_]+)$"
)


def _peer_lines(caplog) -> list[re.Match]:
    return [m for m in (LINE.match(r.getMessage()) for r in caplog.records) if m]


@pytest.mark.asyncio
async def test_a_peer_call_logs_each_phase_and_the_slow_one_shows(monkeypatch, caplog) -> None:
    _patch_bundle_runtime(monkeypatch)
    original = integrations.get_workflow_instance_async

    async def slow_instance(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await original(*args, **kwargs)

    monkeypatch.setattr(integrations, "get_workflow_instance_async", slow_instance)
    with caplog.at_level(logging.INFO, logger="ChatProc.Integrations"):
        result = await _dispatch("canvas_read", _session(["kdcube:role:member"]), internal_peer_call=True)

    assert result["canvas_read"] == {"ok": "canvas_read"}
    lines = _peer_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert line["operation"] == "canvas_read" and line["outcome"] == "ok"
    phases = {name: float(line[name]) for name in ("secrets", "spec", "instance", "prep", "handler")}
    assert all(value >= 0 for value in phases.values())
    assert phases["instance"] >= 45.0
    assert max(phases, key=phases.get) == "instance"
    assert float(line["total"]) >= sum(phases.values()) - 0.5


@pytest.mark.asyncio
async def test_a_direct_call_logs_no_peer_line(monkeypatch, caplog) -> None:
    _patch_bundle_runtime(monkeypatch)
    with caplog.at_level(logging.INFO, logger="ChatProc.Integrations"):
        await _dispatch("canvas_read", _session(["kdcube:role:member"]))
    assert _peer_lines(caplog) == []


@pytest.mark.asyncio
async def test_a_refused_peer_call_names_its_status_and_the_phases_not_reached(monkeypatch, caplog) -> None:
    _patch_bundle_runtime(monkeypatch)
    with caplog.at_level(logging.INFO, logger="ChatProc.Integrations"):
        with pytest.raises(HTTPException):
            await _dispatch("telegram_user_admin_data", _session(["kdcube:role:member"]), internal_peer_call=True)
    (line,) = _peer_lines(caplog)
    assert line["outcome"] == "http_403"
    assert float(line["instance"]) >= 0
    assert line["prep"] == "-1.0" and line["handler"] == "-1.0"
    assert "user-1" not in line.string and "fp-1" not in line.string


def test_the_workflow_fixture_is_the_shared_one() -> None:
    assert _Workflow.__name__ == "_Workflow"
