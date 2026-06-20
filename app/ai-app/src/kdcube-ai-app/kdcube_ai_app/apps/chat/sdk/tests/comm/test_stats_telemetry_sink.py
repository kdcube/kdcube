# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.comm.sink import (
    STATS_COMM_EVENT_SELECTOR,
    StatsTelemetrySink,
    StatsTelemetryTarget,
    configure_stats_event_recording,
    recorded_comm_batch_to_telemetry,
)


class _Relay:
    async def emit(self, *, event: str, data: dict, **kwargs) -> None:
        return None


def _make_comm() -> ChatCommunicator:
    return ChatCommunicator(
        emitter=_Relay(),
        tenant="tenant-a",
        project="project-a",
        user_id="user-1",
        user_type="registered",
        service={
            "request_id": "req-1",
            "tenant": "tenant-a",
            "project": "project-a",
            "user": "user-1",
            "bundle_id": "demo.chat@1",
        },
        conversation={
            "session_id": "session-1",
            "conversation_id": "conversation-1",
            "turn_id": "turn-1",
        },
    )


def test_target_requires_auth_material() -> None:
    target = StatsTelemetryTarget(endpoint_url="http://localhost:8010/telemetry/events")

    with pytest.raises(ValueError, match="requires a bearer token or Authorization header"):
        target.request_headers()


def test_target_custom_token_header_uses_raw_secret() -> None:
    target = StatsTelemetryTarget(
        endpoint_url="http://localhost:8010/telemetry/events",
        token="ingest-secret",
        token_header="X-Telemetry-Token",
    )

    headers = target.request_headers()

    assert headers["X-Telemetry-Token"] == "ingest-secret"
    assert "Authorization" not in headers


@pytest.mark.anyio
async def test_react_tool_call_maps_to_tool_invoke_without_params() -> None:
    comm = _make_comm()
    comm.record(STATS_COMM_EVENT_SELECTOR, scope={"owner": "workflow"}, mode="replace")

    await comm.service_event(
        type="react.tool.call",
        step="react.tool.call",
        status="completed",
        agent="react.tool",
        data={
            "tool_id": "react.rg",
            "tool_call_id": "tc-1",
            "duration_ms": 42,
            "params": {"query": "private text must not be copied"},
        },
    )

    events = recorded_comm_batch_to_telemetry(comm.export_recorded_events(), comm=comm)

    assert len(events) == 1
    event = events[0]
    assert event["schema"] == "kdcube.telemetry.v1"
    assert event["name"] == "tool.invoke"
    assert event["tenant"] == "tenant-a"
    assert event["project"] == "project-a"
    assert event["source_bundle"] == "demo.chat@1"
    assert event["dimensions"]["tool"] == "react.rg"
    assert event["metrics"]["latency_ms"] == 42
    assert "params" not in event.get("data", {})
    assert "private text" not in str(event)


@pytest.mark.anyio
async def test_react_skill_read_maps_one_event_per_skill() -> None:
    comm = _make_comm()
    comm.record(STATS_COMM_EVENT_SELECTOR, mode="replace")

    await comm.service_event(
        type="react.skill.read",
        step="react.read",
        status="completed",
        agent="react.read",
        data={
            "tool_id": "react.read",
            "tool_call_id": "read-1",
            "requested_count": 2,
            "resolved_count": 2,
            "skills": [
                {"id": "public.one", "name": "One", "status": "materialized"},
                {"id": "public.two", "name": "Two", "status": "materialized"},
            ],
        },
    )

    events = recorded_comm_batch_to_telemetry(comm.export_recorded_events(), comm=comm)

    assert [event["name"] for event in events] == ["skill.read", "skill.read"]
    assert [event["dimensions"]["skill_id"] for event in events] == ["public.one", "public.two"]
    assert [event["dimensions"]["skill_name"] for event in events] == ["One", "Two"]
    assert events[0]["event_id"] != events[1]["event_id"]


