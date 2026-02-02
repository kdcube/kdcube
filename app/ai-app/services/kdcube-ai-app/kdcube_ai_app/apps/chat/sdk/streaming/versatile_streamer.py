# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# chat/sdk/streaming/versatile_streamer.py

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable, Awaitable, Tuple

from pydantic import BaseModel

from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module
from kdcube_ai_app.apps.chat.sdk.streaming.artifacts_channeled_streaming import CompositeJsonArtifactStreamer
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase

logger = logging.getLogger(__name__)


@dataclass
class ChannelSpec:
    name: str
    format: str  # markdown|html|json|text
    model: Optional[type[BaseModel]] = None
    replace_citations: bool = True
    strip_usage: bool = True
    emit_marker: Optional[str] = None


@dataclass
class ChannelResult:
    raw: str
    obj: Optional[Any]
    used_sources: List[int]


ChannelEmitFn = Callable[..., Awaitable[None]]


OPEN_RE = re.compile(r"<channel:([a-zA-Z0-9_-]+)>", re.I)
CLOSE_RE = re.compile(r"</channel:([a-zA-Z0-9_-]+)>", re.I)


def _tag_holdback() -> int:
    # Keep a small tail for tag boundaries
    return 64


def _safe_end_for_tags(buf: str, start: int) -> int:
    end = len(buf) - _tag_holdback()
    if end <= start:
        return start
    return end


def _scrub_chunk(text: str, *, strip_usage: bool) -> str:
    if not text:
        return text
    s = citations_module._strip_invisible(text)
    if strip_usage:
        s = citations_module.USAGE_TAG_RE.sub("", s)
        if "[[USAGE" in s.upper():
            s = re.sub(r"\[\[\s*USAGE\s*:.*?\]\]", "", s, flags=re.I | re.S)
    return s


def _truncate_at_channel_tag(text: str) -> str:
    """
    Prevent leaking channel markers into streamed content.
    If a channel tag (open/close) appears inside the slice, truncate before it.
    """
    if not text:
        return text
    m = re.search(r"<\s*/?\s*channel", text, flags=re.I)
    if not m:
        return text
    return text[:m.start()]


_CHANNEL_PREFIX_RE = re.compile(r"<\s*/?\s*ch", re.I)


def _next_possible_channel_prefix(text: str) -> Optional[int]:
    if not text:
        return None
    m = _CHANNEL_PREFIX_RE.search(text)
    return m.start() if m else None


def _replace_citations(
        text: str,
        fmt: str,
        citation_map: Dict[int, Dict[str, str]],
        replace: bool,
        state: Optional[citations_module.CitationStreamState] = None,
) -> str:
    if not replace or not citation_map or not text:
        return text
    if fmt == "html":
        if state:
            return citations_module.replace_citation_tokens_streaming_stateful(
                text, citation_map, state, html=True
            )
        return citations_module.replace_html_citations(
            text, citation_map, keep_unresolved=False, first_only=False
        )
    if fmt in ("markdown", "text"):
        if state:
            return citations_module.replace_citation_tokens_streaming_stateful(
                text, citation_map, state
            )
        return citations_module.replace_citation_tokens_streaming(text, citation_map)
    return text


