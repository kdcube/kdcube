from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import urllib.parse
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from .bot import (
    TelegramMessage,
    _file_delivery_key,
    _file_item_from_meta,
    _telegram_file_kind,
    _telegram_text,
    edit_telegram_text_message,
    send_telegram_messages,
)


log = logging.getLogger("kdcube.integrations.telegram.stream")


SendMessages = Callable[[list[TelegramMessage]], Awaitable[dict[str, Any]]]
EditTextMessage = Callable[[str | int, str, str], Awaitable[dict[str, Any]]]


@dataclass
class _TextBuffer:
    label: str
    text: str = ""
    first_seen: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    sent_chars: int = 0
    completed: bool = False

    def append(self, value: str) -> None:
        if value:
            self.text += value
        self.last_seen = time.monotonic()

    def unsent(self) -> str:
        return self.text[self.sent_chars :]


class TelegramActivityStreamer:
    """Bridge selected ChatCommunicator activity into Telegram progress messages."""

    def __init__(
        self,
        *,
        comm: Any,
        bot_token: str,
        chat_id: str | int,
        turn_id: str | None = None,
        enabled: bool = True,
        quiet_seconds: float = 1.5,
        min_send_interval_seconds: float = 3.0,
        max_message_chars: int = 1400,
        send_messages: SendMessages | None = None,
        edit_text_message: EditTextMessage | None = None,
    ) -> None:
        self.comm = comm
        self.bot_token = str(bot_token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.turn_id = str(turn_id or "").strip()
        self.enabled = bool(enabled and self.bot_token and self.chat_id)
        self.quiet_seconds = max(0.3, float(quiet_seconds or 1.5))
        self.min_send_interval_seconds = max(0.5, float(min_send_interval_seconds or 3.0))
        self.max_message_chars = max(300, int(max_message_chars or 1400))
        self._send_messages = send_messages
        self._edit_text_message = edit_text_message
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=1000)
        self._task: asyncio.Task | None = None
        self._buffers: dict[str, _TextBuffer] = {}
        self._seen_status_keys: set[str] = set()
        self._seen_progress_texts: set[str] = set()
        self._seen_activity_signatures: dict[str, float] = {}
        self._delivered_file_keys: set[str] = set()
        self._progress_message_id: str | int | None = None
        self._progress_chunks: list[str] = []
        self._last_send_at = 0.0
        self._listener_attached = False
        self._relay_attached = False
        self._relay: Any = None
        self._relay_session_id = ""
        self._relay_tenant = ""
        self._relay_project = ""

    def delivered_file_keys(self) -> set[str]:
        return set(self._delivered_file_keys)

    def progress_message_id(self) -> str | int | None:
        return self._progress_message_id

    def progress_summary(self, *, max_chars: int = 3900) -> str:
        return self._progress_body(limit=max(300, int(max_chars or 3900))).strip()

    async def __aenter__(self) -> "TelegramActivityStreamer":
        add_listener = getattr(self.comm, "add_activity_listener", None)
        if self.enabled:
            self._task = asyncio.create_task(self._run(), name="telegram-activity-streamer")
        if self.enabled and callable(add_listener):
            add_listener(self._on_activity)
            self._listener_attached = True
            log.info("[telegram.stream] attached chat_id=%s", self.chat_id)
        else:
            log.info(
                "[telegram.stream] disabled enabled=%s has_listener_api=%s chat_id_present=%s token_present=%s",
                self.enabled,
                callable(add_listener),
                bool(self.chat_id),
                bool(self.bot_token),
            )
        if self.enabled:
            await self._attach_relay_listener()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._detach_relay_listener()
        remove_listener = getattr(self.comm, "remove_activity_listener", None)
        if self._listener_attached and callable(remove_listener):
            remove_listener(self._on_activity)
            self._listener_attached = False
        if self._task is not None:
            await self._queue.put(None)
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
        log.info("[telegram.stream] detached chat_id=%s", self.chat_id)

    async def _on_activity(self, activity: dict[str, Any]) -> None:
        if not self._activity_matches_turn(activity):
            return
        sig = self._activity_signature(activity)
        if sig:
            now = time.monotonic()
            self._seen_activity_signatures = {
                key: ts
                for key, ts in self._seen_activity_signatures.items()
                if now - ts < 60.0
            }
            if sig in self._seen_activity_signatures:
                return
            self._seen_activity_signatures[sig] = now
        try:
            self._queue.put_nowait(activity)
        except asyncio.QueueFull:
            log.warning("[telegram.stream] dropping activity because queue is full")

    def _activity_matches_turn(self, activity: Mapping[str, Any]) -> bool:
        if not self.turn_id:
            return True
        event_turn_id = self._activity_turn_id(activity)
        return not event_turn_id or event_turn_id == self.turn_id

    @staticmethod
    def _activity_turn_id(activity: Mapping[str, Any]) -> str:
        env = activity.get("data") if isinstance(activity, Mapping) else None
        conv = env.get("conversation") if isinstance(env, Mapping) and isinstance(env.get("conversation"), Mapping) else None
        if conv is None and isinstance(activity, Mapping) and isinstance(activity.get("conversation"), Mapping):
            conv = activity.get("conversation")
        if not isinstance(conv, Mapping):
            return ""
        return str(conv.get("turn_id") or "").strip()

    async def _on_relay_message(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            return
        await self._on_activity(
            {
                "source": "relay",
                "event": message.get("event"),
                "broadcast": message.get("target_sid") is None,
                "data": message.get("data") if isinstance(message.get("data"), dict) else {},
                "type": (message.get("data") or {}).get("type") if isinstance(message.get("data"), dict) else None,
                "conversation": (message.get("data") or {}).get("conversation") if isinstance(message.get("data"), dict) else None,
                "ts": int(time.time() * 1000),
            }
        )

    async def _attach_relay_listener(self) -> None:
        relay = getattr(self.comm, "emitter", None)
        acquire = getattr(relay, "acquire_session_channel", None)
        if not callable(acquire):
            return
        conversation = getattr(self.comm, "conversation", None) or {}
        service = getattr(self.comm, "service", None) or {}
        session_id = str(conversation.get("session_id") or getattr(self.comm, "room", None) or "").strip()
        tenant = str(getattr(self.comm, "tenant", None) or service.get("tenant") or "").strip()
        project = str(getattr(self.comm, "project", None) or service.get("project") or "").strip()
        if not session_id or not tenant:
            log.info(
                "[telegram.stream] relay subscription skipped session_id=%s tenant=%s project=%s",
                bool(session_id),
                bool(tenant),
                bool(project),
            )
            return
        await acquire(session_id, tenant, project, callback=self._on_relay_message)
        self._relay = relay
        self._relay_session_id = session_id
        self._relay_tenant = tenant
        self._relay_project = project
        self._relay_attached = True
        log.info("[telegram.stream] relay subscribed session_id=%s tenant=%s project=%s", session_id, tenant, project)

    async def _detach_relay_listener(self) -> None:
        if not self._relay_attached or self._relay is None:
            return
        remove = getattr(self._relay, "remove_listener", None)
        if callable(remove):
            remove(self._on_relay_message)
        release = getattr(self._relay, "release_session_channel", None)
        if callable(release):
            try:
                await release(self._relay_session_id, self._relay_tenant, self._relay_project)
            except Exception:
                log.exception("[telegram.stream] relay release failed")
        self._relay_attached = False
        self._relay = None

    @staticmethod
    def _activity_signature(activity: Mapping[str, Any]) -> str:
        env = activity.get("data") if isinstance(activity, Mapping) else None
        if not isinstance(env, Mapping):
            return ""
        typ = str(env.get("type") or "")
        conv = env.get("conversation") if isinstance(env.get("conversation"), Mapping) else {}
        event = env.get("event") if isinstance(env.get("event"), Mapping) else {}
        delta = env.get("delta") if isinstance(env.get("delta"), Mapping) else {}
        extra = env.get("extra") if isinstance(env.get("extra"), Mapping) else {}
        data_sig = ""
        if typ in {"chat.files", "chat.citations"}:
            data = env.get("data") if isinstance(env.get("data"), Mapping) else {}
            items = data.get("items") if isinstance(data, Mapping) else None
            if isinstance(items, list):
                data_sig = ",".join(TelegramActivityStreamer._event_item_signature(item, index) for index, item in enumerate(items[:20]))
            else:
                data_sig = str(data.get("count") or "")
        elif typ == "chat.compaction":
            data = env.get("data") if isinstance(env.get("data"), Mapping) else {}
            data_sig = "|".join(
                str(data.get(key) or "")
                for key in ("compaction_id", "kind", "reason", "before_tokens", "after_tokens", "compacted_tokens")
            )
        return "|".join(
            str(part or "")
            for part in (
                activity.get("event"),
                typ,
                conv.get("conversation_id"),
                conv.get("turn_id"),
                event.get("step"),
                event.get("status"),
                delta.get("marker"),
                delta.get("index"),
                delta.get("completed"),
                delta.get("text"),
                extra.get("artifact_name"),
                extra.get("sub_type"),
                data_sig,
            )
        )

    @staticmethod
    def _event_item_signature(item: Any, index: int) -> str:
        if not isinstance(item, Mapping):
            return str(index)
        data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
        meta = data.get("meta") if isinstance(data.get("meta"), Mapping) else {}
        for source in (item, meta, data):
            for key in ("hosted_uri", "url", "rn", "key", "artifact_path", "logical_path", "physical_path", "filename", "sid", "href", "title"):
                value = source.get(key) if isinstance(source, Mapping) else None
                if value not in ("", None):
                    return str(value)
        return str(index)

    async def _run(self) -> None:
        while True:
            try:
                activity = await asyncio.wait_for(self._queue.get(), timeout=self.quiet_seconds)
            except asyncio.TimeoutError:
                await self._flush_due(force=False)
                continue
            if activity is None:
                break
            await self._handle_activity(activity)
            await self._flush_due(force=False)
        await self._flush_due(force=True)

    async def _handle_activity(self, activity: Mapping[str, Any]) -> None:
        env = activity.get("data") if isinstance(activity, Mapping) else None
        if not isinstance(env, Mapping):
            return
        typ = str(env.get("type") or "")
        if typ == "chat.delta":
            await self._handle_delta(env)
            return
        if typ == "chat.files":
            await self._handle_files_event(env)
            return
        if typ == "chat.citations":
            await self._handle_citations_event(env)
            return
        if typ == "chat.compaction":
            await self._handle_compaction_event(env)
            return
        if typ in {"chat.step", "chat.service"}:
            await self._handle_status(env)
            return
        if typ == "chat.error":
            data = env.get("data") if isinstance(env.get("data"), Mapping) else {}
            await self._send_text(f"Error: {data.get('error') or 'The turn failed.'}", reason="error")

    async def _handle_delta(self, env: Mapping[str, Any]) -> None:
        delta = env.get("delta") if isinstance(env.get("delta"), Mapping) else {}
        marker = str(delta.get("marker") or "").strip()
        text = str(delta.get("text") or "")
        completed = bool(delta.get("completed"))
        event = env.get("event") if isinstance(env.get("event"), Mapping) else {}
        extra = env.get("extra") if isinstance(env.get("extra"), Mapping) else {}
        if marker == "answer":
            return
        if marker in {"thinking", "timeline_text"}:
            label = "Thinking" if marker == "thinking" else "Notes"
            key = marker
            if _is_trivial_delta_text(text):
                buf = self._buffers.get(key)
                if buf is not None and completed:
                    buf.completed = True
                return
            buf = self._buffers.setdefault(key, _TextBuffer(label=label))
            buf.append(text)
            buf.completed = completed
            return
        agent = str(event.get("agent") or "")
        artifact_name = str(extra.get("artifact_name") or "")
        key = "|".join([marker, agent, artifact_name, str(extra.get("sub_type") or "")])
        if marker == "canvas":
            await self._send_once(
                key=key or f"canvas:{artifact_name}",
                text=f"Working on {self._display_artifact(artifact_name, extra)}",
                reason="canvas",
            )
            return
        if marker == "subsystem":
            await self._send_once(
                key=key or f"subsystem:{artifact_name}",
                text=self._subsystem_status_text(event=event, extra=extra, delta=delta, completed=completed),
                reason="subsystem",
            )

    async def _handle_files_event(self, env: Mapping[str, Any]) -> None:
        data = env.get("data") if isinstance(env.get("data"), Mapping) else {}
        items = data.get("items") if isinstance(data, Mapping) else None
        if not isinstance(items, list):
            return
        messages: list[TelegramMessage] = []
        pending_keys: list[str] = []
        for item in items:
            file_item = self._file_item_from_event_item(item)
            key = _file_delivery_key(file_item)
            if not file_item or not key or key in self._delivered_file_keys or key in pending_keys:
                continue
            pending_keys.append(key)
            messages.append(
                TelegramMessage(
                    kind=_telegram_file_kind(file_item),
                    text=_telegram_text(str(file_item.get("description") or file_item.get("filename") or "")),
                    files=(file_item,),
                )
            )
        if not messages:
            return
        result = await self._send_file_messages(messages, reason="chat.files")
        if result.get("ok"):
            self._delivered_file_keys.update(pending_keys)

    async def _handle_citations_event(self, env: Mapping[str, Any]) -> None:
        data = env.get("data") if isinstance(env.get("data"), Mapping) else {}
        items = data.get("items") if isinstance(data, Mapping) else None
        if not isinstance(items, list) or not items:
            return
        lines = ["Sources ready:"]
        for index, item in enumerate(items[:5], start=1):
            if not isinstance(item, Mapping):
                continue
            title = str(item.get("title") or item.get("url") or item.get("href") or f"Source {index}").strip()
            url = str(item.get("url") or item.get("href") or "").strip()
            sid = item.get("sid")
            prefix = f"{sid}. " if sid is not None else f"{index}. "
            lines.append(f"{prefix}{title} - {url}" if url and title != url else f"{prefix}{title or url}")
        if len(items) > 5:
            lines.append(f"...and {len(items) - 5} more")
        key_parts: list[str] = []
        for index, item in enumerate(items[:8]):
            if isinstance(item, Mapping):
                key_parts.append(str(item.get("sid") or item.get("url") or item.get("href") or index))
            else:
                key_parts.append(str(index))
        await self._send_once(key="citations:" + ",".join(key_parts), text="\n".join(lines), reason="chat.citations")

    async def _handle_compaction_event(self, env: Mapping[str, Any]) -> None:
        event = env.get("event") if isinstance(env.get("event"), Mapping) else {}
        data = env.get("data") if isinstance(env.get("data"), Mapping) else {}
        status = str(data.get("status") or event.get("status") or "").strip().lower()
        compaction_id = str(data.get("compaction_id") or "").strip()
        kind = str(data.get("kind") or "").replace("_", " ").strip()
        reason = str(data.get("reason") or "").replace("_", " ").strip()
        compacted_tokens = _format_int(data.get("compacted_tokens"))
        before_tokens = _format_int(data.get("before_tokens"))
        after_tokens = _format_int(data.get("after_tokens"))

        if status == "started":
            detail = f" ({kind})" if kind else ""
            text = f"Context compaction started{detail}."
        elif status == "completed":
            details: list[str] = []
            if compacted_tokens:
                details.append(f"compacted ~{compacted_tokens} tokens")
            elif before_tokens and after_tokens:
                details.append(f"{before_tokens} -> {after_tokens} tokens")
            if kind:
                details.append(kind)
            suffix = f" ({'; '.join(details)})" if details else ""
            text = f"Context compaction completed{suffix}."
        elif status == "skipped":
            text = "Context compaction skipped" + (f": {reason}." if reason else ".")
        else:
            text = "Context compaction updated."

        key = f"compaction:{compaction_id or status}:{status}:{reason}"
        await self._send_once(key=key, text=text, reason="chat.compaction")

    @staticmethod
    def _file_item_from_event_item(item: Any) -> dict[str, Any]:
        if not isinstance(item, Mapping):
            return {}
        meta: dict[str, Any] = {}
        data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
        if isinstance(data.get("meta"), Mapping):
            meta.update(data.get("meta") or {})
        elif isinstance(item.get("meta"), Mapping):
            meta.update(item.get("meta") or {})
        elif isinstance(item.get("value"), Mapping):
            value = item.get("value") or {}
            meta.update(value)
        else:
            meta.update(item)

        for key in (
            "filename",
            "mime",
            "mime_type",
            "description",
            "title",
            "hosted_uri",
            "url",
            "rn",
            "key",
            "physical_path",
            "local_path",
            "artifact_path",
            "logical_path",
            "base64",
            "size",
            "size_bytes",
        ):
            if item.get(key) not in ("", None) and meta.get(key) in ("", None):
                meta[key] = item.get(key)
        if meta.get("mime_type") in ("", None) and meta.get("mime") not in ("", None):
            meta["mime_type"] = meta.get("mime")
        if meta.get("size_bytes") in ("", None) and meta.get("size") not in ("", None):
            meta["size_bytes"] = meta.get("size")
        visibility = str(meta.get("visibility") or "external").strip().lower()
        if visibility == "internal":
            return {}
        logical_path = str(meta.get("artifact_path") or meta.get("logical_path") or "").strip()
        return _file_item_from_meta(meta, logical_path=logical_path)

    async def _handle_status(self, env: Mapping[str, Any]) -> None:
        event = env.get("event") if isinstance(env.get("event"), Mapping) else {}
        status = str(event.get("status") or "").strip()
        title = str(event.get("title") or "").strip()
        step = str(event.get("step") or "").strip()
        if status not in {"started", "running", "completed", "error"}:
            return
        if step in {"turn", "stream", "workflow_start"}:
            return
        if self._is_internal_status(step=step, title=title):
            return
        if not title and step in {"stream", "event"}:
            return
        label = title or step or "Progress"
        await self._send_once(
            key=f"status:{env.get('type')}:{step}:{status}:{label}",
            text=f"{label}: {status}",
            reason="status",
        )

    @staticmethod
    def _is_internal_status(*, step: str, title: str) -> bool:
        normalized_step = _normalize_status_token(step)
        normalized_title = _normalize_status_token(title)
        internal_tokens = {
            "assistant delta",
            "delta",
            "prepare",
            "user conversation linked",
            "user conversation link",
            "conversation linked",
            "conversation link",
            "assistant messages persisted",
        }
        return normalized_step in internal_tokens or normalized_title in internal_tokens

    async def _flush_due(self, *, force: bool) -> None:
        now = time.monotonic()
        for key, buf in list(self._buffers.items()):
            stale = now - buf.last_seen >= self.quiet_seconds
            rate_ok = now - self._last_send_at >= self.min_send_interval_seconds
            if not force and not buf.completed and not stale:
                continue
            if not force and not buf.completed and not rate_ok:
                continue
            unsent = buf.unsent().strip()
            if not unsent:
                if buf.completed:
                    self._buffers.pop(key, None)
                continue
            if _is_trivial_delta_text(unsent):
                buf.sent_chars += len(buf.unsent())
                if buf.completed or force:
                    self._buffers.pop(key, None)
                continue
            clipped = unsent[: self.max_message_chars].rstrip()
            await self._send_text(_progress_note_html(buf.label, clipped), reason=f"delta:{key}", parse_mode="HTML")
            buf.sent_chars += len(unsent) if force or buf.completed else len(clipped)
            if buf.completed or force:
                self._buffers.pop(key, None)

    async def _send_once(self, *, key: str, text: str, reason: str) -> None:
        key = key.strip()
        if not key or key in self._seen_status_keys:
            return
        self._seen_status_keys.add(key)
        await self._send_text(text, reason=reason)

    async def _send_text(self, text: str, *, reason: str, parse_mode: str = "") -> None:
        raw_value = str(text or "")
        parse_mode = str(parse_mode or "").strip()
        plain_value = _telegram_text(_html_to_plain(raw_value) if parse_mode.upper() == "HTML" else raw_value)
        if not plain_value:
            return
        if _is_internal_progress_text(plain_value):
            return
        text_key = _normalize_progress_text(plain_value)
        if text_key and text_key in self._seen_progress_texts:
            return
        if text_key:
            self._seen_progress_texts.add(text_key)
        if parse_mode.upper() == "HTML":
            chunk = raw_value.strip()
            parse_mode = "HTML"
        else:
            chunk = _progress_plain_html(plain_value)
            parse_mode = "HTML"
        self._progress_chunks.append(chunk)
        body = self._progress_body()
        self._last_send_at = time.monotonic()
        log.info(
            "[telegram.stream] progress upsert reason=%s chars=%s message_id=%s",
            reason,
            len(body),
            self._progress_message_id,
        )
        if self._progress_message_id:
            if self._edit_text_message is not None:
                result = await self._edit_text_message(self._progress_message_id, body, parse_mode)
            else:
                result = await edit_telegram_text_message(
                    bot_token=self.bot_token,
                    chat_id=self.chat_id,
                    message_id=self._progress_message_id,
                    text=body,
                    parse_mode=parse_mode,
                )
            if result.get("ok"):
                return
            if _telegram_edit_not_modified(result):
                return
            log.warning(
                "[telegram.stream] progress edit failed message_id=%s response=%s",
                self._progress_message_id,
                result,
            )
            return
        if self._send_messages is not None:
            result = await self._send_messages([TelegramMessage(kind="text", text=body, parse_mode=parse_mode)])
        else:
            result = await send_telegram_messages(
                bot_token=self.bot_token,
                chat_id=self.chat_id,
                messages=[TelegramMessage(kind="text", text=body, parse_mode=parse_mode)],
            )
        self._remember_progress_message_id(result)

    def _progress_body(self, *, limit: int | None = None) -> str:
        limit = min(3900, int(limit or self.max_message_chars * 3))
        chunks = [chunk for chunk in self._progress_chunks if chunk]
        body = "\n\n".join(chunks).strip()
        if len(body) <= limit:
            return body
        selected: list[str] = []
        total = 0
        for chunk in reversed(chunks):
            extra = len(chunk) + (2 if selected else 0)
            if selected and total + extra > limit:
                break
            if not selected and extra > limit:
                selected.append(_fit_progress_chunk_html(chunk, limit))
                break
            selected.append(chunk)
            total += extra
        return "\n\n".join(reversed(selected)).strip()

    def _remember_progress_message_id(self, result: Mapping[str, Any] | None) -> None:
        if self._progress_message_id or not isinstance(result, Mapping):
            return
        responses = result.get("responses") if isinstance(result.get("responses"), list) else []
        for response in responses:
            if not isinstance(response, Mapping):
                continue
            payload = response.get("result")
            if isinstance(payload, Mapping) and payload.get("message_id") is not None:
                self._progress_message_id = payload.get("message_id")
                return

    async def _send_file_messages(self, messages: list[TelegramMessage], *, reason: str) -> dict[str, Any]:
        self._last_send_at = time.monotonic()
        log.info(
            "[telegram.stream] send files reason=%s messages=%s files=%s",
            reason,
            len(messages),
            sum(1 for message in messages if message.files),
        )
        if self._send_messages is not None:
            return await self._send_messages(messages)
        return await send_telegram_messages(
            bot_token=self.bot_token,
            chat_id=self.chat_id,
            messages=messages,
        )

    @staticmethod
    def _display_artifact(artifact_name: str, extra: Mapping[str, Any]) -> str:
        fmt = str(extra.get("format") or "").strip()
        name = str(artifact_name or "").strip() or "an output"
        return f"{fmt} artifact {name}" if fmt else name

    @staticmethod
    def _subsystem_status_text(*, event: Mapping[str, Any], extra: Mapping[str, Any], delta: Mapping[str, Any], completed: bool) -> str:
        sub_type = str(extra.get("sub_type") or "").strip()
        title = str(event.get("title") or extra.get("title") or "").strip()
        text = str(delta.get("text") or "")
        if sub_type.startswith("web_search."):
            return _web_search_status_text(text=text, completed=completed)
        if sub_type.startswith("web_fetch."):
            return _web_fetch_status_text(text=text, completed=completed)
        if sub_type.startswith("code_exec."):
            return _code_exec_status_text(sub_type=sub_type, text=text, completed=completed)
        if title:
            return title
        return "Tool update received."


def _telegram_edit_not_modified(result: Mapping[str, Any] | None) -> bool:
    if not isinstance(result, Mapping):
        return False
    description = str(result.get("description") or result.get("error") or "").lower()
    return "message is not modified" in description


def _format_int(value: Any) -> str:
    try:
        number = int(value)
    except Exception:
        return ""
    if number <= 0:
        return ""
    return f"{number:,}"


async def deliver_messages_preserving_progress_card(
    *,
    bot_token: str,
    chat_id: str | int,
    telegram_messages: list[TelegramMessage],
    progress_message_id: str | int | None = None,
    progress_summary: str = "",
) -> dict[str, Any]:
    """Append final text to the progress card; send files or overflow normally."""
    messages_to_send = list(telegram_messages or [])
    edit_result: dict[str, Any] | None = None
    edit_text = ""
    edit_parse_mode = ""
    final_appended = False

    if progress_message_id and messages_to_send:
        edit_text, edit_parse_mode, remaining_after_edit = progress_final_card(
            progress_summary=progress_summary,
            telegram_messages=messages_to_send,
        )
        if edit_text:
            edit_result = await edit_telegram_text_message(
                bot_token=bot_token,
                chat_id=chat_id,
                message_id=progress_message_id,
                text=edit_text,
                parse_mode=edit_parse_mode,
            )
            if edit_result.get("ok") or _telegram_edit_not_modified(edit_result):
                messages_to_send = remaining_after_edit
                final_appended = len(remaining_after_edit) < len(telegram_messages)
                log.info(
                    "[telegram.stream] progress finalized message_id=%s appended_final=%s chars=%s",
                    progress_message_id,
                    final_appended,
                    len(edit_text),
                )
            else:
                log.warning(
                    "[telegram.stream] progress final edit failed message_id=%s response=%s",
                    progress_message_id,
                    edit_result,
                )

    delivery = (
        await send_telegram_messages(
            bot_token=bot_token,
            chat_id=chat_id,
            messages=messages_to_send,
        )
        if messages_to_send
        else {"ok": True, "sent": 0, "responses": []}
    )
    return {
        "messages_to_send": messages_to_send,
        "telegram_delivery": delivery,
        "progress_edit": edit_result,
        "progress_edit_text": edit_text,
        "progress_edit_parse_mode": edit_parse_mode,
        "progress_final_appended": final_appended,
    }


def progress_final_card(
    *,
    progress_summary: str,
    telegram_messages: list[TelegramMessage],
    limit: int = 3900,
) -> tuple[str, str, list[TelegramMessage]]:
    text_messages: list[TelegramMessage] = []
    remaining: list[TelegramMessage] = []
    for message in telegram_messages:
        if getattr(message, "kind", "") == "text" and not getattr(message, "files", ()):
            text_messages.append(message)
        else:
            remaining.append(message)
    if not text_messages:
        return "", "", telegram_messages

    progress_html = _existing_progress_html(progress_summary)
    final_html = "\n\n".join(
        _existing_message_html(message)
        for message in text_messages
        if str(getattr(message, "text", "") or "").strip()
    ).strip()
    if not final_html:
        return "", "", telegram_messages

    edit_text = "\n\n".join(
        part
        for part in (
            progress_html,
            f"<b>Final response</b>\n{final_html}",
        )
        if part
    ).strip()
    if len(edit_text) <= limit:
        return edit_text, "HTML", remaining

    fallback_text = "\n\n".join(
        part
        for part in (
            progress_html,
            "<b>Final response</b>\nThe final response follows below.",
        )
        if part
    ).strip()
    if len(fallback_text) <= limit:
        return fallback_text, "HTML", telegram_messages

    return _fit_progress_chunk_html(progress_html or fallback_text, limit), "HTML", telegram_messages


def _existing_progress_html(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _looks_like_telegram_html(text):
        return text
    return html.escape(text, quote=False)


def _existing_message_html(message: TelegramMessage) -> str:
    value = str(getattr(message, "text", "") or "").strip()
    if not value:
        return ""
    if str(getattr(message, "parse_mode", "") or "").strip().upper() == "HTML":
        return value
    return html.escape(value, quote=False)


def _looks_like_telegram_html(value: str) -> bool:
    return bool(re.search(r"</?(?:a|b|blockquote|code|i|pre)\b", str(value or ""), flags=re.IGNORECASE))


def _normalize_status_token(value: str) -> str:
    return " ".join(
        str(value or "")
        .replace("↔", " ")
        .replace("_", " ")
        .replace("-", " ")
        .lower()
        .split()
    )


def _normalize_progress_text(value: str) -> str:
    return _normalize_status_token(value.strip().rstrip("."))


def _is_internal_progress_text(value: str) -> bool:
    normalized = _normalize_progress_text(value)
    return normalized in {
        "assistant delta",
        "prepare completed",
        "prepare",
        "user conversation linked completed",
        "user conversation linked",
        "conversation linked completed",
        "conversation linked",
        "assistant messages persisted completed",
        "assistant messages persisted",
    }


def _is_trivial_delta_text(value: str) -> bool:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [re.sub(r"^\s*[>|]\s*", "", line) for line in text.splitlines()]
    text = "\n".join(lines).strip()
    return not text or not any(ch.isalnum() for ch in text)


def _web_search_status_text(*, text: str, completed: bool) -> str:
    data = _json_object(text)
    queries = _string_list(data.get("queries"))
    result_count = len(data.get("results") or []) if isinstance(data.get("results"), list) else None
    if queries:
        query_text = "; ".join(queries[:3])
        suffix = f" ({result_count} results)" if result_count is not None else ""
        return f"Web search: {query_text}{suffix}"
    return "Web search results are ready." if completed else "Searching the web..."


def _web_fetch_status_text(*, text: str, completed: bool) -> str:
    data = _json_object(text)
    url = _first_url(data)
    if url:
        return f"Fetched: {_display_url(url)}" if completed else f"Fetching: {_display_url(url)}"
    return "Fetched web content." if completed else "Fetching web content..."


def _code_exec_status_text(*, sub_type: str, text: str, completed: bool) -> str:
    data = _json_object(text)
    if sub_type == "code_exec.objective":
        return f"Code objective: {_clip_text(text, 180)}" if text.strip() else "Preparing code execution..."
    if sub_type == "code_exec.program.name":
        return f"Code program: {_clip_text(text, 120)}" if text.strip() else "Preparing code program..."
    if sub_type == "code_exec.contract":
        contract = data.get("contract") if isinstance(data, Mapping) else None
        names = [
            str((item or {}).get("filename") or (item or {}).get("artifact_name") or "").strip()
            for item in (contract or [])
            if isinstance(item, Mapping)
        ]
        names = [item for item in names if item]
        return "Code outputs: " + ", ".join(names[:5]) if names else "Code outputs prepared."
    if sub_type == "code_exec.status":
        status = str(data.get("status") or "").strip().lower() if isinstance(data, Mapping) else ""
        if status in {"gen", "generating", "codegen"}:
            return "Generating code..."
        if status in {"exec", "running"}:
            return "Running code..."
        if status in {"done", "completed", "ok", "success"} or completed:
            return "Code execution finished."
        if status:
            return f"Code status: {status}"
        return "Code execution update received." if completed else "Running code..."
    if sub_type == "code_exec.code":
        return "Code generated." if completed else "Generating code..."
    return "Code execution update received." if completed else "Running code..."


def _json_object(value: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def _first_url(data: Mapping[str, Any]) -> str:
    if not isinstance(data, Mapping):
        return ""
    for key in ("url", "source_url", "href", "uri"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    for key in ("urls", "sources", "items", "results"):
        value = data.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
                if isinstance(item, Mapping):
                    found = _first_url(item)
                    if found:
                        return found
    return ""


def _display_url(value: str) -> str:
    parsed = urllib.parse.urlparse(str(value or "").strip())
    if not parsed.netloc:
        return _clip_text(value, 120)
    path = parsed.path.rstrip("/")
    compact = parsed.netloc + (path if path else "")
    return _clip_text(compact, 120)


def _clip_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _progress_note_html(label: str, text: str) -> str:
    label_key = str(label or "").strip().lower()
    title = "Thinking" if label_key == "thinking" else "Notes"
    raw_text = str(text or "").strip()
    if _is_trivial_delta_text(raw_text):
        return ""
    body = _telegram_text(raw_text)
    if not body:
        return ""
    return f"<b>{html.escape(title, quote=False)}</b>\n<blockquote>{html.escape(body, quote=False)}</blockquote>"


def _progress_plain_html(text: str) -> str:
    return html.escape(str(text or "").strip(), quote=False)


def _html_to_plain(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", str(value or ""))
    return html.unescape(without_tags)


def _fit_progress_chunk_html(chunk: str, limit: int) -> str:
    limit = max(20, int(limit or 3900))
    value = str(chunk or "").strip()
    if len(value) <= limit:
        return value
    match = re.match(r"^(<b>[^<]+</b>\n<blockquote>)(.*)(</blockquote>)$", value, flags=re.DOTALL)
    if match:
        prefix, inner_html, suffix = match.groups()
        max_inner = max(1, limit - len(prefix) - len(suffix) - 1)
        inner = html.unescape(inner_html)
        if len(inner) > max_inner:
            inner = inner[:max_inner].rstrip() + "…"
        return f"{prefix}{html.escape(inner, quote=False)}{suffix}"
    plain = _html_to_plain(value)
    if len(plain) > limit:
        plain = plain[: max(0, limit - 1)].rstrip() + "…"
    return html.escape(plain, quote=False)
