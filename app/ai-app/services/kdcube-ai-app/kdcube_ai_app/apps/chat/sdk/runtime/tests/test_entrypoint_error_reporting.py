# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


class _CaptureLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def log(self, msg, level="INFO"):
        self.messages.append((str(level), str(msg)))


class _CaptureComm:
    def __init__(self) -> None:
        self.error_calls: list[dict] = []
        self.step_calls: list[dict] = []

    async def error(self, **kwargs):
        self.error_calls.append(kwargs)

    async def step(self, **kwargs):
        self.step_calls.append(kwargs)


@pytest.mark.asyncio
async def test_report_turn_error_emits_user_visible_error_and_step():
    entrypoint = object.__new__(BaseEntrypoint)
    entrypoint.logger = _CaptureLogger()
    entrypoint._comm = _CaptureComm()
    entrypoint.comm_context = None

    state = {}

    try:
        raise RuntimeError("boom")
    except Exception as exc:
        await BaseEntrypoint.report_turn_error(
            entrypoint,
            state=state,
            exc=exc,
            title="Turn Error",
        )

    assert state["error_message"] == "boom"
    assert state["final_answer"] == "An error occurred."

    assert entrypoint._comm.error_calls == [
        {
            "message": "boom",
            "data": {
                "error": "boom",
                "error_message": "boom",
                "error_type": "RuntimeError",
            },
            "agent": "turn.error",
            "step": "turn",
            "title": "Turn Error",
        }
    ]
    assert entrypoint._comm.step_calls == [
        {
            "step": "turn",
            "status": "error",
            "title": "Turn Error",
            "data": {
                "error": "boom",
                "error_message": "boom",
                "error_type": "RuntimeError",
            },
            "markdown": "**Error:** boom",
        }
    ]
    assert any(
        "RuntimeError: boom" in message
        for _level, message in entrypoint.logger.messages
    )


@pytest.mark.asyncio
async def test_report_turn_error_reraises_economics_limit_exception():
    entrypoint = object.__new__(BaseEntrypoint)
    entrypoint.logger = _CaptureLogger()
    entrypoint._comm = _CaptureComm()
    entrypoint.comm_context = None

    state = {}

    with pytest.raises(EconomicsLimitException):
        await BaseEntrypoint.report_turn_error(
            entrypoint,
            state=state,
            exc=EconomicsLimitException("rate-limited", code="rate_limited"),
            title="Turn Error",
        )

    assert state == {}
    assert entrypoint._comm.error_calls == []
    assert entrypoint._comm.step_calls == []
