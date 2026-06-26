# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
conversations_export tool logic.

Normalizes conversation turns into flat per-conversation records and sweeps every
tenant/project by default (the only true storage partition; conversations are
returned across all bundles because the underlying control-plane data source
bypasses ambient bundle_id filtering). This is the server-side reuse of the
Phase-0 extractor's normalizer.

The ``DataSource`` is an injected adapter so this logic stays unit-testable; the
production adapter wraps ``conversations_browser`` (list/fetch + materialize).
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
