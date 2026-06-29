# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_comm,
    get_current_request_context,
    touch_current_task_activity,
)
from kdcube_ai_app.infra.accounting import track_llm
from kdcube_ai_app.infra.accounting.usage import ServiceUsage, _norm_usage_dict
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.streaming import (
    CLAUDE_CODE_PROVIDER,
    accumulate_usage,
    accumulate_transcript,
    extract_model_from_claude_event,
    extract_result_metrics_from_claude_event,
    extract_text_from_claude_event,
    extract_usage_from_claude_event,
    is_result_event,
    is_usage_bearing_message_event,
)
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.types import (
    CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX,
    CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX,
    ClaudeCodeAgentConfig,
    ClaudeCodeBinding,
    ClaudeCodeRunResult,
    ClaudeCodeTurnKind,
    ClaudeCodeWorkspaceConfig,
)


_STREAM_READ_CHUNK_BYTES = 64 * 1024
_STREAM_LINE_BUFFER_LIMIT_BYTES = 64 * 1024 * 1024
_RAW_OUTPUT_LINE_KEEP_CHARS = 256 * 1024
_SUBPROCESS_ACTIVITY_TOUCH_INTERVAL_SEC = 5.0
_FAILURE_TAIL_CHARS = 2000


def _cap_raw_output_line(line: str) -> str:
    if len(line) <= _RAW_OUTPUT_LINE_KEEP_CHARS:
        return line
    omitted = len(line) - _RAW_OUTPUT_LINE_KEEP_CHARS
    return f"{line[:_RAW_OUTPUT_LINE_KEEP_CHARS]}\n[TRUNCATED raw claude output line: omitted {omitted} chars]"


def _tail_text(value: Any, *, max_chars: int = _FAILURE_TAIL_CHARS) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _compact_jsonish(value: Any, *, max_chars: int = _FAILURE_TAIL_CHARS) -> Any:
    if isinstance(value, str):
        return _tail_text(value, max_chars=max_chars)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        compact: dict[str, Any] = {}
        for key, item in list(value.items())[:20]:
            compact[str(key)] = _compact_jsonish(item, max_chars=max_chars)
        return compact
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_compact_jsonish(item, max_chars=max_chars) for item in list(value)[-5:]]
    return _tail_text(value, max_chars=max_chars)


