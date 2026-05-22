# SPDX-License-Identifier: MIT

"""BundleState tests for bundles (Type 4).

Test that request/response state is handled correctly.
Tests work with any bundle selected by folder.

Run with:
  BUNDLE_UNDER_TEST=/abs/path/to/bundle pytest test_bundle_state.py -v
  pytest test_bundle_state.py --bundle-path=/abs/path/to/bundle -v
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from kdcube_ai_app.infra.service_hub.inventory import Config
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


class TestBundleState:
    """Test that request/response state is handled correctly."""

    def test_create_initial_state_preserves_required_fields(self):
        """create_initial_state preserves request_id, tenant, project, user."""
        payload = {
            "request_id": "req-123",
            "tenant": "acme",
            "project": "main",
            "user": "user-456",
            "user_type": "registered",
            "session_id": "sess-789",
            "conversation_id": "conv-abc",
            "text": "hello world",
        }
        state = BaseEntrypoint.create_initial_state(payload)

        assert state["request_id"] == "req-123"
        assert state["tenant"] == "acme"
        assert state["project"] == "main"
        assert state["user"] == "user-456"
        assert state["user_type"] == "registered"
        assert state["session_id"] == "sess-789"
        assert state["conversation_id"] == "conv-abc"
        assert state["text"] == "hello world"

    def test_create_initial_state_generates_request_id_if_missing(self):
        """create_initial_state generates request_id when not provided."""
        payload = {"tenant": "t", "project": "p", "user": "u"}
        state = BaseEntrypoint.create_initial_state(payload)
        assert state["request_id"]
        assert isinstance(state["request_id"], str)
        assert len(state["request_id"]) > 0

    def test_create_initial_state_initializes_step_logs(self):
        """create_initial_state initializes step_logs to empty list."""
        state = BaseEntrypoint.create_initial_state({})
        assert "step_logs" in state
        assert state["step_logs"] == []

    def test_create_initial_state_initializes_start_time(self):
        """create_initial_state records a start_time timestamp."""
        import time
        before = time.time()
        state = BaseEntrypoint.create_initial_state({})
        after = time.time()
        assert "start_time" in state
        assert before <= state["start_time"] <= after

    def test_create_initial_state_handles_attachments_from_payload_key(self):
        """Attachments read from top-level 'attachments' key."""
        attachments = [{"type": "image", "url": "https://example.com/img.png"}]
        payload = {"attachments": attachments}
        state = BaseEntrypoint.create_initial_state(payload)
        assert state["attachments"] == attachments

    def test_create_initial_state_handles_attachments_from_nested_payload(self):
        """Attachments read from nested payload.attachments when top-level missing."""
        attachments = [{"type": "file", "name": "doc.pdf"}]
        payload = {"payload": {"attachments": attachments}}
        state = BaseEntrypoint.create_initial_state(payload)
        assert state["attachments"] == attachments

    def test_create_initial_state_defaults_attachments_to_empty_list(self):
        """Attachments default to empty list when not provided."""
        state = BaseEntrypoint.create_initial_state({})
        assert state["attachments"] == []

    def test_create_initial_state_strips_whitespace_from_text(self):
        """Text is stripped of leading/trailing whitespace."""
        payload = {"text": "  hello  "}
        state = BaseEntrypoint.create_initial_state(payload)
        assert state["text"] == "hello"

    def test_create_initial_state_text_defaults_to_empty_string(self):
        """Text defaults to empty string when not provided."""
        state = BaseEntrypoint.create_initial_state({})
        assert state["text"] == ""

    def test_bundle_state_final_answer_field_exists_in_type(self):
        """BundleState TypedDict contains final_answer field."""
        from kdcube_ai_app.infra.service_hub.inventory import BundleState
        annotations = BundleState.__annotations__
        assert "final_answer" in annotations

    def test_bundle_state_followups_field_exists_in_type(self):
        """BundleState TypedDict contains followups field."""
        from kdcube_ai_app.infra.service_hub.inventory import BundleState
        annotations = BundleState.__annotations__
        assert "followups" in annotations

    def test_bundle_state_error_message_field_exists_in_type(self):
        """BundleState TypedDict contains error_message field."""
        from kdcube_ai_app.infra.service_hub.inventory import BundleState
        annotations = BundleState.__annotations__
        assert "error_message" in annotations

    def test_bundle_state_attachments_field_exists_in_type(self):
        """BundleState TypedDict contains attachments field."""
        from kdcube_ai_app.infra.service_hub.inventory import BundleState
        annotations = BundleState.__annotations__
        assert "attachments" in annotations

    def test_bundle_state_turn_delivery_fields_exist_in_type(self):
        """BundleState preserves turn-level payloads used by external delivery adapters."""
        from kdcube_ai_app.infra.service_hub.inventory import BundleState
        annotations = BundleState.__annotations__
        assert "turn_log" in annotations
        assert "timeline" in annotations

    def test_state_does_not_leak_between_bundle_instances(self, bundle_dir):
        """Two separate bundle instances do not share state."""
        try:
            from kdcube_ai_app.infra.plugin.bundle_loader import (
                BundleSpec,
                _resolve_module,
                _discover_decorated,
            )
            spec = BundleSpec(path=str(bundle_dir), module="entrypoint")
            mod = _resolve_module(spec)
            chosen = _discover_decorated(mod)
            if chosen is None or chosen[0] != "class":
                pytest.skip("No class-based bundle found")

            bundle_cls = chosen[2]
            instance_a = bundle_cls(config=Config(), redis=MagicMock(), comm_context=MagicMock())
            instance_b = bundle_cls(config=Config(), redis=MagicMock(), comm_context=MagicMock())

            # Mutate A's bundle_props — B must be unaffected
            instance_a.bundle_props["_test_marker"] = "instance-a"
            assert instance_b.bundle_props.get("_test_marker") is None, (
                "Mutating instance A's bundle_props must not affect instance B"
            )
        except ImportError as e:
            pytest.skip(f"Cannot import bundle infrastructure: {e}")

    def test_project_app_state_includes_app_state_keys(self, bundle):
        """project_app_state() outputs all APP_STATE_KEYS."""
        from kdcube_ai_app.infra.service_hub.inventory import APP_STATE_KEYS

        full_state = {
            "request_id": "req-1",
            "tenant": "t",
            "project": "p",
            "user": "u",
            "session_id": "s",
            "text": "hi",
            "attachments": [],
            "final_answer": "The answer",
            "followups": ["follow up?"],
            "error_message": None,
            "step_logs": [],
        }
        out = type(bundle).project_app_state(full_state)

        for key in APP_STATE_KEYS:
            assert key in out, f"project_app_state() must include '{key}'"

    def test_project_app_state_preserves_turn_delivery_payloads(self, bundle):
        """project_app_state() preserves turn payloads for external delivery adapters."""
        turn_log = {"turn_id": "turn_1", "blocks": [{"type": "assistant.completion"}]}
        timeline = {"turn_id": "turn_1", "blocks": []}

        out = type(bundle).project_app_state({"turn_log": turn_log, "timeline": timeline})

        assert out["turn_log"] == turn_log
        assert out["timeline"] == timeline

    @pytest.mark.asyncio
    async def test_bundle_state_graph_preserves_turn_delivery_payloads(self):
        """StateGraph(BundleState) must not drop turn payloads before project_app_state()."""
        from langgraph.graph import END, START, StateGraph
        from kdcube_ai_app.infra.service_hub.inventory import BundleState

        async def node(state: BundleState) -> BundleState:
            state["turn_log"] = {"turn_id": "turn_1", "blocks": [{"type": "assistant.completion"}]}
            state["timeline"] = {"turn_id": "turn_1", "blocks": []}
            return state

        graph = StateGraph(BundleState)
        graph.add_node("node", node)
        graph.add_edge(START, "node")
        graph.add_edge("node", END)

        out = await graph.compile().ainvoke({})

        assert out["turn_log"]["blocks"][0]["type"] == "assistant.completion"
        assert out["timeline"]["turn_id"] == "turn_1"

    def test_project_app_state_includes_bundle_context(self, bundle):
        """project_app_state() includes bundle ID in context."""
        out = type(bundle).project_app_state({})
        assert "context" in out
        assert "bundle" in out["context"]
