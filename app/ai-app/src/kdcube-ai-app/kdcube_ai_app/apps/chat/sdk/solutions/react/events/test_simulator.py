import pytest

from kdcube_ai_app.apps.chat.sdk.context.memory import tools as memory_tools
from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.simulator import (
    render_external_events_preview_payload,
)


@pytest.mark.asyncio
async def test_preview_payload_renders_context_event_ref_without_model_run(tmp_path):
    result = await render_external_events_preview_payload(
        {
            "conversation_id": "conv_1",
            "turn_id": "turn_dry_run",
            "target": {"agent_id": "main"},
            "external_events": [
                {
                    "event_id": "evt_1",
                    "type": "event.external",
                    "event_source_id": "bundle.context.focus",
                    "reactive": False,
                    "hosted_uri": "mem:mem_1",
                    "payload": {
                        "mime": "application/json",
                        "event_ref": "mem:mem_1",
                        "event": {
                            "context_role": "context",
                            "id": "mem:mem_1",
                            "kind": "memory",
                            "label": "A memory",
                            "ref": "mem:mem_1",
                        },
                    },
                }
            ],
        },
        runtime_identity={
            "tenant": "tenant",
            "project": "project",
            "user": "user_1",
            "user_type": "registered",
        },
        bundle_id="bundle@1",
        debug_dir=tmp_path,
    )

    assert result["ok"] is True
    assert result["event_count"] == 1
    assert '"event_ref": "mem:mem_1"' in result["timeline_text"]
    assert "mem:mem_1" in result["rendered_text"]
    assert "kdcube.memory.context" not in result["rendered_text"]
    assert result["debug_paths"]


@pytest.mark.asyncio
async def test_preview_payload_uses_memory_context_policy_for_mem_refs(tmp_path):
    event_sources = EventSourceSubsystem(modules=[{"mod": memory_tools, "alias": "memory"}])

    result = await render_external_events_preview_payload(
        {
            "conversation_id": "conv_1",
            "turn_id": "turn_dry_run",
            "target": {"agent_id": "main"},
            "external_events": [
                {
                    "event_id": "evt_1",
                    "type": "event.external",
                    "event_source_id": "memory.context",
                    "reactive": False,
                    "hosted_uri": "mem:mem_1",
                    "payload": {
                        "mime": "application/json",
                        "event_ref": "mem:mem_1",
                        "event": {
                            "context_role": "context",
                            "id": "mem:mem_1",
                            "kind": "memory",
                            "label": "Excel with openpyxl charts",
                            "summary": "Never use openpyxl native chart objects when files may be opened outside Excel.",
                            "ref": "mem:mem_1",
                        },
                    },
                }
            ],
        },
        event_sources=event_sources,
        runtime_identity={
            "tenant": "tenant",
            "project": "project",
            "user": "user_1",
            "user_type": "registered",
        },
        bundle_id="bundle@1",
        debug_dir=tmp_path,
    )

    assert result["ok"] is True
    assert "[MEMORY CONTEXT]" in result["rendered_text"]
    assert "object_ref: mem:mem_1" in result["rendered_text"]
    assert "label: Excel with openpyxl charts" in result["rendered_text"]
    assert "kind: memory" not in result["rendered_text"]


@pytest.mark.asyncio
async def test_preview_payload_rejects_missing_external_events():
    result = await render_external_events_preview_payload({})

    assert result == {"ok": False, "error": "external_events must be a list", "status": 400}
