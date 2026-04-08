# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_comm,
    get_current_request_context,
)
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.streaming import (
    compute_incremental_chunk,
    extract_text_from_claude_event,
)
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.types import (
    ClaudeCodeAgentConfig,
    ClaudeCodeBinding,
    ClaudeCodeRunResult,
    ClaudeCodeTurnKind,
)


class ClaudeCodeAgent:
    def __init__(
        self,
        *,
        config: ClaudeCodeAgentConfig,
        binding: ClaudeCodeBinding,
        comm: object | None = None,
        logger: logging.Logger | None = None,
    ):
        self.config = config
        self.binding = binding
        self.comm = comm if comm is not None else get_current_comm()
        self.logger = logger or logging.getLogger("ClaudeCodeAgent")

    @classmethod
    def from_current_context(
        cls,
        *,
        agent_name: str,
        workspace_path: str | Path,
        allowed_tools: Sequence[str] = (),
        additional_directories: Sequence[str | Path] = (),
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        step_name: str = "claude_code.agent",
        delta_marker: str = "answer",
        emit_stderr_steps: bool = True,
        command: str = "claude",
        permission_mode: str | None = "acceptEdits",
    ) -> "ClaudeCodeAgent":
        request_context = get_current_request_context()
        if request_context is None:
            raise ValueError("ClaudeCodeAgent requires a current request context")

        return cls(
            config=ClaudeCodeAgentConfig(
                agent_name=agent_name,
                workspace_path=Path(workspace_path),
                allowed_tools=allowed_tools,
                additional_directories=tuple(Path(path) for path in additional_directories),
                extra_args=extra_args,
                env=env or {},
                step_name=step_name,
                delta_marker=delta_marker,
                emit_stderr_steps=emit_stderr_steps,
                command=command,
                permission_mode=permission_mode,
            ),
            binding=_binding_from_request_context(request_context, agent_name=agent_name),
            comm=get_current_comm(),
        )

    def build_args(self, prompt: str, *, resume_existing: bool = False) -> list[str]:
        args = [
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
        ]
        if self.config.allowed_tools:
            args.extend(["--allowedTools", ",".join(self.config.allowed_tools)])
        if self.config.permission_mode:
            args.extend(["--permission-mode", self.config.permission_mode])
        for path in self.config.additional_directories:
            args.extend(["--add-dir", str(path)])
        args.extend(["--agent", self.config.agent_name])
        if resume_existing:
            args.extend(["--resume", self.binding.claude_session_id])
        else:
            args.extend(["--session-id", self.binding.claude_session_id])
        args.extend(list(self.config.extra_args))
        args.append(prompt)
        return args

    async def run_turn(
        self,
        prompt: str,
        *,
        kind: ClaudeCodeTurnKind = "regular",
        resume_existing: bool = False,
    ) -> ClaudeCodeRunResult:
        workspace_path = self.config.workspace_path
        if not workspace_path.exists() or not workspace_path.is_dir():
            message = f"Claude workspace path does not exist or is not a directory: {workspace_path}"
            await self._emit_final_error(
                message=message,
                kind=kind,
                exit_code=None,
                stderr_lines=[],
                resume_existing=resume_existing,
            )
            raise FileNotFoundError(message)

        metadata = self._metadata(kind=kind, resume_existing=resume_existing)
        await self._emit_step(
            step=self.config.step_name,
            status="started",
            title=f"Running {self.config.agent_name}",
            data=metadata,
        )

        raw_output_lines: list[str] = []
        stderr_lines: list[str] = []
        delta_count = 0
        snapshot = ""
        final_text = ""

        try:
            process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.build_args(prompt, resume_existing=resume_existing),
                cwd=str(workspace_path),
                env=self._build_env(kind=kind),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            await self._emit_final_error(
                message=str(exc),
                kind=kind,
                exit_code=None,
                stderr_lines=[],
                resume_existing=resume_existing,
            )
            raise

        async def _consume_stdout() -> tuple[str, int]:
            nonlocal snapshot, final_text, delta_count
            assert process.stdout is not None
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").replace("\r", "").rstrip("\n")
                if not line.strip():
                    continue
                raw_output_lines.append(line)
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    final_text += line
                    await self._emit_delta(text=line, index=delta_count)
                    delta_count += 1
                    continue

                text = extract_text_from_claude_event(parsed)
                if not text:
                    continue
                snapshot, chunk = compute_incremental_chunk(snapshot, text)
                final_text = snapshot
                if not chunk:
                    continue
                await self._emit_delta(text=chunk, index=delta_count)
                delta_count += 1
            return final_text, delta_count

        async def _consume_stderr() -> None:
            assert process.stderr is not None
            async for raw_line in process.stderr:
                line = raw_line.decode("utf-8", errors="replace").replace("\r", "").rstrip("\n")
                if not line.strip():
                    continue
                stderr_lines.append(line)
                self.logger.warning("[ClaudeCodeAgent] stderr: %s", line)
                if self.config.emit_stderr_steps:
                    await self._emit_step(
                        step=f"{self.config.step_name}.stderr",
                        status="running",
                        title="Claude stderr",
                        data={
                            **metadata,
                            "line": line,
                            "stderr_index": len(stderr_lines) - 1,
                        },
                    )

        stdout_task = asyncio.create_task(_consume_stdout())
        stderr_task = asyncio.create_task(_consume_stderr())

        exit_code = await process.wait()
        await asyncio.gather(stdout_task, stderr_task)

        status = "completed" if exit_code == 0 else "failed"
        result = ClaudeCodeRunResult(
            status=status,
            session_id=self.binding.claude_session_id,
            final_text=final_text,
            delta_count=delta_count,
            exit_code=exit_code,
            stderr_lines=stderr_lines,
            raw_output_lines=raw_output_lines,
            turn_kind=kind,
            agent_name=self.config.agent_name,
        )

        if status == "completed":
            await self._emit_step(
                step=self.config.step_name,
                status="completed",
                title=f"Completed {self.config.agent_name}",
                data={
                    **metadata,
                    "delta_count": delta_count,
                    "exit_code": exit_code,
                },
            )
        else:
            message = stderr_lines[-1] if stderr_lines else f"Claude exited with code {exit_code}"
            await self._emit_final_error(
                message=message,
                kind=kind,
                exit_code=exit_code,
                stderr_lines=stderr_lines,
                resume_existing=resume_existing,
            )

        return result

    async def run_followup(self, prompt: str) -> ClaudeCodeRunResult:
        return await self.run_turn(prompt, kind="followup", resume_existing=True)

    async def run_steer(self, prompt: str) -> ClaudeCodeRunResult:
        return await self.run_turn(prompt, kind="steer", resume_existing=True)

    def _build_env(self, *, kind: ClaudeCodeTurnKind) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.config.env)
        env["KDCUBE_USER_ID"] = self.binding.user_id
        env["KDCUBE_CONVERSATION_ID"] = self.binding.conversation_id
        env["KDCUBE_SESSION_ID"] = self.binding.session_id
        env["KDCUBE_CLAUDE_SESSION_ID"] = self.binding.claude_session_id
        env["KDCUBE_AGENT_NAME"] = self.config.agent_name
        env["KDCUBE_TURN_KIND"] = kind
        return env

    def _metadata(self, *, kind: ClaudeCodeTurnKind, resume_existing: bool = False) -> dict:
        return {
            "agent_name": self.config.agent_name,
            "turn_kind": kind,
            "resume_existing": resume_existing,
            "claude_session_id": self.binding.claude_session_id,
            "workspace_path": str(self.config.workspace_path),
            "allowed_tools": list(self.config.allowed_tools),
            "additional_directories": [str(path) for path in self.config.additional_directories],
            "permission_mode": self.config.permission_mode,
            "user_id": self.binding.user_id,
            "conversation_id": self.binding.conversation_id,
            "session_id": self.binding.session_id,
        }

    async def _emit_step(self, *, step: str, status: str, title: str, data: dict) -> None:
        if self.comm is None:
            return
        await self.comm.step(
            step=step,
            status=status,
            title=title,
            data=data,
            agent=self.config.agent_name,
        )

    async def _emit_delta(self, *, text: str, index: int) -> None:
        if self.comm is None:
            return
        await self.comm.delta(
            text=text,
            index=index,
            marker=self.config.delta_marker,
            agent=self.config.agent_name,
        )

    async def _emit_final_error(
        self,
        *,
        message: str,
        kind: ClaudeCodeTurnKind,
        exit_code: int | None,
        stderr_lines: list[str],
        resume_existing: bool,
    ) -> None:
        await self._emit_step(
            step=self.config.step_name,
            status="error",
            title=f"{self.config.agent_name} failed",
            data={
                **self._metadata(kind=kind, resume_existing=resume_existing),
                "error": message,
                "exit_code": exit_code,
                "stderr_lines": list(stderr_lines),
            },
        )


def _binding_from_request_context(
    request_context: ChatTaskPayload,
    *,
    agent_name: str,
) -> ClaudeCodeBinding:
    routing = getattr(request_context, "routing", None)
    user = getattr(request_context, "user", None)
    if routing is None:
        raise ValueError("ClaudeCodeAgent requires routing in request context")

    user_id = (
        getattr(user, "user_id", None)
        or getattr(user, "fingerprint", None)
        or "anonymous"
    )
    conversation_id = (
        getattr(routing, "conversation_id", None)
        or getattr(routing, "session_id", None)
        or "conversation"
    )
    kdcube_session_id = getattr(routing, "session_id", None) or conversation_id
    claude_session_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"kdcube/claude-code/{user_id}/{conversation_id}/{agent_name}",
        )
    )
    return ClaudeCodeBinding(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        session_id=str(kdcube_session_id),
        claude_session_id=claude_session_id,
    )