@pytest.mark.anyio
async def test_copilot_mcp_call_maps_to_mcp_call() -> None:
    comm = _make_comm()
    comm.record(STATS_COMM_EVENT_SELECTOR, mode="replace")

    await comm.service_event(
        type="kdcube.copilot.mcp.call",
        step="mcp",
        status="completed",
        agent="kdcube.copilot.mcp",
        data={
            "mcp_address": "kdcube.copilot/mcp/doc_reader",
            "mcp_endpoint": "search_knowledge",
            "mcp_name": "doc_reader",
            "tool": "search",
            "duration_ms": 125,
            "result_count": 3,
            "query_len": 22,
            "reported_values": [{"concept": "search query", "value": "how to configure telemetry"}],
        },
    )

    events = recorded_comm_batch_to_telemetry(comm.export_recorded_events(), comm=comm)

    assert len(events) == 1
    assert events[0]["name"] == "mcp.call"
    assert events[0]["dimensions"]["mcp_address"] == "kdcube.copilot/mcp/doc_reader"
    assert events[0]["dimensions"]["mcp_endpoint"] == "search_knowledge"
    assert events[0]["metrics"]["latency_ms"] == 125
    assert events[0]["data"]["reported_values"] == [
        {"concept": "search query", "value": "how to configure telemetry"}
    ]


@pytest.mark.anyio
async def test_chat_accept_maps_to_chat_message_without_text() -> None:
    comm = _make_comm()
    comm.record(STATS_COMM_EVENT_SELECTOR, mode="replace")

    await comm.service_event(
        type="chat.conversation.accepted",
        step="chat.user.message",
        status="completed",
        agent="user",
        data={
            "text": "private user text must not be copied",
            "input_kind": "regular",
            "message_len": 36,
            "attachment_count": 2,
        },
    )

    events = recorded_comm_batch_to_telemetry(comm.export_recorded_events(), comm=comm)

    assert len(events) == 1
    event = events[0]
    assert event["name"] == "chat.message"
    assert event["dimensions"]["input_kind"] == "message"
    assert event["dimensions"]["role"] == "user"
    assert event["metrics"]["message_len"] == 36
    assert event["metrics"]["attachment_count"] == 2
    assert "private user text" not in str(event)


@pytest.mark.anyio
async def test_continuation_accept_maps_followup_event_to_chat_message() -> None:
    comm = _make_comm()
    comm.record(STATS_COMM_EVENT_SELECTOR, mode="replace")

    await comm.service_event(
        type="event.continuation.accepted",
        step="event.continuation",
        status="completed",
        agent="ingress",
        data={
            "event_type": "event.user.followup",
            "is_continuation": True,
            "message_len": 19,
            "attachment_count": 1,
        },
    )

    events = recorded_comm_batch_to_telemetry(comm.export_recorded_events(), comm=comm)

    assert len(events) == 1
    assert events[0]["name"] == "chat.message"
    assert events[0]["dimensions"]["input_kind"] == "followup"
    assert events[0]["metrics"]["message_len"] == 19
    assert events[0]["metrics"]["attachment_count"] == 1


@pytest.mark.anyio
async def test_turn_completed_event_forwards_conversation_activity_metrics() -> None:
    comm = _make_comm()
    comm.record(STATS_COMM_EVENT_SELECTOR, mode="replace")

    await comm.service_event(
        type="chat.conversation.turn.completed",
        step="plan.done",
        status="completed",
        agent="planner",
        data={
            "active_seconds": 7.25,
            "duration_ms": 7250,
            "produced_file_count": 2,
            "citation_count": 3,
        },
    )

    events = recorded_comm_batch_to_telemetry(comm.export_recorded_events(), comm=comm)

    assert len(events) == 1
    assert events[0]["name"] == "workflow.step"
    assert events[0]["dimensions"]["type"] == "chat.conversation.turn.completed"
    assert events[0]["metrics"]["active_seconds"] == 7.25
    assert events[0]["metrics"]["latency_ms"] == 7250
    assert events[0]["metrics"]["produced_file_count"] == 2
    assert events[0]["metrics"]["citation_count"] == 3


