# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# chat/sdk/streaming/versatile_streamer_v3.py

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel

from kdcube_ai_app.apps.chat.sdk.streaming.artifacts_channeled_streaming import CompositeJsonArtifactStreamer
from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module
from kdcube_ai_app.apps.chat.sdk.util import _json_loads_loose_with_err
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
    started_at: Optional[float]
    finished_at: Optional[float]
    error: Optional[str]


ChannelEmitFn = Callable[..., Awaitable[None]]
ChannelSubscriberFactory = Callable[[str, int], Optional[Union[ChannelEmitFn, List[ChannelEmitFn]]]]


OPEN_RE = re.compile(r"<channel:([a-zA-Z0-9_-]+)>", re.I)
CLOSE_RE = re.compile(r"</channel:([a-zA-Z0-9_-]+)>", re.I)
TAG_RE = re.compile(r"<\s*/?\s*channel:[a-zA-Z0-9_-]+\s*>", re.I)
_CHANNEL_PREFIX_RE = re.compile(r"<\s*/?\s*ch", re.I)


class ChannelSubscribers:
    def __init__(self) -> None:
        self._subs: Dict[str, List[ChannelEmitFn]] = {}
        self._factories: Dict[str, List[ChannelSubscriberFactory]] = {}
        self._instance_subs: Dict[Tuple[str, int], List[ChannelEmitFn]] = {}

    def subscribe(self, channel: str, fn: ChannelEmitFn) -> "ChannelSubscribers":
        if not channel or fn is None:
            return self
        self._subs.setdefault(channel, []).append(fn)
        return self

    def subscribe_factory(self, channel: str, fn: ChannelSubscriberFactory) -> "ChannelSubscribers":
        if not channel or fn is None:
            return self
        self._factories.setdefault(channel, []).append(fn)
        return self

    def extend(self, channel: str, fns: List[ChannelEmitFn]) -> "ChannelSubscribers":
        if not channel or not fns:
            return self
        self._subs.setdefault(channel, []).extend(fns)
        return self

    def _coerce_emits(self, item: Optional[Union[ChannelEmitFn, List[ChannelEmitFn]]]) -> List[ChannelEmitFn]:
        if item is None:
            return []
        if isinstance(item, list):
            return [fn for fn in item if fn is not None]
        return [item]

    def ensure_instance(self, channel: str, channel_instance: int) -> List[ChannelEmitFn]:
        key = (channel, int(channel_instance))
        existing = self._instance_subs.get(key)
        if existing is not None:
            return list(existing)
        created: List[ChannelEmitFn] = []
        for factory in self._factories.get(channel) or []:
            try:
                created.extend(self._coerce_emits(factory(channel, int(channel_instance))))
            except Exception:
                logger.exception(
                    "versatile_streamer_v3 subscriber factory failed: channel=%s instance=%s",
                    channel,
                    channel_instance,
                )
        self._instance_subs[key] = created
        return list(created)

    def get(self, channel: str, *, channel_instance: Optional[int] = None) -> List[ChannelEmitFn]:
        out = list(self._subs.get(channel) or [])
        if channel_instance is not None:
            out.extend(self.ensure_instance(channel, int(channel_instance)))
        return out

    def to_dict(self) -> Dict[str, List[ChannelEmitFn]]:
        return dict(self._subs)


def _is_valid_channel_tag_start(text: str, idx: int) -> bool:
    try:
        src = text[:idx]
        in_fence = False
        in_inline = False
        i = 0
        while i < len(src):
            if src.startswith("```", i) and not in_inline:
                in_fence = not in_fence
                i += 3
                continue
            if src[i] == "`" and not in_fence:
                in_inline = not in_inline
                i += 1
                continue
            i += 1
        return not in_fence and not in_inline
    except Exception:
        return True


def _find_next_valid_tag(text: str, start: int) -> Optional[re.Match[str]]:
    pos = max(0, int(start or 0))
    while True:
        m = TAG_RE.search(text, pos)
        if not m:
            return None
        if _is_valid_channel_tag_start(text, m.start()):
            return m
        pos = m.start() + 1


