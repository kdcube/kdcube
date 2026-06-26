# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Production data adapter for the conversations_export MCP tool.

Reuses ``conversations_browser`` primitives (``_build_ctx`` ->
``ctx.list_conversations`` / ``ctx.fetch_conversation_artifacts`` with the
``ctx={}`` bundle-bypass) and the Phase-0 extractor's turn-collapse logic. The
collapse step (artifacts -> flat user/assistant/attachments/citations) is pure
and unit-tested; the DB-backed sweep is exercised live in Phase 2.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.ingress.oauth_mcp.export_tool import export_conversations


def _payload(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        if "payload" in data and isinstance(data["payload"], dict):
            return data["payload"]
        return data
    return {}


def collapse_turn(turn: Dict[str, Any]) -> Dict[str, Any]:
    """Collapse a fetched turn's artifacts into a flat record.

    Ported from scripts/feedback/extract_conversations.py (Phase-0). Artifact
    types come from conversations_browser._build_turn_rows.
    """
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


def _parse_iso(value: Optional[str]) -> Optional[_dt.datetime]:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


class ControlPlaneDataSource:
    """DataSource over the control-plane conversation store (all bundles).

    Integration seam: the sweep wiring is validated live in Phase 2.
    """

    def __init__(self, pool):
        self._pool = pool

    async def list_tenant_projects(self) -> List[tuple]:
        async with self._pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    "SELECT tenant, project FROM kdcube_control_plane.registered_projects "
                    "ORDER BY tenant, project"
                )
            except Exception:
                rows = []
        return [(r["tenant"], r["project"]) for r in rows]

    async def _list_users(self, ctx, tenant: str, project: str) -> List[str]:
        # ctx.list_conversations is per-user; discover users via the schema.
        from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema
        schema = project_schema(tenant, project)
        async with self._pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    f"SELECT DISTINCT user_id FROM {schema}.conv_messages "
                    f"WHERE user_id IS NOT NULL ORDER BY user_id LIMIT 2000"
                )
            except Exception:
                return []
        return [r["user_id"] for r in rows]

    async def list_conversations(self, tenant: str, project: str, since: Optional[str]) -> List[Dict[str, Any]]:
        from kdcube_ai_app.apps.chat.ingress.control_plane.conversations_browser import _build_ctx

        ctx = await _build_ctx(self._pool, tenant, project)
        started_after = _parse_iso(since)
        out: List[Dict[str, Any]] = []
        for user_id in await self._list_users(ctx, tenant, project):
            convs = await ctx.list_conversations(
                user_id=user_id, last_n=None, started_after=started_after,
                days=3650, include_titles=True, ctx={},
            )
            for conv in convs or []:
                conv_id = conv.get("conversation_id") or conv.get("id")
                if not conv_id:
                    continue
                fetched = await ctx.fetch_conversation_artifacts(
                    user_id=user_id, conversation_id=conv_id, turn_ids=None,
                    materialize=True, days=3650, ctx={},
                )
                out.append({
                    "conversation_id": conv_id,
                    "user_id": user_id,
                    "started_at": conv.get("started_at") or conv.get("created_at") or "",
                    "title": conv.get("title") or "",
                    "turns": [collapse_turn(t) for t in (fetched.get("turns") or [])],
                })
        return out


def build_default_tools(request) -> Dict[str, Any]:
    """Production MCP tool set: conversations_export over the control-plane store."""
    async def _runner(arguments: Dict[str, Any], user: Dict[str, Any]):
        from kdcube_ai_app.apps.chat.ingress.resolvers import get_pg_pool

        pool = await get_pg_pool()
        ds = ControlPlaneDataSource(pool)
        records = await export_conversations(
            ds,
            since=arguments.get("since"),
            tenant=arguments.get("tenant"),
            project=arguments.get("project"),
        )
        return {"count": len(records), "conversations": records}

    return {"conversations_export": _runner}
