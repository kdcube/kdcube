# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Awaitable, Callable, Dict, List, Optional, Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.common import infer_format_from_path
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.artifacts import normalize_relpath
from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module


class ToolContentStreamer:
    """
    Stream tool_call.params.content from decision JSON into UI.
    - Always streams react.write.
    - Streams rendering_tools.write_* only when content is literal (not ref:).
    """

    def __init__(
            self,
            *,
            emit_delta: Callable[..., Awaitable[None]],
            agent: str,
            artifact_name: str,
            sources_list: Optional[List[Dict[str, object]]] = None,
            turn_id: Optional[str] = None,
            stream_tool_id: str = "react.write",
            write_tool_prefix: str = "rendering_tools.write_",
    ) -> None:
        self.emit_delta = emit_delta
        self.agent = agent
        self.default_artifact_name = artifact_name
        self.record_artifact_name: Optional[str] = None
        self.turn_id = turn_id or ""
        self.stream_tool_id = stream_tool_id
        self.write_tool_prefix = write_tool_prefix

        self.stream_xpath = "tool_call.params.content"
        self.path_xpath = "tool_call.params.path"
        self.channel_xpath = "tool_call.params.channel"
        self.kind_xpath = "tool_call.params.kind"
        self.tool_id_xpath = "tool_call.tool_id"

        self.stream_parent, self.stream_key = self._split_path(self.stream_xpath)
        self.path_parent, self.path_key = self._split_path(self.path_xpath)
        self.channel_parent, self.channel_key = self._split_path(self.channel_xpath)
        self.kind_parent, self.kind_key = self._split_path(self.kind_xpath)
        self.tool_id_parent, self.tool_id_key = self._split_path(self.tool_id_xpath)

        self.current_tool_id: Optional[str] = None
        self.current_format: str = "markdown"
        self.current_channel: str = "canvas"
        self.current_kind: str = "display"
        self.current_path: Optional[str] = None

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
        self.streaming_content = False
        self.started = False
        self.index = 0

        self.citation_map = citations_module.build_citation_map_from_sources(sources_list or [])
        self.citation_state = citations_module.CitationStreamState()

    def update_sources(self, sources_list: Optional[List[Dict[str, object]]] = None) -> None:
        """Refresh citation map for streaming outputs."""
        try:
            self.citation_map = citations_module.build_citation_map_from_sources(sources_list or [])
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
        if self.current_tool_id == self.stream_tool_id:
            return True
        if self.current_tool_id and self.current_tool_id.startswith(self.write_tool_prefix):
            return True
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

    def _matches_tool_id_path(self) -> bool:
        if not self.tool_id_key or self.last_key != self.tool_id_key:
            return False
        if self.tool_id_parent and not self._path_has(self.tool_id_parent):
            return False
        return True

    def _emit_artifact_name(self) -> str:
        return self.record_artifact_name or self.default_artifact_name

    async def _emit_chunk(self, text: str) -> None:
        if not text:
            return
        if self.current_channel == "internal":
            return
        rendered = citations_module.replace_citation_tokens_streaming_stateful(
            text,
            self.citation_map,
            self.citation_state,
            html=(self.current_format == "html"),
        )
        if not rendered:
            return
        marker = "timeline_text" if self.current_tool_id == self.stream_tool_id and self.current_channel == "timeline_text" else "canvas"
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
                            if self.capturing_tool_id:
                                self.tool_value_buf += decoded
                            self.active_value_buf += decoded
                            if self.streaming_content and len(self.active_value_buf) >= 256:
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
                        if self.streaming_content and len(self.active_value_buf) >= 256:
                            await self._emit_chunk(self.active_value_buf)
                            self.active_value_buf = ""
                    continue

                if ch == "\\":
                    self.escaping = True
                    continue

                if ch == '"':
                    self.in_string = False
                    if self.reading_key:
                        self.last_key = self.current_key
                        self.current_key = ""
                        self.reading_key = False
                    elif self.active_key:
                        if self.capturing_tool_id:
                            self.current_tool_id = self.tool_value_buf
                            self.tool_value_buf = ""
                            self.capturing_tool_id = False
                        if self._matches_path_path():
                            path_val = self.active_value_buf.strip()
                            if path_val:
                                norm_path = normalize_relpath(path_val, turn_id=self.turn_id)
                                self.current_path = norm_path
                                self.record_artifact_name = norm_path
                                self.current_format = infer_format_from_path(norm_path)
                                print(f"ToolContentStreamer: Inferred format {self.current_format} from path {norm_path}")
                        if self._matches_channel_path():
                            channel_val = self.active_value_buf.strip().lower()
                            if channel_val:
                                self.current_channel = channel_val
                        if self._matches_kind_path():
                            kind_val = self.active_value_buf.strip().lower()
                            if kind_val:
                                self.current_kind = kind_val
                        if self.streaming_content and self.active_value_buf:
                            await self._emit_chunk(self.active_value_buf)
                        self.active_value_buf = ""
                        self.streaming_content = False
                        self.active_key = None
                    continue

                if self.reading_key:
                    self.current_key += ch
                elif self.active_key:
                    if self.capturing_tool_id:
                        self.tool_value_buf += ch
                    self.active_value_buf += ch
                    if self.streaming_content:
                        if self.current_tool_id and self.current_tool_id.startswith(self.write_tool_prefix):
                            if self.active_value_buf.startswith("ref:"):
                                self.streaming_content = False
                                self.active_value_buf = ""
                                continue
                        if len(self.active_value_buf) >= 256:
                            await self._emit_chunk(self.active_value_buf)
                            self.active_value_buf = ""
                continue

            if ch == '"':
                self.in_string = True
                if self.expecting_value:
                    self.active_key = self.last_key
                    self.active_value_buf = ""
                    self.expecting_value = False
                    self.streaming_content = self._matches_stream_path()
                    if self.streaming_content:
                        if self.current_tool_id and self.current_tool_id.startswith(self.write_tool_prefix):
                            if self.active_value_buf.startswith("ref:"):
                                self.streaming_content = False
                                self.active_value_buf = ""
                    self.capturing_tool_id = self._matches_tool_id_path()
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

        if self.streaming_content and self.active_value_buf:
            await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""

    async def finish(self) -> None:
        if self.streaming_content and self.active_value_buf:
            await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""
        if not self.started:
            return
        if self.current_channel == "internal":
            return
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
        marker = "timeline_text" if self.current_tool_id == self.stream_tool_id and self.current_channel == "timeline_text" else "canvas"
        await self.emit_delta(
            text="",
            index=self.index,
            marker=marker,
            agent=self.agent,
            format=self.current_format,
            artifact_name=self._emit_artifact_name(),
            completed=True,
        )