def _event_type(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return ""
    return str(
        payload.get("type")
        or payload.get("subtype")
        or payload.get("event")
        or payload.get("message_type")
        or ""
    ).strip()


async def _iter_stream_text_lines(
    reader: asyncio.StreamReader,
    *,
    chunk_size: int = _STREAM_READ_CHUNK_BYTES,
    max_buffer_bytes: int = _STREAM_LINE_BUFFER_LIMIT_BYTES,
):
    buffer = bytearray()
    while True:
        chunk = await reader.read(chunk_size)
        if not chunk:
            break
        buffer.extend(chunk)
        while True:
            newline_index = buffer.find(b"\n")
            if newline_index < 0:
                break
            raw_line = bytes(buffer[:newline_index])
            del buffer[: newline_index + 1]
            yield raw_line.decode("utf-8", errors="replace").replace("\r", "")
        if len(buffer) > max_buffer_bytes:
            raw_line = bytes(buffer)
            buffer.clear()
            yield raw_line.decode("utf-8", errors="replace").replace("\r", "")
    if buffer:
        yield bytes(buffer).decode("utf-8", errors="replace").replace("\r", "")


def _claude_code_provider_extractor(result, *_args, **_kwargs) -> str:
    return str(getattr(result, "provider", None) or CLAUDE_CODE_PROVIDER)


def _claude_code_model_extractor(result, *_args, **_kwargs) -> str:
    model = getattr(result, "model", None)
    if model:
        return str(model)
    return "unknown"


def _claude_code_usage_extractor(result, *_args, **_kwargs) -> ServiceUsage:
    if result is None:
        return ServiceUsage(requests=0)

    usage = getattr(result, "usage", None) or {}
    normalized = _norm_usage_dict(usage if isinstance(usage, dict) else {})
    requests = 1
    if isinstance(usage, dict):
        try:
            requests = max(int(usage.get("requests") or 1), 0)
        except Exception:
            requests = 1

    cost_usd = None
    if isinstance(usage, dict) and usage.get("cost_usd") is not None:
        try:
            cost_usd = float(usage.get("cost_usd"))
        except Exception:
            cost_usd = None
    elif getattr(result, "cost_usd", None) is not None:
        try:
            cost_usd = float(result.cost_usd)
        except Exception:
            cost_usd = None

    return ServiceUsage(
        input_tokens=int((usage.get("input_tokens") if isinstance(usage, dict) else None) or normalized.get("input_tokens", 0) or 0),
        output_tokens=int((usage.get("output_tokens") if isinstance(usage, dict) else None) or normalized.get("output_tokens", 0) or 0),
        thinking_tokens=int((usage.get("thinking_tokens") if isinstance(usage, dict) else None) or normalized.get("thinking_tokens", 0) or 0),
        cache_creation_tokens=int((usage.get("cache_creation_tokens") if isinstance(usage, dict) else None) or normalized.get("cache_creation_input_tokens", 0) or 0),
        cache_read_tokens=int((usage.get("cache_read_tokens") if isinstance(usage, dict) else None) or normalized.get("cache_read_input_tokens", 0) or 0),
        cache_creation=(usage.get("cache_creation") if isinstance(usage, dict) else None) or normalized.get("cache_creation") or {},
        total_tokens=int((usage.get("total_tokens") if isinstance(usage, dict) else None) or normalized.get("total_tokens", 0) or 0),
        requests=requests,
        cost_usd=cost_usd,
    )


def _claude_code_meta_extractor(result, *args, **kwargs) -> dict:
    agent = args[0] if args else None
    prompt = args[1] if len(args) > 1 else kwargs.get("prompt", "")
    kind = kwargs.get("kind", "regular")
    resume_existing = bool(kwargs.get("resume_existing", False))

    meta: dict = {}
    if agent is not None and hasattr(agent, "_metadata"):
        meta = dict(agent._metadata(kind=kind, resume_existing=resume_existing))

    meta.update(
        {
            "runtime": "claude_code",
            "accounting_source": "claude_code_cli_stream_json",
            "prompt_chars": len(prompt or ""),
        }
    )

    if result is not None:
        meta.update(
            {
                "exit_code": getattr(result, "exit_code", None),
                "delta_count": getattr(result, "delta_count", None),
                "duration_ms": getattr(result, "duration_ms", None),
                "api_duration_ms": getattr(result, "api_duration_ms", None),
                "cost_usd": getattr(result, "cost_usd", None),
                "model_resolution": "stream_json" if getattr(result, "resolved_from_stream", False) else "unknown",
            }
        )
    return meta


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
        model: str | None = None,
        allowed_tools: Sequence[str] = (),
        additional_directories: Sequence[str | Path] = (),
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        step_name: str = "claude_code.agent",
        delta_marker: str = "answer",
        emit_stderr_steps: bool = True,
        command: str = "claude",
        permission_mode: str | None = "acceptEdits",
        timeout_seconds: float | None = None,
        structured_output_prefixes: Sequence[str] = (),
        executive_journal_prefixes: Sequence[str] | None = None,
        executive_journal_max_entries: int = 100,
        log_stream_output: bool = False,
        log_stream_output_max_chars: int = 1200,
        on_structured_output=None,
        on_text_chunk=None,
        workspace_config: ClaudeCodeWorkspaceConfig | None = None,
    ) -> "ClaudeCodeAgent":
        request_context = get_current_request_context()
        if request_context is None:
            raise ValueError("ClaudeCodeAgent requires a current request context")

        return cls(
            config=ClaudeCodeAgentConfig(
                agent_name=agent_name,
                workspace_path=Path(workspace_path),
                model=model,
                allowed_tools=allowed_tools,
                additional_directories=tuple(Path(path) for path in additional_directories),
                extra_args=extra_args,
                env=env or {},
                step_name=step_name,
                delta_marker=delta_marker,
                emit_stderr_steps=emit_stderr_steps,
                command=command,
                permission_mode=permission_mode,
                timeout_seconds=timeout_seconds,
                structured_output_prefixes=structured_output_prefixes,
                executive_journal_prefixes=(
                    executive_journal_prefixes
                    if executive_journal_prefixes is not None
                    else (
                        CLAUDE_CODE_EXECUTIVE_JOURNAL_PREFIX,
                        CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX,
                    )
                ),
                executive_journal_max_entries=executive_journal_max_entries,
                log_stream_output=log_stream_output,
                log_stream_output_max_chars=log_stream_output_max_chars,
                on_structured_output=on_structured_output,
                on_text_chunk=on_text_chunk,
                workspace_config=workspace_config,
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
        if self.config.model and self.config.model != "default":
            args.extend(["--model", self.config.model])
        if self.config.permission_mode:
            args.extend(["--permission-mode", self.config.permission_mode])
        for path in self.config.additional_directories:
            args.extend(["--add-dir", str(path)])
        # Only pass --agent when the named agent is actually defined in the
        # workspace (.claude/agents/<name>.md). The CLI runs with cwd=workspace
        # and validates --agent against its registered/defined agents, so passing
        # a name used purely as a label (no definition seeded) now hard-fails
        # with "agent not found". Without a definition, run the default agent
        # with the seeded CLAUDE.md instructions.
        agent_definition = (
            self.config.workspace_path / ".claude" / "agents" / f"{self.config.agent_name}.md"
        )
        if agent_definition.exists():
            args.extend(["--agent", self.config.agent_name])
        else:
            self.logger.debug(
                "[ClaudeCodeAgent] no agent definition at %s; omitting --agent %s (running default agent)",
                agent_definition,
                self.config.agent_name,
            )
        if resume_existing:
            args.extend(["--resume", self.binding.claude_session_id])
        else:
            args.extend(["--session-id", self.binding.claude_session_id])
        args.extend(list(self.config.extra_args))
        args.append(prompt)
        return args

    @track_llm(
        provider_extractor=_claude_code_provider_extractor,
        model_extractor=_claude_code_model_extractor,
        usage_extractor=_claude_code_usage_extractor,
        metadata_extractor=_claude_code_meta_extractor,
    )
    async def _run_accountable_turn(
        self,
        prompt: str,
        *,
        kind: ClaudeCodeTurnKind = "regular",
        resume_existing: bool = False,
    ) -> ClaudeCodeRunResult:
        workspace_path = self.config.workspace_path
        raw_output_lines: list[str] = []
        stderr_lines: list[str] = []
        delta_count = 0
        snapshot = ""
        final_text = ""
        transcript = ""
        usage_totals: dict[str, object] | None = None
        usage_message_events = 0
        resolved_model: str | None = None
        cost_usd: float | None = None
        duration_ms: int | None = None
        api_duration_ms: int | None = None
        raw_result_event: dict | None = None
        resolved_from_stream = False
        timed_out = False
        structured_events: list[dict[str, Any]] = []
        executive_journal: list[dict[str, Any]] = []
        structured_buffer = ""
        started_at = time.monotonic()

        def _touch_activity(kind: str) -> None:
            try:
                touch_current_task_activity(kind)
            except Exception:
                self.logger.debug("[ClaudeCodeAgent] failed to touch task activity kind=%s", kind, exc_info=True)

        def _log_stdout_event(raw_line: str, *, parsed: Mapping[str, Any] | None = None, text: str = "") -> None:
            if not self.config.log_stream_output:
                return
            try:
                max_chars = int(self.config.log_stream_output_max_chars or 1200)
                event_type = _event_type(parsed) if parsed is not None else "text"
                preview = (text or "").strip()
                if not preview:
                    preview = raw_line.strip()
                self.logger.info(
                    "[ClaudeCodeAgent] stdout agent=%s session=%s event=%s raw_chars=%s text_tail=%s",
                    self.config.agent_name,
                    self.binding.claude_session_id,
                    event_type or "unknown",
                    len(raw_line or ""),
                    _tail_text(preview, max_chars=max_chars),
                )
            except Exception:
                self.logger.debug("[ClaudeCodeAgent] failed to log stdout event", exc_info=True)

        process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.build_args(prompt, resume_existing=resume_existing),
            cwd=str(workspace_path),
            env=self._build_env(kind=kind),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _touch_activity("claude_code.process_started")

        async def _emit_structured_event(record: dict[str, Any]) -> None:
            structured_events.append(record)
            callback = self.config.on_structured_output
            if callback is None:
                return
            try:
                maybe_coro = callback(record)
                if asyncio.iscoroutine(maybe_coro):
                    await maybe_coro
            except Exception:
                self.logger.exception("[ClaudeCodeAgent] structured output callback failed")

        def _structured_prefixes() -> tuple[str, ...]:
            out: list[str] = []
            seen: set[str] = set()
            for prefix in tuple(self.config.structured_output_prefixes) + tuple(self.config.executive_journal_prefixes):
                if prefix and prefix not in seen:
                    out.append(prefix)
                    seen.add(prefix)
            return tuple(sorted(out, key=len, reverse=True))

        def _record_executive_journal(record: dict[str, Any]) -> None:
            if self.config.executive_journal_max_entries <= 0:
                return
            executive_journal.append(dict(record))
            overflow = len(executive_journal) - self.config.executive_journal_max_entries
            if overflow > 0:
                del executive_journal[:overflow]

        def _journal_channel_from_payload(prefix: str, payload: Any) -> str:
            if prefix == CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX:
                return "code"
            if isinstance(payload, Mapping):
                channel = str(payload.get("channel") or "").strip().lower()
                if channel in {"note", "struct", "code"}:
                    return channel
                if isinstance(payload.get("code"), str):
                    return "code"
                return "struct"
            if isinstance(payload, list):
                return "struct"
            return "note"

        async def _ingest_structured_line(raw_line: str) -> None:
            stripped = raw_line.strip()
            if not stripped:
                return
            journal_prefixes = set(self.config.executive_journal_prefixes)
            structured_prefixes = set(self.config.structured_output_prefixes)
            for prefix in _structured_prefixes():
                if not stripped.startswith(prefix):
                    continue
                if len(stripped) > len(prefix) and not stripped[len(prefix)].isspace():
                    continue
                payload_raw = stripped[len(prefix):].strip()
                if not payload_raw:
                    return
                if prefix in journal_prefixes:
                    record = {
                        "prefix": prefix,
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "raw_line": stripped,
                    }
                    try:
                        parsed = json.loads(payload_raw)
                    except json.JSONDecodeError:
                        if prefix == CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX:
                            record["channel"] = "code"
                            record["code"] = payload_raw
                        else:
                            record["channel"] = "note"
                            record["text"] = payload_raw
                    else:
                        record["channel"] = _journal_channel_from_payload(prefix, parsed)
                        if isinstance(parsed, (dict, list)):
                            record["payload"] = parsed
                        else:
                            record["text"] = str(parsed)
                        if prefix == CLAUDE_CODE_EXECUTIVE_JOURNAL_CODE_PREFIX and "code" not in record:
                            if isinstance(parsed, Mapping) and isinstance(parsed.get("code"), str):
                                record["code"] = parsed.get("code")
                            elif not isinstance(parsed, (Mapping, list)):
                                record["code"] = str(parsed)
                    _record_executive_journal(record)
                    if prefix not in structured_prefixes:
                        return
                    if "payload" not in record:
                        return
                    await _emit_structured_event(
                        {
                            "prefix": prefix,
                            "payload": record["payload"],
                            "raw_line": stripped,
                        }
                    )
                    return
                try:
                    payload = json.loads(payload_raw)
                except json.JSONDecodeError:
                    self.logger.warning(
                        "[ClaudeCodeAgent] failed to parse structured output prefix=%s line=%s",
                        prefix,
                        stripped,
                    )
                    return
                record = {
                    "prefix": prefix,
                    "payload": payload,
                    "raw_line": stripped,
                }
                await _emit_structured_event(record)
                return

        async def _ingest_structured_chunk(chunk: str) -> None:
            nonlocal structured_buffer
            if not chunk or not _structured_prefixes():
                return
            structured_buffer += chunk
            while True:
                newline_index = structured_buffer.find("\n")
                if newline_index < 0:
                    break
                line = structured_buffer[:newline_index]
                structured_buffer = structured_buffer[newline_index + 1 :]
                await _ingest_structured_line(line)

        async def _consume_stdout() -> None:
            nonlocal snapshot
            nonlocal final_text
            nonlocal transcript
            nonlocal delta_count
            nonlocal usage_totals
            nonlocal usage_message_events
            nonlocal resolved_model
            nonlocal cost_usd
            nonlocal duration_ms
            nonlocal api_duration_ms
            nonlocal raw_result_event
            nonlocal resolved_from_stream

            assert process.stdout is not None
            async for line in _iter_stream_text_lines(process.stdout):
                _touch_activity("claude_code.stdout")
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                raw_output_lines.append(_cap_raw_output_line(line))
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    _log_stdout_event(line)
                    final_text += line
                    await self._emit_delta(text=line, index=delta_count)
                    delta_count += 1
                    continue

                model_name = extract_model_from_claude_event(parsed)
                if model_name:
                    resolved_model = model_name
                    resolved_from_stream = True

                usage_payload = extract_usage_from_claude_event(parsed)
                if isinstance(usage_payload, dict):
                    should_accumulate = True
                    if is_result_event(parsed) and usage_message_events > 0:
                        should_accumulate = False
                    if should_accumulate:
                        usage_totals = accumulate_usage(usage_totals, usage_payload)
                    if is_usage_bearing_message_event(parsed):
                        usage_message_events += 1
                        resolved_from_stream = True

                metrics = extract_result_metrics_from_claude_event(parsed)
                if "cost_usd" in metrics:
                    cost_usd = float(metrics["cost_usd"])
                    resolved_from_stream = True
                if "duration_ms" in metrics:
                    duration_ms = int(metrics["duration_ms"])
                if "duration_api_ms" in metrics:
                    api_duration_ms = int(metrics["duration_api_ms"])
                elif "api_duration_ms" in metrics:
                    api_duration_ms = int(metrics["api_duration_ms"])

                if is_result_event(parsed):
                    raw_result_event = parsed
                    resolved_from_stream = True

                text = extract_text_from_claude_event(parsed)
                _log_stdout_event(line, parsed=parsed if isinstance(parsed, Mapping) else None, text=text)
                if not text:
                    continue
                transcript, snapshot, chunk = accumulate_transcript(transcript, snapshot, text)
                final_text = f"{transcript}\n\n{snapshot}" if transcript and snapshot else (snapshot or transcript)
                if not chunk:
                    continue
                await _ingest_structured_chunk(chunk)
                callback = self.config.on_text_chunk
                if callback is not None:
                    try:
                        maybe_coro = callback(chunk)
                        if asyncio.iscoroutine(maybe_coro):
                            await maybe_coro
                    except Exception:
                        self.logger.exception("[ClaudeCodeAgent] text chunk callback failed")
                await self._emit_delta(text=chunk, index=delta_count)
                delta_count += 1

        async def _consume_stderr() -> None:
            assert process.stderr is not None
            async for line in _iter_stream_text_lines(process.stderr):
                _touch_activity("claude_code.stderr")
                line = line.rstrip("\n")
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
                            **self._metadata(kind=kind, resume_existing=resume_existing),
                            "line": line,
                            "stderr_index": len(stderr_lines) - 1,
                        },
                    )

        async def _touch_running_subprocess() -> None:
            while True:
                await asyncio.sleep(_SUBPROCESS_ACTIVITY_TOUCH_INTERVAL_SEC)
                if getattr(process, "returncode", None) is not None:
                    return
                _touch_activity("claude_code.subprocess.running")

        stdout_task = asyncio.create_task(_consume_stdout())
        stderr_task = asyncio.create_task(_consume_stderr())
        activity_task = asyncio.create_task(_touch_running_subprocess())

        try:
            if self.config.timeout_seconds is not None:
                exit_code = await asyncio.wait_for(process.wait(), timeout=self.config.timeout_seconds)
            else:
                exit_code = await process.wait()
        except asyncio.TimeoutError:
            timed_out = True
            self.logger.error(
                "[ClaudeCodeAgent] timed out agent=%s session=%s timeout_seconds=%s",
                self.config.agent_name,
                self.binding.claude_session_id,
                self.config.timeout_seconds,
            )
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                exit_code = await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                exit_code = await process.wait()
        activity_task.cancel()
        try:
            await activity_task
        except asyncio.CancelledError:
            pass
        stream_results = await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        stream_error = None
        for result in stream_results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                stream_error = result
                self.logger.error(
                    "[ClaudeCodeAgent] stream reader failed",
                    exc_info=(type(result), result, result.__traceback__),
                )
                stderr_lines.append(f"Claude stream reader failed: {type(result).__name__}: {result}")
                break
        if structured_buffer.strip():
            await _ingest_structured_line(structured_buffer)
        wall_duration_ms = int((time.monotonic() - started_at) * 1000)
        if duration_ms is None:
            duration_ms = wall_duration_ms

        status = "completed" if exit_code == 0 and not timed_out and stream_error is None else "failed"
        usage_payload = dict(usage_totals or {}) or None
        if usage_payload is not None:
            if usage_payload.get("requests") in (None, 0):
                usage_payload["requests"] = max(usage_message_events, 1)
            if cost_usd is not None and usage_payload.get("cost_usd") is None:
                usage_payload["cost_usd"] = float(cost_usd)
            try:
                self.logger.info(
                    "[ClaudeCodeAgent] usage summary agent=%s session=%s requested_model=%s resolved_model=%s "
                    "requests=%s input=%s output=%s thinking=%s cache_read=%s cache_write_total=%s "
                    "cache_5m=%s cache_1h=%s cost_usd=%s duration_ms=%s api_duration_ms=%s",
                    self.config.agent_name,
                    self.binding.claude_session_id,
                    self.config.model,
                    resolved_model,
                    usage_payload.get("requests"),
                    usage_payload.get("input_tokens"),
                    usage_payload.get("output_tokens"),
                    usage_payload.get("thinking_tokens"),
                    usage_payload.get("cache_read_tokens"),
                    usage_payload.get("cache_creation_tokens"),
                    ((usage_payload.get("cache_creation") or {}) if isinstance(usage_payload.get("cache_creation"), dict) else {}).get("ephemeral_5m_input_tokens"),
                    ((usage_payload.get("cache_creation") or {}) if isinstance(usage_payload.get("cache_creation"), dict) else {}).get("ephemeral_1h_input_tokens"),
                    usage_payload.get("cost_usd", cost_usd),
                    duration_ms,
                    api_duration_ms,
                )
            except Exception:
                pass

        error_message = None
        if status != "completed":
            if timed_out:
                error_message = (
                    f"Claude exceeded timeout of {self.config.timeout_seconds}s"
                    if self.config.timeout_seconds is not None
                    else "Claude turn timed out"
                )
            else:
                error_message = stderr_lines[-1] if stderr_lines else f"Claude exited with code {exit_code}"

        failure_diagnostics: dict[str, Any] = {}
        if status != "completed":
            raw_result_type = ""
            if isinstance(raw_result_event, Mapping):
                raw_result_type = str(
                    raw_result_event.get("type")
                    or raw_result_event.get("subtype")
                    or raw_result_event.get("event")
                    or ""
                ).strip()
            if timed_out and raw_result_event is None:
                reason = "timeout_waiting_for_process_result"
                interpretation = (
                    "Claude Code was still running at the timeout and did not emit a final "
                    "result event before termination. Inspect stdout/stderr tails and "
                    "executive_journal_tail for the last useful progress signal."
                )
            elif timed_out:
                reason = "timeout_after_result_event"
                interpretation = (
                    "Claude Code emitted a result event but the process did not exit before "
                    "the timeout, which points to post-result cleanup or subprocess shutdown."
                )
            elif stream_error is not None:
                reason = "stream_reader_failed"
                interpretation = "The stdout/stderr reader failed while consuming Claude Code output."
            else:
                reason = "nonzero_exit"
                interpretation = "Claude Code exited non-zero; inspect stderr_tail and raw_result_event."
            failure_diagnostics = {
                "reason": reason,
                "interpretation": interpretation,
                "timed_out": bool(timed_out),
                "timeout_seconds": self.config.timeout_seconds,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "api_duration_ms": api_duration_ms,
                "delta_count": delta_count,
                "stdout_line_count": len(raw_output_lines),
                "stderr_line_count": len(stderr_lines),
                "stdout_tail": [_tail_text(line) for line in raw_output_lines[-3:]],
                "stderr_tail": [_tail_text(line) for line in stderr_lines[-5:]],
                "final_text_tail": _tail_text(final_text),
                "raw_result_event_seen": raw_result_event is not None,
                "raw_result_event_type": raw_result_type or None,
                "raw_result_event": _compact_jsonish(raw_result_event) if raw_result_event else None,
                "structured_events_count": len(structured_events),
                "structured_events_tail": _compact_jsonish(structured_events[-3:]) if structured_events else [],
                "executive_journal_count": len(executive_journal),
                "executive_journal_tail": _compact_jsonish(executive_journal[-5:]) if executive_journal else [],
                "usage": dict(usage_payload or {}),
                "requested_model": self.config.model,
                "resolved_model": resolved_model,
                "resolved_from_stream": bool(resolved_from_stream),
            }
            self.logger.error(
                "[ClaudeCodeAgent] failure diagnostics agent=%s session=%s reason=%s "
                "timed_out=%s exit_code=%s duration_ms=%s stdout_lines=%s stderr_lines=%s "
                "delta_count=%s raw_result_event_seen=%s last_stdout=%s last_stderr=%s",
                self.config.agent_name,
                self.binding.claude_session_id,
                failure_diagnostics.get("reason"),
                timed_out,
                exit_code,
                duration_ms,
                len(raw_output_lines),
                len(stderr_lines),
                delta_count,
                raw_result_event is not None,
                (failure_diagnostics.get("stdout_tail") or [None])[-1],
                (failure_diagnostics.get("stderr_tail") or [None])[-1],
            )

        return ClaudeCodeRunResult(
            status=status,
            session_id=self.binding.claude_session_id,
            final_text=final_text,
            delta_count=delta_count,
            exit_code=exit_code,
            stderr_lines=stderr_lines,
            raw_output_lines=raw_output_lines,
            turn_kind=kind,
            agent_name=self.config.agent_name,
            provider=CLAUDE_CODE_PROVIDER,
            requested_model=self.config.model,
            model=resolved_model,
            usage=usage_payload,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            api_duration_ms=api_duration_ms,
            raw_result_event=raw_result_event,
            resolved_from_stream=resolved_from_stream,
            error_message=error_message,
            timed_out=timed_out,
            timeout_seconds=self.config.timeout_seconds,
            failure_diagnostics=failure_diagnostics,
            structured_events=structured_events,
            executive_journal=executive_journal,
        )

    async def run_turn(
        self,
        prompt: str,
        *,
        kind: ClaudeCodeTurnKind = "regular",
        resume_existing: bool = False,
    ) -> ClaudeCodeRunResult:
        workspace_path = self.config.workspace_path
        if self.config.workspace_config is not None:
            from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.workspace import (
                prepare_claude_code_workspace,
            )

            prepared = prepare_claude_code_workspace(
                workspace_path,
                self.config.workspace_config,
            )
            self.logger.info(
                "[ClaudeCodeAgent] workspace prepared agent=%s workspace=%s files=%s mcp_servers=%s",
                self.config.agent_name,
                prepared.get("workspace_path"),
                prepared.get("written_files"),
                prepared.get("mcp_servers"),
            )
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

        try:
            result = await self._run_accountable_turn(
                prompt,
                kind=kind,
                resume_existing=resume_existing,
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

        if result.status == "completed":
            await self._emit_step(
                step=self.config.step_name,
                status="completed",
                title=f"Completed {self.config.agent_name}",
                data={
                    **metadata,
                    "delta_count": result.delta_count,
                    "exit_code": result.exit_code,
                    "model": result.model,
                    "requested_model": result.requested_model,
                    "cost_usd": result.cost_usd,
                    "usage": dict(result.usage or {}),
                    "structured_events": list(result.structured_events),
                    "executive_journal": list(result.executive_journal),
                },
            )
        else:
            self.logger.error(
                "[ClaudeCodeAgent] run failed agent=%s session=%s kind=%s exit_code=%s timed_out=%s "
                "last_stderr=%s raw_result_event=%s failure_reason=%s",
                self.config.agent_name,
                self.binding.claude_session_id,
                kind,
                result.exit_code,
                result.timed_out,
                (result.stderr_lines[-1] if result.stderr_lines else None),
                result.raw_result_event,
                (result.failure_diagnostics or {}).get("reason"),
            )
            await self._emit_final_error(
                message=result.error_message or f"Claude exited with code {result.exit_code}",
                kind=kind,
                exit_code=result.exit_code,
                stderr_lines=result.stderr_lines,
                resume_existing=resume_existing,
                raw_result_event=result.raw_result_event,
                timed_out=result.timed_out,
                timeout_seconds=result.timeout_seconds,
                failure_diagnostics=result.failure_diagnostics,
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
            "agent": self.config.agent_name,
            "agent_name": self.config.agent_name,
            "turn_kind": kind,
            "resume_existing": resume_existing,
            "claude_session_id": self.binding.claude_session_id,
            "workspace_path": str(self.config.workspace_path),
            "allowed_tools": list(self.config.allowed_tools),
            "additional_directories": [str(path) for path in self.config.additional_directories],
            "requested_model": self.config.model,
            "permission_mode": self.config.permission_mode,
            "timeout_seconds": self.config.timeout_seconds,
            "structured_output_prefixes": list(self.config.structured_output_prefixes),
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
        raw_result_event: dict[str, Any] | None = None,
        timed_out: bool = False,
        timeout_seconds: float | None = None,
        failure_diagnostics: dict[str, Any] | None = None,
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
                "last_stderr_line": stderr_lines[-1] if stderr_lines else None,
                "raw_result_event": raw_result_event,
                "timed_out": timed_out,
                "timeout_seconds": timeout_seconds,
                "failure_diagnostics": dict(failure_diagnostics or {}),
            },
        )


def _binding_from_request_context(
    request_context: ExternalEventPayload,
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