@pytest.mark.anyio
async def test_accounting_usage_preserves_breakdown_as_list() -> None:
    comm = _make_comm()
    comm.record(STATS_COMM_EVENT_SELECTOR, mode="replace")

    await comm.service_event(
        type="accounting.usage",
        step="accounting",
        status="completed",
        data={
            "breakdown": {
                "service_type": "llm",
                "provider": "anthropic",
                "model_or_service": "claude-sonnet",
                "input_tokens": 10,
                "output_tokens": 4,
                "cache_read_tokens": 2,
            },
            "cost_total_usd": 0.03,
        },
    )

    events = recorded_comm_batch_to_telemetry(comm.export_recorded_events(), comm=comm)

    assert len(events) == 1
    event = events[0]
    assert event["name"] == "accounting.usage"
    assert event["dimensions"]["provider"] == "anthropic"
    assert event["dimensions"]["service_type"] == "llm"
    assert event["dimensions"]["model_or_service"] == "claude-sonnet"
    assert event["metrics"]["cost_total_usd"] == 0.03
    assert event["data"]["breakdown"] == [
        {
            "service_type": "llm",
            "provider": "anthropic",
            "model_or_service": "claude-sonnet",
            "input_tokens": 10.0,
            "output_tokens": 4.0,
            "cache_read_tokens": 2.0,
        }
    ]


@pytest.mark.anyio
async def test_accounting_usage_preserves_service_specific_breakdown_fields() -> None:
    comm = _make_comm()
    comm.record(STATS_COMM_EVENT_SELECTOR, mode="replace")

    await comm.service_event(
        type="accounting.usage",
        step="accounting",
        status="completed",
        data={
            "breakdown": [
                {
                    "service_type": "embedding",
                    "provider": "openai",
                    "model_or_service": "text-embedding-3-small",
                    "tokens": 450,
                    "cost_usd": 0.000009,
                },
                {
                    "service_type": "web_search",
                    "provider": "brave",
                    "model_or_service": "unknown",
                    "tier": "free",
                    "search_queries": 1,
                    "search_results": 6,
                    "cost_per_1k_requests": 0,
                    "cost_usd": 0,
                },
            ],
            "cost_total_usd": 0.000009,
        },
    )

    events = recorded_comm_batch_to_telemetry(comm.export_recorded_events(), comm=comm)
    breakdown = events[0]["data"]["breakdown"]

    assert breakdown[0]["embedding_tokens"] == 450.0
    assert breakdown[1]["tier"] == "free"
    assert breakdown[1]["search_queries"] == 1.0
    assert breakdown[1]["search_results"] == 6.0


@pytest.mark.anyio
async def test_accounting_usage_event_id_is_stable_across_chat_and_service_envelopes() -> None:
    comm = _make_comm()
    comm.record(STATS_COMM_EVENT_SELECTOR, mode="replace")
    data = {
        "breakdown": [
            {
                "service_type": "llm",
                "provider": "anthropic",
                "model_or_service": "claude-sonnet",
                "input_tokens": 10,
                "output_tokens": 4,
                "cache_5m_write_tokens": 7,
                "cache_read_tokens": 20,
                "cost_usd": 0.03,
            }
        ],
        "cost_total_usd": 0.03,
    }

    await comm.event(
        agent="default.react.agent",
        type="accounting.usage",
        step="accounting",
        status="completed",
        data=data,
    )
    await comm.service_event(
        type="accounting.usage",
        step="accounting",
        status="completed",
        agent="default.react.agent",
        data=data,
    )

    records = comm.export_recorded_events()
    assert len(records) == 2
    assert records[0]["record_id"] != records[1]["record_id"]

    events = recorded_comm_batch_to_telemetry(records, comm=comm)

    assert len(events) == 2
    assert events[0]["event_id"] == events[1]["event_id"]
    assert events[0]["data"]["breakdown"] == events[1]["data"]["breakdown"]


