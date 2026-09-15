from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.examples.tests.hosted_agent_harness.runtime_client import (
    AgentTarget,
    DemoError,
    LaneEvent,
    RuntimeChatClient,
    TurnEvidence,
    iter_sse_frames,
    load_bearer_token,
    load_runtime_descriptor,
    require_bundle,
    validate_demonstration,
)


def _event(event_type: str, *, turn: str = "turn-1", data=None, **extra) -> LaneEvent:
    payload = {
        "type": event_type,
        "conversation": {"conversation_id": "conv-1", "turn_id": turn},
        "event": {"step": extra.pop("step", "event"), "status": "completed"},
        "data": data or {},
        **extra,
    }
    return LaneEvent(route=event_type.replace(".", "_"), payload=payload, received_at="now")


def _write_runtime_descriptors(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "assembly.yaml").write_text(
        """
context: {tenant: demo, project: project}
ports: {ingress: '8010'}
platform:
  services:
    proc:
      exec:
        py_code_exec_image: py-code-exec:test
""".strip(),
        encoding="utf-8",
    )
    (config / "bundles.yaml").write_text(
        """
bundles:
  items:
    - id: sample@1-0
      config:
        surfaces:
          as_provider:
            bundle: {default_chat: true}
          as_consumer:
            agents:
              main:
                tools:
                  - allowed: [web_search]
""".strip(),
        encoding="utf-8",
    )


def test_load_runtime_descriptor_reads_current_shape(tmp_path: Path) -> None:
    _write_runtime_descriptors(tmp_path)

    runtime = load_runtime_descriptor(tmp_path)

    assert runtime.tenant == "demo"
    assert runtime.project == "project"
    assert runtime.base_url == "http://localhost:8010"
    assert runtime.exec_image == "py-code-exec:test"
    assert runtime.bundle_ids == frozenset({"sample@1-0"})
    assert runtime.bundle_configs["sample@1-0"]["surfaces"]["as_provider"]["bundle"][
        "default_chat"
    ] is True


def test_require_bundle_checks_agent_and_required_operations(tmp_path: Path) -> None:
    _write_runtime_descriptors(tmp_path)
    runtime = load_runtime_descriptor(tmp_path)
    require_bundle(
        runtime,
        AgentTarget(
            adapter="sample",
            bundle_id="sample@1-0",
            agent_id="main",
            needs_exec_image=False,
            description="sample",
            required_operations=("web_search",),
        ),
    )


def test_require_bundle_reports_missing_operation(tmp_path: Path) -> None:
    _write_runtime_descriptors(tmp_path)
    runtime = load_runtime_descriptor(tmp_path)
    with pytest.raises(DemoError, match="run_python"):
        require_bundle(
            runtime,
            AgentTarget(
                adapter="sample",
                bundle_id="sample@1-0",
                agent_id="main",
                needs_exec_image=False,
                description="sample",
                required_operations=("run_python",),
            ),
        )


def test_load_bearer_token_accepts_json_without_rewriting_it(tmp_path: Path) -> None:
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"access_token": "secret-token"}), encoding="utf-8")
    token_file.chmod(0o600)

    assert load_bearer_token(token_file) == "secret-token"
    assert token_file.read_text(encoding="utf-8") == '{"access_token": "secret-token"}'


