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
) -> Dict[str, ChannelResult]:
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
    emit_from = 0
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

    async def on_delta(piece: str):
        nonlocal buf, emit_from, current
        piece = citations_module._strip_invisible(piece)
        if not piece:
            return
        buf += piece

        while True:
            if current is None:
                m = OPEN_RE.search(buf, emit_from)
                if not m:
                    emit_from = _safe_end_for_tags(buf, emit_from)
                    break
                emit_from = m.end()
                current = m.group(1)
                continue

            close_pat = re.compile(rf"</channel:{re.escape(current)}>", re.I)
            m_close = close_pat.search(buf, emit_from)
            if m_close:
                raw_slice = buf[emit_from:m_close.start()]
                if raw_slice:
                    holdback = 0 if current in citation_states else 12
                    emit_now, tail, needs_more = citations_module.split_safe_stream_prefix_with_holdback(
                        raw_slice, holdback=holdback
                    )
                    if emit_now:
                        if composite_streamer and current == composite_channel:
                            await composite_streamer.feed(emit_now)
                        raw_by_channel[current].append(emit_now)
                        await _emit_channel(current, emit_now)
                        emit_from += len(emit_now)
                    if needs_more:
                        break
                await _flush_channel_citations(current)
                emit_from = m_close.end()
                current = None
                continue

            safe_end = _safe_end_for_tags(buf, emit_from)
            if safe_end > emit_from:
                raw_slice = buf[emit_from:safe_end]
                holdback = 0 if current in citation_states else 12
                emit_now, tail, needs_more = citations_module.split_safe_stream_prefix_with_holdback(
                    raw_slice, holdback=holdback
                )
                if emit_now:
                    if composite_streamer and current == composite_channel:
                        await composite_streamer.feed(emit_now)
                    raw_by_channel[current].append(emit_now)
                    await _emit_channel(current, emit_now)
                    emit_from += len(emit_now)
                if needs_more:
                    break
            break

    async def on_complete(_ret):
        # Flush remaining buffered content
        if current and emit_from < len(buf):
            raw_slice = buf[emit_from:]
            holdback = 0 if current in citation_states else 12
            emit_now, _, _ = citations_module.split_safe_stream_prefix_with_holdback(raw_slice, holdback=holdback)
            if emit_now:
                if composite_streamer and current == composite_channel:
                    await composite_streamer.feed(emit_now)
                raw_by_channel[current].append(emit_now)
                await _emit_channel(current, emit_now)
            await _flush_channel_citations(current)
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

    await svc.stream_model_text_tracked(
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

    results: Dict[str, ChannelResult] = {}
    for name, spec in channel_specs.items():
        raw = "".join(raw_by_channel.get(name, []))
        obj = None
        if spec.model and raw:
            try:
                data = json.loads(raw)
                obj = spec.model.parse_obj(data)
            except Exception:
                logger.exception("Failed to parse channel %s into model %s", name, spec.model)
        results[name] = ChannelResult(
            raw=raw,
            obj=obj,
            used_sources=sorted(used_by_channel.get(name, set())),
        )

    return results
