# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import time
import mimetypes
import pathlib
from typing import Awaitable, Callable, List, Optional


class CodegenChanneledStreamingWidget:
    """
    Stream a string-valued JSON field selected by an xpath into a canvas + subsystem channel.
    """

    def __init__(
        self,
        *,
        emit_delta: Callable[..., Awaitable[None]],
        agent: str,
        artifact_name: str,
        stream_xpath: str,
        execution_id: Optional[str] = None,
        tool_id_xpath: Optional[str] = None,
        tool_id_value: Optional[str] = None,
        subsystem_marker: str = "subsystem",
        subsystem_sub_type: str = "code_exec.code",
        subsystem_format: str = "text",
        subsystem_title: str = "Generated Code",
        subsystem_language: Optional[str] = "python",
        fence_language: Optional[str] = None,
    ):
        self.emit_delta = emit_delta
        self.agent = agent
        self.artifact_name = artifact_name
        self.execution_id = execution_id
        self.subsystem_marker = subsystem_marker
        self.subsystem_sub_type = subsystem_sub_type
        self.subsystem_format = subsystem_format
        self.subsystem_title = subsystem_title
        self.subsystem_language = subsystem_language
        self.fence_language = fence_language

        self.stream_xpath = (stream_xpath or "").strip(".")
        parts = [p for p in self.stream_xpath.split(".") if p]
        self.stream_parent = parts[:-1]
        self.stream_key = parts[-1] if parts else ""

        self.tool_id_xpath = (tool_id_xpath or "").strip(".")
        tool_parts = [p for p in self.tool_id_xpath.split(".") if p]
        self.tool_id_parent = tool_parts[:-1]
        self.tool_id_key = tool_parts[-1] if tool_parts else ""
        self.tool_id_value = tool_id_value
        self.current_tool_id: Optional[str] = None

        self.in_string = False
        self.escaping = False
        self.unicode_mode = False
        self.unicode_buf = ""

        self.reading_key = False
        self.current_key = ""
        self.last_key: Optional[str] = None
        self.expecting_value = False
        self.active_key: Optional[str] = None
        self.active_value_buf = ""

        self.capturing_tool_id = False
        self.tool_value_buf = ""

        self.path_stack: List[Optional[str]] = []
        self.streaming_code = False

        self.started = False
        self.index = 0
        self.subsystem_started = False
        self.subsystem_index = 0
        self.contract_index = 0
        self.status_index = 0
        self.timings = {"codegen": None, "exec": None}
        self.program_name: Optional[str] = None
        self.current_status: Optional[str] = None
        self.event_log: List[dict] = []
        self.captured_code: str = ""
        self.contract_emitted = False
        self.program_emitted = False
        self.params_buffer: str = ""
        self.params_parsed = False

    def set_execution_id(self, execution_id: Optional[str]) -> None:
        if execution_id:
            self.execution_id = execution_id
        self._log_event("set_execution_id", execution_id=execution_id)

    def set_timings(self, *, codegen_ms: Optional[int] = None, exec_ms: Optional[int] = None) -> None:
        if codegen_ms is not None:
            self.timings["codegen"] = codegen_ms
        if exec_ms is not None:
            self.timings["exec"] = exec_ms
        self._log_event("set_timings", codegen_ms=codegen_ms, exec_ms=exec_ms)

    def _log_event(self, name: str, **data: object) -> None:
        self.event_log.append({"ts": time.time(), "event": name, **data})

    async def emit_reasoning(self, text: str) -> None:
        if not text:
            return
        self._log_event("emit_reasoning", size=len(text))
        await self._emit_subsystem_delta(
            text=text,
            index=0,
            sub_type="code_exec.objective",
            fmt="text",
            title="Objective",
            language=None,
        )

    async def emit_program_name(self, name: str) -> None:
        name = (name or "").strip()
        if not name or name == self.program_name:
            return
        self._log_event("emit_program_name", program_name=name)
        self.program_name = name
        await self._emit_subsystem_delta(
            text=name,
            index=0,
            sub_type="code_exec.program.name",
            fmt="text",
            title="Program Name",
            language=None,
        )
        self.program_emitted = True

    def get_code(self) -> str:
        return self.captured_code

    def set_code(self, code: str) -> None:
        self.captured_code = code or ""

    async def capture_params(self, params: dict) -> None:
        if not isinstance(params, dict):
            return
        prog_name = (params.get("prog_name") or "").strip()
        if prog_name and not self.program_emitted:
            await self.emit_program_name(prog_name)
            self.program_emitted = True
        contract_spec = params.get("contract")
        if contract_spec and not self.contract_emitted:
            try:
                from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import build_exec_output_contract
                contract, _, err = build_exec_output_contract(contract_spec)
                if contract and not err:
                    await self.emit_contract(contract)
                    self.contract_emitted = True
            except Exception:
                return

    async def feed_json(
        self,
        text: Optional[str] = None,
        *,
        completed: bool = False,
        **_kwargs,
    ) -> None:
        if self.params_parsed:
            return
        chunk = text
        if chunk is None:
            chunk = text or ""
        if chunk:
            self.params_buffer += chunk
        await self._try_parse_params(final=completed)

    async def _try_parse_params(self, *, final: bool = False) -> None:
        if self.params_parsed:
            return
        raw = (self.params_buffer or "").strip()
        if not raw:
            if final:
                self.params_parsed = True
            return
        # Strip ```json fences if present.
        if "```" in raw:
            fence = raw
            start = fence.find("```")
            if start >= 0:
                fence = fence[start + 3 :]
                fence = fence.lstrip()
                if fence.startswith("json"):
                    fence = fence[4:]
                end = fence.rfind("```")
                if end >= 0:
                    fence = fence[:end]
                raw = fence.strip()
        try:
            data = json.loads(raw)
        except Exception:
            if final:
                self.params_parsed = True
            return
        tool_call = data.get("tool_call") if isinstance(data, dict) else None
        if not isinstance(tool_call, dict):
            if final:
                self.params_parsed = True
            return
        tool_id = (tool_call.get("tool_id") or "").strip()
        if self.tool_id_value and tool_id != self.tool_id_value and not self.captured_code:
            if final:
                self.params_parsed = True
            return
        params = tool_call.get("params")
        if isinstance(params, dict):
            try:
                await self.capture_params(params)
            except Exception:
                pass
        self.params_parsed = True

    def _path_has(self, keys: List[str]) -> bool:
        stack = [k for k in self.path_stack if k]
        if len(stack) < len(keys):
            return False
        return stack[-len(keys) :] == list(keys)

    def _matches_stream_path(self) -> bool:
        if not self.stream_key:
            return False
        if self.last_key != self.stream_key:
            return False
        if self.stream_parent and not self._path_has(self.stream_parent):
            return False
        if self.tool_id_value:
            return self.current_tool_id == self.tool_id_value
        return True

    def _matches_tool_id_path(self) -> bool:
        if not self.tool_id_key or self.last_key != self.tool_id_key:
            return False
        if self.tool_id_parent and not self._path_has(self.tool_id_parent):
            return False
        return True

    def _subsystem_artifact_name(self, sub_type: str) -> str:
        if not sub_type:
            return self.artifact_name
        suffix = sub_type.replace(".", "_")
        return f"{self.artifact_name}.{suffix}"

    async def _emit_subsystem_delta(
        self,
        *,
        text: str,
        index: int,
        sub_type: str,
        fmt: str,
        title: str,
        completed: bool = False,
        language: Optional[str] = None,
    ) -> None:
        await self.emit_delta(
            text=text,
            index=index,
            marker=self.subsystem_marker,
            agent=self.agent,
            format=fmt,
            artifact_name=self._subsystem_artifact_name(sub_type),
            title=title,
            sub_type=sub_type,
            execution_id=self.execution_id,
            completed=completed,
            language=language,
        )

    async def _emit_chunk(self, text: str) -> None:
        if not text:
            return
        if self.current_status != "gen":
            await self.send_status(status="gen")
        await self.emit_delta(
            text=text,
            index=self.subsystem_index,
            marker=self.subsystem_marker,
            agent=self.agent,
            format=self.subsystem_format,
            artifact_name=self._subsystem_artifact_name(self.subsystem_sub_type),
            title=self.subsystem_title,
            sub_type=self.subsystem_sub_type,
            execution_id=self.execution_id,
            language=self.subsystem_language,
        )
        self.subsystem_started = True
        self.subsystem_index += 1

    async def feed_raw(self, chunk: str) -> None:
        if not chunk:
            return
        self._log_event("feed_raw", size=len(chunk))
        self.captured_code += chunk
        await self._emit_chunk(chunk)

    async def feed_code(
        self,
            text: Optional[str] = None,
        *,
        completed: bool = False,
        **_kwargs,
    ) -> None:
        if completed:
            await self.finish()
            return
        chunk = text
        if chunk is None:
            chunk = text or ""
        await self.feed_raw(chunk)

    async def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self._log_event("feed", size=len(chunk))

        for ch in chunk:
            if self.in_string:
                if self.unicode_mode:
                    self.unicode_buf += ch
                    if len(self.unicode_buf) == 4:
                        try:
                            code = int(self.unicode_buf, 16)
                            decoded = chr(code)
                        except Exception:
                            decoded = ""
                        if self.reading_key:
                            self.current_key += decoded
                        elif self.active_key:
                            if self.capturing_tool_id:
                                self.tool_value_buf += decoded
                            self.active_value_buf += decoded
                            if self.streaming_code and len(self.active_value_buf) >= 256:
                                await self._emit_chunk(self.active_value_buf)
                                self.active_value_buf = ""
                        self.unicode_mode = False
                        self.unicode_buf = ""
                    continue

                if self.escaping:
                    self.escaping = False
                    decoded = self._decode_escape(ch)
                    if decoded is None:
                        continue
                    if self.reading_key:
                        self.current_key += decoded
                    elif self.active_key:
                        if self.capturing_tool_id:
                            self.tool_value_buf += decoded
                        self.active_value_buf += decoded
                        if self.streaming_code and len(self.active_value_buf) >= 256:
                            await self._emit_chunk(self.active_value_buf)
                            self.active_value_buf = ""
                    continue

                if ch == "\\":
                    self.escaping = True
                    continue

                if ch == '"':
                    self.in_string = False
                    if self.reading_key:
                        self.reading_key = False
                        self.last_key = self.current_key
                        self.current_key = ""
                    elif self.active_key:
                        if self.streaming_code and self.active_value_buf:
                            await self._emit_chunk(self.active_value_buf)
                            self.active_value_buf = ""
                        if self.capturing_tool_id:
                            if self._matches_tool_id_path():
                                self.current_tool_id = self.tool_value_buf
                            self.tool_value_buf = ""
                            self.capturing_tool_id = False
                        if self.last_key == self.stream_key and self.streaming_code:
                            self.streaming_code = False
                        self.active_key = None
                    continue

                if self.reading_key:
                    self.current_key += ch
                elif self.active_key:
                    if self.capturing_tool_id:
                        self.tool_value_buf += ch
                    self.active_value_buf += ch
                    if self.streaming_code and len(self.active_value_buf) >= 256:
                        await self._emit_chunk(self.active_value_buf)
                        self.active_value_buf = ""
                continue

            if ch == "{":
                if self.expecting_value and self.last_key:
                    self.path_stack.append(self.last_key)
                else:
                    self.path_stack.append(None)
                self.expecting_value = False
                continue

            if ch == "}":
                if self.path_stack:
                    self.path_stack.pop()
                self.expecting_value = False
                continue

            if ch == "[":
                if self.expecting_value and self.last_key:
                    self.path_stack.append(self.last_key)
                else:
                    self.path_stack.append(None)
                self.expecting_value = False
                continue

            if ch == "]":
                if self.path_stack:
                    self.path_stack.pop()
                self.expecting_value = False
                continue

            if ch == '"':
                self.in_string = True
                if not self.expecting_value:
                    self.reading_key = True
                    self.current_key = ""
                else:
                    if self.last_key:
                        self.active_key = self.last_key
                        self.active_value_buf = ""
                        self.streaming_code = self._matches_stream_path()
                        self.capturing_tool_id = self._matches_tool_id_path()
                        self.tool_value_buf = ""
                continue

            if ch == ":":
                if self.last_key is not None:
                    self.expecting_value = True
                continue

            if ch == ",":
                self.expecting_value = False
                continue

        if self.streaming_code and self.active_value_buf:
            await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""

    def _decode_escape(self, ch: str) -> Optional[str]:
        if ch == "n":
            return "\n"
        if ch == "r":
            return "\r"
        if ch == "t":
            return "\t"
        if ch == "b":
            return "\b"
        if ch == "f":
            return "\f"
        if ch == "u":
            self.unicode_mode = True
            self.unicode_buf = ""
            return None
        if ch in ('"', "\\", "/"):
            return ch
        return ch

    async def finish(self) -> None:
        self._log_event("finish")
        if not self.started and not self.streaming_code and not self.subsystem_started:
            return
        if self.streaming_code and self.active_value_buf:
            await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""
        self.index += 1
        if self.subsystem_started:
            await self.emit_delta(
                text="",
                index=self.subsystem_index,
                marker=self.subsystem_marker,
                agent=self.agent,
                format=self.subsystem_format,
                artifact_name=self._subsystem_artifact_name(self.subsystem_sub_type),
                title=self.subsystem_title,
                sub_type=self.subsystem_sub_type,
                execution_id=self.execution_id,
                completed=True,
            )
            self.subsystem_index += 1

    async def emit_contract(self, contract: dict) -> None:
        self._log_event("emit_contract")
        contract_items = []
        for name, spec in (contract or {}).items():
            if not isinstance(spec, dict):
                continue
            raw_filename = spec.get("filename") or ""
            leaf = pathlib.Path(raw_filename).name if isinstance(raw_filename, str) else ""
            display_name = pathlib.Path(leaf).stem if leaf else str(name)
            if not display_name:
                display_name = str(name)
            mime = (spec.get("mime") or "").strip()
            if not mime:
                mime = mimetypes.guess_type(leaf)[0] or ""
            contract_items.append(
                {
                    "artifact_name": display_name,
                    "description": spec.get("description") or "",
                    "mime": mime,
                    "filename": leaf,
                }
            )
        payload = {
            "execution_id": self.execution_id,
            "contract": contract_items,
        }
        await self._emit_subsystem_delta(
            text=json.dumps(payload, ensure_ascii=True),
            index=self.contract_index,
            sub_type="code_exec.contract",
            fmt="json",
            title="Execution Contract",
            language="json",
        )
        self.contract_index += 1
        await self.send_status(status="exec")
        self.contract_emitted = True

    async def emit_status(self, *, status: str, error: Optional[dict] = None) -> None:
        self._log_event("emit_status", status=status, error=bool(error))
        await self.send_status(status=status, error=error)

    async def send_status(self, *, status: str, error: Optional[dict] = None) -> None:
        self.current_status = status
        self._log_event("send_status", status=status, error=bool(error))
        payload = {
            "status": status,
            "timings": {
                "codegen": self.timings.get("codegen"),
                "exec": self.timings.get("exec"),
            },
        }
        if error:
            payload["error"] = error
        await self._emit_subsystem_delta(
            text=json.dumps(payload, ensure_ascii=True),
            index=0,
            sub_type="code_exec.status",
            fmt="json",
            title="Execution Status",
            language="json",
        )


class DecisionExecCodeStreamer(CodegenChanneledStreamingWidget):
    def __init__(
        self,
        *,
        emit_delta: Callable[..., Awaitable[None]],
        agent: str,
        artifact_name: str,
        execution_id: Optional[str] = None,
    ):
        super().__init__(
            emit_delta=emit_delta,
            agent=agent,
            artifact_name=artifact_name,
            execution_id=execution_id,
            stream_xpath="",
            tool_id_xpath="",
            tool_id_value="exec_tools.execute_code_python",
        )


class CodegenJsonCodeStreamer(CodegenChanneledStreamingWidget):
    def __init__(
        self,
        *,
        agent: str,
        artifact_name: str,
        emit_delta: Callable[..., Awaitable[None]],
        execution_id: Optional[str] = None,
    ):
        super().__init__(
            emit_delta=emit_delta,
            agent=agent,
            artifact_name=artifact_name,
            execution_id=execution_id,
            stream_xpath="files.content",
        )
