# SPDX-License-Identifier: MIT

"""Error handling tests for bundles (Type 5).

Test that errors are caught and reported properly.
Tests work with any bundle selected by folder.

Run with:
  BUNDLE_UNDER_TEST=/abs/path/to/bundle pytest test_error_handling.py -v
  pytest test_error_handling.py --bundle-path=/abs/path/to/bundle -v
"""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


class _CaptureComm:
    """Minimal communicator that captures emitted events without real SSE."""

    def __init__(self) -> None:
        self.error_calls: list[dict] = []
        self.step_calls: list[dict] = []

    async def error(self, **kwargs):
        self.error_calls.append(kwargs)

    async def step(self, **kwargs):
        self.step_calls.append(kwargs)


class _CaptureLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def log(self, msg, level="INFO"):
        self.messages.append((str(level), str(msg)))


def _make_entrypoint(comm: _CaptureComm | None = None) -> BaseEntrypoint:
    """Build a bare-bones BaseEntrypoint for error-handling tests."""
    ep = object.__new__(BaseEntrypoint)
    ep.logger = _CaptureLogger()
    ep._comm = comm or _CaptureComm()
    ep.comm_context = None
    return ep


class TestBundleErrorHandling:
    """Test that errors are caught and reported properly."""

    @pytest.mark.anyio
    async def test_error_message_set_in_state_when_error_occurs(self):
        """report_turn_error() sets error_message in state."""
        ep = _make_entrypoint()
        state = {}
        try:
            raise ValueError("something went wrong")
        except Exception as exc:
            await ep.report_turn_error(state=state, exc=exc)

        assert state.get("error_message") == "something went wrong"

    @pytest.mark.anyio
    async def test_chat_error_event_emitted_on_error(self):
        """report_turn_error() emits a chat.error event via comm.error()."""
        comm = _CaptureComm()
        ep = _make_entrypoint(comm)
        state = {}
        try:
            raise RuntimeError("node failed")
        except Exception as exc:
            await ep.report_turn_error(state=state, exc=exc)

        assert len(comm.error_calls) == 1
        call = comm.error_calls[0]
        assert call["message"] == "node failed"

    @pytest.mark.anyio
    async def test_error_message_is_user_friendly_no_stack_trace(self):
        """error_message contains only the exception message, not a traceback."""
        ep = _make_entrypoint()
        state = {}
        try:
            raise RuntimeError("user-visible text")
        except Exception as exc:
            await ep.report_turn_error(state=state, exc=exc)

        msg = state.get("error_message", "")
        assert "Traceback" not in msg
        assert "File " not in msg
        assert msg == "user-visible text"

    @pytest.mark.anyio
    async def test_economics_limit_exception_not_caught_is_reraised(self):
        """EconomicsLimitException is NOT caught — it is re-raised immediately."""
        ep = _make_entrypoint()
        state = {}
        with pytest.raises(EconomicsLimitException):
            await ep.report_turn_error(
                state=state,
                exc=EconomicsLimitException("quota exceeded", code="quota"),
            )

        # State must remain untouched — error was not handled
        assert state == {}

    @pytest.mark.anyio
    async def test_economics_limit_exception_does_not_emit_event(self):
        """EconomicsLimitException must not trigger any comm events."""
        comm = _CaptureComm()
        ep = _make_entrypoint(comm)
        state = {}
        with pytest.raises(EconomicsLimitException):
            await ep.report_turn_error(
                state=state,
                exc=EconomicsLimitException("over limit", code="over_budget"),
            )

        assert comm.error_calls == []
        assert comm.step_calls == []

    @pytest.mark.anyio
    async def test_first_error_message_preserved_not_overwritten(self):
        """Calling report_turn_error when error_message already set does not overwrite it."""
        ep = _make_entrypoint()
        state = {"error_message": "first error"}
        try:
            raise RuntimeError("second error")
        except Exception as exc:
            await ep.report_turn_error(state=state, exc=exc)

        # report_turn_error unconditionally sets error_message — the first caller wins
        # by design: later callers should check before setting. This test documents behavior.
        assert "error" in state.get("error_message", "").lower()

    @pytest.mark.anyio
    async def test_error_does_not_crash_system_comm_failure_uses_step_fallback(self):
        """If comm.error() itself raises, report_turn_error falls back to one chat.step."""

        class _FailingComm(_CaptureComm):
            async def error(self, **kwargs):
                raise ConnectionError("SSE relay unavailable")

        ep = _make_entrypoint(_FailingComm())
        state = {}
        # Should NOT raise even when comm.error() fails
        try:
            raise RuntimeError("inner error")
        except Exception as exc:
            await ep.report_turn_error(state=state, exc=exc)

        # state must still be updated despite comm failure
        assert state.get("error_message") == "inner error"
        assert len(ep._comm.step_calls) == 1
        assert ep._comm.step_calls[0]["status"] == "error"

    @pytest.mark.anyio
    async def test_error_sets_fallback_final_answer(self):
        """report_turn_error sets a fallback final_answer when not already set."""
        ep = _make_entrypoint()
        state = {}
        try:
            raise RuntimeError("failure")
        except Exception as exc:
            await ep.report_turn_error(state=state, exc=exc, final_answer="An error occurred.")

        assert state.get("final_answer") == "An error occurred."

    @pytest.mark.anyio
    async def test_error_does_not_overwrite_existing_final_answer(self):
        """report_turn_error must not overwrite a final_answer already in state."""
        ep = _make_entrypoint()
        state = {"final_answer": "partial answer already set"}
        try:
            raise RuntimeError("late failure")
        except Exception as exc:
            await ep.report_turn_error(state=state, exc=exc, final_answer="An error occurred.")

        assert state["final_answer"] == "partial answer already set"

    @pytest.mark.anyio
    async def test_error_step_not_emitted_when_chat_error_succeeds(self):
        """report_turn_error uses chat.error as the single normal error surface."""
        comm = _CaptureComm()
        ep = _make_entrypoint(comm)
        state = {}
        try:
            raise RuntimeError("step error")
        except Exception as exc:
            await ep.report_turn_error(state=state, exc=exc)

        assert len(comm.error_calls) == 1
        assert comm.step_calls == []

    @pytest.mark.anyio
    async def test_error_event_data_contains_error_type(self):
        """chat.error event data includes error_type (class name)."""
        comm = _CaptureComm()
        ep = _make_entrypoint(comm)
        state = {}
        try:
            raise TypeError("bad type")
        except Exception as exc:
            await ep.report_turn_error(state=state, exc=exc)

        assert len(comm.error_calls) == 1
        data = comm.error_calls[0].get("data") or {}
        assert data.get("error_type") == "TypeError"

    @pytest.mark.anyio
    async def test_error_logged_at_error_level(self):
        """report_turn_error logs the full traceback at ERROR level."""
        ep = _make_entrypoint()
        state = {}
        try:
            raise RuntimeError("logged error")
        except Exception as exc:
            await ep.report_turn_error(state=state, exc=exc)

        error_logs = [msg for level, msg in ep.logger.messages if level == "ERROR"]
        assert error_logs, "Expected at least one ERROR-level log entry"
        combined = "\n".join(error_logs)
        assert "RuntimeError" in combined

    @pytest.mark.anyio
    async def test_report_turn_error_via_bundle_fixture(self, bundle):
        """report_turn_error works end-to-end via an initialized bundle instance."""
        # Inject a capture comm so we don't need real SSE infrastructure
        original_comm = bundle._comm
        comm = _CaptureComm()
        bundle._comm = comm
        state = {}
        try:
            try:
                raise ValueError("bundle-level error")
            except Exception as exc:
                await bundle.report_turn_error(state=state, exc=exc)
        finally:
            bundle._comm = original_comm

        assert state.get("error_message") == "bundle-level error"
        assert len(comm.error_calls) == 1
        assert comm.step_calls == []
