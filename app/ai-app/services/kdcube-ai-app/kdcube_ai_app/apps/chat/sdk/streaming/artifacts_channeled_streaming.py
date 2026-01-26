# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/streaming/artifacts_channeled_streaming.py

from typing import Optional, Dict, Any, List, Set, Callable, Awaitable

from kdcube_ai_app.apps.chat.sdk.tools.citations import split_safe_citation_prefix, replace_citation_tokens_streaming
from kdcube_ai_app.apps.chat.sdk.tools.text_proc_utils import _rm_invis


class CompositeJsonArtifactStreamer:
    """
    Incremental scanner for a single top-level JSON object that contains
    multiple string-valued artifact fields (used with target_format=managed_json_artifact).

    - Tracks JSON structure at depth 1.
    - Decodes string keys and values (handles escapes and \\uXXXX).
    - When it enters the value string for a "stream key", it streams decoded
      characters to canvas as the nested artifact with its own format.
    """

    def __init__(
            self,
            artifacts_cfg: dict,
            citation_map: Dict[int, Dict[str, Any]],
            channel: str,
            agent: str,
            emit_delta: Callable[..., Awaitable[None]],
            on_delta_fn: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        """
        artifacts_cfg: mapping of top-level JSON key -> artifact format
                       (markdown|text|html|json|yaml|mermaid).
        """
        self.artifacts_cfg = artifacts_cfg or {}
        self.stream_keys: Set[str] = set(self.artifacts_cfg.keys())
        self.citation_map = citation_map or {}
        self.channel = channel
        self.agent = agent
        self.on_delta_fn = on_delta_fn
        self.emit_delta = emit_delta

        # JSON scan state
        self.depth = 0
        self.in_string = False
        self.escaping = False
        self.unicode_mode = False
        self.unicode_buf = ""

        self.reading_key = False
        self.current_key = ""
        self.last_key: Optional[str] = None
        self.expecting_value = False

        self.active_key: Optional[str] = None   # currently streaming this key's value
        self.active_value_buf = ""              # decoded chars for active key (per-chunk)

        # per-artifact stream state: key -> {"pending": str, "index": int}
        self.artifact_state: Dict[str, Dict[str, Any]] = {}
        self.emit_delta = emit_delta

    def _get_state_for(self, key: str) -> Dict[str, Any]:
        st = self.artifact_state.get(key)
        if st is None:
            st = {"pending": "", "index": 0}
            self.artifact_state[key] = st
        return st

    def _artifact_format(self, key: str) -> str:
        fmt = (self.artifacts_cfg.get(key) or "markdown").lower()
        # hard guard: we assume validation upstream, but be defensive
        if fmt not in ("markdown", "text", "html", "json", "yaml", "mermaid"):
            fmt = "markdown"
        return fmt

    def _should_replace_citations_for_format(self, fmt: str) -> bool:
        # We only rewrite [[S:n]] inline for text-ish formats.
        # For HTML we expect the model to emit <sup class="cite" ...>; for JSON/YAML we rely on sidecar.
        return fmt in ("markdown", "text", "html")

    async def _emit_chunk(self, key: str, text: str):
        """Append text to artifact's pending buffer and emit safe prefix."""
        if not text:
            return
        st = self._get_state_for(key)
        st["pending"] += text

        # respect citation token safety
        safe, _ = split_safe_citation_prefix(st["pending"])
        if not safe:
            return

        safe = _rm_invis(safe)

        fmt = self._artifact_format(key)
        if self.citation_map and self._should_replace_citations_for_format(fmt):
            out = replace_citation_tokens_streaming(safe, self.citation_map)
        else:
            out = safe

        idx = st["index"]
        await self.emit_delta(
            out,
            index=idx,
            marker=self.channel,
            agent=self.agent,
            format=fmt,
            artifact_name=key,
        )
        st["index"] += 1
        st["pending"] = st["pending"][len(safe):]

        if self.on_delta_fn:
            await self.on_delta_fn(out)

    async def _close_artifact(self, key: str):
        """Flush remaining pending text and send completed signal."""
        st = self.artifact_state.get(key)
        if not st:
            return

        fmt = self._artifact_format(key)

        pending = st["pending"]
        if pending:
            pending = _rm_invis(pending)
            if self.citation_map and self._should_replace_citations_for_format(fmt):
                out = replace_citation_tokens_streaming(pending, self.citation_map)
            else:
                out = pending

            idx = st["index"]
            await self.emit_delta(
                out,
                index=idx,
                marker=self.channel,
                agent=self.agent,
                format=fmt,
                artifact_name=key,
            )
            st["index"] += 1
            st["pending"] = ""

            if self.on_delta_fn:
                await self.on_delta_fn(out)

        await self.emit_delta(
            "",
            index=st["index"],
            marker=self.channel,
            agent=self.agent,
            format=fmt,
            artifact_name=key,
            completed=True,
        )
        st["index"] += 1

        if self.on_delta_fn:
            await self.on_delta_fn("")

    # ---------- main streaming API ----------

    async def feed(self, chunk: str):
        """
        Consume a raw JSON text chunk (as produced by the LLM) and stream
        decoded artifact values as we go.
        """
        if not chunk:
            return

        for ch in chunk:
            # ---------- inside a string ----------
            if self.in_string:
                # in \uXXXX mode
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
                            self.active_value_buf += decoded
                        self.unicode_mode = False
                        self.unicode_buf = ""
                    continue

                # in simple escape mode
                if self.escaping:
                    self.escaping = False
                    if ch == "n":
                        decoded = "\n"
                    elif ch == "r":
                        decoded = "\r"
                    elif ch == "t":
                        decoded = "\t"
                    elif ch == "b":
                        decoded = "\b"
                    elif ch == "f":
                        decoded = "\f"
                    elif ch == "u":
                        self.unicode_mode = True
                        self.unicode_buf = ""
                        continue
                    else:
                        # \" \\ \/ or unknown escape
                        decoded = ch

                    if self.reading_key:
                        self.current_key += decoded
                    elif self.active_key:
                        self.active_value_buf += decoded
                    continue

                # start escape sequence
                if ch == "\\":
                    self.escaping = True
                    continue

                # closing quote ends string
                if ch == '"':
                    self.in_string = False
                    if self.reading_key:
                        self.reading_key = False
                        self.last_key = self.current_key
                        self.current_key = ""
                    elif self.active_key:
                        # flush any buffered decoded chars before closing
                        if self.active_value_buf:
                            await self._emit_chunk(self.active_key, self.active_value_buf)
                            self.active_value_buf = ""
                        await self._close_artifact(self.active_key)
                        self.active_key = None
                    continue

                # normal char inside string
                if self.reading_key:
                    self.current_key += ch
                elif self.active_key:
                    self.active_value_buf += ch
                continue

            # ---------- outside of string ----------

            # structure
            if ch == "{":
                self.depth += 1
                continue
            if ch == "}":
                self.depth -= 1
                continue

            # start of key or value string
            if ch == '"':
                self.in_string = True
                if self.depth == 1:
                    if not self.expecting_value:
                        # new key
                        self.reading_key = True
                        self.current_key = ""
                    else:
                        # value for last_key
                        if self.last_key and self.last_key in self.stream_keys:
                            self.active_key = self.last_key
                            self.active_value_buf = ""
                continue

            # key : value
            if ch == ":":
                if self.depth == 1 and self.last_key is not None:
                    self.expecting_value = True
                continue

            # end of "key":value pair at depth 1
            if ch == ",":
                if self.depth == 1:
                    self.expecting_value = False
                continue

            # other characters (whitespace, etc.) are ignored here

        # At end of chunk, if we're in the middle of a streamed value,
        # push decoded text to pending (safe prefix will be emitted).
        if self.active_key and self.active_value_buf:
            await self._emit_chunk(self.active_key, self.active_value_buf)
            self.active_value_buf = ""

    async def finish(self):
        """
        Called when the model signals completion. As a safeguard, closes any
        artifact that might still be in progress.
        """
        if self.active_key:
            if self.active_value_buf:
                await self._emit_chunk(self.active_key, self.active_value_buf)
                self.active_value_buf = ""
            await self._close_artifact(self.active_key)
            self.active_key = None

        # Ensure all artifacts are completed (in case of weird JSON)
        for key in list(self.artifact_state.keys()):
            st = self.artifact_state.get(key)
            if not st:
                continue
            # if completed already, st["pending"] should be "", index consumed
            if st["pending"]:
                await self._close_artifact(key)