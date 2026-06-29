# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventPayload,
    ExternalEventRequest,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import set_skills_descriptor
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code import (
    ClaudeCodeAgent,
    ClaudeCodeWorkspaceConfig,
    prepare_claude_code_workspace,
)
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.types import ClaudeCodeAgentConfig, ClaudeCodeBinding
from kdcube_ai_app.infra.accounting import AccountingSystem, clear_context


class _RecordingEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, *, event: str, data: dict, **kwargs) -> None:
        del kwargs
        self.events.append((event, data))


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout_lines: list[str],
        stderr_lines: list[str],
        returncode: int,
        wait_delay: float = 0.0,
    ):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode = None
        self._stdout_lines = list(stdout_lines)
        self._stderr_lines = list(stderr_lines)
        self._planned_returncode = returncode
        self._wait_delay = wait_delay
        self._done = asyncio.Event()
        self._task = asyncio.create_task(self._feed())

    async def _feed(self) -> None:
        for line in self._stdout_lines:
            self.stdout.feed_data(line.encode("utf-8"))
            await asyncio.sleep(0)
        self.stdout.feed_eof()
        for line in self._stderr_lines:
            self.stderr.feed_data(line.encode("utf-8"))
            await asyncio.sleep(0)
        self.stderr.feed_eof()
        if self._wait_delay > 0:
            try:
                await asyncio.wait_for(self._done.wait(), timeout=self._wait_delay)
            except asyncio.TimeoutError:
                pass
        if self.returncode is None:
            self.returncode = self._planned_returncode
        self._done.set()

    async def wait(self) -> int:
        await asyncio.shield(self._task)
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15
        self._done.set()

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9
        self._done.set()


class _RecordingAccountingBackend:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    async def write_text_a(self, path: str, content: str) -> None:
        self.writes.append((path, content))


def _ctx() -> ExternalEventPayload:
    return ExternalEventPayload(
        request=ExternalEventRequest(request_id="req-claude-code"),
        routing=ExternalEventRouting(
            session_id="sid-claude",
            conversation_id="conv-claude",
            turn_id="turn-claude",
            bundle_id="bundle.claude",
        ),
        actor=ExternalEventActor(
            tenant_id="demo-tenant",
            project_id="demo-project",
        ),
        user=ExternalEventUser(
            user_type="privileged",
            user_id="admin-user-1",
            fingerprint="fingerprint-1",
            username="admin",
            roles=["kdcube:role:super-admin"],
            permissions=["kdcube:*:chat:*;read;write;delete"],
            timezone="UTC",
        ),
    )


def _make_comm() -> tuple[ChatCommunicator, _RecordingEmitter]:
    emitter = _RecordingEmitter()
    comm = ChatCommunicator(
        emitter=emitter,
        tenant="demo-tenant",
        project="demo-project",
        user_id="admin-user-1",
        user_type="privileged",
        service={
            "request_id": "req-claude-code",
            "tenant": "demo-tenant",
            "project": "demo-project",
            "user": "admin-user-1",
        },
        conversation={
            "session_id": "sid-claude",
            "conversation_id": "conv-claude",
            "turn_id": "turn-claude",
        },
    )
    return comm, emitter


def _config(workspace_path: Path) -> ClaudeCodeAgentConfig:
    return ClaudeCodeAgentConfig(
        agent_name="kb-writer",
        workspace_path=workspace_path,
        allowed_tools=("Read", "Grep", "WebSearch"),
        additional_directories=(workspace_path / "repos" / "output",),
        extra_args=("--append-system-prompt", "Stay concise."),
        env={"EXTRA_ENV": "yes"},
    )


@pytest.mark.asyncio
async def test_from_current_context_derives_deterministic_binding(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, _ = _make_comm()
    ctx = _ctx()

    with bind_current_request_context(ctx, comm=comm):
        first = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
        )
        second = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
        )

    assert first.binding == second.binding
    assert first.binding.user_id == "admin-user-1"
    assert first.binding.conversation_id == "conv-claude"
    assert first.binding.session_id == "sid-claude"
    assert first.binding.claude_session_id