class TimelineStreamer:
    """
    Stream selected JSON string fields into timeline-related UI channels.
    - Streams root-level notes into timeline_text.
    - Streams final_answer into timeline_text.
    """

    def __init__(
            self,
            *,
            emit_delta: Callable[..., Awaitable[None]],
            agent: str,
            sources_list: Optional[List[Dict[str, object]]] = None,
            stream_notes: bool = True,
            stream_final_answer: bool = True,
            notes_xpath: str = "notes",
            final_answer_xpath: str = "final_answer",
            notes_marker: str = "timeline_text",
            final_answer_marker: str = "timeline_text",
            notes_format: str = "markdown",
            final_answer_format: str = "markdown",
            notes_artifact_name: str = "timeline_text.react.decision",
            final_answer_artifact_name: str = "react.final_answer",
    ) -> None:
        self.emit_delta = emit_delta
        self.agent = agent

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
                "index": 0,
                "started": False,
            })

        for t in self.targets:
            parent, key = self._split_path(t["xpath"])
            t["parent"] = parent
            t["key"] = key

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

        self.path_stack: List[Optional[str]] = []
        self.streaming_target: Optional[Dict[str, Any]] = None

        self.citation_map = citations_module.build_citation_map_from_sources(sources_list or [])
        self.citation_states: Dict[str, citations_module.CitationStreamState] = {}
        for t in self.targets:
            if t.get("use_citations"):
                self.citation_states[t["name"]] = citations_module.CitationStreamState()

    def update_sources(self, sources_list: Optional[List[Dict[str, object]]] = None) -> None:
        """Refresh citation map for streaming outputs."""
        try:
            self.citation_map = citations_module.build_citation_map_from_sources(sources_list or [])
            self.citation_states = {}
            for t in self.targets:
                if t.get("use_citations"):
                    self.citation_states[t["name"]] = citations_module.CitationStreamState()
        except Exception:
            pass

    def has_started(self, name: str) -> bool:
        for t in self.targets:
            if t.get("name") == name:
                return bool(t.get("started"))
        return False

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

    def _match_target(self) -> Optional[Dict[str, Any]]:
        for t in self.targets:
            if not t.get("key") or self.last_key != t.get("key"):
                continue
            parent = t.get("parent") or []
            if parent and not self._path_has(parent):
                continue
            return t
        return None

    async def _emit_chunk(self, text: str) -> None:
        if not text or not self.streaming_target:
            return
        t = self.streaming_target
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
                    self.in_string = False
                    if self.reading_key:
                        self.last_key = self.current_key
                        self.current_key = ""
                        self.reading_key = False
                    elif self.active_key:
                        if self.streaming_target and self.active_value_buf:
                            await self._emit_chunk(self.active_value_buf)
                        self.active_value_buf = ""
                        self.streaming_target = None
                        self.active_key = None
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
                    self.streaming_target = self._match_target()
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
        if self.streaming_target and self.active_value_buf:
            await self._emit_chunk(self.active_value_buf)
            self.active_value_buf = ""
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