def test_load_bearer_token_normalizes_authorization_prefix(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("Bearer secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)

    assert load_bearer_token(token_file) == "secret-token"


def test_load_bearer_token_rejects_world_readable_file(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o644)

    with pytest.raises(DemoError, match="chmod 600"):
        load_bearer_token(token_file)


def test_sse_parser_supports_multiline_data() -> None:
    async def stream(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'event: chat_step\ndata: {"type":\ndata: "chat.step"}\n\n',
        )

    async def run() -> list[tuple[str, dict]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(stream)) as client:
            response = await client.get("http://runtime/sse/stream")
            return [(route, dict(payload)) async for route, payload in iter_sse_frames(response)]

    assert asyncio.run(run()) == [("chat_step", {"type": "chat.step"})]


def test_evidence_requires_real_web_event_and_files() -> None:
    first = TurnEvidence(
        turn_id="turn-1",
        conversation_id="conv-1",
        events=[
            _event("chat.step", step="web_search"),
            _event("chat.delta", delta={"marker": "answer", "index": 0, "text": "researched"}),
            _event("accounting.usage"),
            _event("chat.complete"),
        ],
    )
    second = TurnEvidence(
        turn_id="turn-2",
        conversation_id="conv-1",
        events=[
            _event("chat.delta", turn="turn-2", delta={"marker": "answer", "index": 0, "text": "done"}),
            _event("accounting.usage", turn="turn-2"),
            _event(
                "chat.files",
                turn="turn-2",
                data={
                    "items": [
                        {"filename": "agent-harness-research.pdf"},
                        {"output": {"path": "files/agent-harness-research.xlsx"}},
                    ]
                },
            ),
            _event("chat.complete", turn="turn-2"),
        ],
    )

    validate_demonstration(first, second)


def test_prompt_text_does_not_count_as_web_activity() -> None:
    evidence = TurnEvidence(
        turn_id="turn-1",
        conversation_id="conv-1",
        events=[
            _event("chat.start", data={"message": "please call web_search"}),
            _event("chat.delta", delta={"marker": "answer", "index": 0, "text": "no tool"}),
            _event("accounting.usage"),
            _event("chat.complete"),
        ],
    )

    assert not evidence.has_web_activity
    with pytest.raises(DemoError, match="web-search/fetch activity"):
        validate_demonstration(evidence, evidence)


def _collect(events, *, grace: float) -> TurnEvidence:
    async def run() -> TurnEvidence:
        client = RuntimeChatClient(
            None,  # type: ignore[arg-type]
            bearer_token="token",
            evidence_path=Path("unused.jsonl"),
            accounting_grace_seconds=grace,
        )
        for event in events:
            await client._queue.put(event)
        return await client._collect_turn(
            turn_id="turn-1", conversation_id="conv-1", timeout_seconds=5
        )

    return asyncio.run(run())


def test_turn_keeps_accounting_that_settles_after_complete() -> None:
    # A run-to-completion lane (hosted LangGraph) emits chat.complete from its
    # own loop; the economics door settles and emits accounting.usage after it.
    evidence = _collect(
        [
            _event("chat.delta", delta={"marker": "answer", "index": 0, "text": "answer"}),
            _event("chat.complete"),
            _event("chat.delta", turn="turn-2"),
            _event("accounting.usage"),
        ],
        grace=2,
    )

    assert evidence.event_types == ["chat.delta", "chat.complete", "accounting.usage"]
    assert evidence.has_accounting


def test_turn_without_accounting_ends_after_the_grace() -> None:
    evidence = _collect([_event("chat.complete")], grace=0.2)

    assert evidence.event_types == ["chat.complete"]
    assert not evidence.has_accounting


def test_native_react_tool_call_counts_as_web_activity() -> None:
    # The native ReAct lane names the tool in `data.tool_id`; its step and title
    # carry only the call id ("react.tool.call.tc_…", "ReAct Tool Call").
    evidence = TurnEvidence(
        turn_id="turn-1",
        conversation_id="conv-1",
        events=[
            _event(
                "react.tool.call",
                data={"tool_id": "web_tools.web_search", "tool_call_id": "tc_32e31ef6b5c9"},
                step="react.tool.call.tc_32e31ef6b5c9",
            ),
        ],
    )

    assert evidence.has_web_activity


def test_claude_tool_metadata_counts_as_web_activity() -> None:
    evidence = TurnEvidence(
        turn_id="turn-1",
        conversation_id="conv-1",
        events=[
            _event("chat.step", data={"tool": "WebSearch"}, step="tool.1"),
        ],
    )

    assert evidence.has_web_activity
