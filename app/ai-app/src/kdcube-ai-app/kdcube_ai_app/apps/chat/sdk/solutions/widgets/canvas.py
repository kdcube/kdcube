# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
from typing import Awaitable, Callable, Dict, List, Optional, Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.common import infer_format_from_path
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.artifacts import normalize_relpath
from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module


class ToolContentStreamerBase:
    """
    Stream tool_call.params.content from decision JSON into UI.
    - Streams react.write only when action == call_tool.
    - Streams rendering_tools.write_* only when content is literal (not ref:).
    """

    def __init__(
        self,
        *,
        emit_delta: Callable[..., Awaitable[None]],
        agent: str,
        artifact_name: str,
        sources_list: Optional[List[Dict[str, object]]] = None,
        sources_getter: Optional[Callable[[], List[Dict[str, object]]]] = None,
        turn_id: Optional[str] = None,
        stream_tool_id: str = "react.write",
        write_tool_prefix: str = "rendering_tools.write_",
        stream_xpath: str = "tool_call.params.content",
    ) -> None:
        self.emit_delta = emit_delta
        self.agent = agent
        self.default_artifact_name = artifact_name
        self.record_artifact_name: Optional[str] = None
        self.sources_getter = sources_getter
        self.turn_id = turn_id or ""
        self.stream_tool_id = stream_tool_id
        self.write_tool_prefix = write_tool_prefix

        self.stream_xpath = stream_xpath
        self.path_xpath = "tool_call.params.path"
        self.channel_xpath = "tool_call.params.channel"
        self.kind_xpath = "tool_call.params.kind"
        self.format_xpath = "tool_call.params.format"
        self.tool_id_xpath = "tool_call.tool_id"
        self.action_xpath = "action"

        self.stream_parent, self.stream_key = self._split_path(self.stream_xpath)
        self.path_parent, self.path_key = self._split_path(self.path_xpath)
        self.channel_parent, self.channel_key = self._split_path(self.channel_xpath)
        self.kind_parent, self.kind_key = self._split_path(self.kind_xpath)
        self.format_parent, self.format_key = self._split_path(self.format_xpath)
        self.tool_id_parent, self.tool_id_key = self._split_path(self.tool_id_xpath)
        self.action_parent, self.action_key = self._split_path(self.action_xpath)

        self.current_tool_id: Optional[str] = None
        self.action_value: Optional[str] = None
        self.current_format: str = "markdown"
        self.current_format_explicit: bool = False
        self.current_channel: str = "canvas"
        self.current_kind: str = "display"
        self.current_path: Optional[str] = None

        self.in_string = False
        self.escaping = False
        self.unicode_mode = False
        self.unicode_buf = ""
        self.pending_string_quote = False
        self.pending_string_quote_ws = ""

        self.reading_key = False
        self.current_key = ""
        self.last_key: Optional[str] = None
        self.expecting_value = False
        self.active_key: Optional[str] = None
        self.active_value_buf = ""
        self.stream_value_active = False
        self.pending_ref_check = False
        self.ref_check_buf = ""
        self.skip_stream_value = False
        self.channel_pending = False
        self.channel_seen = False
        self.pending_channel_stream = False
        self.pending_channel_buf = ""
        self.pending_string_quote = False
        self.pending_string_quote_ws = ""

        self.capturing_tool_id = False
        self.tool_value_buf = ""

        self.path_stack: List[Optional[str]] = []
        self.streaming_content = False
        self.started = False
        self.index = 0

        self.citation_map = citations_module.build_citation_map_from_sources(sources_list or [])
        self.citation_state = citations_module.CitationStreamState()
        self._sources_sig = self._sources_signature(sources_list or [])

    def _sources_signature(self, sources_list: List[Dict[str, object]]) -> tuple[int, int, int]:
        if not sources_list:
            return (0, 0, 0)
        sids: List[int] = []
        for row in sources_list:
            if not isinstance(row, dict):
                continue
            try:
                sid = int(row.get("sid") or 0)
            except Exception:
                sid = 0
            if sid > 0:
                sids.append(sid)
        if not sids:
            return (0, 0, 0)
        return (len(sids), min(sids), max(sids))

    def _maybe_refresh_sources(self) -> None:
        if not self.sources_getter:
            return
        try:
            sources = self.sources_getter() or []
        except Exception:
            sources = []
        if not sources:
            return
        sig = self._sources_signature(sources)
        if sig != self._sources_sig:
            self.update_sources(sources)

    def _channel_allowed(self) -> bool:
        if not self.current_channel:
            return True
        ch = self.current_channel.strip().lower()
        return ch in {"canvas", "timeline", "timeline_text"}

    def _requires_channel(self) -> bool:
        return False

    def _start_tool_call(self) -> None:
        self.channel_seen = False
        self.channel_pending = False
        self.pending_channel_stream = False
        self.pending_channel_buf = ""
        self.current_tool_id = None
        self.tool_value_buf = ""
        self.capturing_tool_id = False
        self.current_channel = "canvas"
        self.current_kind = "display"
        self.current_format = "markdown"
        self.current_format_explicit = False
        self.current_path = None
        self.record_artifact_name = None

    async def _flush_pending_channel_stream(self, *, force_default: bool = False) -> None:
        if not self.pending_channel_stream:
            return
        if not self.channel_seen:
            if not force_default:
                return
            if not self.current_channel:
                self.current_channel = "canvas"
        if self._channel_allowed() and self.pending_channel_buf:
            await self._emit_chunk(self.pending_channel_buf)
        self.pending_channel_stream = False
        self.pending_channel_buf = ""

    def update_sources(self, sources_list: Optional[List[Dict[str, object]]] = None) -> None:
        """Refresh citation map for streaming outputs."""
        try:
            sources = sources_list or []
            self.citation_map = citations_module.build_citation_map_from_sources(sources)
            self._sources_sig = self._sources_signature(sources)
            if self.citation_state is None:
                self.citation_state = citations_module.CitationStreamState()
        except Exception:
            pass

    def _split_path(self, path: str) -> tuple[List[str], str]:
        cleaned = (path or "").strip(".")
        parts = [p for p in cleaned.split(".") if p]
        if not parts:
            return [], ""
        return parts[:-1], parts[-1]

    def _path_has(self, keys: List[str]) -> bool:
        stack = [k for k in self.path_stack if k]
        if len(stack) < len(keys):
            return False
        return stack[-len(keys):] == list(keys)

    def _matches_stream_path(self) -> bool:
        if not self.stream_key or self.last_key != self.stream_key:
            return False
        if self.stream_parent and not self._path_has(self.stream_parent):
            return False
        if not self._tool_allows_stream():
            return False
        if self.action_value is not None and (self.action_value or "").strip() != "call_tool":
            return False
        return True

    def _tool_allows_stream(self) -> bool:
        return False

    def _matches_path_path(self) -> bool:
        if not self.path_key or self.last_key != self.path_key:
            return False
        if self.path_parent and not self._path_has(self.path_parent):
            return False
        return True

    def _matches_channel_path(self) -> bool:
        if not self.channel_key or self.last_key != self.channel_key:
            return False
        if self.channel_parent and not self._path_has(self.channel_parent):
            return False
        return True

    def _matches_kind_path(self) -> bool:
        if not self.kind_key or self.last_key != self.kind_key:
            return False
        if self.kind_parent and not self._path_has(self.kind_parent):
            return False
        return True

    def _matches_format_path(self) -> bool:
        if not self.format_key or self.last_key != self.format_key:
            return False
        if self.format_parent and not self._path_has(self.format_parent):
            return False
        return True

    def _matches_tool_id_path(self) -> bool:
        if not self.tool_id_key or self.last_key != self.tool_id_key:
            return False
        if self.tool_id_parent and not self._path_has(self.tool_id_parent):
            return False
        return True

    def _matches_action_path(self) -> bool:
        if not self.action_key or self.last_key != self.action_key:
            return False
        if self.action_parent and not self._path_has(self.action_parent):
            return False
        return True

    def _emit_artifact_name(self) -> str:
        return self.record_artifact_name or self.default_artifact_name

    def _is_timeline_channel(self) -> bool:
        ch = (self.current_channel or "").strip().lower()
        return ch in {"timeline", "timeline_text"}

    def _format_from_path(self, norm_path: str) -> str:
        return infer_format_from_path(norm_path)

    def _normalize_declared_format(self, value: str) -> Optional[str]:
        raw = (value or "").strip().lower()
        if not raw:
            return None
        aliases = {
            "md": "markdown",
            "markdown": "markdown",
            "htm": "html",
            "html": "html",
            "txt": "text",
            "text": "text",
            "yml": "yaml",
            "yaml": "yaml",
            "json": "json",
            "xml": "xml",
            "svg": "svg",
            "mermaid": "mermaid",
            "mmd": "mermaid",
        }
        return aliases.get(raw, raw)

    async def _emit_chunk(self, text: str) -> None:
        if not text:
            return
        if self.current_channel == "internal":
            return
        self._maybe_refresh_sources()
        rendered = citations_module.replace_citation_tokens_streaming_stateful(
            text,
            self.citation_map,
            self.citation_state,
            html=(self.current_format == "html"),
        )
        if not rendered:
            return
        marker = "timeline_text" if self.current_tool_id == self.stream_tool_id and self._is_timeline_channel() else "canvas"
        await self.emit_delta(
            text=rendered,
            index=self.index,
            marker=marker,
            agent=self.agent,
            format=self.current_format,
            artifact_name=self._emit_artifact_name(),
            completed=False,
        )
        self.index += 1
        self.started = True

    async def _append_active_text(self, text: str) -> None:
        if not text or not self.active_key:
            return
        if self.capturing_tool_id:
            self.tool_value_buf += text
        if self.skip_stream_value and self.stream_value_active:
            return
        self.active_value_buf += text
        if self.pending_ref_check and self.stream_value_active:
            if len(self.ref_check_buf) < 4:
                need = 4 - len(self.ref_check_buf)
                self.ref_check_buf += text[:need]
                if len(self.ref_check_buf) >= 4:
                    if self.ref_check_buf.lower().startswith("ref:"):
                        self.skip_stream_value = True
                        self.pending_ref_check = False
                        self.streaming_content = False
                        self.active_value_buf = ""
                    else:
                        self.pending_ref_check = False
                        if self.channel_pending and not self.channel_seen:
                            self.streaming_content = False
                        else:
                            self.streaming_content = True
                            if self.active_value_buf:
                                await self._emit_chunk(self.active_value_buf)
                                self.active_value_buf = ""
        elif self.streaming_content and len(self.active_value_buf) >= 256:
            await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""

    async def _close_active_string(self) -> None:
        if not self.active_key:
            return
        if self.capturing_tool_id:
            self.current_tool_id = self.tool_value_buf
            self.tool_value_buf = ""
            self.capturing_tool_id = False
        if self._matches_action_path():
            self.action_value = self.active_value_buf.strip()
        if self._matches_path_path():
            path_val = self.active_value_buf.strip()
            if path_val:
                norm_path = normalize_relpath(path_val, turn_id=self.turn_id)
                self.current_path = norm_path
                self.record_artifact_name = norm_path
                if not self.current_format_explicit:
                    self.current_format = self._format_from_path(norm_path)
                    print(f"ContentStreamer: Inferred format {self.current_format} from path {norm_path}")
        if self._matches_channel_path():
            channel_val = self.active_value_buf.strip().lower()
            if channel_val:
                if channel_val == "timeline":
                    channel_val = "timeline_text"
                self.current_channel = channel_val
                self.channel_seen = True
                await self._flush_pending_channel_stream()
        if self._matches_format_path():
            format_val = self._normalize_declared_format(self.active_value_buf)
            if format_val:
                self.current_format = format_val
                self.current_format_explicit = True
        if self._matches_kind_path():
            kind_val = self.active_value_buf.strip().lower()
            if kind_val:
                self.current_kind = kind_val
        if self.stream_value_active:
            if self.pending_ref_check:
                if self.active_value_buf.lower().startswith("ref:"):
                    self.skip_stream_value = True
                    self.streaming_content = False
                else:
                    if self.channel_pending and not self.channel_seen:
                        self.streaming_content = False
                    else:
                        self.streaming_content = True
                self.pending_ref_check = False
            if self.channel_pending and not self.channel_seen:
                if not self.skip_stream_value and self.active_value_buf:
                    self.pending_channel_buf = self.active_value_buf
                    self.pending_channel_stream = True
                self.channel_pending = False
                self.streaming_content = False
            if self.streaming_content and self.active_value_buf:
                await self._emit_chunk(self.active_value_buf)
        self.active_value_buf = ""
        self.streaming_content = False
        self.stream_value_active = False
        self.pending_ref_check = False
        self.ref_check_buf = ""
        self.skip_stream_value = False
        self.active_key = None
        self.pending_string_quote = False
        self.pending_string_quote_ws = ""

    async def _process_non_string_char(self, ch: str) -> None:
        if ch == '"':
            self.in_string = True
            self.pending_string_quote = False
            self.pending_string_quote_ws = ""
            if self.expecting_value:
                self.active_key = self.last_key
                self.active_value_buf = ""
                self.expecting_value = False
                self.stream_value_active = False
                self.streaming_content = False
                self.pending_ref_check = False
                self.ref_check_buf = ""
                self.skip_stream_value = False
                if self._matches_stream_path() and (not self._requires_channel() or self._channel_allowed()):
                    self.stream_value_active = True
                    self.pending_ref_check = True
                    if self._requires_channel() and not self.channel_seen:
                        self.channel_pending = True
                self.capturing_tool_id = self._matches_tool_id_path()
            else:
                self.reading_key = True
                self.current_key = ""
            return

        if ch in "{[":
            self.path_stack.append(self.last_key)
            if self.last_key == "tool_call":
                self._start_tool_call()
            self.last_key = None
            self.expecting_value = False
            return

        if ch in "}]":
            if self.path_stack:
                popped = self.path_stack.pop()
                if popped == "tool_call":
                    await self._flush_pending_channel_stream(force_default=True)
            self.last_key = None
            self.expecting_value = False
            return

        if ch == ":":
            if self.last_key is not None:
                self.expecting_value = True
            return

        if ch == ",":
            self.expecting_value = False
            return

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

    async def feed(self, chunk: str) -> None:
        if not chunk:
            return

        for ch in chunk:
            if self.in_string:
                if self.pending_string_quote and self.active_key and self.stream_value_active:
                    if ch in " \t\r\n":
                        self.pending_string_quote_ws += ch
                        continue
                    if ch in ",}]":
                        self.in_string = False
                        await self._close_active_string()
                        await self._process_non_string_char(ch)
                        continue
                    buffered = '"' + self.pending_string_quote_ws + ch
                    self.pending_string_quote = False
                    self.pending_string_quote_ws = ""
                    await self._append_active_text(buffered)
                    continue

                if self.unicode_mode:
                    self.unicode_buf += ch
                    if len(self.unicode_buf) == 4:
                        try:
                            decoded = chr(int(self.unicode_buf, 16))
                        except Exception:
                            decoded = ""
                        if self.reading_key:
                            self.current_key += decoded
                        elif self.active_key:
                            await self._append_active_text(decoded)
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
                        await self._append_active_text(decoded)
                    continue

                if ch == "\\":
                    self.escaping = True
                    continue

                if ch == '"':
                    if self.reading_key:
                        self.in_string = False
                        self.last_key = self.current_key
                        self.current_key = ""
                        self.reading_key = False
                    elif self.active_key:
                        if self.stream_value_active:
                            self.pending_string_quote = True
                            self.pending_string_quote_ws = ""
                            continue
                        self.in_string = False
                        await self._close_active_string()
                    continue

                if self.reading_key:
                    self.current_key += ch
                elif self.active_key:
                    await self._append_active_text(ch)
                continue

            await self._process_non_string_char(ch)

        if self.streaming_content and self.active_value_buf:
            await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""

    async def finish(self) -> None:
        if self.pending_string_quote and self.active_key:
            self.in_string = False
            await self._close_active_string()
        if self.streaming_content and self.active_value_buf:
            await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""
        if self.pending_channel_stream:
            await self._flush_pending_channel_stream(force_default=True)
        if not self.started:
            return
        if self.current_channel == "internal":
            return
        self._maybe_refresh_sources()
        flushed = citations_module.replace_citation_tokens_streaming_stateful(
            "",
            self.citation_map,
            self.citation_state,
            html=(self.current_format == "html"),
            flush=True,
        )
        if flushed:
            await self.emit_delta(
                text=flushed,
                index=self.index,
                marker="canvas",
                agent=self.agent,
                format=self.current_format,
                artifact_name=self._emit_artifact_name(),
                completed=False,
            )
            self.index += 1
        marker = "timeline_text" if self.current_tool_id == self.stream_tool_id and self._is_timeline_channel() else "canvas"
        await self.emit_delta(
            text="",
            index=self.index,
            marker=marker,
            agent=self.agent,
            format=self.current_format,
            artifact_name=self._emit_artifact_name(),
            completed=True,
        )