async def stream_with_channels(
        svc: ModelServiceBase,
        *,
        messages: List[Any],
        role: str,
        channels: List[ChannelSpec],
        emit: ChannelEmitFn,
        agent: str,
        artifact_name: Optional[str] = None,
        sources_list: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 8000,
        temperature: float = 0.3,
        debug: bool = False,
        composite_cfg: Optional[Dict[str, str]] = None,
        composite_channel: Optional[str] = None,
        composite_marker: str = "canvas",
        return_full_raw: bool = False,
) -> Dict[str, ChannelResult] | Tuple[Dict[str, ChannelResult], Dict[str, Any]]:
    """
    Versatile multi-channel streamer using namespaced tags.

    Required output protocol (model must follow):
      <channel:thinking> ... </channel:thinking>
      <channel:answer> ... </channel:answer>
      <channel:followup> {"followups": [...]} </channel:followup>

    Behavior:
      - Streams each channel via `emit(...)` with a `channel` kwarg.
      - Each channel can configure format + citation replacement.
      - Optional `sources_list` enables inline citation replacement and per-channel used_sources.
      - Returns dict of channel -> {raw, obj, used_sources}.
    """
    channel_specs = {c.name: c for c in channels}
    citation_map = citations_module.build_citation_map_from_sources(sources_list or [])

    buf = ""
    cursor = 0
    current: Optional[str] = None

    raw_by_channel: Dict[str, List[str]] = {c.name: [] for c in channels}
    used_by_channel: Dict[str, set[int]] = {c.name: set() for c in channels}
    delta_counts: Dict[str, int] = {c.name: 0 for c in channels}
    citation_states: Dict[str, citations_module.CitationStreamState] = {}
    for spec in channels:
        if spec.replace_citations and spec.format in ("markdown", "text", "html") and citation_map:
            citation_states[spec.name] = citations_module.CitationStreamState()

    async def _emit_channel(name: str, raw_text: str) -> None:
        spec = channel_specs.get(name)
        if not spec:
            return
        scrubbed = _scrub_chunk(raw_text, strip_usage=spec.strip_usage)
        rendered = _replace_citations(
            scrubbed,
            spec.format,
            citation_map,
            replace=spec.replace_citations,
            state=citation_states.get(name),
        )
        if scrubbed:
            used_by_channel[name].update(citations_module.sids_in_text(scrubbed))
        idx = delta_counts.get(name, 0)
        delta_counts[name] = idx + 1
        await emit(
            text=rendered,
            index=idx,
            marker=spec.emit_marker or "answer",
            agent=agent,
            format=spec.format,
            artifact_name=artifact_name,
            channel=name,
            completed=False,
        )

    async def _flush_channel_citations(name: str) -> None:
        state = citation_states.get(name)
        if not state:
            return
        flushed = citations_module.replace_citation_tokens_streaming_stateful(
            "",
            citation_map,
            state,
            flush=True,
            html=(channel_specs.get(name).format == "html"),
        )
        if flushed:
            await _emit_channel(name, flushed)

    def _get_holdback_for_channel(channel_name: str) -> int:
        """Get appropriate holdback for channel based on format and citation needs."""
        if channel_name in citation_states:
            return 0  # Citation-aware channels handle their own buffering
        spec = channel_specs.get(channel_name)
        if spec and spec.format in ("json",):
            return 0  # JSON doesn't need citation holdback
        return 12  # Default holdback for other formats

    composite_streamer: Optional[CompositeJsonArtifactStreamer] = None
    if composite_cfg and composite_channel:
        async def _composite_emit(*args, **kwargs):
            if args:
                kwargs = dict(kwargs)
                kwargs["text"] = args[0]
            await emit(channel=composite_channel, **kwargs)

        composite_streamer = CompositeJsonArtifactStreamer(
            artifacts_cfg=composite_cfg,
            citation_map=citation_map,
            channel=composite_marker,
            agent=agent,
            emit_delta=_composite_emit,
            on_delta_fn=None,
        )

    TAG_RE = re.compile(r"<\s*/?\s*channel:[a-zA-Z0-9_-]+\s*>", re.I)

    def _parse_tag(tag_text: str) -> tuple[bool, Optional[str]]:
        m_open = OPEN_RE.match(tag_text)
        if m_open:
            return False, m_open.group(1)
        m_close = CLOSE_RE.match(tag_text)
        if m_close:
            return True, m_close.group(1)
        return False, None

    async def _emit_raw_slice(name: str, raw_slice: str) -> None:
        if not raw_slice:
            return
        if composite_streamer and name == composite_channel:
            await composite_streamer.feed(raw_slice)
        raw_by_channel[name].append(raw_slice)
        await _emit_channel(name, raw_slice)

    async def _process_buffer(final: bool = False) -> None:
        nonlocal buf, cursor, current
        loop_guard = 0
        while True:
            loop_guard += 1
            if loop_guard > 2000:
                logger.warning(
                    "versatile_streamer loop guard hit: current=%s cursor=%s buf_len=%s tail=%r",
                    current, cursor, len(buf), buf[max(0, cursor - 80): cursor + 80],
                )
                break

            # Compact buffer occasionally to avoid unbounded growth
            if cursor > 4096:
                buf = buf[cursor:]
                cursor = 0

            m_tag = TAG_RE.search(buf, cursor)
            if not m_tag:
                if current is None:
                    # No channel open; keep a short tail to catch tag boundaries
                    if len(buf) > _tag_holdback():
                        buf = buf[-_tag_holdback():]
                        cursor = 0
                    break

                # Emit safe content within current channel
                safe_end = len(buf) if final else _safe_end_for_tags(buf, cursor)
                if safe_end <= cursor:
                    break
                raw_slice = buf[cursor:safe_end]
                raw_slice = _truncate_at_channel_tag(raw_slice)
                holdback = 0 if final else _get_holdback_for_channel(current)
                emit_now, _, needs_more = citations_module.split_safe_stream_prefix_with_holdback(
                    raw_slice, holdback=holdback
                )
                if emit_now:
                    await _emit_raw_slice(current, emit_now)
                    cursor += len(emit_now)
                if needs_more and not final:
                    break
                continue

            # If we see a tag, emit content before it (if any)
            tag_start = m_tag.start()
            tag_end = m_tag.end()
            if current is not None and tag_start > cursor:
                raw_slice = buf[cursor:tag_start]
                raw_slice = _truncate_at_channel_tag(raw_slice)
                if raw_slice:
                    await _emit_raw_slice(current, raw_slice)
                cursor = tag_start

            is_close, tag_name = _parse_tag(m_tag.group(0))
            if tag_name is None:
                cursor = tag_end
                continue

            # Skip close tags for other channels
            if is_close and current != tag_name:
                cursor = tag_end
                continue

            if is_close and current == tag_name:
                await _flush_channel_citations(current)
                cursor = tag_end
                current = None
                continue

            # Opening a channel: switch to it (implicitly closes previous if any)
            if not is_close:
                if current is not None:
                    await _flush_channel_citations(current)
                current = tag_name
                cursor = tag_end
                continue

            cursor = tag_end

    async def on_delta(piece: str):
        nonlocal buf
        piece = citations_module._strip_invisible(piece)
        if not piece:
            return
        buf += piece
        await _process_buffer(final=False)

    async def on_complete(_ret):
        await _process_buffer(final=True)

        if composite_streamer:
            await composite_streamer.finish()

        # Emit completed markers per channel
        for name, spec in channel_specs.items():
            idx = delta_counts.get(name, 0)
            await emit(
                text="",
                index=idx,
                marker=spec.emit_marker or "answer",
                agent=agent,
                format=spec.format,
                artifact_name=artifact_name,
                channel=name,
                completed=True,
            )

    client = svc.get_client(role)
    cfg = svc.describe_client(client, role=role)

    out = await svc.stream_model_text_tracked(
        client,
        messages,
        on_delta=on_delta,
        on_complete=on_complete,
        temperature=temperature,
        max_tokens=max_tokens,
        client_cfg=cfg,
        debug=debug,
        role=role,
        debug_citations=True,
    )

    # Fallback parsing on full raw text if a channel had no streamed content.
    full_raw = out.get("text") or ""
    if full_raw:
        for name in channel_specs.keys():
            if raw_by_channel.get(name):
                continue
            patt = re.compile(
                rf"<channel:{re.escape(name)}>(.*?)</channel:{re.escape(name)}>",
                re.I | re.S,
                )
            matches = patt.findall(full_raw)
            if matches:
                raw_by_channel[name] = [m for m in matches if m is not None]

    results: Dict[str, ChannelResult] = {}
    for name, spec in channel_specs.items():
        raw = "".join(raw_by_channel.get(name, []))
        obj = None
        if spec.model and raw:
            try:
                data = json.loads(raw)
                obj = spec.model.model_validate(data)
            except Exception:
                logger.exception("Failed to parse channel %s into model %s", name, spec.model)
        results[name] = ChannelResult(
            raw=raw,
            obj=obj,
            used_sources=sorted(used_by_channel.get(name, set())),
        )

    if return_full_raw:
        meta = {
            "raw": full_raw,
            "service_error": (out.get("service_error") or None),
            "usage": out.get("usage") or {},
            "model_name": out.get("model_name") or None,
            "provider_message_id": out.get("provider_message_id") or None,
            "thoughts": out.get("thoughts") or [],
            "tool_calls": out.get("tool_calls") or [],
            "citations": out.get("citations") or [],
        }
        return results, meta
    return results