def _extract_valid_channel_bodies(full_raw: str, channel_name: str) -> List[str]:
    patt = re.compile(
        rf"<channel:{re.escape(channel_name)}>(.*?)</channel:{re.escape(channel_name)}>",
        re.I | re.S,
    )
    out: List[str] = []
    for match in patt.finditer(full_raw or ""):
        if not _is_valid_channel_tag_start(full_raw or "", match.start()):
            continue
        body = match.group(1)
        if body is not None:
            out.append(body)
    return out


def _advance_channel_markup_state(
    text: str,
    *,
    in_fence: bool,
    in_inline: bool,
) -> tuple[bool, bool]:
    i = 0
    while i < len(text):
        if text.startswith("```", i) and not in_inline:
            in_fence = not in_fence
            i += 3
            continue
        if text[i] == "`" and not in_fence:
            in_inline = not in_inline
            i += 1
            continue
        i += 1
    return in_fence, in_inline


def _find_next_tag_within_channel(
    text: str,
    start: int,
    *,
    in_fence: bool,
    in_inline: bool,
) -> tuple[Optional[re.Match[str]], bool, bool]:
    i = max(0, int(start or 0))
    while i < len(text):
        if text.startswith("```", i) and not in_inline:
            in_fence = not in_fence
            i += 3
            continue
        if text[i] == "`" and not in_fence:
            in_inline = not in_inline
            i += 1
            continue
        if not in_fence and not in_inline:
            m = TAG_RE.match(text, i)
            if m:
                return m, in_fence, in_inline
        i += 1
    return None, in_fence, in_inline


def _tag_holdback() -> int:
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
    if not text:
        return text
    m = _find_next_valid_tag(text, 0)
    if not m:
        return text
    return text[:m.start()]


def _next_possible_channel_prefix(text: str) -> Optional[int]:
    if not text:
        return None
    m = _CHANNEL_PREFIX_RE.search(text)
    return m.start() if m else None