class ReactWriteContentStreamer(ToolContentStreamerBase):
    def _tool_allows_stream(self) -> bool:
        return self.current_tool_id == self.stream_tool_id

    def _requires_channel(self) -> bool:
        return True


class ReactPatchContentStreamer(ToolContentStreamerBase):
    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("stream_xpath", "tool_call.params.patch")
        super().__init__(**kwargs)

    def _tool_allows_stream(self) -> bool:
        return self.current_tool_id == self.stream_tool_id

    def _requires_channel(self) -> bool:
        return True

    def _format_from_path(self, norm_path: str) -> str:
        return "markdown"


class RenderingWriteContentStreamer(ToolContentStreamerBase):
    def _tool_allows_stream(self) -> bool:
        return bool(self.current_tool_id and self.current_tool_id.startswith(self.write_tool_prefix))


class TimelineStreamer:
    """
    Stream selected JSON string fields into timeline-related UI channels.
    - Streams root-level notes into timeline_text.
    - Streams final_answer into answer.
    """

    def __init__(
            self,
            *,
            emit_delta: Callable[..., Awaitable[None]],
            agent: str,
            sources_list: Optional[List[Dict[str, object]]] = None,
            sources_getter: Optional[Callable[[], List[Dict[str, object]]]] = None,
            stream_notes: bool = True,
            stream_final_answer: bool = True,
        stream_plan: bool = True,
        notes_xpath: str = "notes",
        final_answer_xpath: str = "final_answer",
        notes_marker: str = "timeline_text",
            final_answer_marker: str = "answer",
            notes_format: str = "markdown",
            final_answer_format: str = "markdown",
            notes_artifact_name: str = "timeline_text.react.decision",
            final_answer_artifact_name: str = "react.final_answer",
            final_answer_start_index: int = 0,
        plan_marker: str = "timeline_text",
        plan_format: str = "markdown",
        plan_artifact_name: str = "timeline_text.react.plan",
    ) -> None:
        self.emit_delta = emit_delta
        self.agent = agent
        self.sources_getter = sources_getter

        self.targets: List[Dict[str, Any]] = []
        if stream_notes:
            self.targets.append({
                "name": "notes",
                "xpath": notes_xpath,
                "marker": notes_marker,
                "format": notes_format,
                "artifact_name": notes_artifact_name,
                "use_citations": True,
                "index": 0,
                "started": False,
            })
        if stream_final_answer:
            self.targets.append({
                "name": "final_answer",
                "xpath": final_answer_xpath,
                "marker": final_answer_marker,
                "format": final_answer_format,
                "artifact_name": final_answer_artifact_name,
                "use_citations": True,
                "index": max(0, int(final_answer_start_index or 0)),
                "started": False,
            })
        if stream_plan:
            self.targets.append({
                "name": "plan",
                "xpath": "",
                "marker": plan_marker,
                "format": plan_format,
                "artifact_name": plan_artifact_name,
                "use_citations": True,
                "index": 0,
                "started": False,
            })

        for t in self.targets:
            parent, key = self._split_path(t["xpath"])
            t["parent"] = parent
            t["key"] = key
            t["buffer"] = ""
            t["pending"] = False
            t["deferred"] = False

        self.in_string = False
        self.escaping = False
        self.unicode_mode = False
        self.unicode_buf = ""
        self.pending_string_quote = False
        self.pending_string_quote_ws = ""

        self.reading_key = False
        self.current_key = ""
        self.last_key: Optional[str] = None
        self.expecting_value = False
        self.active_key: Optional[str] = None
        self.active_value_buf = ""

        self.path_stack: List[Optional[str]] = []
        self.streaming_target: Optional[Dict[str, Any]] = None

        self.action_xpath = "action"
        self.tool_id_xpath = "tool_call.tool_id"
        self.action_parent, self.action_key = self._split_path(self.action_xpath)
        self.tool_id_parent, self.tool_id_key = self._split_path(self.tool_id_xpath)
        self.action_value: Optional[str] = None
        self.tool_id_value: Optional[str] = None
        self.raw_json_buffer: str = ""

        self.citation_map = citations_module.build_citation_map_from_sources(sources_list or [])
        self.citation_states: Dict[str, citations_module.CitationStreamState] = {}
        for t in self.targets:
            if t.get("use_citations"):
                self.citation_states[t["name"]] = citations_module.CitationStreamState()
        self._sources_sig = self._sources_signature(sources_list or [])

    def _sources_signature(self, sources_list: List[Dict[str, object]]) -> tuple[int, int, int]:
        if not sources_list:
            return (0, 0, 0)
        sids: List[int] = []
        for row in sources_list:
            if not isinstance(row, dict):
                continue
            try:
                sid = int(row.get("sid") or 0)
            except Exception:
                sid = 0
            if sid > 0:
                sids.append(sid)
        if not sids:
            return (0, 0, 0)
        return (len(sids), min(sids), max(sids))

    def _maybe_refresh_sources(self) -> None:
        if not self.sources_getter:
            return
        try:
            sources = self.sources_getter() or []
        except Exception:
            sources = []
        if not sources:
            return
        sig = self._sources_signature(sources)
        if sig != self._sources_sig:
            self.update_sources(sources)

    def update_sources(self, sources_list: Optional[List[Dict[str, object]]] = None) -> None:
        """Refresh citation map for streaming outputs."""
        try:
            sources = sources_list or []
            self.citation_map = citations_module.build_citation_map_from_sources(sources)
            self._sources_sig = self._sources_signature(sources)
            for t in self.targets:
                if t.get("use_citations") and t.get("name") not in self.citation_states:
                    self.citation_states[t["name"]] = citations_module.CitationStreamState()
        except Exception:
            pass

    def has_started(self, name: str) -> bool:
        for t in self.targets:
            if t.get("name") == name:
                return bool(t.get("started"))
        return False

    def next_index(self, name: str) -> int:
        for t in self.targets:
            if t.get("name") == name:
                try:
                    return max(0, int(t.get("index") or 0))
                except Exception:
                    return 0
        return 0

    async def emit_full(self, name: str, text: str) -> None:
        if not text:
            return
        target = None
        for t in self.targets:
            if t.get("name") == name:
                target = t
                break
        if not target:
            return
        rendered = text
        if target.get("use_citations"):
            if target.get("format") == "html":
                rendered = citations_module.replace_html_citations(
                    text,
                    self.citation_map,
                    keep_unresolved=True,
                    first_only=False,
                )
            else:
                rendered = citations_module.replace_citation_tokens_batch(text, self.citation_map)
        await self.emit_delta(
            text=rendered,
            index=int(target.get("index") or 0),
            marker=target.get("marker") or "timeline_text",
            agent=self.agent,
            format=target.get("format") or "markdown",
            artifact_name=target.get("artifact_name") or "",
            completed=False,
        )
        target["index"] = int(target.get("index") or 0) + 1
        target["started"] = True
        await self.emit_delta(
            text="",
            index=int(target.get("index") or 0),
            marker=target.get("marker") or "timeline_text",
            agent=self.agent,
            format=target.get("format") or "markdown",
            artifact_name=target.get("artifact_name") or "",
            completed=True,
        )
        target["index"] = int(target.get("index") or 0) + 1

    def _split_path(self, path: str) -> tuple[List[str], str]:
        cleaned = (path or "").strip(".")
        parts = [p for p in cleaned.split(".") if p]
        if not parts:
            return [], ""
        return parts[:-1], parts[-1]

    def _path_has(self, keys: List[str]) -> bool:
        stack = [k for k in self.path_stack if k]
        if len(stack) < len(keys):
            return False
        return stack[-len(keys):] == list(keys)

    def _matches_action_path(self) -> bool:
        if not self.action_key or self.last_key != self.action_key:
            return False
        if self.action_parent and not self._path_has(self.action_parent):
            return False
        return True

    def _matches_tool_id_path(self) -> bool:
        if not self.tool_id_key or self.last_key != self.tool_id_key:
            return False
        if self.tool_id_parent and not self._path_has(self.tool_id_parent):
            return False
        return True

    def _allow_target(self, target: Dict[str, Any]) -> Optional[bool]:
        name = target.get("name")
        if name == "notes":
            if self.action_value is None:
                return None
            if (self.action_value or "").strip() != "call_tool":
                return False
            if self.tool_id_value is None:
                return None
            return bool(str(self.tool_id_value).strip())
        if name == "final_answer":
            # final_answer must stream as soon as it appears. Waiting for `action`
            # causes the whole answer to stay buffered when models emit
            # `final_answer` before `action`, which makes it pop at once later.
            if self.action_value is None:
                return True
            return (self.action_value or "").strip() in {"complete", "exit", "call_tool"}
        return True

    def _match_target(self) -> Optional[Dict[str, Any]]:
        for t in self.targets:
            if t.get("started"):
                continue
            if not t.get("key") or self.last_key != t.get("key"):
                continue
            parent = t.get("parent") or []
            if parent and not self._path_has(parent):
                continue
            return t
        return None

    def _parse_stream_payload(self) -> Optional[Dict[str, Any]]:
        raw = (self.raw_json_buffer or "").strip()
        if not raw:
            return None
        if "```" in raw:
            fenced = raw
            start = fenced.find("```")
            if start >= 0:
                fenced = fenced[start + 3:]
                fenced = fenced.lstrip()
                if fenced.startswith("json"):
                    fenced = fenced[4:]
                end = fenced.rfind("```")
                if end >= 0:
                    fenced = fenced[:end]
                raw = fenced.strip()
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _first_nonempty_line(text: str) -> str:
        for raw in (text or "").splitlines():
            line = raw.strip()
            if line:
                return line
        return ""

    def _format_plan_timeline_text(self, payload: Dict[str, Any]) -> str:
        action = str(payload.get("action") or "").strip()
        tool_call = payload.get("tool_call") if isinstance(payload.get("tool_call"), dict) else {}
        tool_id = str(tool_call.get("tool_id") or "").strip()
        if action != "call_tool" or tool_id != "react.plan":
            return ""
        params = tool_call.get("params") if isinstance(tool_call.get("params"), dict) else {}
        mode = str(params.get("mode") or "").strip().lower()
        if mode not in {"new", "activate", "replace", "close"}:
            return ""
        target_plan_id = str(params.get("plan_id") or "").strip()
        steps = [str(s).strip() for s in (params.get("steps") or []) if isinstance(s, str) and str(s).strip()]
        title = {
            "new": "• New Plan",
            "activate": "• Activated Plan",
            "replace": "• Replaced Plan",
            "close": "• Closed Plan",
        }.get(mode, "• Plan")
        summary = self._first_nonempty_line(str(payload.get("notes") or ""))
        list_steps = list(steps)
        if not summary:
            if list_steps:
                summary = list_steps[0]
                list_steps = list_steps[1:]
            elif mode == "close" and target_plan_id:
                summary = f"Close plan `{target_plan_id}`."
            elif mode == "activate" and target_plan_id:
                summary = f"Activate plan `{target_plan_id}`."
            elif mode == "replace" and target_plan_id:
                summary = f"Replace plan `{target_plan_id}`."
            elif mode == "new":
                summary = "Start a new plan."
        lines = [title]
        if summary:
            lines.append(f"└ {summary}")
        if mode in {"new", "replace"}:
            for step in list_steps:
                lines.append(f"  □ {step}")
        return "\n".join(lines).strip()

    async def _maybe_emit_plan_target(self) -> None:
        payload = self._parse_stream_payload()
        if not payload:
            return
        text = self._format_plan_timeline_text(payload)
        if not text:
            return
        await self.emit_full("plan", text)

    async def _emit_chunk(self, text: str) -> None:
        if not text or not self.streaming_target:
            return
        t = self.streaming_target
        if t.get("deferred"):
            t["buffer"] = (t.get("buffer") or "") + text
            return
        self._maybe_refresh_sources()
        rendered = text
        if t.get("use_citations"):
            state = self.citation_states.get(t["name"])
            rendered = citations_module.replace_citation_tokens_streaming_stateful(
                text,
                self.citation_map,
                state,
                html=(t.get("format") == "html"),
            )
        if not rendered:
            return
        await self.emit_delta(
            text=rendered,
            index=int(t.get("index") or 0),
            marker=t.get("marker") or "timeline_text",
            agent=self.agent,
            format=t.get("format") or "markdown",
            artifact_name=t.get("artifact_name") or "",
            completed=False,
        )
        t["index"] = int(t.get("index") or 0) + 1
        t["started"] = True

    async def _flush_pending_targets(self, *, force: bool = False) -> None:
        for t in self.targets:
            if not t.get("pending"):
                continue
            allow = self._allow_target(t)
            if allow is None:
                if force:
                    allow = False
                else:
                    continue
            buf = t.get("buffer") or ""
            t["pending"] = False
            t["deferred"] = False
            t["buffer"] = ""
            if not allow or not buf:
                continue
            self.streaming_target = t
            await self._emit_chunk(buf)
            self.streaming_target = None

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

    async def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self.raw_json_buffer += chunk

        for ch in chunk:
            if self.in_string:
                if self.pending_string_quote and self.active_key and self.streaming_target:
                    if ch in " \t\r\n":
                        self.pending_string_quote_ws += ch
                        continue
                    if ch in ",}]":
                        self.in_string = False
                        self.pending_string_quote = False
                        self.pending_string_quote_ws = ""
                        if self._matches_action_path():
                            self.action_value = self.active_value_buf.strip()
                        if self._matches_tool_id_path():
                            self.tool_id_value = self.active_value_buf.strip()
                        if self.streaming_target and self.active_value_buf:
                            await self._emit_chunk(self.active_value_buf)
                        self.active_value_buf = ""
                        self.streaming_target = None
                        self.active_key = None
                        await self._flush_pending_targets()
                        if ch in "}]":
                            if self.path_stack:
                                self.path_stack.pop()
                            self.last_key = None
                            self.expecting_value = False
                        elif ch == ",":
                            self.expecting_value = False
                        continue
                    buffered = '"' + self.pending_string_quote_ws + ch
                    self.pending_string_quote = False
                    self.pending_string_quote_ws = ""
                    self.active_value_buf += buffered
                    if self.streaming_target and len(self.active_value_buf) >= 256:
                        await self._emit_chunk(self.active_value_buf)
                        self.active_value_buf = ""
                    continue

                if self.unicode_mode:
                    self.unicode_buf += ch
                    if len(self.unicode_buf) == 4:
                        try:
                            decoded = chr(int(self.unicode_buf, 16))
                        except Exception:
                            decoded = ""
                        if self.reading_key:
                            self.current_key += decoded
                        elif self.active_key:
                            self.active_value_buf += decoded
                            if self.streaming_target and len(self.active_value_buf) >= 256:
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
                        self.active_value_buf += decoded
                        if self.streaming_target and len(self.active_value_buf) >= 256:
                            await self._emit_chunk(self.active_value_buf)
                            self.active_value_buf = ""
                    continue

                if ch == "\\":
                    self.escaping = True
                    continue

                if ch == '"':
                    if self.reading_key:
                        self.in_string = False
                        self.last_key = self.current_key
                        self.current_key = ""
                        self.reading_key = False
                    elif self.active_key:
                        if self.streaming_target:
                            self.pending_string_quote = True
                            self.pending_string_quote_ws = ""
                            continue
                        self.in_string = False
                        if self._matches_action_path():
                            self.action_value = self.active_value_buf.strip()
                        if self._matches_tool_id_path():
                            self.tool_id_value = self.active_value_buf.strip()
                        if self.streaming_target and self.active_value_buf:
                            await self._emit_chunk(self.active_value_buf)
                        self.active_value_buf = ""
                        self.streaming_target = None
                        self.active_key = None
                        await self._flush_pending_targets()
                    continue

                if self.reading_key:
                    self.current_key += ch
                elif self.active_key:
                    self.active_value_buf += ch
                    if self.streaming_target and len(self.active_value_buf) >= 256:
                        await self._emit_chunk(self.active_value_buf)
                        self.active_value_buf = ""
                continue

            if ch == '"':
                self.in_string = True
                if self.expecting_value:
                    self.active_key = self.last_key
                    self.active_value_buf = ""
                    self.expecting_value = False
                    self.streaming_target = None
                    target = self._match_target()
                    if target:
                        target["buffer"] = ""
                        allow = self._allow_target(target)
                        if allow is True:
                            target["deferred"] = False
                            target["pending"] = False
                            self.streaming_target = target
                        elif allow is False:
                            target["deferred"] = False
                            target["pending"] = False
                            self.streaming_target = None
                        else:
                            target["deferred"] = True
                            target["pending"] = True
                            self.streaming_target = target
                else:
                    self.reading_key = True
                    self.current_key = ""
                continue

            if ch in "{[":
                self.path_stack.append(self.last_key)
                self.last_key = None
                self.expecting_value = False
                continue

            if ch in "}]":
                if self.path_stack:
                    self.path_stack.pop()
                self.last_key = None
                self.expecting_value = False
                continue

            if ch == ":":
                if self.last_key is not None:
                    self.expecting_value = True
                continue

            if ch == ",":
                self.expecting_value = False
                continue

        if self.streaming_target and self.active_value_buf:
            await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""

    async def finish(self) -> None:
        if self.pending_string_quote and self.active_key:
            self.in_string = False
            self.pending_string_quote = False
            self.pending_string_quote_ws = ""
            if self._matches_action_path():
                self.action_value = self.active_value_buf.strip()
            if self._matches_tool_id_path():
                self.tool_id_value = self.active_value_buf.strip()
            if self.streaming_target and self.active_value_buf:
                await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""
            self.streaming_target = None
            self.active_key = None
        if self.streaming_target and self.active_value_buf:
            await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""
        await self._flush_pending_targets(force=True)
        self._maybe_refresh_sources()
        for t in self.targets:
            if not t.get("started"):
                continue
            if t.get("use_citations"):
                state = self.citation_states.get(t["name"])
                flushed = citations_module.replace_citation_tokens_streaming_stateful(
                    "",
                    self.citation_map,
                    state,
                    html=(t.get("format") == "html"),
                    flush=True,
                )
                if flushed:
                    await self.emit_delta(
                        text=flushed,
                        index=int(t.get("index") or 0),
                        marker=t.get("marker") or "timeline_text",
                        agent=self.agent,
                        format=t.get("format") or "markdown",
                        artifact_name=t.get("artifact_name") or "",
                        completed=False,
                    )
                    t["index"] = int(t.get("index") or 0) + 1
            await self.emit_delta(
                text="",
                index=int(t.get("index") or 0),
                marker=t.get("marker") or "timeline_text",
                agent=self.agent,
                format=t.get("format") or "markdown",
                artifact_name=t.get("artifact_name") or "",
                completed=True,
            )
            t["index"] = int(t.get("index") or 0) + 1
        await self._maybe_emit_plan_target()
