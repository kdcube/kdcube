# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping, Sequence


ClaudeCodeTurnKind = Literal["regular", "followup", "steer"]
ClaudeCodeStructuredEventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
ClaudeCodeTextChunkCallback = Callable[[str], Awaitable[None] | None]
CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX = "EXECUTIVE_JOURNAL"
CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX = "EXECUTIVE_JOURNAL_CODE"


@dataclass(frozen=True)
class ClaudeCodeWorkspaceConfig:
    mcp_servers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    enabled_mcp_servers: Sequence[str] | None = None
    allowed_tools: Sequence[str] = field(default_factory=tuple)
    denied_tools: Sequence[str] = field(default_factory=tuple)
    skill_ids: Sequence[str] = field(default_factory=tuple)
    skill_allowed_tools: Mapping[str, Sequence[str]] = field(default_factory=dict)
    instructions_markdown: str | None = None
    settings: Mapping[str, Any] = field(default_factory=dict)
    mcp_config: Mapping[str, Any] = field(default_factory=dict)
    overwrite: bool = True

    def __post_init__(self) -> None:
        def _normalize_tools(raw_tools: Any) -> tuple[str, ...]:
            if isinstance(raw_tools, str):
                raw_tools = [raw_tools]
            return tuple(str(tool).strip() for tool in raw_tools or () if str(tool).strip())

        object.__setattr__(
            self,
            "mcp_servers",
            {
                str(name): dict(config)
                for name, config in dict(self.mcp_servers or {}).items()
                if str(name).strip() and isinstance(config, Mapping)
            },
        )
        if self.enabled_mcp_servers is not None:
            object.__setattr__(
                self,
                "enabled_mcp_servers",
                tuple(str(item).strip() for item in self.enabled_mcp_servers if str(item).strip()),
            )
        object.__setattr__(
            self,
            "allowed_tools",
            tuple(str(tool).strip() for tool in self.allowed_tools if str(tool).strip()),
        )
        object.__setattr__(
            self,
            "denied_tools",
            tuple(str(tool).strip() for tool in self.denied_tools if str(tool).strip()),
        )
        object.__setattr__(
            self,
            "skill_ids",
            tuple(str(skill_id).strip() for skill_id in self.skill_ids if str(skill_id).strip()),
        )
        object.__setattr__(
            self,
            "skill_allowed_tools",
            {
                str(skill_id).strip(): _normalize_tools(tools)
                for skill_id, tools in dict(self.skill_allowed_tools or {}).items()
                if str(skill_id).strip()
            },
        )
        object.__setattr__(self, "settings", dict(self.settings or {}))
        object.__setattr__(self, "mcp_config", dict(self.mcp_config or {}))
        if self.instructions_markdown is not None:
            object.__setattr__(self, "instructions_markdown", str(self.instructions_markdown))


@dataclass(frozen=True)
class ClaudeCodeAgentConfig:
    agent_name: str
    workspace_path: Path
    model: str | None = None
    allowed_tools: Sequence[str] = field(default_factory=tuple)
    additional_directories: Sequence[Path] = field(default_factory=tuple)
    extra_args: Sequence[str] = field(default_factory=tuple)
    env: Mapping[str, str] = field(default_factory=dict)
    step_name: str = "claude_code.agent"
    delta_marker: str = "answer"
    emit_stderr_steps: bool = True
    command: str = "claude"
    permission_mode: str | None = "acceptEdits"
    timeout_seconds: float | None = None
    structured_output_prefixes: Sequence[str] = field(default_factory=tuple)
    executive_journal_prefixes: Sequence[str] = (
        CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX,
        CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX,
    )
    executive_journal_max_entries: int = 100
    log_stream_output: bool = False
    log_stream_output_max_chars: int = 1200
    on_structured_output: ClaudeCodeStructuredEventCallback | None = None
    on_text_chunk: ClaudeCodeTextChunkCallback | None = None
    workspace_config: ClaudeCodeWorkspaceConfig | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_path", Path(self.workspace_path))
        object.__setattr__(
            self,
            "allowed_tools",
            tuple(tool.strip() for tool in self.allowed_tools if str(tool).strip()),
        )
        object.__setattr__(
            self,
            "additional_directories",
            tuple(Path(path) for path in self.additional_directories if str(path).strip()),
        )
        object.__setattr__(
            self,
            "extra_args",
            tuple(str(arg) for arg in self.extra_args if str(arg).strip()),
        )
        object.__setattr__(
            self,
            "env",
            {str(key): str(value) for key, value in dict(self.env or {}).items() if str(value).strip()},
        )
        object.__setattr__(
            self,
            "structured_output_prefixes",
            tuple(str(prefix).strip() for prefix in self.structured_output_prefixes if str(prefix).strip()),
        )
        object.__setattr__(
            self,
            "executive_journal_prefixes",
            tuple(str(prefix).strip() for prefix in self.executive_journal_prefixes if str(prefix).strip()),
        )
        try:
            max_entries = int(self.executive_journal_max_entries or 0)
        except Exception as exc:
            raise ValueError("executive_journal_max_entries must be numeric") from exc
        object.__setattr__(self, "executive_journal_max_entries", max(0, max_entries))
        try:
            log_max_chars = int(self.log_stream_output_max_chars or 0)
        except Exception as exc:
            raise ValueError("log_stream_output_max_chars must be numeric") from exc
        object.__setattr__(self, "log_stream_output", bool(self.log_stream_output))
        object.__setattr__(self, "log_stream_output_max_chars", max(120, log_max_chars))
        object.__setattr__(self, "model", str(self.model or "").strip() or None)
        if self.permission_mode is not None:
            object.__setattr__(self, "permission_mode", str(self.permission_mode).strip() or None)
        if self.timeout_seconds is not None:
            try:
                timeout_seconds = float(self.timeout_seconds)
            except Exception as exc:
                raise ValueError("timeout_seconds must be numeric") from exc
            object.__setattr__(self, "timeout_seconds", timeout_seconds if timeout_seconds > 0 else None)


@dataclass(frozen=True)
class ClaudeCodeBinding:
    user_id: str
    conversation_id: str
    session_id: str
    claude_session_id: str


@dataclass
class ClaudeCodeRunResult:
    status: Literal["completed", "failed"]
    session_id: str
    final_text: str
    delta_count: int
    exit_code: int | None
    stderr_lines: list[str]
    raw_output_lines: list[str]
    turn_kind: ClaudeCodeTurnKind
    agent_name: str
    provider: str | None = None
    requested_model: str | None = None
    model: str | None = None
    usage: dict[str, Any] | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    api_duration_ms: int | None = None
    raw_result_event: dict[str, Any] | None = None
    resolved_from_stream: bool = False
    error_message: str | None = None
    timed_out: bool = False
    timeout_seconds: float | None = None
    failure_diagnostics: dict[str, Any] = field(default_factory=dict)
    structured_events: list[dict[str, Any]] = field(default_factory=list)
    executive_journal: list[dict[str, Any]] = field(default_factory=list)
