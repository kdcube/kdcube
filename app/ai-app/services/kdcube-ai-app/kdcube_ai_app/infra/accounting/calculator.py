# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/accounting/calculator.py
"""
Optimized read-only usage aggregation leveraging conversation-aware file naming.

NEW FILENAME FORMAT:
- Conversation-based: cb|<user_id>|<conversation_id>|<turn_id>|<agent_name>|<timestamp>.json
- Knowledge-based: kb|<timestamp>.json

This enables efficient prefix filtering:
- All files for user: "cb|user-123|"
- All files for conversation: "cb|user-123|conv-abc|"
- Specific turn: "cb|user-123|conv-abc|turn-001|"
- Specific agent in turn: "cb|user-123|conv-abc|turn-001|answer_generator|"
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple, AsyncIterator

from kdcube_ai_app.infra.accounting import get_turn_events
from kdcube_ai_app.storage.storage import IStorageBackend, create_storage_backend

logger = logging.getLogger("AccountingCalculator")

# -----------------------------
# Query model
# -----------------------------
@dataclass
class AccountingQuery:
    # storage location
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None

    # time window (inclusive) — ISO "YYYY-MM-DD"
    date_from: Optional[str] = None
    date_to: Optional[str] = None

    # NEW: conversation-aware filters (enable prefix optimization)
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    turn_id: Optional[str] = None
    agent_name: Optional[str] = None  # NEW: agent-level filtering

    # context filters (exact match if provided)
    app_bundle_id: Optional[str] = None
    client_id: Optional[str] = None
    session_id: Optional[str] = None
    component: Optional[str] = None
    provider: Optional[str] = None
    model_or_service: Optional[str] = None
    service_types: Optional[List[str]] = None

    # free-form predicate
    predicate: Optional[Any] = None

    # safety: max number of files to read
    hard_file_limit: Optional[int] = None

# -----------------------------
# Filename parsing helpers
# -----------------------------
def _parse_filename(filename: str) -> Optional[Dict[str, str]]:
    """
    Parse new filename format:
    - cb|<user>|<conv>|<turn>|<agent>|<ts>.json
    - kb|<ts>.json

    Returns dict with keys: type, user_id, conversation_id, turn_id, agent_name, timestamp
    """
    if not filename.endswith('.json'):
        return None

    parts = filename[:-5].split('|')  # Remove .json and split

    if len(parts) == 2 and parts[0] == 'kb':
        # Knowledge-based file
        return {
            'type': 'kb',
            'user_id': None,
            'conversation_id': None,
            'turn_id': None,
            'agent_name': None,
            'timestamp': parts[1]
        }
    elif len(parts) == 6 and parts[0] == 'cb':
        # Conversation-based file with agent
        return {
            'type': 'cb',
            'user_id': parts[1],
            'conversation_id': parts[2],
            'turn_id': parts[3],
            'agent_name': parts[4],
            'timestamp': parts[5],
        }

    # Old format or unknown - return None to trigger full scan
    return None


def _build_filename_prefix(
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        agent_name: Optional[str] = None,
) -> Optional[str]:
    """
    Build prefix for efficient file filtering.

    Examples:
    - user_id only: "cb|user-123|"
    - user + conv: "cb|user-123|conv-abc|"
    - user + conv + turn: "cb|user-123|conv-abc|turn-001|"
    - user + conv + turn + agent: "cb|user-123|conv-abc|turn-001|answer_generator|"
    """
    if not user_id:
        return None

    prefix = f"cb|{user_id}|"

    if conversation_id:
        prefix += f"{conversation_id}|"

        if turn_id:
            prefix += f"{turn_id}|"

            if agent_name:
                prefix += f"{agent_name}|"

    return prefix


# -----------------------------
# Usage accumulation helpers
# -----------------------------
_BUCKETS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}


def _floor_bucket(ts: datetime, granularity: str) -> str:
    s = _BUCKETS.get(granularity)
    if not s:
        raise ValueError(f"Unknown granularity: {granularity}")
    epoch = int(ts.timestamp())
    start = epoch - (epoch % s)
    return datetime.utcfromtimestamp(start).isoformat(timespec="seconds") + "Z"


def _spent_seed(service_type: str) -> Dict[str, int]:
    """Create initial spent dict with all possible token types."""
    if service_type == "llm":
        return {
            "input": 0,
            "output": 0,
            "cache_creation": 0,
            "cache_read": 0,
            "cache_5m_write": 0,
            "cache_1h_write": 0,
        }
    if service_type == "embedding":
        return {"tokens": 0}
    return {}


def _accumulate_compact(spent: Dict[str, int], ev_usage: Dict[str, Any], service_type: str) -> None:
    """Accumulate token usage including detailed cache breakdowns."""
    if service_type == "llm":
        spent["input"] = int(spent.get("input", 0)) + int(ev_usage.get("input_tokens") or 0)
        spent["output"] = int(spent.get("output", 0)) + int(ev_usage.get("output_tokens") or 0)
        spent["cache_read"] = int(spent.get("cache_read", 0)) + int(ev_usage.get("cache_read_tokens") or 0)

        cache_creation = ev_usage.get("cache_creation")
        if isinstance(cache_creation, dict):
            cache_5m = int(cache_creation.get("ephemeral_5m_input_tokens") or 0)
            cache_1h = int(cache_creation.get("ephemeral_1h_input_tokens") or 0)

            spent["cache_5m_write"] = int(spent.get("cache_5m_write", 0)) + cache_5m
            spent["cache_1h_write"] = int(spent.get("cache_1h_write", 0)) + cache_1h
            spent["cache_creation"] = int(spent.get("cache_creation", 0)) + cache_5m + cache_1h
        else:
            cache_total = int(ev_usage.get("cache_creation_tokens") or 0)
            if cache_total > 0:
                spent["cache_creation"] = int(spent.get("cache_creation", 0)) + cache_total
                spent["cache_5m_write"] = int(spent.get("cache_5m_write", 0)) + cache_total

    elif service_type == "embedding":
        spent["tokens"] = int(spent.get("tokens", 0)) + int(ev_usage.get("embedding_tokens") or 0)


_USAGE_KEYS = [
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "cache_creation",
    "total_tokens",
    "embedding_tokens",
    "embedding_dimensions",
    "search_queries",
    "search_results",
    "image_count",
    "image_pixels",
    "audio_seconds",
    "requests",
    "cost_usd",
]


def _new_usage_acc() -> Dict[str, Any]:
    acc = {k: (0.0 if k in ("audio_seconds", "cost_usd") else 0) for k in _USAGE_KEYS}
    acc["cache_creation"] = None
    return acc


def _extract_usage(ev: Dict[str, Any]) -> Dict[str, Any] | None:
    u = ev.get("usage")
    if not isinstance(u, dict):
        return None

    out = {}
    for k in _USAGE_KEYS:
        v = u.get(k)

        if k == "cache_creation":
            if isinstance(v, dict):
                out[k] = dict(v)
            else:
                out[k] = None
            continue

        if v is None:
            out[k] = 0.0 if k in ("audio_seconds", "cost_usd") else 0
        else:
            if k in ("audio_seconds", "cost_usd"):
                out[k] = float(v)
            elif isinstance(v, (int, float)):
                out[k] = int(v)
            else:
                out[k] = v

    return out


def _accumulate(acc: Dict[str, Any], usage: Dict[str, Any]) -> None:
    for k in _USAGE_KEYS:
        if k == "cache_creation":
            continue
        elif k == "cost_usd":
            acc["cost_usd"] = float(acc.get("cost_usd", 0.0)) + float(usage.get("cost_usd", 0.0))
        elif k == "audio_seconds":
            acc[k] = float(acc.get(k, 0.0)) + float(usage.get(k, 0.0))
        else:
            acc[k] = int(acc.get(k, 0)) + int(usage.get(k, 0))


def _finalize_cost(acc: Dict[str, Any]) -> None:
    pass


def _group_key(ev: Dict[str, Any], group_by: List[str]) -> Tuple[Any, ...]:
    ctx = ev.get("context") or {}
    metadata = ev.get("metadata") or {}

    dt_label = None
    if "date" in group_by:
        ts = ev.get("timestamp")
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
            dt_label = d.isoformat()
        except Exception:
            dt_label = None

    out: List[Any] = []
    for g in group_by:
        if g == "date":
            out.append(dt_label)
            continue
        if g == "agent" or g == "agent_name":
            # Try multiple locations for agent name
            agent = (
                    metadata.get("agent")
                    or ctx.get("agent")
                    or ev.get("agent")
                    or ev.get("agent_name")
            )
            out.append(agent)
            continue
        out.append(ev.get(g) if g in ev else ctx.get(g))
    return tuple(out)


def _tokens_for_event(ev: Dict[str, Any]) -> int:
    u = ev.get("usage") or {}
    tot = u.get("total_tokens")
    if tot is None:
        tot = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
    tot += int(u.get("embedding_tokens") or 0)
    try:
        return int(tot)
    except Exception:
        return 0


# -----------------------------
# Date helpers
# -----------------------------
def _parse_iso_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _looks_dot_date(s: str) -> bool:
    if len(s) != 10:
        return False
    try:
        datetime.strptime(s, "%Y.%m.%d")
        return True
    except Exception:
        return False


class _DateRange:
    def __init__(self, dfrom: Optional[str], dto: Optional[str]):
        self.df = _parse_iso_date(dfrom) if dfrom else None
        self.dt = _parse_iso_date(dto) if dto else None

    def contains_dot(self, dot_date: str) -> bool:
        try:
            d = datetime.strptime(dot_date, "%Y.%m.%d").date()
        except Exception:
            return True
        return self._contains(d)

    def contains_iso(self, iso_date: str) -> bool:
        try:
            d = _parse_iso_date(iso_date)
        except Exception:
            return True
        return self._contains(d)

    def _contains(self, d: date) -> bool:
        if self.df and d < self.df:
            return False
        if self.dt and d > self.dt:
            return False
        return True


# -----------------------------
# Calculator (OPTIMIZED)
# -----------------------------
class AccountingCalculator:
    def __init__(self, storage_backend: IStorageBackend, *, base_path: str = "accounting"):
        self.fs = storage_backend
        self.base = base_path.strip("/")

    # ---------- Optimized path iteration ----------

    async def _iter_event_paths(self, query: AccountingQuery) -> AsyncIterator[str]:
        """
        Yield event file paths with optimization for conversation-aware queries.

        OPTIMIZATION: When user_id/conversation_id/turn_id/agent_name are specified,
        use prefix filtering instead of listing all files.
        """
        # Normalize alias
        if query.client_id and not query.app_bundle_id:
            query.app_bundle_id = query.client_id

        # Discover tenant roots
        tenant_roots: List[str] = []
        if query.tenant_id:
            tenant_roots = [f"{self.base}/{query.tenant_id}"]
        else:
            for t in await self._safe_listdir(self.base):
                tenant_roots.append(f"{self.base}/{t}")

        # Project layer
        project_roots: List[str] = []
        for troot in tenant_roots:
            if query.project_id:
                project_roots.append(f"{troot}/{query.project_id}")
            else:
                for p in await self._safe_listdir(troot):
                    project_roots.append(f"{troot}/{p}")

        # Date layer
        wanted_dates = _DateRange(query.date_from, query.date_to)

        for proot in project_roots:
            # Get date directories (both formats)
            entries = await self._safe_listdir(proot)
            dot_dates = [d for d in entries if _looks_dot_date(d)]
            years = [
                y
                for y in entries
                if y.isdigit() and len(y) == 4 and y not in dot_dates
            ]

            # Scan dot-date layout (primary)
            for d in sorted(dot_dates):
                if not wanted_dates.contains_dot(d):
                    continue
                droot = f"{proot}/{d}"
                async for p in self._iter_events_under_date_root(droot, query):
                    yield p

            # Scan legacy yyyy/mm/dd layout
            for y in sorted(years):
                yroot = f"{proot}/{y}"
                months = [
                    m for m in await self._safe_listdir(yroot)
                    if m.isdigit() and len(m) == 2
                ]
                for m in sorted(months):
                    mroot = f"{yroot}/{m}"
                    days = [
                        dd for dd in await self._safe_listdir(mroot)
                        if dd.isdigit() and len(dd) == 2
                    ]
                    for dd in sorted(days):
                        d_iso = f"{y}-{m}-{dd}"
                        if not wanted_dates.contains_iso(d_iso):
                            continue
                        droot = f"{mroot}/{dd}"
                        async for p in self._iter_events_under_date_root(droot, query):
                            yield p

    async def _iter_events_under_date_root(
            self,
            date_root: str,
            query: AccountingQuery,
    ) -> AsyncIterator[str]:
        """
        Iterate events under a date root with prefix optimization.

        Structure: <date_root>/<service_type>/<group>/files
        """
        stypes = await self._safe_listdir(date_root)

        for st in sorted(stypes):
            # Filter by service type
            if query.service_types and st not in query.service_types:
                continue

            st_root = f"{date_root}/{st}"

            # List group directories
            groups = await self._safe_listdir(st_root)

            # Check if this is flat (files directly) or grouped (subdirs)
            has_json_files = any(name.endswith('.json') for name in groups)

            if has_json_files:
                # Flat structure
                async for path in self._iter_files_in_group(st_root, query):
                    yield path
            else:
                # Grouped structure
                for g in groups:
                    groot = f"{st_root}/{g}"
                    async for path in self._iter_files_in_group(groot, query):
                        yield path

    async def _iter_files_in_group(
            self,
            group_path: str,
            query: AccountingQuery,
    ) -> AsyncIterator[str]:
        """
        Iterate files in a group directory with prefix optimization.

        KEY OPTIMIZATION: Use prefix filtering when possible.
        """
        # Build prefix for efficient filtering (now includes agent_name)
        prefix = _build_filename_prefix(
            user_id=query.user_id,
            conversation_id=query.conversation_id,
            turn_id=query.turn_id,
            agent_name=query.agent_name,
        )

        if prefix:
            # OPTIMIZED: Use prefix filtering
            filenames = await self.fs.list_with_prefix_a(group_path, prefix)
        else:
            # Fallback: List all files
            filenames = [
                f for f in await self._safe_listdir(group_path)
                if f.endswith(".json")
            ]

        for fname in filenames:
            yield f"{group_path}/{fname}"

    async def _safe_listdir(self, path: str) -> List[str]:
        try:
            return await self.fs.list_dir_a(path)
        except Exception:
            return []

    # ---------- Filter helpers ----------

    def _match(self, ev: Dict[str, Any], query: AccountingQuery) -> bool:
        """Filter event by query criteria."""

        def eq(k: str, qv: Optional[str]) -> bool:
            if qv is None:
                return True
            evv = ev.get(k) or (ev.get("context") or {}).get(k)
            return evv == qv

        if not eq("tenant_id", query.tenant_id):
            return False
        if not eq("project_id", query.project_id):
            return False
        if not eq("app_bundle_id", query.app_bundle_id):
            return False
        if not eq("user_id", query.user_id):
            return False
        if not eq("conversation_id", query.conversation_id):
            return False
        if not eq("turn_id", query.turn_id):
            return False
        if not eq("session_id", query.session_id):
            return False
        if not eq("component", query.component):
            return False
        if not eq("provider", query.provider):
            return False
        if not eq("model_or_service", query.model_or_service):
            return False

        # Agent name matching (check multiple locations)
        if query.agent_name:
            metadata = ev.get("metadata") or {}
            context = ev.get("context") or {}
            agent = (
                    metadata.get("agent")
                    or context.get("agent")
                    or ev.get("agent")
                    or ev.get("agent_name")
            )
            if agent != query.agent_name:
                return False

        if query.service_types:
            st = ev.get("service_type")
            if st not in query.service_types:
                return False

        # Date window
        if query.date_from or query.date_to:
            ts = ev.get("timestamp")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
            except Exception:
                dt = None
            if dt:
                d = dt.date()
                if query.date_from and d < _parse_iso_date(query.date_from):
                    return False
                if query.date_to and d > _parse_iso_date(query.date_to):
                    return False

        if query.predicate and callable(query.predicate):
            try:
                if not bool(query.predicate(ev)):
                    return False
            except Exception:
                return False

        return True

    # ---------- Aggregation core ----------

    async def _aggregate(
            self,
            paths: AsyncIterator[str],
            query: AccountingQuery,
            group_by: List[str],
    ) -> Tuple[Dict[str, Any], Dict[Tuple[Any, ...], Dict[str, Any]], int]:
        """Aggregate usage from event files."""
        totals = _new_usage_acc()
        groups: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        count = 0

        max_files = query.hard_file_limit if query.hard_file_limit and query.hard_file_limit > 0 else None
        processed = 0

        async for p in paths:
            if max_files is not None and processed >= max_files:
                break
            processed += 1

            try:
                raw = await self.fs.read_text_a(p)
            except Exception:
                continue

            try:
                ev = json.loads(raw)
            except Exception:
                logger.warning("Skipping unreadable JSON: %s", p)
                continue

            if not self._match(ev, query):
                continue

            usage = _extract_usage(ev)
            if not usage:
                count += 1
                continue

            _accumulate(totals, usage)

            if group_by:
                key = _group_key(ev, group_by)
                if key not in groups:
                    groups[key] = _new_usage_acc()
                _accumulate(groups[key], usage)

            count += 1

        _finalize_cost(totals)
        for k in groups:
            _finalize_cost(groups[k])

        # Prettify groups
        pretty_groups: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for k_tuple, usage_dict in groups.items():
            labels = {group_by[i]: k_tuple[i] for i in range(len(group_by))}
            labels.update(usage_dict)
            pretty_groups[k_tuple] = labels

        return totals, pretty_groups, count

    # ---------- Public generic API ----------

    async def query_usage(
            self,
            query: AccountingQuery,
            *,
            group_by: Optional[List[str]] = None,
            include_event_count: bool = True,
    ) -> Dict[str, Any]:
        """
        Read matching events and return totals + optional grouped totals.

        OPTIMIZED: Automatically uses prefix filtering when user_id/conversation_id/turn_id/agent_name
        are specified in the query.
        """
        events_iter = self._iter_event_paths(query)
        total, groups, n_events = await self._aggregate(events_iter, query, group_by or [])

        out = {
            "filters": asdict(query),
            "total": total,
            "groups": groups if group_by else {},
        }
        if include_event_count:
            out["event_count"] = n_events
        return out

    async def usage_rollup_compact(
            self,
            query: AccountingQuery,
            *,
            include_zero: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Return compact list grouped by (service_type, provider, model_or_service).

        OPTIMIZED: Uses prefix filtering when applicable.
        """
        rollup: Dict[Tuple[str, str, str], Dict[str, int]] = {}

        max_files = query.hard_file_limit if query.hard_file_limit and query.hard_file_limit > 0 else None
        processed = 0

        async for p in self._iter_event_paths(query):
            if max_files is not None and processed >= max_files:
                break
            processed += 1

            try:
                raw = await self.fs.read_text_a(p)
                ev = json.loads(raw)
            except Exception:
                continue

            if not self._match(ev, query):
                continue

            service = str(ev.get("service_type") or "").strip()
            if not service:
                continue

            provider = str(ev.get("provider") or (ev.get("context") or {}).get("provider") or "").strip()
            model = str(ev.get("model_or_service") or (ev.get("context") or {}).get("model_or_service") or "").strip()

            key = (service, provider, model)
            spent = rollup.get(key)
            if not spent:
                spent = _spent_seed(service)
                rollup[key] = spent

            usage = _extract_usage(ev) or {}
            _accumulate_compact(spent, usage, service)

        items: List[Dict[str, Any]] = []
        for (service, provider, model) in sorted(rollup.keys()):
            spent = rollup[(service, provider, model)]
            if not include_zero:
                if service == "llm" and (spent.get("input", 0) == 0 and spent.get("output", 0) == 0):
                    continue
                if service == "embedding" and spent.get("tokens", 0) == 0:
                    continue
            items.append({
                "service": service,
                "provider": provider or None,
                "model": model or None,
                "spent": {k: int(v) for k, v in spent.items()},
            })

        return items

    # ---------- aggregate-aware helper for usage_all_users ----------

    async def _usage_all_users_with_aggregates(
        self,
        *,
        tenant_id: str,
        project_id: str,
        date_from: str,
        date_to: str,
        app_bundle_id: Optional[str] = None,
        service_types: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Aggregate-aware implementation of usage_all_users.

        It will:
        - Use daily aggregate buckets when present:
          accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>.daily/aggregate.json
        - For days without aggregates, fall back to raw-file scanning
          (via query_usage + usage_rollup_compact) for that sub-range only.

        RETURNS
        -------
        dict with same shape as usage_all_users result, or
        None if date range cannot be parsed.
        """
        try:
            df = _parse_iso_date(date_from)
            dt = _parse_iso_date(date_to)
        except Exception:
            return None

        if df > dt:
            return None

        # Precompute per-day availability of daily aggregates
        days: List[date] = []
        cur = df
        while cur <= dt:
            days.append(cur)
            cur += timedelta(days=1)

        if not days:
            return None

        agg_exists: Dict[date, bool] = {}
        for d in days:
            bname = f"{d.year:04d}.{d.month:02d}.{d.day:02d}.daily"
            folder = f"{self.base}/{tenant_id}/{project_id}/{bname}"
            path = f"{folder}/aggregate.json"
            try:
                agg_exists[d] = await self.fs.exists_a(path)
            except Exception:
                agg_exists[d] = False

        # Build contiguous segments [start, end, is_aggregated]
        segments: List[Tuple[date, date, bool]] = []
        current_start = days[0]
        current_flag = agg_exists[days[0]]

        for d in days[1:]:
            flag = agg_exists[d]
            if flag == current_flag:
                continue
            # close previous segment [current_start, previous_day]
            prev_day = d - timedelta(days=1)
            segments.append((current_start, prev_day, current_flag))
            current_start = d
            current_flag = flag

        # close last segment
        segments.append((current_start, days[-1], current_flag))

        # Global accumulators
        total = _new_usage_acc()
        rollup_map: Dict[Tuple[str, str, str], Dict[str, int]] = {}
        user_ids: set[str] = set()
        event_count = 0

        def _merge_rollup_items(items: List[Dict[str, Any]]) -> None:
            for it in items:
                service = it.get("service")
                provider = it.get("provider") or ""
                model = it.get("model") or ""
                spent = it.get("spent") or {}
                key = (service, provider, model)

                existing = rollup_map.get(key)
                if not existing:
                    existing = _spent_seed(service or "")
                    rollup_map[key] = existing

                for k, v in spent.items():
                    existing[k] = int(existing.get(k, 0)) + int(v or 0)

        # Walk segments
        for seg_start, seg_end, is_agg in segments:
            if is_agg:
                # Use daily aggregates directly
                d = seg_start
                while d <= seg_end:
                    bname = f"{d.year:04d}.{d.month:02d}.{d.day:02d}.daily"
                    folder = f"{self.base}/{tenant_id}/{project_id}/{bname}"
                    path = f"{folder}/aggregate.json"

                    try:
                        raw = await self.fs.read_text_a(path)
                        payload = json.loads(raw)
                    except Exception:
                        # If we can't read now, we just skip this day and let raw
                        # scanning cover it in a future run; for this run, we ignore it.
                        logger.debug(
                            "[usage_all_users_with_aggregates] Failed to read %s, skipping",
                            path,
                            exc_info=True,
                        )
                        d += timedelta(days=1)
                        continue

                    bucket_total = payload.get("total") or {}
                    _accumulate(total, bucket_total)

                    _merge_rollup_items(payload.get("rollup") or [])

                    nonlocal_event_count = int(payload.get("event_count") or 0)
                    event_count += nonlocal_event_count

                    for uid in payload.get("user_ids", []):
                        if uid is not None:
                            user_ids.add(str(uid))

                    d += timedelta(days=1)
            else:
                # No aggregates for this segment: fall back to raw scan
                seg_from = seg_start.isoformat()
                seg_to = seg_end.isoformat()

                seg_query = AccountingQuery(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    date_from=seg_from,
                    date_to=seg_to,
                    app_bundle_id=app_bundle_id,
                    service_types=service_types,
                    hard_file_limit=None,  # we are already in "no limit" path
                )

                res = await self.query_usage(seg_query, group_by=["user_id"])
                _accumulate(total, res["total"])
                event_count += int(res.get("event_count") or 0)

                # user_ids: union of group keys
                for _key_tuple, usage in res["groups"].items():
                    uid = usage.get("user_id")
                    if uid:
                        user_ids.add(str(uid))

                seg_rollup = await self.usage_rollup_compact(seg_query)
                _merge_rollup_items(seg_rollup)

        # Build final rollup list
        rollup_list: List[Dict[str, Any]] = []
        for (service, provider, model), spent in sorted(rollup_map.items()):
            rollup_list.append(
                {
                    "service": service,
                    "provider": provider or None,
                    "model": model or None,
                    "spent": {k: int(v) for k, v in spent.items()},
                }
            )

        return {
            "total": total,
            "rollup": rollup_list,
            "user_count": len(user_ids),
            "event_count": event_count,
        }

    # ---------- NEW: User-level queries ----------

    async def usage_by_user(
            self,
            *,
            tenant_id: str,
            project_id: str,
            date_from: str,
            date_to: str,
            app_bundle_id: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Query (1): Spendings per user in given timeframe.

        Returns:
          {
            "user-123": {"total": {...}, "rollup": [...]},
            "user-456": {"total": {...}, "rollup": [...]},
            ...
          }

        OPTIMIZED: Groups results by user without pre-filtering (must scan all users).
        """
        query = AccountingQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            app_bundle_id=app_bundle_id,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )

        # Group by user_id
        result = await self.query_usage(query, group_by=["user_id"])

        # Reorganize by user
        by_user: Dict[str, Dict[str, Any]] = {}
        for key_tuple, usage in result["groups"].items():
            user_id = usage.get("user_id")
            if user_id:
                by_user[user_id] = {"total": usage}

        # Add compact rollup per user
        for user_id in list(by_user.keys()):
            user_query = AccountingQuery(
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=user_id,
                date_from=date_from,
                date_to=date_to,
                app_bundle_id=app_bundle_id,
                service_types=service_types,
                hard_file_limit=hard_file_limit,
            )
            by_user[user_id]["rollup"] = await self.usage_rollup_compact(user_query)

        return by_user

    async def usage_all_users(
            self,
            *,
            tenant_id: str,
            project_id: str,
            date_from: str,
            date_to: str,
            app_bundle_id: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Query (2): Total spendings for all users in given timeframe.

        Returns:
          {
            "total": {...},
            "rollup": [...],
            "user_count": N,
            "event_count": M,
        }

        AGGREGATE-AWARE:
        - If possible (global query: no app_bundle_id, no service_types, no hard_file_limit),
          it will combine:
            * daily aggregates where present, and
            * raw-file scan for "gap" days.
        - Otherwise falls back to pure raw scan.
        """
        # Fast path: use aggregates when query is truly global
        use_aggregates = (
            app_bundle_id is None
            and (not service_types or len(service_types) == 0)
            and hard_file_limit is None
        )

        if use_aggregates:
            try:
                agg_res = await self._usage_all_users_with_aggregates(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    date_from=date_from,
                    date_to=date_to,
                    app_bundle_id=app_bundle_id,
                    service_types=service_types,
                )
            except Exception:
                logger.exception(
                    "[usage_all_users] aggregate path failed, falling back to raw scan"
                )
                agg_res = None

            if agg_res is not None:
                return agg_res

        # Fallback: previous behavior (pure raw scan)
        query = AccountingQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            app_bundle_id=app_bundle_id,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )

        result = await self.query_usage(query, group_by=["user_id"])

        # Count unique users
        user_count = len([k for k in result["groups"].keys() if k[0]])  # Filter out None user_ids

        return {
            "total": result["total"],
            "rollup": await self.usage_rollup_compact(query),
            "user_count": user_count,
            "event_count": result.get("event_count", 0),
        }

    async def usage_user_conversation(
            self,
            *,
            tenant_id: str,
            project_id: str,
            user_id: str,
            conversation_id: str,
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            app_bundle_id: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Query (3): Usage for specific user conversation in given timeframe.

        HIGHLY OPTIMIZED: Uses prefix "cb|<user>|<conversation>|" for efficient filtering.
        """
        # Auto-set date range if not provided
        if not date_from and not date_to:
            from datetime import timedelta

            today = datetime.utcnow().date()
            date_from = (today - timedelta(days=7)).isoformat()
            date_to = today.isoformat()

        query = AccountingQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=user_id,
            conversation_id=conversation_id,
            date_from=date_from,
            date_to=date_to,
            app_bundle_id=app_bundle_id,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )

        result = await self.query_usage(query, group_by=["turn_id"])
        rollup = await self.usage_rollup_compact(query)

        return {
            "total": result["total"],
            "rollup": rollup,
            "turns": result["groups"],
            "event_count": result.get("event_count", 0),
        }

    # ---------- NEW: Agent-level queries ----------

    async def usage_by_agent(
            self,
            *,
            tenant_id: str,
            project_id: str,
            date_from: str,
            date_to: str,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            turn_id: Optional[str] = None,
            app_bundle_id: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Query: Spendings per agent in given timeframe/scope.

        Returns:
          {
            "answer_generator": {"total": {...}, "rollup": [...]},
            "solver.codegen": {"total": {...}, "rollup": [...]},
            ...
          }

        HIGHLY OPTIMIZED: Uses prefix filtering when user_id/conversation_id/turn_id provided.
        """
        query = AccountingQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            date_from=date_from,
            date_to=date_to,
            app_bundle_id=app_bundle_id,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )

        # Group by agent
        result = await self.query_usage(query, group_by=["agent_name"])

        # Reorganize by agent
        by_agent: Dict[str, Dict[str, Any]] = {}
        for key_tuple, usage in result["groups"].items():
            agent_name = usage.get("agent_name")
            if agent_name:
                by_agent[agent_name] = {"total": usage}

        # Add compact rollup per agent
        for agent_name in list(by_agent.keys()):
            agent_query = AccountingQuery(
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                agent_name=agent_name,
                date_from=date_from,
                date_to=date_to,
                app_bundle_id=app_bundle_id,
                service_types=service_types,
                hard_file_limit=hard_file_limit,
            )
            by_agent[agent_name]["rollup"] = await self.usage_rollup_compact(agent_query)

        return by_agent

    async def usage_for_agent(
            self,
            *,
            tenant_id: str,
            project_id: str,
            agent_name: str,
            date_from: str,
            date_to: str,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            turn_id: Optional[str] = None,
            app_bundle_id: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Query: Usage for specific agent in given timeframe/scope.

        HIGHLY OPTIMIZED: Uses prefix filtering with agent name for maximum efficiency.
        """
        query = AccountingQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            agent_name=agent_name,
            date_from=date_from,
            date_to=date_to,
            app_bundle_id=app_bundle_id,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )

        result = await self.query_usage(query)
        rollup = await self.usage_rollup_compact(query)

        return {
            "agent_name": agent_name,
            "total": result["total"],
            "rollup": rollup,
            "event_count": result.get("event_count", 0),
        }


