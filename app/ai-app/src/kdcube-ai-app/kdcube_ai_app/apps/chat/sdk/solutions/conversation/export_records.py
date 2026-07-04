# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation export record normalization.

This is conversation-domain code. It is deliberately independent from
Connection Hub delegated OAuth; OAuth may authorize a caller, but it does not
own how conversations are listed, materialized, or flattened.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


def source_for_user(user_id: str) -> str:
    """Telegram subjects are ``telegram:*``; everything else is a web/oauth user."""
    return "telegram" if (user_id or "").startswith("telegram:") else "web"


def normalize_turn(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "turn_id": raw.get("turn_id"),
        "ts": raw.get("ts"),
        "user": raw.get("user"),
        "assistant": raw.get("assistant"),
        "attachments": raw.get("attachments", []),
        "citations": raw.get("citations", []),
    }


def normalize_conversation(raw: Dict[str, Any], *, tenant: str, project: str) -> Dict[str, Any]:
    user_id = raw.get("user_id")
    return {
        "conversation_id": raw.get("conversation_id"),
        "tenant": tenant,
        "project": project,
        "user_id": user_id,
        "source": source_for_user(user_id),
        "started_at": raw.get("started_at"),
        "title": raw.get("title"),
        "turns": [normalize_turn(t) for t in raw.get("turns", [])],
    }


class DataSource(Protocol):
    async def list_tenant_projects(self) -> List[tuple]: ...
    async def list_conversations(self, tenant: str, project: str, since: Optional[str]) -> List[Dict[str, Any]]: ...


async def export_conversations(
    datasource: DataSource,
    *,
    since: Optional[str] = None,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if tenant and project:
        targets = [(tenant, project)]
    else:
        targets = await datasource.list_tenant_projects()

    records: List[Dict[str, Any]] = []
    for t, p in targets:
        for raw in await datasource.list_conversations(t, p, since):
            records.append(normalize_conversation(raw, tenant=t, project=p))
    return records


def _payload(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        if "payload" in data and isinstance(data["payload"], dict):
            return data["payload"]
        return data
    return {}


def collapse_turn(turn: Dict[str, Any]) -> Dict[str, Any]:
    """Collapse a fetched turn's artifacts into a flat record."""
    user_msgs: List[str] = []
    assistant_msgs: List[str] = []
    attachments: List[Dict[str, Any]] = []
    followups: List[str] = []
    citations: List[Dict[str, Any]] = []
    bot_artifacts: List[str] = []

    for art in turn.get("artifacts") or []:
        if not isinstance(art, dict):
            continue
        art_type = art.get("type") or ""
        data = art.get("data")
        if art_type == "chat:user":
            text = (_payload(data).get("text") or "").strip()
            if text:
                user_msgs.append(text)
        elif art_type == "chat:assistant":
            text = (_payload(data).get("text") or "").strip()
            if text:
                assistant_msgs.append(text)
        elif art_type == "artifact:user.attachment":
            p = _payload(data)
            attachments.append({k: v for k, v in p.items() if k not in {"base64", "bytes"}})
        elif art_type == "artifact:conv.user_shortcuts":
            items = _payload(data).get("items") or []
            if isinstance(items, list):
                followups.extend(str(i) for i in items if i)
        elif art_type == "artifact:solver.program.citables":
            items = _payload(data).get("items") or []
            if isinstance(items, list):
                citations.extend(i for i in items if isinstance(i, dict))
        elif art_type.startswith("artifact:"):
            bot_artifacts.append(art_type)

    return {
        "turn_id": turn.get("turn_id") or "",
        "user": "\n\n".join(user_msgs),
        "assistant": "\n\n".join(assistant_msgs),
        "attachments": attachments,
        "followups": list(dict.fromkeys(followups)),
        "citations": citations,
        "bot_artifacts": list(dict.fromkeys(bot_artifacts)),
    }


__all__ = [
    "DataSource",
    "collapse_turn",
    "export_conversations",
    "normalize_conversation",
    "normalize_turn",
    "source_for_user",
]
