# SPDX-License-Identifier: MIT

import json
from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.sdk.solutions.react.round as round_mod
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.round import ReactRound
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser


class _FakeComm:
    def __init__(self):
        self.events = []

    async def service_event(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.asyncio
async def test_react_round_emits_redacted_tool_call_event(monkeypatch, tmp_path):
    async def _fake_rg(*, ctx_browser, state, tool_call_id):
        state["last_tool_result"] = []
        return state

    monkeypatch.setattr(round_mod.react_tools, "handle_react_rg", _fake_rg)

    runtime = RuntimeCtx(turn_id="turn_tool", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    comm = _FakeComm()
    react = SimpleNamespace(ctx_browser=ctx, comm=comm)
    state = {
        "pending_tool_origin_iteration": 2,
        "last_decision": {
            "tool_call": {
                "tool_id": "react.rg",
                "tool_call_id": "tc_rg",
                "params": {
                    "query": "sensitive search text",
                    "paths": ["fi:turn_tool.files/report.md", "sk:public.skill"],
                    "items": [{"path": "ks:docs/readme.md"}],
                    "top_k": 5,
                },
            },
        },
    }

    await ReactRound.execute(react, state)

    assert len(comm.events) == 1
    event = comm.events[0]
    assert event["type"] == "react.tool.call"
    assert event["status"] == "completed"
    assert event["data"]["tool_id"] == "react.rg"
    assert event["data"]["tool_call_id"] == "tc_rg"
    assert event["data"]["iteration"] == 2
    assert event["data"]["params"]["redacted"] is True
    assert event["data"]["params"]["query_len"] == len("sensitive search text")
    assert event["data"]["params"]["paths_count"] == 2
    assert event["data"]["params"]["path_prefixes"] == ["fi:", "sk:"]
    assert event["data"]["params"]["item_path_prefixes"] == ["ks:"]
    assert "sensitive search text" not in json.dumps(event, ensure_ascii=False)


@pytest.mark.asyncio
async def test_react_round_tool_call_event_reports_managed_error(monkeypatch, tmp_path):
    async def _fake_pull(*, ctx_browser, state, tool_call_id):
        state["last_tool_result"] = [{"error": {"code": "pull_failed"}}]
        return state

    monkeypatch.setattr(round_mod.react_tools, "handle_react_pull", _fake_pull)

    runtime = RuntimeCtx(turn_id="turn_tool", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    comm = _FakeComm()
    react = SimpleNamespace(ctx_browser=ctx, comm=comm)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "react.pull",
                "tool_call_id": "tc_pull",
                "params": {"paths": ["fi:turn_tool.files/missing.md"]},
            },
        },
    }

    await ReactRound.execute(react, state)

    event = comm.events[0]
    assert event["type"] == "react.tool.call"
    assert event["status"] == "error"
    assert event["data"]["error_count"] == 1
    assert event["data"]["error_code"] == "pull_failed"