def test_build_args_includes_session_allowed_tools_and_agent(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "repos" / "output").mkdir(parents=True)
    # --agent is only passed when the named agent is defined in the workspace.
    agents_dir = workspace / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "kb-writer.md").write_text("# Agent\n", encoding="utf-8")
    binding = ClaudeCodeBinding(
        user_id="admin-user-1",
        conversation_id="conv-claude",
        session_id="sid-claude",
        claude_session_id="claude-session-1",
    )
    agent = ClaudeCodeAgent(config=_config(workspace), binding=binding, comm=None)

    args = agent.build_args("Explain the repo")
    resume_args = agent.build_args("Explain the repo", resume_existing=True)

    assert "--allowedTools" in args
    assert "Read,Grep,WebSearch" in args
    assert "--permission-mode" in args
    assert "acceptEdits" in args
    assert "--add-dir" in args
    assert str(workspace / "repos" / "output") in args
    assert "--session-id" in args
    assert "claude-session-1" in args
    assert "--resume" in resume_args
    assert "claude-session-1" in resume_args
    assert "--session-id" not in resume_args
    assert "--agent" in args
    assert "kb-writer" in args
    assert args[-1] == "Explain the repo"


def test_build_args_omits_agent_when_no_definition(tmp_path: Path):
    """agent_name may be used purely as a label. With no
    .claude/agents/<name>.md definition the CLI rejects --agent, so the runner
    omits it and runs the default agent with the seeded instructions."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    binding = ClaudeCodeBinding(
        user_id="admin-user-1",
        conversation_id="conv-claude",
        session_id="sid-claude",
        claude_session_id="claude-session-1",
    )
    agent = ClaudeCodeAgent(config=_config(workspace), binding=binding, comm=None)

    args = agent.build_args("Explain the repo")

    assert "--agent" not in args
    assert "kb-writer" not in args
    assert args[-1] == "Explain the repo"


@pytest.mark.asyncio
async def test_run_turn_closes_stdin_when_prompt_is_passed_as_argument(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, _ = _make_comm()
    ctx = _ctx()
    captured_kwargs: list[dict] = []

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args
        captured_kwargs.append(kwargs)
        return _FakeProcess(stdout_lines=[], stderr_lines=[], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with bind_current_request_context(ctx, comm=comm):
        agent = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
        )
        await agent.run_turn("Explain the repo")

    assert captured_kwargs
    assert captured_kwargs[0]["stdin"] is asyncio.subprocess.DEVNULL


def test_build_args_includes_selected_model(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    binding = ClaudeCodeBinding(
        user_id="admin-user-1",
        conversation_id="conv-claude",
        session_id="sid-claude",
        claude_session_id="claude-session-1",
    )
    config = ClaudeCodeAgentConfig(
        agent_name="kb-writer",
        workspace_path=workspace,
        model="claude-opus-4-6",
    )
    agent = ClaudeCodeAgent(config=config, binding=binding, comm=None)

    args = agent.build_args("Explain the repo")

    assert "--model" in args
    assert "claude-opus-4-6" in args


def test_prepare_claude_code_workspace_writes_mcp_settings_and_instructions(tmp_path: Path):
    workspace = tmp_path / "workspace"

    prepared = prepare_claude_code_workspace(
        workspace,
        ClaudeCodeWorkspaceConfig(
            mcp_servers={
                "scoped_data": {
                    "type": "http",
                    "url": "http://127.0.0.1:8020/mcp",
                    "headers": {"X-Test-Token": "token"},
                }
            },
            allowed_tools=("mcp__scoped_data__task_context",),
            denied_tools=("Bash", "Read"),
            instructions_markdown="# Scoped Data\nUse only MCP tools.\n",
        ),
    )

    assert prepared["mcp_servers"] == ["scoped_data"]
    assert (workspace / ".mcp.json").exists()
    assert (workspace / ".claude" / "settings.local.json").exists()
    assert (workspace / "CLAUDE.md").read_text(encoding="utf-8") == "# Scoped Data\nUse only MCP tools.\n"

    mcp_config = json.loads((workspace / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp_config["mcpServers"]["scoped_data"]["headers"]["X-Test-Token"] == "token"

    settings = json.loads((workspace / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert settings["enableAllProjectMcpServers"] is False
    assert settings["enabledMcpjsonServers"] == ["scoped_data"]
    assert settings["permissions"]["allow"] == ["mcp__scoped_data__task_context"]
    assert settings["permissions"]["deny"] == ["Bash", "Read"]


def test_prepare_claude_code_workspace_materializes_kdcube_skills(tmp_path: Path):
    skills_root = tmp_path / "bundle" / "skills"
    skill_dir = skills_root / "product" / "email-analysis"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: email-analysis",
                "description: Analyze scoped email candidates.",
                "namespace: product",
                "when_to_use:",
                "  - Classifying email messages",
                "---",
                "",
                "# Email Analysis",
                "",
                "Use the scoped email MCP tools only.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "reference.md").write_text("Reference notes", encoding="utf-8")
    workspace = tmp_path / "workspace"

    try:
        set_skills_descriptor({"CUSTOM_SKILLS_ROOT": str(skills_root)})
        prepared = prepare_claude_code_workspace(
            workspace,
            ClaudeCodeWorkspaceConfig(
                skill_ids=("product.email-analysis",),
                skill_allowed_tools={
                    "product.email-analysis": (
                        "mcp__email__task_context",
                        "mcp__email__record_processing_result",
                    )
                },
            ),
        )
    finally:
        set_skills_descriptor(None)

    skill_path = workspace / ".claude" / "skills" / "product-email-analysis" / "SKILL.md"
    support_path = workspace / ".claude" / "skills" / "product-email-analysis" / "reference.md"
    skill_text = skill_path.read_text(encoding="utf-8")

    assert prepared["materialized_skill_ids"] == ["product.email-analysis"]
    assert skill_path.exists()
    assert support_path.read_text(encoding="utf-8") == "Reference notes"
    assert "name: email-analysis" in skill_text
    assert "Analyze scoped email candidates." in skill_text
    assert "Classifying email messages" in skill_text
    assert "allowed-tools: mcp__email__task_context, mcp__email__record_processing_result" in skill_text
    assert "Use the scoped email MCP tools only." in skill_text


@pytest.mark.asyncio
async def test_run_turn_streams_incremental_deltas(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, emitter = _make_comm()
    ctx = _ctx()

    outputs = [
        json.dumps({"message": {"content": [{"type": "text", "text": "Hello"}]}}) + "\n",
        json.dumps({"message": {"content": [{"type": "text", "text": "Hello world"}]}}) + "\n",
    ]

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return _FakeProcess(stdout_lines=outputs, stderr_lines=[], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with bind_current_request_context(ctx, comm=comm):
        agent = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
            allowed_tools=("Read", "WebSearch"),
        )
        result = await agent.run_turn("Summarize repo")

    deltas = [
        envelope["delta"]["text"]
        for _, envelope in emitter.events
        if envelope.get("type") == "chat.delta"
    ]
    steps = [
        envelope
        for _, envelope in emitter.events
        if envelope.get("type") == "chat.step"
    ]

    assert result.status == "completed"
    assert result.final_text == "Hello world"
    assert result.delta_count == 2
    assert deltas == ["Hello", " world"]
    assert steps[0]["event"]["status"] == "started"
    assert steps[0]["data"]["turn_kind"] == "regular"
    assert steps[-1]["event"]["status"] == "completed"


@pytest.mark.asyncio
async def test_run_turn_handles_large_single_stdout_json_line(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, emitter = _make_comm()
    ctx = _ctx()
    large_text = "x" * (96 * 1024)
    outputs = [
        json.dumps({"message": {"content": [{"type": "text", "text": large_text}]}}) + "\n",
    ]

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return _FakeProcess(stdout_lines=outputs, stderr_lines=[], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with bind_current_request_context(ctx, comm=comm):
        agent = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
            allowed_tools=("Read",),
        )
        result = await agent.run_turn("Summarize repo")

    deltas = [
        envelope["delta"]["text"]
        for _, envelope in emitter.events
        if envelope.get("type") == "chat.delta"
    ]

    assert result.status == "completed"
    assert result.final_text == large_text
    assert result.delta_count == 1
    assert deltas == [large_text]


@pytest.mark.asyncio
async def test_run_turn_preserves_multiple_distinct_claude_messages(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, emitter = _make_comm()
    ctx = _ctx()

    outputs = [
        json.dumps({"message": {"content": [{"type": "text", "text": "First message"}]}}) + "\n",
        json.dumps({"message": {"content": [{"type": "text", "text": "Second message"}]}}) + "\n",
    ]

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return _FakeProcess(stdout_lines=outputs, stderr_lines=[], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with bind_current_request_context(ctx, comm=comm):
        agent = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
            allowed_tools=("Read",),
        )
        result = await agent.run_turn("Summarize repo")

    deltas = [
        envelope["delta"]["text"]
        for _, envelope in emitter.events
        if envelope.get("type") == "chat.delta"
    ]

    assert result.status == "completed"
    assert result.final_text == "First message\n\nSecond message"
    assert deltas == ["First message", "\n\nSecond message"]


@pytest.mark.asyncio
async def test_run_turn_emits_stderr_and_failure_step(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, emitter = _make_comm()
    ctx = _ctx()

    outputs = [
        json.dumps(
            {
                "type": "result",
                "subtype": "error",
                "duration_ms": 800,
                "total_cost_usd": 0.001,
            }
        )
        + "\n",
    ]

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return _FakeProcess(stdout_lines=outputs, stderr_lines=["fatal: boom\n"], returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with bind_current_request_context(ctx, comm=comm):
        agent = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
        )
        result = await agent.run_followup("Continue")

    steps = [
        envelope
        for _, envelope in emitter.events
        if envelope.get("type") == "chat.step"
    ]

    assert result.status == "failed"
    assert result.turn_kind == "followup"
    assert any(step["event"]["step"] == "claude_code.agent.stderr" for step in steps)
    assert steps[-1]["event"]["status"] == "error"
    assert steps[-1]["data"]["error"] == "fatal: boom"
    assert steps[-1]["data"]["last_stderr_line"] == "fatal: boom"
    assert steps[-1]["data"]["raw_result_event"] == {
        "type": "result",
        "subtype": "error",
        "duration_ms": 800,
        "total_cost_usd": 0.001,
    }


@pytest.mark.asyncio
async def test_run_turn_collects_structured_events_from_streamed_chunks(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, emitter = _make_comm()
    ctx = _ctx()
    observed: list[dict] = []

    outputs = [
        json.dumps({"message": {"content": [{"type": "text", "text": "NEWS_PIPELINE_EVENT {\"type\":\"phase\""}]}}) + "\n",
        json.dumps({"message": {"content": [{"type": "text", "text": "NEWS_PIPELINE_EVENT {\"type\":\"phase\",\"phase\":\"draft_markdown\",\"status\":\"started\"}\nDone"}]}}) + "\n",
    ]

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return _FakeProcess(stdout_lines=outputs, stderr_lines=[], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with bind_current_request_context(ctx, comm=comm):
        agent = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
            structured_output_prefixes=("NEWS_PIPELINE_EVENT",),
            on_structured_output=lambda event: observed.append(event),
        )
        result = await agent.run_turn("Summarize repo")

    completed_steps = [
        envelope
        for _, envelope in emitter.events
        if envelope.get("type") == "chat.step" and envelope.get("event", {}).get("status") == "completed"
    ]

    assert result.status == "completed"
    assert result.structured_events == [
        {
            "prefix": "NEWS_PIPELINE_EVENT",
            "payload": {"type": "phase", "phase": "draft_markdown", "status": "started"},
            "raw_line": "NEWS_PIPELINE_EVENT {\"type\":\"phase\",\"phase\":\"draft_markdown\",\"status\":\"started\"}",
        }
    ]
    assert observed == result.structured_events
    assert completed_steps[-1]["data"]["structured_events"] == result.structured_events


@pytest.mark.asyncio
async def test_run_turn_collects_executive_journal_from_standard_prefix(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, emitter = _make_comm()
    ctx = _ctx()

    outputs = [
        json.dumps({"message": {"content": [{"type": "text", "text": "EXECUTIVE_JOURNAL searched scoped messages; found two candidates\nStill working"}]}}) + "\n",
        json.dumps({"message": {"content": [{"type": "text", "text": "EXECUTIVE_JOURNAL_CODE print('checkpoint')\nDone"}]}}) + "\n",
    ]

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return _FakeProcess(stdout_lines=outputs, stderr_lines=[], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with bind_current_request_context(ctx, comm=comm):
        agent = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
        )
        result = await agent.run_turn("Summarize repo")

    completed_steps = [
        envelope
        for _, envelope in emitter.events
        if envelope.get("type") == "chat.step" and envelope.get("event", {}).get("status") == "completed"
    ]

    assert result.status == "completed"
    assert len(result.executive_journal) == 2
    assert result.executive_journal[0]["prefix"] == "EXECUTIVE_JOURNAL"
    assert result.executive_journal[0]["channel"] == "note"
    assert result.executive_journal[0]["text"] == "searched scoped messages; found two candidates"
    assert result.executive_journal[0]["captured_at"]
    assert result.executive_journal[1]["prefix"] == "EXECUTIVE_JOURNAL_CODE"
    assert result.executive_journal[1]["channel"] == "code"
    assert result.executive_journal[1]["code"] == "print('checkpoint')"
    assert completed_steps[-1]["data"]["executive_journal"] == result.executive_journal


@pytest.mark.asyncio
async def test_run_turn_timeout_marks_failure_and_emits_timeout_metadata(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, emitter = _make_comm()
    ctx = _ctx()

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return _FakeProcess(
            stdout_lines=["working on the mailbox\n"],
            stderr_lines=["warning: still running\n"],
            returncode=0,
            wait_delay=5.0,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with bind_current_request_context(ctx, comm=comm):
        agent = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
            timeout_seconds=0.01,
        )
        result = await agent.run_turn("Summarize repo")

    error_steps = [
        envelope
        for _, envelope in emitter.events
        if envelope.get("type") == "chat.step" and envelope.get("event", {}).get("status") == "error"
    ]

    assert result.status == "failed"
    assert result.timed_out is True
    assert result.timeout_seconds == pytest.approx(0.01)
    assert result.duration_ms is not None
    assert result.duration_ms >= 0
    assert "timeout" in (result.error_message or "").lower()
    assert result.failure_diagnostics["reason"] == "timeout_waiting_for_process_result"
    assert result.failure_diagnostics["stdout_tail"][-1] == "working on the mailbox"
    assert result.failure_diagnostics["stderr_tail"][-1] == "warning: still running"
    assert result.failure_diagnostics["raw_result_event_seen"] is False
    assert error_steps[-1]["data"]["timed_out"] is True
    assert error_steps[-1]["data"]["timeout_seconds"] == pytest.approx(0.01)
    assert error_steps[-1]["data"]["failure_diagnostics"]["reason"] == "timeout_waiting_for_process_result"


@pytest.mark.asyncio
async def test_followup_and_steer_resume_same_claude_session_id(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, emitter = _make_comm()
    ctx = _ctx()
    captured: list[tuple] = []

    async def _fake_create_subprocess_exec(*args, **kwargs):
        captured.append(args)
        del kwargs
        return _FakeProcess(stdout_lines=[], stderr_lines=[], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with bind_current_request_context(ctx, comm=comm):
        agent = ClaudeCodeAgent.from_current_context(
            agent_name="kb-writer",
            workspace_path=workspace,
        )
        first = await agent.run_followup("Continue")
        second = await agent.run_steer("Change direction")

    assert first.session_id == second.session_id
    assert captured
    session_ids = []
    for args in captured:
        idx = args.index("--resume")
        session_ids.append(args[idx + 1])
    assert len(set(session_ids)) == 1

    started_steps = [
        envelope["data"]["turn_kind"]
        for _, envelope in emitter.events
        if envelope.get("type") == "chat.step" and envelope.get("event", {}).get("status") == "started"
    ]
    assert started_steps == ["followup", "steer"]


@pytest.mark.asyncio
async def test_run_turn_emits_accounting_event_with_usage(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, _ = _make_comm()
    ctx = _ctx()
    backend = _RecordingAccountingBackend()

    outputs = [
        json.dumps({"type": "system", "subtype": "init", "model": "sonnet"}) + "\n",
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 30,
                        "cache_creation_input_tokens": 40,
                        "cache_read_input_tokens": 5,
                    },
                    "content": [{"type": "text", "text": "Done"}],
                },
            }
        )
        + "\n",
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "duration_ms": 1500,
                "duration_api_ms": 1200,
                "total_cost_usd": 0.0123,
            }
        )
        + "\n",
    ]

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return _FakeProcess(stdout_lines=outputs, stderr_lines=[], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    AccountingSystem.init_storage(
        backend,
        enabled=True,
        cache_in_memory=False,
        redis_turn_cache=False,
    )
    AccountingSystem.set_context(
        user_id="admin-user-1",
        session_id="sid-claude",
        tenant_id="demo-tenant",
        project_id="demo-project",
        request_id="req-claude-code",
        app_bundle_id="bundle.claude",
        component="bundle.claude",
    )

    try:
        with bind_current_request_context(ctx, comm=comm):
            agent = ClaudeCodeAgent.from_current_context(
                agent_name="kb-writer",
                workspace_path=workspace,
            )
            result = await agent.run_turn("Summarize repo")
    finally:
        clear_context()
        AccountingSystem.init_storage(None, enabled=False)

    assert result.status == "completed"
    assert result.model == "claude-sonnet-4-5-20250929"
    assert result.cost_usd == pytest.approx(0.0123)
    assert backend.writes, "Claude Code run should emit an accounting event"

    _, content = backend.writes[-1]
    event = json.loads(content)
    assert event["service_type"] == "llm"
    assert event["provider"] == "anthropic"
    assert event["model_or_service"] == "claude-sonnet-4-5-20250929"
    assert event["success"] is True
    assert event["usage"]["input_tokens"] == 120
    assert event["usage"]["output_tokens"] == 30
    assert event["usage"]["cache_creation_tokens"] == 40
    assert event["usage"]["cache_read_tokens"] == 5
    assert event["usage"]["cost_usd"] == pytest.approx(0.0123)
    assert event["metadata"]["agent"] == "kb-writer"
    assert event["metadata"]["agent_name"] == "kb-writer"
    assert event["metadata"]["runtime"] == "claude_code"
    assert event["metadata"]["turn_kind"] == "regular"


@pytest.mark.asyncio
async def test_failed_run_marks_accounting_event_unsuccessful(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comm, _ = _make_comm()
    ctx = _ctx()
    backend = _RecordingAccountingBackend()

    outputs = [
        json.dumps({"type": "system", "subtype": "init", "model": "haiku"}) + "\n",
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-haiku-4-5-20251001",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 10,
                    },
                },
            }
        )
        + "\n",
        json.dumps(
            {
                "type": "result",
                "subtype": "error",
                "duration_ms": 800,
                "total_cost_usd": 0.001,
            }
        )
        + "\n",
    ]

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return _FakeProcess(stdout_lines=outputs, stderr_lines=["fatal: boom\n"], returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    AccountingSystem.init_storage(
        backend,
        enabled=True,
        cache_in_memory=False,
        redis_turn_cache=False,
    )
    AccountingSystem.set_context(
        user_id="admin-user-1",
        session_id="sid-claude",
        tenant_id="demo-tenant",
        project_id="demo-project",
        request_id="req-claude-code",
        app_bundle_id="bundle.claude",
        component="bundle.claude",
    )

    try:
        with bind_current_request_context(ctx, comm=comm):
            agent = ClaudeCodeAgent.from_current_context(
                agent_name="kb-writer",
                workspace_path=workspace,
            )
            result = await agent.run_turn("Continue")
    finally:
        clear_context()
        AccountingSystem.init_storage(None, enabled=False)

    assert result.status == "failed"
    assert backend.writes, "Failed Claude Code run should still emit an accounting event"

    _, content = backend.writes[-1]
    event = json.loads(content)
    assert event["service_type"] == "llm"
    assert event["provider"] == "anthropic"
    assert event["success"] is False
    assert event["error_message"] == "fatal: boom"
    assert event["usage"]["input_tokens"] == 20
    assert event["usage"]["output_tokens"] == 10
    assert event["usage"]["cost_usd"] == pytest.approx(0.001)