@pytest.mark.anyio
async def test_comm_metadata_carries_agent_id_to_telemetry() -> None:
    comm = _make_comm()
    comm.service["agent_id"] = "research.react.agent"
    comm.record(STATS_COMM_EVENT_SELECTOR, mode="replace")

    await comm.service_event(
        type="accounting.usage",
        step="accounting",
        status="completed",
        agent="research.react.agent",
        data={"breakdown": [], "cost_total_usd": 0.0},
    )

    records = comm.export_recorded_events()
    assert records[0]["metadata"]["agent_id"] == "research.react.agent"

    events = recorded_comm_batch_to_telemetry(records, comm=comm)
    assert events[0]["meta"]["comm_metadata"]["agent_id"] == "research.react.agent"


@pytest.mark.anyio
async def test_recording_selector_filters_live_envelopes_by_metadata_agent_id() -> None:
    comm = _make_comm()
    comm.record(
        {"include": {"types": ["accounting.usage"], "agent_ids": ["research.react.agent"]}},
        mode="replace",
    )

    comm.service["agent_id"] = "planner.react.agent"
    await comm.service_event(
        type="accounting.usage",
        step="accounting",
        status="completed",
        agent="planner.react.agent",
        data={"breakdown": [], "cost_total_usd": 0.0},
    )

    comm.service["agent_id"] = "research.react.agent"
    await comm.service_event(
        type="accounting.usage",
        step="accounting",
        status="completed",
        agent="research.react.agent",
        data={"breakdown": [], "cost_total_usd": 0.0},
    )

    records = comm.export_recorded_events()
    assert len(records) == 1
    assert records[0]["metadata"]["agent_id"] == "research.react.agent"


@pytest.mark.anyio
async def test_export_recorded_events_filters_by_metadata_agent_id() -> None:
    comm = _make_comm()
    comm.record("accounting.usage", mode="replace")

    for agent_id in ("planner.react.agent", "research.react.agent"):
        comm.service["agent_id"] = agent_id
        await comm.service_event(
            type="accounting.usage",
            step="accounting",
            status="completed",
            agent=agent_id,
            data={"breakdown": [], "cost_total_usd": 0.0},
        )

    records = comm.export_recorded_events({"include": {"metadata.agent_id": ["research.react.agent"]}})
    assert len(records) == 1
    assert records[0]["metadata"]["agent_id"] == "research.react.agent"


@pytest.mark.anyio
async def test_sink_posts_batch_and_clears_records() -> None:
    comm = _make_comm()
    posted = []
    endpoint_url = "http://localhost:8010/telemetry/events"

    async def sender(url, payload, headers, timeout):
        posted.append((url, payload, headers, timeout))
        return {"ok": True, "accepted": len(payload["events"]), "duplicates": 0}

    target = StatsTelemetryTarget(
        endpoint_url=endpoint_url,
        token="token-1",
    )
    sink = StatsTelemetrySink(target, sender=sender)
    configure_stats_event_recording(
        comm,
        sink,
        scope={"owner": "workflow", "bundle": "demo.chat@1"},
    )

    await comm.service_event(
        type="react.tool.call",
        step="react.tool.call",
        status="completed",
        data={"tool_id": "react.plan", "duration_ms": 7},
    )
    result = await comm.send_recorded_events(STATS_COMM_EVENT_SELECTOR)

    assert result["ok"] is True
    assert result["sent"] == 1
    assert result["sink_result"]["telemetry_events"] == 1
    assert comm.export_recorded_events() == []
    assert posted[0][0] == endpoint_url
    assert posted[0][2]["Authorization"] == "Bearer token-1"
    assert posted[0][1]["events"][0]["name"] == "tool.invoke"