def _strip_structured_fences(text: str) -> str:
    if not text:
        return text
    s = text.strip()
    if not s.startswith("```"):
        return s
    nl = s.find("\n")
    if nl < 0:
        return ""
    s = s[nl + 1:]
    end = s.rfind("```")
    if end >= 0:
        s = s[:end]
    return s.strip()


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
    subscribers: Optional[Union[Dict[str, List[ChannelEmitFn]], ChannelSubscribers]] = None,
    raw_emit: Optional[Callable[[str], Awaitable[None]]] = None,
    max_tokens: int = 8000,
    temperature: float = 0.3,
    debug: bool = False,
    composite_cfg: Optional[Dict[str, str]] = None,
    composite_channel: Optional[str] = None,
    composite_marker: str = "canvas",
    return_full_raw: bool = False,
) -> Dict[str, ChannelResult] | Tuple[Dict[str, ChannelResult], Dict[str, Any]]:
    channel_specs = {c.name: c for c in channels}
    citation_map = citations_module.build_citation_map_from_sources(sources_list or [])

    subscriber_registry = ChannelSubscribers()
    if isinstance(subscribers, ChannelSubscribers):
        subscriber_registry = subscribers
    elif isinstance(subscribers, dict):
        for channel_name, fns in subscribers.items():
            subscriber_registry.extend(channel_name, list(fns or []))

    buf = ""
    cursor = 0
    current: Optional[str] = None
    current_instance: Optional[int] = None
    current_in_fence = False
    current_in_inline = False

    raw_by_channel: Dict[str, List[str]] = {c.name: [] for c in channels}
    used_by_channel: Dict[str, set[int]] = {c.name: set() for c in channels}
    delta_counts: Dict[str, int] = {c.name: 0 for c in channels}
    next_instance_by_channel: Dict[str, int] = {c.name: 0 for c in channels}
    channel_times: Dict[str, Dict[str, Optional[float]]] = {
        c.name: {"started_at": None, "finished_at": None} for c in channels
    }
    citation_states: Dict[str, citations_module.CitationStreamState] = {}
    for spec in channels:
        if spec.replace_citations and spec.format in ("markdown", "text", "html") and citation_map:
            citation_states[spec.name] = citations_module.CitationStreamState()

    async def _emit_subscribers(name: str, *, channel_instance: Optional[int], **kwargs) -> None:
        subs = subscriber_registry.get(name, channel_instance=channel_instance)
        if not subs:
            return
        for fn in subs:
            try:
                await fn(channel_instance=channel_instance, **kwargs)
            except Exception:
                continue

    async def _emit_completed(name: str, *, channel_instance: Optional[int]) -> None:
        spec = channel_specs.get(name)
        if not spec:
            return
        idx = delta_counts.get(name, 0)
        delta_counts[name] = idx + 1
        payload = {
            "text": "",
            "index": idx,
            "marker": spec.emit_marker or "answer",
            "agent": agent,
            "format": spec.format,
            "artifact_name": artifact_name,
            "channel": name,
            "channel_instance": channel_instance,
            "completed": True,
        }
        await emit(**payload)
        await _emit_subscribers(name, **payload)

    async def _emit_channel(name: str, raw_text: str, *, channel_instance: Optional[int]) -> None:
        spec = channel_specs.get(name)
        if not spec:
            return
        if raw_text and channel_times[name]["started_at"] is None:
            channel_times[name]["started_at"] = time.time()
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
        payload = {
            "text": rendered,
            "index": idx,
            "marker": spec.emit_marker or "answer",
            "agent": agent,
            "format": spec.format,
            "artifact_name": artifact_name,
            "channel": name,
            "channel_instance": channel_instance,
            "completed": False,
        }
        await emit(**payload)
        await _emit_subscribers(name, **payload)

    async def _flush_channel_citations(name: str, *, channel_instance: Optional[int]) -> None:
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
            await _emit_channel(name, flushed, channel_instance=channel_instance)

    def _get_holdback_for_channel(channel_name: str) -> int:
        if channel_name in citation_states:
            return 0
        spec = channel_specs.get(channel_name)
        if spec and spec.format in ("json",):
            return 0
        return 12

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

    def _parse_tag(tag_text: str) -> tuple[bool, Optional[str]]:
        m_open = OPEN_RE.match(tag_text)
        if m_open:
            return False, m_open.group(1)
        m_close = CLOSE_RE.match(tag_text)
        if m_close:
            return True, m_close.group(1)
        return False, None

    async def _emit_raw_slice(name: str, raw_slice: str, *, channel_instance: Optional[int]) -> None:
        if not raw_slice:
            return
        if composite_streamer and name == composite_channel:
            await composite_streamer.feed(raw_slice)
        raw_by_channel[name].append(raw_slice)
        await _emit_channel(name, raw_slice, channel_instance=channel_instance)

    async def _close_current_channel() -> None:
        nonlocal current, current_instance, current_in_fence, current_in_inline
        if current is None:
            return
        await _flush_channel_citations(current, channel_instance=current_instance)
        channel_times[current]["finished_at"] = time.time()
        await _emit_completed(current, channel_instance=current_instance)
        current = None
        current_instance = None
        current_in_fence = False
        current_in_inline = False

    async def _process_buffer(final: bool = False) -> None:
        nonlocal buf, cursor, current, current_instance, current_in_fence, current_in_inline
        loop_guard = 0
        while True:
            loop_guard += 1
            if loop_guard > 2000:
                logger.warning(
                    "versatile_streamer_v3 loop guard hit: current=%s cursor=%s buf_len=%s tail=%r",
                    current, cursor, len(buf), buf[max(0, cursor - 80): cursor + 80],
                )
                break

            if cursor > 4096:
                buf = buf[cursor:]
                cursor = 0

            if current is None:
                m_tag = TAG_RE.search(buf, cursor)
            else:
                m_tag, _, _ = _find_next_tag_within_channel(
                    buf,
                    cursor,
                    in_fence=current_in_fence,
                    in_inline=current_in_inline,
                )
            if not m_tag:
                if current is None:
                    if len(buf) > _tag_holdback():
                        buf = buf[-_tag_holdback():]
                        cursor = 0
                    break

                safe_end = len(buf) if final else _safe_end_for_tags(buf, cursor)
                if safe_end <= cursor:
                    break
                raw_slice = buf[cursor:safe_end]
                holdback = 0 if final else _get_holdback_for_channel(current)
                emit_now, _, needs_more = citations_module.split_safe_stream_prefix_with_holdback(
                    raw_slice, holdback=holdback
                )
                if emit_now:
                    await _emit_raw_slice(current, emit_now, channel_instance=current_instance)
                    current_in_fence, current_in_inline = _advance_channel_markup_state(
                        emit_now,
                        in_fence=current_in_fence,
                        in_inline=current_in_inline,
                    )
                    cursor += len(emit_now)
                if needs_more and not final:
                    break
                continue

            tag_start = m_tag.start()
            tag_end = m_tag.end()
            if current is not None and tag_start > cursor:
                raw_slice = buf[cursor:tag_start]
                if raw_slice:
                    await _emit_raw_slice(current, raw_slice, channel_instance=current_instance)
                    current_in_fence, current_in_inline = _advance_channel_markup_state(
                        raw_slice,
                        in_fence=current_in_fence,
                        in_inline=current_in_inline,
                    )
                cursor = tag_start

            is_close, tag_name = _parse_tag(m_tag.group(0))
            if tag_name is None:
                cursor = tag_end
                continue

            if is_close and current != tag_name:
                cursor = tag_end
                continue

            if is_close and current == tag_name:
                await _close_current_channel()
                cursor = tag_end
                continue

            if not is_close:
                if current is not None:
                    await _close_current_channel()
                if channel_times[tag_name]["started_at"] is None:
                    channel_times[tag_name]["started_at"] = time.time()
                current = tag_name
                current_instance = next_instance_by_channel.get(tag_name, 0)
                current_in_fence = False
                current_in_inline = False
                next_instance_by_channel[tag_name] = int(current_instance) + 1
                subscriber_registry.ensure_instance(tag_name, int(current_instance))
                cursor = tag_end
                continue

            cursor = tag_end

    async def on_delta(piece: str):
        nonlocal buf
        if piece and raw_emit is not None:
            try:
                await raw_emit(piece)
            except Exception:
                pass
        piece = citations_module._strip_invisible(piece)
        if not piece:
            return
        buf += piece
        await _process_buffer(final=False)

    async def on_complete(_ret):
        await _process_buffer(final=True)
        if current is not None:
            await _close_current_channel()
        if composite_streamer:
            await composite_streamer.finish()

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

    full_raw = out.get("text") or ""
    if full_raw:
        for name in channel_specs.keys():
            if raw_by_channel.get(name):
                continue
            matches = _extract_valid_channel_bodies(full_raw, name)
            if matches:
                recovered = [m for m in matches if m is not None]
                raw_by_channel[name] = recovered
                if next_instance_by_channel.get(name, 0) == 0:
                    subscriber_registry.ensure_instance(name, 0)
                    if channel_times[name]["started_at"] is None and any(recovered):
                        channel_times[name]["started_at"] = time.time()
                    for body in recovered:
                        if body:
                            await _emit_raw_slice(name, body, channel_instance=0)
                    if channel_times[name]["finished_at"] is None:
                        channel_times[name]["finished_at"] = time.time()
                    await _emit_completed(name, channel_instance=0)
                    next_instance_by_channel[name] = 1

    results: Dict[str, ChannelResult] = {}
    for name, spec in channel_specs.items():
        raw = "".join(raw_by_channel.get(name, []))
        if spec.format in ("json", "yaml", "xml", "html", "mermaid"):
            raw = _strip_structured_fences(raw)
        obj = None
        err: Optional[str] = None
        if spec.model and raw:
            try:
                data, err = _json_loads_loose_with_err(raw)
                if data is not None:
                    obj = spec.model.model_validate(data)
                elif err:
                    err = f"{err}\nreact.decision raw json: {raw}"
                    logger.error("Failed to parse channel %s into model %s: %s", name, spec.model, err)
            except Exception as ex:
                err = f"{ex}\nreact.decision raw json: {raw}"
                logger.exception("Failed to parse channel %s into model %s", name, spec.model)
        results[name] = ChannelResult(
            raw=raw,
            obj=obj,
            used_sources=sorted(used_by_channel.get(name, set())),
            started_at=channel_times[name]["started_at"],
            finished_at=channel_times[name]["finished_at"],
            error=err,
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