# -----------------------------
# RateCalculator (extends with time-series and async methods)
# -----------------------------
class RateCalculator(AccountingCalculator):
    """Extended calculator with rate stats and async methods."""

    async def time_series(
            self,
            query: AccountingQuery,
            *,
            granularity: str = "1h",
            group_by: Optional[List[str]] = None,
            include_event_count: bool = True,
    ) -> Dict[str, Any]:
        """Return usage grouped into fixed time buckets."""
        if granularity not in _BUCKETS:
            raise ValueError(f"granularity must be one of {list(_BUCKETS)}")

        paths = [p async for p in self._iter_event_paths(query)]
        max_files = query.hard_file_limit if query.hard_file_limit and query.hard_file_limit > 0 else None
        if max_files is not None:
            paths = paths[:max_files]

        series: Dict[str, Dict[str, Any]] = {}
        gby = group_by or []

        for p in paths:
            try:
                raw = await self.fs.read_text_a(p)
                ev = json.loads(raw)
            except Exception:
                continue

            if not self._match(ev, query):
                continue

            ts = ev.get("timestamp")
            try:
                dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
            except Exception:
                continue

            bucket = _floor_bucket(dt, granularity)

            slot = series.get(bucket)
            if not slot:
                slot = {"total": _new_usage_acc(), "groups": {}, "event_count": 0}
                series[bucket] = slot

            usage = _extract_usage(ev) or _new_usage_acc()
            _accumulate(slot["total"], usage)
            slot["event_count"] += 1

            if gby:
                key = _group_key(ev, gby)
                grp = slot["groups"].get(key)
                if not grp:
                    grp = _new_usage_acc()
                    slot["groups"][key] = grp
                _accumulate(grp, usage)

        out_series = []
        for bucket in sorted(series.keys()):
            slot = series[bucket]
            _finalize_cost(slot["total"])
            pretty_groups = {}
            if gby:
                for k_tuple, usage_dict in slot["groups"].items():
                    _finalize_cost(usage_dict)
                    labels = {gby[i]: k_tuple[i] for i in range(len(gby))}
                    labels.update(usage_dict)
                    pretty_groups[k_tuple] = labels
            item = {
                "bucket": bucket,
                "total": slot["total"],
                "groups": pretty_groups if gby else {},
            }
            if include_event_count:
                item["event_count"] = slot["event_count"]
            out_series.append(item)

        return {"granularity": granularity, "series": out_series}

    async def rate_stats(
            self,
            query: AccountingQuery,
            *,
            granularity: str = "1h",
            group_by: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Convert time_series buckets into per-second rates."""
        ts = await self.time_series(query, granularity=granularity, group_by=group_by)
        bucket_secs = _BUCKETS[granularity]
        rate_series = []

        for row in ts["series"]:
            total = dict(row["total"])
            req = float(total.get("requests", 0) or 0)
            toks = float(total.get("total_tokens", 0) or 0)
            row_rates = {
                **row,
                "req_rate_per_sec": req / bucket_secs,
                "tok_rate_per_sec": toks / bucket_secs,
            }

            if group_by:
                groups_with_rates = {}
                for k, g in row["groups"].items():
                    req_g = float(g.get("requests", 0) or 0)
                    tok_g = float(g.get("total_tokens", 0) or 0)
                    groups_with_rates[k] = {
                        **g,
                        "req_rate_per_sec": req_g / bucket_secs,
                        "tok_rate_per_sec": tok_g / bucket_secs,
                    }
                row_rates["groups"] = groups_with_rates
            rate_series.append(row_rates)

        return {"granularity": granularity, "series": rate_series}

    # ---------- Async methods for turn-level queries ----------

    async def query_turn_usage(
            self,
            *,
            tenant_id: str,
            project_id: str,
            conversation_id: str,
            turn_id: str,
            app_bundle_id: Optional[str] = None,
            user_id: Optional[str] = None,
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            date_hint: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = 5000,
    ) -> Dict[str, Any]:
        """
        Query (4): Usage for specific user turn in conversation.

        HIGHLY OPTIMIZED: Uses prefix "cb|<user>|<conversation>|<turn>|" for maximum efficiency.
        """
        # Build date range
        if date_from or date_to:
            from datetime import timedelta

            if not date_from:
                date_to_dt = datetime.strptime(date_to, "%Y-%m-%d").date()
                date_from = (date_to_dt - timedelta(days=1)).isoformat()
            if not date_to:
                date_from_dt = datetime.strptime(date_from, "%Y-%m-%d").date()
                date_to = (date_from_dt + timedelta(days=1)).isoformat()
        elif date_hint:
            date_from = date_hint
            date_to = date_hint
        else:
            from datetime import timedelta

            today = datetime.utcnow().date()
            yesterday = today - timedelta(days=1)
            date_from = yesterday.isoformat()
            date_to = today.isoformat()

        query = AccountingQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            app_bundle_id=app_bundle_id,
            date_from=date_from,
            date_to=date_to,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )

        paths = [p async for p in self._iter_event_paths(query)]
        totals = _new_usage_acc()
        count = 0
        evs: List[Dict[str, Any]] = []

        for p in paths:
            try:
                ev = json.loads(await self.fs.read_text_a(p))
            except Exception:
                continue

            if not self._match(ev, query):
                continue

            usage = _extract_usage(ev) or {}
            _accumulate(totals, usage)
            evs.append(ev)
            count += 1

        _finalize_cost(totals)

        return {
            "turn_id": turn_id,
            "event_count": count,
            "total_usage": totals,
            "tokens": sum(_tokens_for_event(e) for e in evs),
        }

    async def tokens_for_turn(
            self,
            *,
            tenant_id: str,
            project_id: str,
            turn_id: str,
            conversation_id: str = None,
            user_id: Optional[str] = None,
            app_bundle_id: Optional[str] = None,
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            date_hint: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = 5000,
    ) -> int:
        """Quick token count for a turn."""
        r = await self.query_turn_usage(
            tenant_id=tenant_id,
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_id=user_id,
            app_bundle_id=app_bundle_id,
            date_from=date_from,
            date_to=date_to,
            date_hint=date_hint,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )
        return int(r.get("tokens") or 0)

    async def turn_usage_rollup_compact(
            self,
            *,
            tenant_id: str,
            project_id: str,
            conversation_id: str,
            turn_id: str,
            app_bundle_id: Optional[str] = None,
            user_id: Optional[str] = None,
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            date_hint: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = 5000,
            include_zero: bool = False,
            use_memory_cache: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Compact rollup for a single turn.

        When use_memory_cache=True, this now reads from the Redis-backed
        turn cache via the accounting layer instead of the in-process
        AccountingContext cache.

        Otherwise it falls back to scanning storage with prefix optimization.
        """
        events: List[Dict[str, Any]] = []

        if use_memory_cache:
            # --- FAST PATH: Redis turn cache (via accounting layer) ---
            try:
                cached_events = await get_turn_events(
                    tenant=tenant_id,
                    project=project_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            except Exception as e:
                logger.warning(
                    "Failed to read turn events from Redis cache "
                    "(tenant=%s, project=%s, conv=%s, turn=%s): %s",
                    tenant_id,
                    project_id,
                    conversation_id,
                    turn_id,
                    e,
                )
                cached_events = []

            for ev in cached_events:
                # Filter by app bundle
                if app_bundle_id:
                    ev_bundle = (
                            ev.get("app_bundle_id")
                            or (ev.get("context") or {}).get("app_bundle_id")
                    )
                    if ev_bundle != app_bundle_id:
                        continue

                # Filter by user_id if specified
                if user_id:
                    ev_user = ev.get("user_id") or (ev.get("context") or {}).get("user_id")
                    if ev_user != user_id:
                        continue

                # Filter by service types if specified
                if service_types and ev.get("service_type") not in service_types:
                    continue

                events.append(ev)
        else:
            # --- SLOW PATH: read from storage with prefix optimization ---
            if date_from or date_to:
                from datetime import timedelta

                if not date_from:
                    date_to_dt = datetime.strptime(date_to, "%Y-%m-%d").date()
                    date_from = (date_to_dt - timedelta(days=1)).isoformat()
                if not date_to:
                    date_from_dt = datetime.strptime(date_from, "%Y-%m-%d").date()
                    date_to = (date_from_dt + timedelta(days=1)).isoformat()
            elif date_hint:
                date_from = date_hint
                date_to = date_hint
            else:
                from datetime import timedelta

                today = datetime.utcnow().date()
                yesterday = today - timedelta(days=1)
                date_from = yesterday.isoformat()
                date_to = today.isoformat()

            query = AccountingQuery(
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                app_bundle_id=app_bundle_id,
                date_from=date_from,
                date_to=date_to,
                service_types=service_types,
                hard_file_limit=hard_file_limit,
            )

            paths = [p async for p in self._iter_event_paths(query)]

            for p in paths:
                try:
                    ev = json.loads(await self.fs.read_text_a(p))
                except Exception:
                    continue

                if not self._match(ev, query):
                    continue

                events.append(ev)

        # --- Common aggregation logic for both paths ---
        rollup: Dict[Tuple[str, str, str], Dict[str, int]] = {}

        for ev in events:
            service = str(ev.get("service_type") or "").strip()
            if not service:
                continue

            context = ev.get("context") or {}

            provider = str(
                ev.get("provider") or context.get("provider") or ""
            ).strip()
            model = str(
                ev.get("model_or_service") or context.get("model_or_service") or ""
            ).strip()

            key = (service, provider, model)
            spent = rollup.get(key)
            if not spent:
                spent = _spent_seed(service)
                rollup[key] = spent

            usage = _extract_usage(ev) or {}
            _accumulate_compact(spent, usage, service)

        items: List[Dict[str, Any]] = []
        for (service, provider, model) in sorted(rollup.keys()):
            spent = rollup[(service, provider, model)]
            if not include_zero:
                if service == "llm" and (
                        spent.get("input", 0) == 0 and spent.get("output", 0) == 0
                ):
                    continue
                if service == "embedding" and spent.get("tokens", 0) == 0:
                    continue
            items.append(
                {
                    "service": service,
                    "provider": provider or None,
                    "model": model or None,
                    "spent": {k: int(v) for k, v in spent.items()},
                }
            )

        return items


    async def turn_usage_by_agent(
            self,
            *,
            tenant_id: str,
            project_id: str,
            conversation_id: str,
            turn_id: str,
            app_bundle_id: Optional[str] = None,
            user_id: Optional[str] = None,
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = 5000,
            include_zero: bool = False,
            use_memory_cache: bool = False,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Return usage grouped by agent, then by (service, provider, model).

        When use_memory_cache=True, this now reads from the Redis-backed
        turn cache via the accounting layer instead of the in-process
        AccountingContext cache.

        Otherwise it falls back to scanning storage with prefix optimization.
        """
        events: List[Dict[str, Any]] = []

        if use_memory_cache:
            # --- FAST PATH: Redis turn cache (via accounting layer) ---
            try:
                cached_events = await get_turn_events(
                    tenant=tenant_id,
                    project=project_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            except Exception as e:
                logger.warning(
                    "Failed to read turn events from Redis cache "
                    "(tenant=%s, project=%s, conv=%s, turn=%s): %s",
                    tenant_id,
                    project_id,
                    conversation_id,
                    turn_id,
                    e,
                )
                cached_events = []

            for ev in cached_events:
                if app_bundle_id:
                    ev_bundle = (
                            ev.get("app_bundle_id")
                            or (ev.get("context") or {}).get("app_bundle_id")
                    )
                    if ev_bundle != app_bundle_id:
                        continue

                if user_id:
                    ev_user = ev.get("user_id") or (ev.get("context") or {}).get("user_id")
                    if ev_user != user_id:
                        continue

                if service_types and ev.get("service_type") not in service_types:
                    continue

                events.append(ev)
        else:
            # --- SLOW PATH: read from storage with prefix optimization ---
            if date_from or date_to:
                from datetime import timedelta

                if not date_from:
                    date_to_dt = datetime.strptime(date_to, "%Y-%m-%d").date()
                    date_from = (date_to_dt - timedelta(days=1)).isoformat()
                if not date_to:
                    date_from_dt = datetime.strptime(date_from, "%Y-%m-%d").date()
                    date_to = (date_from_dt + timedelta(days=1)).isoformat()
            else:
                from datetime import timedelta

                today = datetime.utcnow().date()
                yesterday = today - timedelta(days=1)
                date_from = yesterday.isoformat()
                date_to = today.isoformat()

            query = AccountingQuery(
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                app_bundle_id=app_bundle_id,
                date_from=date_from,
                date_to=date_to,
                service_types=service_types,
                hard_file_limit=hard_file_limit,
            )

            paths = [p async for p in self._iter_event_paths(query)]

            for p in paths:
                try:
                    ev = json.loads(await self.fs.read_text_a(p))
                except Exception:
                    continue

                if not self._match(ev, query):
                    continue

                events.append(ev)

        # --- Common aggregation logic for both paths ---
        agent_rollup: Dict[str, Dict[Tuple[str, str, str], Dict[str, int]]] = {}

        for ev in events:
            metadata = ev.get("metadata") or {}
            context = ev.get("context") or {}

            agent = (
                    metadata.get("agent")
                    or context.get("agent")
                    or ev.get("agent")
                    or ev.get("agent_name")
                    or "unknown"
            )

            service = str(ev.get("service_type") or "").strip()
            if not service:
                continue

            provider = str(
                ev.get("provider") or context.get("provider") or ""
            ).strip()
            model = str(
                ev.get("model_or_service") or context.get("model_or_service") or ""
            ).strip()

            if agent not in agent_rollup:
                agent_rollup[agent] = {}

            key = (service, provider, model)
            spent = agent_rollup[agent].get(key)
            if not spent:
                spent = _spent_seed(service)
                agent_rollup[agent][key] = spent

            usage = _extract_usage(ev) or {}
            _accumulate_compact(spent, usage, service)

        result: Dict[str, List[Dict[str, Any]]] = {}

        for agent in sorted(agent_rollup.keys()):
            items: List[Dict[str, Any]] = []

            for (service, provider, model) in sorted(agent_rollup[agent].keys()):
                spent = agent_rollup[agent][(service, provider, model)]

                if not include_zero:
                    if service == "llm" and (
                            spent.get("input", 0) == 0 and spent.get("output", 0) == 0
                    ):
                        continue
                    if service == "embedding" and spent.get("tokens", 0) == 0:
                        continue

                items.append(
                    {
                        "service": service,
                        "provider": provider or None,
                        "model": model or None,
                        "spent": {k: int(v) for k, v in spent.items()},
                    }
                )

            if items:
                result[agent] = items

        return result

# -----------------------------
# Price calculation helpers
# -----------------------------
def price_table():
    """Enhanced price table with separate cache type pricing."""
    sonnet_45 = "claude-sonnet-4-5-20250929"
    haiku_4 = "claude-haiku-4-5-20251001"

    return {
        "llm": [
            {
                "model": sonnet_45,
                "provider": "anthropic",
                "input_tokens_1M": 3.00,
                "output_tokens_1M": 15.00,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 3.00,
                        "read_tokens_1M": 0.30,
                    },
                    "1h": {
                        "write_tokens_1M": 3.75,
                        "read_tokens_1M": 0.30,
                    },
                },
                "cache_write_tokens_1M": 3.00,
                "cache_read_tokens_1M": 0.30,
            },
            {
                "model": haiku_4,
                "provider": "anthropic",
                "input_tokens_1M": 1,
                "output_tokens_1M": 5,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 1,
                        "read_tokens_1M": 0.1,
                    },
                    "1h": {
                        "write_tokens_1M": 2,
                        "read_tokens_1M": 0.1,
                    },
                },
                "cache_write_tokens_1M": 2,
                "cache_read_tokens_1M": 0.1,
            },
            {
                "model": "claude-3-5-haiku-20241022",
                "provider": "anthropic",
                "input_tokens_1M": 0.80,
                "output_tokens_1M": 4.00,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 0.80,
                        "read_tokens_1M": 0.08,
                    },
                    "1h": {
                        "write_tokens_1M": 1.00,
                        "read_tokens_1M": 0.08,
                    },
                },
                "cache_write_tokens_1M": 0.80,
                "cache_read_tokens_1M": 0.08,
            },
            {
                "model": "claude-3-haiku-20240307",
                "provider": "anthropic",
                "input_tokens_1M": 0.25,
                "output_tokens_1M": 1.25,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 0.25,
                        "read_tokens_1M": 0.03,
                    },
                    "1h": {
                        "write_tokens_1M": 0.30,
                        "read_tokens_1M": 0.03,
                    },
                },
                "cache_write_tokens_1M": 0.25,
                "cache_read_tokens_1M": 0.03,
            },
            {
                "model": "gpt-4o",
                "provider": "openai",
                "input_tokens_1M": 2.50,
                "output_tokens_1M": 10.00,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 1.25,
            },
            {
                "model": "gpt-4o-mini",
                "provider": "openai",
                "input_tokens_1M": 0.15,
                "output_tokens_1M": 0.60,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 0.075,
            },
            {
                "model": "o1",
                "provider": "openai",
                "input_tokens_1M": 15.00,
                "output_tokens_1M": 60.00,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 7.50,
            },
            {
                "model": "o3-mini",
                "provider": "openai",
                "input_tokens_1M": 1.10,
                "output_tokens_1M": 4.40,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 0.55,
            },
        ],
        "embedding": [
            {
                "model": "text-embedding-3-small",
                "provider": "openai",
                "tokens_1M": 0.02,
            },
            {
                "model": "text-embedding-3-large",
                "provider": "openai",
                "tokens_1M": 0.13,
            },
        ],
    }


def _calculate_agent_costs(
        agent_usage: Dict[str, List[Dict[str, Any]]],
        llm_pricelist: List[Dict[str, Any]],
        emb_pricelist: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Calculate costs per agent."""

    def _find_llm_price(provider: str, model: str) -> Optional[Dict[str, Any]]:
        for p in llm_pricelist:
            if p.get("provider") == provider and p.get("model") == model:
                return p
        return None

    def _find_emb_price(provider: str, model: str) -> Optional[Dict[str, Any]]:
        for p in emb_pricelist:
            if p.get("provider") == provider and p.get("model") == model:
                return p
        return None

    agent_costs: Dict[str, Dict[str, Any]] = {}

    for agent, items in agent_usage.items():
        total_cost = 0.0
        breakdown = []
        token_summary = {
            "input": 0,
            "output": 0,
            "cache_5m_write": 0,
            "cache_1h_write": 0,
            "cache_read": 0,
            "embedding": 0,
        }

        for item in items:
            service = item.get("service")
            provider = item.get("provider")
            model = item.get("model")
            spent = item.get("spent", {}) or {}

            cost_usd = 0.0

            if service == "llm":
                pr = _find_llm_price(provider, model)
                if pr:
                    input_cost = (float(spent.get("input", 0)) / 1_000_000.0) * float(pr.get("input_tokens_1M", 0.0))
                    output_cost = (float(spent.get("output", 0)) / 1_000_000.0) * float(pr.get("output_tokens_1M", 0.0))
                    cache_read_cost = (float(spent.get("cache_read", 0)) / 1_000_000.0) * float(
                        pr.get("cache_read_tokens_1M", 0.0)
                    )

                    cache_write_cost = 0.0
                    cache_pricing = pr.get("cache_pricing")

                    if cache_pricing and isinstance(cache_pricing, dict):
                        cache_5m_tokens = float(spent.get("cache_5m_write", 0))
                        cache_1h_tokens = float(spent.get("cache_1h_write", 0))

                        if cache_5m_tokens > 0:
                            price_5m = float(cache_pricing.get("5m", {}).get("write_tokens_1M", 0.0))
                            cache_write_cost += (cache_5m_tokens / 1_000_000.0) * price_5m

                        if cache_1h_tokens > 0:
                            price_1h = float(cache_pricing.get("1h", {}).get("write_tokens_1M", 0.0))
                            cache_write_cost += (cache_1h_tokens / 1_000_000.0) * price_1h
                    else:
                        cache_write_tokens = float(spent.get("cache_creation", 0))
                        cache_write_price = float(pr.get("cache_write_tokens_1M", 0.0))
                        cache_write_cost = (cache_write_tokens / 1_000_000.0) * cache_write_price

                    cost_usd = input_cost + output_cost + cache_write_cost + cache_read_cost

                    token_summary["input"] += spent.get("input", 0)
                    token_summary["output"] += spent.get("output", 0)
                    token_summary["cache_5m_write"] += spent.get("cache_5m_write", 0)
                    token_summary["cache_1h_write"] += spent.get("cache_1h_write", 0)
                    token_summary["cache_read"] += spent.get("cache_read", 0)

            elif service == "embedding":
                pr = _find_emb_price(provider, model)
                if pr:
                    cost_usd = (float(spent.get("tokens", 0)) / 1_000_000.0) * float(pr.get("tokens_1M", 0.0))
                    token_summary["embedding"] += spent.get("tokens", 0)

            total_cost += cost_usd
            breakdown.append({
                "service": service,
                "provider": provider,
                "model": model,
                "cost_usd": cost_usd,
            })

        agent_costs[agent] = {
            "total_cost_usd": total_cost,
            "breakdown": breakdown,
            "tokens": token_summary,
        }

    return agent_costs


# ----
# Examples
# ----
async def example_1(tenant, project, bundle_id):
    kdcube_path = os.getenv("KDCUBE_STORAGE_PATH", "file:///tmp/kdcube_data")
    backend = create_storage_backend(kdcube_path)
    calc = RateCalculator(backend, base_path="accounting")
    user_id = os.getenv("TEST_USER_ID")

    # 1) Totals for a bundle for a day (both llm + embedding)
    q = AccountingQuery(
        tenant_id=tenant,
        project_id=project,
        app_bundle_id=bundle_id,
        date_from="2025-09-28",
        date_to="2025-09-28",
        user_id=user_id,
        service_types=["llm", "embedding"],
    )
    res = await calc.query_usage(q, group_by=["service_type", "model_or_service"])
    print(f'Total: {res["total"]}, Event count: {res.get("event_count", 0)}')

    # 2) Per-session rollup for a bundle across a range
    q2 = AccountingQuery(
        tenant_id=tenant,
        project_id=project,
        app_bundle_id=bundle_id,
        date_from="2025-09-27",
        date_to="2025-09-28",
        user_id=user_id,
    )
    res2 = await calc.query_usage(q2, group_by=["session_id"])
    for key_tuple, grp in res2["groups"].items():
        print(key_tuple, grp)
    print()

    res3 = await calc.rate_stats(
        AccountingQuery(
            tenant_id=tenant,
            project_id=project,
            app_bundle_id=bundle_id,
            date_from="2025-09-28",
            date_to="2025-09-28",
            user_id=user_id,
        ),
        granularity="1m",
        group_by=["session_id"],
    )
    print(res3)
    print()


async def example_2(tenant, project, bundle_id):
    from datetime import datetime

    turn_id = "turn_1760550498939_cr526c"
    kdcube_path = os.getenv("KDCUBE_STORAGE_PATH", "file:///tmp/kdcube_data")
    backend = create_storage_backend(kdcube_path)
    calc = RateCalculator(backend, base_path="accounting")

    tokens = await calc.tokens_for_turn(
        tenant_id=tenant,
        project_id=project,
        turn_id=turn_id,
        app_bundle_id=bundle_id,
        date_hint=datetime.utcnow().date().isoformat(),  # optional optimization
        service_types=["llm", "embedding"],
    )
    print(f"Tokens: {tokens}")


async def example_grouped_calc(tenant, project, bundle_id):
    turn_id = "turn_1760612140724_splgg3"
    conversation_id = "88f56ef9-36fc-4a4f-8f27-5d53ff19dd03"
    kdcube_path = os.getenv("KDCUBE_STORAGE_PATH", "file:///tmp/kdcube_data")
    backend = create_storage_backend(kdcube_path)
    calc = RateCalculator(backend, base_path="accounting")

    # Example global rollup (not used further here)
    _ = await calc.usage_rollup_compact(
        AccountingQuery(
            tenant_id=tenant,
            project_id=project,
            app_bundle_id=bundle_id,
            date_from="2025-10-15",
            date_to="2025-10-15",
            service_types=["llm", "embedding"],
        )
    )

    # per-turn rollup
    rollup = await calc.turn_usage_rollup_compact(
        tenant_id=tenant,
        project_id=project,
        conversation_id=conversation_id,
        turn_id=turn_id,
        app_bundle_id=bundle_id,
        date_hint="2025-10-16",
        service_types=["llm", "embedding"],
    )

    # Weighted LLM tokens (ignore provider for weighting):
    #   tokens = input_tokens * 0.4 + output_tokens * 1.0
    llm_input_sum = sum(
        int(item.get("spent", {}).get("input", 0))
        for item in rollup
        if item.get("service") == "llm"
    )
    llm_output_sum = sum(
        int(item.get("spent", {}).get("output", 0))
        for item in rollup
        if item.get("service") == "llm"
    )
    weighted_tokens = int(llm_input_sum * 0.4 + llm_output_sum * 1.0)

    configuration = price_table()
    # Compute estimated spend (no cache accounting yet)
    acct_cfg = (configuration or {}).get("accounting", {})
    llm_pricelist = acct_cfg.get("llm", []) or []
    emb_pricelist = acct_cfg.get("embedding", []) or []

    def _find_llm_price(provider: str, model: str) -> Optional[Dict[str, Any]]:
        for p in llm_pricelist:
            if p.get("provider") == provider and p.get("model") == model:
                return p
        return None

    def _find_emb_price(provider: str, model: str) -> Optional[Dict[str, Any]]:
        for p in emb_pricelist:
            if p.get("provider") == provider and p.get("model") == model:
                return p
        return None

    cost_total_usd = 0.0
    cost_breakdown = []
    for item in rollup:
        service = item.get("service")
        provider = item.get("provider")
        model = item.get("model")
        spent = item.get("spent", {}) or {}

        cost_usd = 0.0
        if service == "llm":
            # price: input_tokens_1M and output_tokens_1M
            pr = _find_llm_price(provider, model)
            if pr:
                cost_usd = (
                        (float(spent.get("input", 0)) / 1_000_000.0)
                        * float(pr.get("input_tokens_1M", 0.0))
                        + (float(spent.get("output", 0)) / 1_000_000.0)
                        * float(pr.get("output_tokens_1M", 0.0))
                )
        elif service == "embedding":
            pr = _find_emb_price(provider, model)
            if pr:
                cost_usd = (float(spent.get("tokens", 0)) / 1_000_000.0) * float(pr.get("tokens_1M", 0.0))

        cost_total_usd += cost_usd
        cost_breakdown.append({
            "service": service,
            "provider": provider,
            "model": model,
            "cost_usd": cost_usd,
        })

    # Log weighted tokens and cost estimate
    print(
        f"[Conversation id: {conversation_id}; Turn id: {turn_id}] Weighted tokens (LLM only): {weighted_tokens}"
    )
    print(
        f"[Conversation id: {conversation_id}; Turn id: {turn_id}] Estimated spend (no cache): {cost_total_usd:.6f} USD; breakdown: {json.dumps(cost_breakdown, ensure_ascii=False)}"
    )
    print()


if __name__ == "__main__":
    from dotenv import load_dotenv, find_dotenv

    load_dotenv(find_dotenv())

    import asyncio

    logging.basicConfig(level=logging.INFO)

    bundle_id = "with.codegen"
    tenant = os.getenv("DEFAULT_TENANT", "home")
    project = os.getenv("DEFAULT_PROJECT_NAME", "demo")

    asyncio.run(example_grouped_calc(tenant=tenant, project=project, bundle_id=bundle_id))
