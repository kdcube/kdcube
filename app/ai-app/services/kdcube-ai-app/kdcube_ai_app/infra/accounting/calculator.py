# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/accounting/calculator.py
"""
Read-only usage aggregation on top of the accounting event store.

- Works with any IStorageBackend (LocalFileSystemBackend, S3StorageBackend, InMemoryStorageBackend).
- Scans the accounting base path and sums usage from JSON events.
- Filters by tenant, project, date range, bundle (app_bundle_id), user/session, provider/model/service_type/component.
- Optional group_by to get rollups per field (e.g., "service_type", "session_id", ...).

Example
-------
from kdcube_ai_app.storage.storage import create_storage_backend
from kdcube_ai_app.infra.accounting.calculator import AccountingCalculator, AccountingQuery

backend = create_storage_backend("file:///Users/you/path/to/data/kdcube")
calc = AccountingCalculator(backend, base_path="accounting")

q = AccountingQuery(
    tenant_id="home",
    project_id="demo",
    app_bundle_id="kdcube.demo.1",             # set in with_accounting(..., app_bundle_id=...)
    # session_id="abc123",
    date_from="2025-09-28",                    # inclusive
    date_to="2025-09-29",                      # inclusive
    service_types=["llm", "embedding"],        # or None for all
)

res = calc.query_usage(q, group_by=["service_type", "model_or_service"])
print(res["total"])       # grand totals across filtered events
print(res["groups"])      # nested dict keyed by tuples ("llm", "gpt-4o-mini"), etc.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, date
from typing import Any, Dict, Iterable, List, Optional, Tuple

from kdcube_ai_app.storage.storage import IStorageBackend

logger = logging.getLogger("AccountingCalculator")

# -----------------------------
# Query model
# -----------------------------
@dataclass
class AccountingQuery:
    # storage location (root: "<base_path>/<tenant>/<project>/...")
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None

    # time window (inclusive) — ISO "YYYY-MM-DD"
    date_from: Optional[str] = None
    date_to: Optional[str] = None

    # context filters (exact match if provided)
    app_bundle_id: Optional[str] = None
    client_id: Optional[str] = None            # alias for app_bundle_id (if you prefer that name)
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    component: Optional[str] = None
    provider: Optional[str] = None
    model_or_service: Optional[str] = None
    service_types: Optional[List[str]] = None  # e.g., ["llm", "embedding"]

    # free-form extra predicate (event dict -> bool) if you need it
    predicate: Optional[Any] = None

    # safety: max number of files to read (None = unlimited)
    hard_file_limit: Optional[int] = None

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

def _merge_usage(a: Dict[str, Any], b: Dict[str, Any]) -> None:
    _accumulate(a, b)

def _tokens_for_event(ev: Dict[str, Any]) -> int:
    u = ev.get("usage") or {}
    tot = u.get("total_tokens")
    if tot is None:
        tot = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
    # include embeddings “as tokens” (approx ok)
    tot += int(u.get("embedding_tokens") or 0)
    try:
        return int(tot)
    except Exception:
        return 0

def _spent_seed(service_type: str) -> Dict[str, int]:
    """Create initial spent dict with all possible token types."""
    if service_type == "llm":
        return {
            "input": 0,
            "output": 0,
            "cache_creation": 0,  # Total cache writes (all types)
            "cache_read": 0,
            # Detailed cache write breakdown (Anthropic)
            "cache_5m_write": 0,
            "cache_1h_write": 0,
        }
    if service_type == "embedding":
        return {"tokens": 0}
    return {}

# In calculator.py

def _accumulate_compact(spent: Dict[str, int], ev_usage: Dict[str, Any], service_type: str) -> None:
    """Accumulate token usage including detailed cache breakdowns."""
    if service_type == "llm":
        spent["input"]  = int(spent.get("input", 0))  + int(ev_usage.get("input_tokens") or 0)
        spent["output"] = int(spent.get("output", 0)) + int(ev_usage.get("output_tokens") or 0)
        spent["cache_read"] = int(spent.get("cache_read", 0)) + int(ev_usage.get("cache_read_tokens") or 0)

        # Handle detailed cache_creation breakdown if present
        cache_creation = ev_usage.get("cache_creation")
        if isinstance(cache_creation, dict):
            # Anthropic detailed breakdown
            cache_5m = int(cache_creation.get("ephemeral_5m_input_tokens") or 0)
            cache_1h = int(cache_creation.get("ephemeral_1h_input_tokens") or 0)

            spent["cache_5m_write"] = int(spent.get("cache_5m_write", 0)) + cache_5m
            spent["cache_1h_write"] = int(spent.get("cache_1h_write", 0)) + cache_1h
            spent["cache_creation"] = int(spent.get("cache_creation", 0)) + cache_5m + cache_1h
        else:
            # Legacy: total cache_creation_tokens without breakdown
            # This handles both OpenAI (no cache_creation dict) and old format
            cache_total = int(ev_usage.get("cache_creation_tokens") or 0)
            if cache_total > 0:
                spent["cache_creation"] = int(spent.get("cache_creation", 0)) + cache_total
                # For backward compatibility: assume it's 5m if no breakdown
                spent["cache_5m_write"] = int(spent.get("cache_5m_write", 0)) + cache_total

    elif service_type == "embedding":
        spent["tokens"] = int(spent.get("tokens", 0)) + int(ev_usage.get("embedding_tokens") or 0)
# -----------------------------
# Calculator
# -----------------------------
class AccountingCalculator:
    def __init__(self, storage_backend: IStorageBackend, *, base_path: str = "accounting"):
        """
        base_path — the same you gave to FileAccountingStorage (default "accounting")
        """
        self.fs = storage_backend
        self.base = base_path.strip("/")

    # ---------- public API ----------

    def query_usage(
            self,
            query: AccountingQuery,
            *,
            group_by: Optional[List[str]] = None,
            include_event_count: bool = True,
    ) -> Dict[str, Any]:
        """
        Read matching events and return totals + optional grouped totals.

        group_by: list of event fields to roll up by.
                  Supported keys include: "service_type", "provider", "model_or_service",
                  "app_bundle_id", "user_id", "session_id", "component", "date" (YYYY-MM-DD).
        """
        # normalize alias
        if query.client_id and not query.app_bundle_id:
            query.app_bundle_id = query.client_id

        events_iter = self._iter_event_paths(query)
        total, groups, n_events = self._aggregate(events_iter, query, group_by or [])
        out = {
            "filters": asdict(query),
            "total": total,
            "groups": groups if group_by else {},
        }
        if include_event_count:
            out["event_count"] = n_events
        return out

    # ---------- core scan ----------

    def _iter_event_paths(self, query: AccountingQuery) -> Iterable[str]:
        """
        Yield event file paths (relative to backend base) under:
           accounting/<tenant>/<project>/<date-pattern>/<service_type>/...

        Supports:
          - grouped_by_component_and_seed(): dates like "YYYY.MM.DD"
          - legacy default path(): "YYYY/MM/DD"
        """
        # discover tenant layer
        tenant_roots = []
        if query.tenant_id:
            tenant_roots = [f"{self.base}/{query.tenant_id}"]
        else:
            # all tenants
            for t in self._safe_listdir(self.base):
                tenant_roots.append(f"{self.base}/{t}")

        # project layer
        project_roots = []
        for troot in tenant_roots:
            if query.project_id:
                project_roots.append(f"{troot}/{query.project_id}")
            else:
                for p in self._safe_listdir(troot):
                    project_roots.append(f"{troot}/{p}")

        # date layer
        wanted_dates = _DateRange(query.date_from, query.date_to)
        for proot in project_roots:
            # First, "YYYY.MM.DD"
            dot_dates = [d for d in self._safe_listdir(proot) if _looks_dot_date(d)]
            # Then, legacy "YYYY/MM/DD"
            years = [y for y in self._safe_listdir(proot) if y.isdigit() and len(y) == 4 and y not in dot_dates]

            # scan dot-date layout
            for d in sorted(dot_dates):
                if not wanted_dates.contains_dot(d):
                    continue
                droot = f"{proot}/{d}"
                yield from self._iter_events_under_date_root(droot, query)

            # scan legacy yyyy/mm/dd layout
            for y in sorted(years):
                yroot = f"{proot}/{y}"
                for m in sorted([m for m in self._safe_listdir(yroot) if m.isdigit() and len(m) == 2]):
                    mroot = f"{yroot}/{m}"
                    for dd in sorted([dd for dd in self._safe_listdir(mroot) if dd.isdigit() and len(dd) == 2]):
                        d_iso = f"{y}-{m}-{dd}"
                        if not wanted_dates.contains_iso(d_iso):
                            continue
                        droot = f"{mroot}/{dd}"
                        yield from self._iter_events_under_date_root(droot, query)

    def _iter_events_under_date_root(self, date_root: str, query: AccountingQuery) -> Iterable[str]:
        # service type layer (llm, embedding, web_search, ...)
        stypes = self._safe_listdir(date_root)
        for st in sorted(stypes):
            if query.service_types and st not in query.service_types:
                continue
            st_root = f"{date_root}/{st}"

            # next can be either:
            # - grouped: <group>/<usage_*.json>
            # - flat: usage_*.json
            # Try grouped first.
            groups = self._safe_listdir(st_root)
            if any(name.endswith(".json") for name in groups):
                # flat
                for fname in groups:
                    if fname.endswith(".json"):
                        yield f"{st_root}/{fname}"
            else:
                # grouped dirs
                for g in groups:
                    groot = f"{st_root}/{g}"
                    for fname in self._safe_listdir(groot):
                        if fname.endswith(".json"):
                            yield f"{groot}/{fname}"

    # ---------- aggregation ----------

    def _aggregate(
            self,
            paths: Iterable[str],
            query: AccountingQuery,
            group_by: List[str],
    ) -> Tuple[Dict[str, Any], Dict[Tuple[Any, ...], Dict[str, Any]], int]:
        totals = _new_usage_acc()
        groups: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        count = 0

        # apply hard file limit if specified
        max_files = query.hard_file_limit if query.hard_file_limit and query.hard_file_limit > 0 else None
        processed = 0

        for p in paths:
            if max_files is not None and processed >= max_files:
                break
            processed += 1

            try:
                raw = self.fs.read_text(p)
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
                # keep counting events even with empty usage
                count += 1
                continue

            _accumulate(totals, usage)

            if group_by:
                key = _group_key(ev, group_by)
                if key not in groups:
                    groups[key] = _new_usage_acc()
                _accumulate(groups[key], usage)

            count += 1

        # compute cost_usd as sum if any sub-values present
        _finalize_cost(totals)
        for k in groups:
            _finalize_cost(groups[k])

        # prettify groups: map tuple key -> { field_name:value,..., usage... }
        pretty_groups: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for k_tuple, usage_dict in groups.items():
            labels = {group_by[i]: k_tuple[i] for i in range(len(group_by))}
            labels.update(usage_dict)
            pretty_groups[k_tuple] = labels

        return totals, pretty_groups, count

    # ---------- filter ----------

    def _match(self, ev: Dict[str, Any], query: AccountingQuery) -> bool:
        def eq(k: str, qv: Optional[str]) -> bool:
            if qv is None:
                return True
            evv = ev.get(k) or (ev.get("context") or {}).get(k)
            return evv == qv

        if not eq("tenant_id", query.tenant_id): return False
        if not eq("project_id", query.project_id): return False
        if not eq("app_bundle_id", query.app_bundle_id): return False
        if not eq("user_id", query.user_id): return False
        if not eq("session_id", query.session_id): return False
        if not eq("component", query.component): return False
        if not eq("provider", query.provider): return False
        if not eq("model_or_service", query.model_or_service): return False

        if query.service_types:
            st = ev.get("service_type")
            if st not in query.service_types:
                return False

        # date window (from event.timestamp)
        if query.date_from or query.date_to:
            ts = ev.get("timestamp")
            try:
                # supports both with and without Z
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
            except Exception:
                dt = None
            if dt:
                d = dt.date()
                if query.date_from and d < _parse_iso_date(query.date_from): return False
                if query.date_to and d > _parse_iso_date(query.date_to): return False

        if query.predicate and callable(query.predicate):
            try:
                if not bool(query.predicate(ev)):
                    return False
            except Exception:
                return False

        return True

    # ---------- utils ----------

    def _safe_listdir(self, path: str) -> List[str]:
        try:
            return self.fs.list_dir(path)
        except Exception:
            return []

    def usage_rollup_compact(
            self,
            query: AccountingQuery,
            *,
            include_zero: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Return a compact list grouped by (service_type, provider, model_or_service)
        with the aggregated token spend per service kind.

        Output:
          [
            { "service": "llm", "provider": "openai", "model": "gpt-4o-mini",
              "spent": { "input": N, "output": M } },
            { "service": "embedding", "provider": "openai", "model": "text-embedding-3-small",
              "spent": { "tokens": K } }
          ]
        """
        # normalize alias
        if query.client_id and not query.app_bundle_id:
            query.app_bundle_id = query.client_id

        paths = self._iter_event_paths(query)

        # group key → spent dict
        rollup: Dict[Tuple[str, str, str], Dict[str, int]] = {}

        max_files = query.hard_file_limit if query.hard_file_limit and query.hard_file_limit > 0 else None
        processed = 0

        for p in paths:
            if max_files is not None and processed >= max_files:
                break
            processed += 1

            # read + parse event
            try:
                raw = self.fs.read_text(p)
                ev = json.loads(raw)
            except Exception:
                continue

            if not self._match(ev, query):
                continue

            service = str(ev.get("service_type") or "").strip()
            if not service:
                continue  # skip malformed

            provider = str(ev.get("provider") or (ev.get("context") or {}).get("provider") or "").strip()
            model    = str(ev.get("model_or_service") or (ev.get("context") or {}).get("model_or_service") or "").strip()

            key = (service, provider, model)
            spent = rollup.get(key)
            if not spent:
                spent = _spent_seed(service)
                rollup[key] = spent

            usage = _extract_usage(ev) or {}
            _accumulate_compact(spent, usage, service)

        # build sorted, pretty list
        items: List[Dict[str, Any]] = []
        for (service, provider, model) in sorted(rollup.keys()):
            spent = rollup[(service, provider, model)]
            if not include_zero:
                # drop groups that stayed all zeros (defensive)
                if service == "llm" and (spent.get("input", 0) == 0 and spent.get("output", 0) == 0):
                    continue
                if service == "embedding" and spent.get("tokens", 0) == 0:
                    continue
            items.append({
                "service": service,
                "provider": provider or None,
                "model": model or None,
                "spent": {k: int(v) for k, v in spent.items()}
            })

        return items

class RateCalculator(AccountingCalculator):
    def time_series(
            self,
            query: AccountingQuery,
            *,
            granularity: str = "1h",
            group_by: Optional[List[str]] = None,
            include_event_count: bool = True,
    ) -> Dict[str, Any]:
        """
        Return usage grouped into fixed time buckets.
        Output:
          {
            "granularity": "1h",
            "series": [
              {
                "bucket": "2025-09-28T10:00:00Z",
                "total": { ... usage sums ... },
                "groups": { (key...): { ... usage ... }, ... },
                "event_count": N
              },
              ...
            ]
          }
        """
        if granularity not in _BUCKETS:
            raise ValueError(f"granularity must be one of {list(_BUCKETS)}")

        # 1) collect paths once
        paths = list(self._iter_event_paths(query))
        max_files = query.hard_file_limit if query.hard_file_limit and query.hard_file_limit > 0 else None
        if max_files is not None:
            paths = paths[:max_files]

        # 2) scan + bucket
        series: Dict[str, Dict[str, Any]] = {}  # bucket_label -> {total, groups, count}
        gby = group_by or []
        for p in paths:
            try:
                raw = self.fs.read_text(p)
                ev = json.loads(raw)
            except Exception:
                continue
            if not self._match(ev, query):
                continue

            # bucket label from event timestamp
            ts = ev.get("timestamp")
            try:
                dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
            except Exception:
                # fallback: unknown timestamp → skip bucketed rates
                continue
            bucket = _floor_bucket(dt, granularity)

            slot = series.get(bucket)
            if not slot:
                slot = {"total": _new_usage_acc(), "groups": {}, "event_count": 0}
                series[bucket] = slot

            usage = _extract_usage(ev) or _new_usage_acc()
            _merge_usage(slot["total"], usage)
            slot["event_count"] += 1

            if gby:
                key = _group_key(ev, gby)
                grp = slot["groups"].get(key)
                if not grp:
                    grp = _new_usage_acc()
                    slot["groups"][key] = grp
                _merge_usage(grp, usage)

        # finalize cost fields and prettify groups
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

    def rate_stats(
            self,
            query: AccountingQuery,
            *,
            granularity: str = "1h",
            group_by: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Convert time_series buckets into per-second rates.
        Adds:
          - req_rate_per_sec  (requests / bucket_seconds)
          - tok_rate_per_sec  (total_tokens / bucket_seconds)
        """
        ts = self.time_series(query, granularity=granularity, group_by=group_by)
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

    async def query_turn_usage(
            self,
            *,
            tenant_id: str,
            project_id: str,
            conversation_id: str,
            turn_id: str,
            app_bundle_id: Optional[str] = None,
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            date_hint: Optional[str] = None,          # "YYYY-MM-DD" if you know it; else we scan 2 recent days
            service_types: Optional[List[str]] = None, # e.g. ["llm","embedding"]
            hard_file_limit: Optional[int] = 5000,
    ) -> Dict[str, Any]:
        """
        Query usage for a specific turn, supporting multi-day spans.
        """
        q = AccountingQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            app_bundle_id=app_bundle_id,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )

        # Build paths based on date range
        if date_from or date_to:
            if not date_from:
                from datetime import datetime, timedelta
                date_to_dt = datetime.strptime(date_to, "%Y-%m-%d").date()
                date_from = (date_to_dt - timedelta(days=1)).isoformat()
            if not date_to:
                from datetime import datetime, timedelta
                date_from_dt = datetime.strptime(date_from, "%Y-%m-%d").date()
                date_to = (date_from_dt + timedelta(days=1)).isoformat()

            q.date_from = date_from
            q.date_to = date_to
            paths = list(self._iter_event_paths(q))

        elif date_hint:
            q.date_from = date_hint
            q.date_to = date_hint
            paths = list(self._iter_event_paths(q))

        else:
            from datetime import datetime, timedelta
            today = datetime.utcnow().date()
            yesterday = today - timedelta(days=1)
            candidates = []
            for d in (yesterday.isoformat(), today.isoformat()):
                q.date_from, q.date_to = d, d
                candidates.extend(list(self._iter_event_paths(q)))
            paths = candidates

        totals = _new_usage_acc()
        count = 0
        evs: List[Dict[str, Any]] = []

        for p in paths:
            try:
                ev = json.loads(await self.fs.read_text_a(p))
            except Exception:
                continue

            if app_bundle_id:
                ev_bundle = ev.get("app_bundle_id") or (ev.get("context") or {}).get("app_bundle_id")
                if ev_bundle != app_bundle_id:
                    continue

            # turn id may be recorded under metadata or context
            md_tid = (ev.get("metadata") or {}).get("turn_id")
            ctx_tid = (ev.get("context") or {}).get("turn_id")
            if md_tid != turn_id and ctx_tid != turn_id:
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
            app_bundle_id: Optional[str] = None,
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            date_hint: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = 5000,
    ) -> int:
        r = await self.query_turn_usage(
            tenant_id=tenant_id,
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
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
            date_from: Optional[str] = None,           # ← NEW: "YYYY-MM-DD"
            date_to: Optional[str] = None,
            date_hint: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = 5000,
            include_zero: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Convenience for a single turn. Applies same grouping as usage_rollup_compact,
        but filters to events that belong to the given turn.

        Args:
            date_from: Start date (inclusive), e.g., "2025-10-15"
            date_to: End date (inclusive), e.g., "2025-10-16"
            date_hint: DEPRECATED - use date_from/date_to instead. If provided and
                       date_from/date_to are None, will scan only that single day.

        Note: If neither date_from/date_to nor date_hint are provided, defaults to
              scanning yesterday and today.
        """
        q = AccountingQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            app_bundle_id=app_bundle_id,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )

        # Build candidate paths based on date range
        if date_from or date_to:
            # Use explicit date range (can span multiple days)
            if not date_from:
                # If only date_to provided, scan from yesterday to date_to
                from datetime import datetime, timedelta
                date_to_dt = datetime.strptime(date_to, "%Y-%m-%d").date()
                date_from = (date_to_dt - timedelta(days=1)).isoformat()
            if not date_to:
                # If only date_from provided, scan from date_from to tomorrow
                from datetime import datetime, timedelta
                date_from_dt = datetime.strptime(date_from, "%Y-%m-%d").date()
                date_to = (date_from_dt + timedelta(days=1)).isoformat()

            q.date_from = date_from
            q.date_to = date_to
            paths = list(self._iter_event_paths(q))

        elif date_hint:
            # Backward compatibility: single-day optimization
            q.date_from = date_hint
            q.date_to = date_hint
            paths = list(self._iter_event_paths(q))

        else:
            # Default: scan yesterday and today
            from datetime import datetime, timedelta
            today = datetime.utcnow().date()
            yesterday = today - timedelta(days=1)
            candidates = []
            for d in (yesterday.isoformat(), today.isoformat()):
                q.date_from, q.date_to = d, d
                candidates.extend(list(self._iter_event_paths(q)))
            paths = candidates

        # Now do the same rollup but filtered by turn_id
        rollup: Dict[Tuple[str, str, str], Dict[str, int]] = {}
        max_files = q.hard_file_limit if q.hard_file_limit and q.hard_file_limit > 0 else None
        processed = 0

        for p in paths:
            if max_files is not None and processed >= max_files:
                break
            processed += 1

            try:
                ev = json.loads(await self.fs.read_text_a(p))
            except Exception:
                continue

            # Quick service filter already applied by q.service_types; keep tenant/project/bundle match
            if app_bundle_id:
                ev_bundle = ev.get("app_bundle_id") or (ev.get("context") or {}).get("app_bundle_id")
                if ev_bundle != app_bundle_id:
                    continue

            # Match turn_id (can be in metadata or context)
            md_tid = (ev.get("metadata") or {}).get("turn_id")
            ctx_tid = (ev.get("context") or {}).get("turn_id")
            if md_tid != turn_id and ctx_tid != turn_id:
                continue

            service  = str(ev.get("service_type") or "").strip()
            if not service:
                continue
            provider = str(ev.get("provider") or (ev.get("context") or {}).get("provider") or "").strip()
            model    = str(ev.get("model_or_service") or (ev.get("context") or {}).get("model_or_service") or "").strip()

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
                "spent": {k: int(v) for k, v in spent.items()}
            })
        return items

    async def turn_usage_by_agent(
            self,
            *,
            tenant_id: str,
            project_id: str,
            conversation_id: str,
            turn_id: str,
            app_bundle_id: Optional[str] = None,
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = 5000,
            include_zero: bool = False,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Return usage grouped by agent, then by (service, provider, model).

        Output:
          {
            "answer_generator": [
              {"service": "llm", "provider": "anthropic", "model": "...", "spent": {...}},
              ...
            ],
            "precheck_gate": [...],
            ...
          }
        """
        q = AccountingQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            app_bundle_id=app_bundle_id,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )

        # Build candidate paths
        if date_from or date_to:
            if not date_from:
                from datetime import datetime, timedelta
                date_to_dt = datetime.strptime(date_to, "%Y-%m-%d").date()
                date_from = (date_to_dt - timedelta(days=1)).isoformat()
            if not date_to:
                from datetime import datetime, timedelta
                date_from_dt = datetime.strptime(date_from, "%Y-%m-%d").date()
                date_to = (date_from_dt + timedelta(days=1)).isoformat()

            q.date_from = date_from
            q.date_to = date_to
            paths = list(self._iter_event_paths(q))
        else:
            # Default: scan yesterday and today
            from datetime import datetime, timedelta
            today = datetime.utcnow().date()
            yesterday = today - timedelta(days=1)
            candidates = []
            for d in (yesterday.isoformat(), today.isoformat()):
                q.date_from, q.date_to = d, d
                candidates.extend(list(self._iter_event_paths(q)))
            paths = candidates

        # Nested rollup: agent -> (service, provider, model) -> spent
        agent_rollup: Dict[str, Dict[Tuple[str, str, str], Dict[str, int]]] = {}

        max_files = q.hard_file_limit if q.hard_file_limit and q.hard_file_limit > 0 else None
        processed = 0

        for p in paths:
            if max_files is not None and processed >= max_files:
                break
            processed += 1

            try:
                ev = json.loads(await self.fs.read_text_a(p))
            except Exception:
                continue

            # Match turn_id
            if app_bundle_id:
                ev_bundle = ev.get("app_bundle_id") or (ev.get("context") or {}).get("app_bundle_id")
                if ev_bundle != app_bundle_id:
                    continue

            md_tid = (ev.get("metadata") or {}).get("turn_id")
            ctx_tid = (ev.get("context") or {}).get("turn_id")
            if md_tid != turn_id and ctx_tid != turn_id:
                continue

            # Extract agent
            agent = (
                    (ev.get("metadata") or {}).get("agent") or
                    (ev.get("context") or {}).get("agent") or
                    "unknown"
            )

            service = str(ev.get("service_type") or "").strip()
            if not service:
                continue

            provider = str(ev.get("provider") or (ev.get("context") or {}).get("provider") or "").strip()
            model = str(ev.get("model_or_service") or (ev.get("context") or {}).get("model_or_service") or "").strip()

            # Initialize agent bucket if needed
            if agent not in agent_rollup:
                agent_rollup[agent] = {}

            key = (service, provider, model)
            spent = agent_rollup[agent].get(key)
            if not spent:
                spent = _spent_seed(service)
                agent_rollup[agent][key] = spent

            usage = _extract_usage(ev) or {}
            _accumulate_compact(spent, usage, service)

        # Build output: agent -> list of items
        result: Dict[str, List[Dict[str, Any]]] = {}

        for agent in sorted(agent_rollup.keys()):
            items: List[Dict[str, Any]] = []

            for (service, provider, model) in sorted(agent_rollup[agent].keys()):
                spent = agent_rollup[agent][(service, provider, model)]

                if not include_zero:
                    if service == "llm" and (spent.get("input", 0) == 0 and spent.get("output", 0) == 0):
                        continue
                    if service == "embedding" and spent.get("tokens", 0) == 0:
                        continue

                items.append({
                    "service": service,
                    "provider": provider or None,
                    "model": model or None,
                    "spent": {k: int(v) for k, v in spent.items()}
                })

            if items:  # Only include agents that have usage
                result[agent] = items

        return result

# -----------------------------
# Helpers
# -----------------------------
def _parse_iso_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()

def _looks_dot_date(s: str) -> bool:
    # "YYYY.MM.DD"
    if len(s) != 10: return False
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
        # dot_date: "YYYY.MM.DD"
        try:
            d = datetime.strptime(dot_date, "%Y.%m.%d").date()
        except Exception:
            return True  # don't filter if unknown format; we'll re-check via timestamp inside the file
        return self._contains(d)

    def contains_iso(self, iso_date: str) -> bool:
        try:
            d = _parse_iso_date(iso_date)
        except Exception:
            return True
        return self._contains(d)

    def _contains(self, d: date) -> bool:
        if self.df and d < self.df: return False
        if self.dt and d > self.dt: return False
        return True

# build a fresh accumulator with all usage keys we may see
_USAGE_KEYS = [
    "input_tokens", "output_tokens",
    "cache_creation_tokens", "cache_read_tokens", "cache_creation",
    "total_tokens", "embedding_tokens", "embedding_dimensions", "search_queries",
    "search_results", "image_count", "image_pixels", "audio_seconds", "requests",
    "cost_usd",
]

def _new_usage_acc() -> Dict[str, Any]:
    acc = {k: (0.0 if k in ("audio_seconds", "cost_usd") else 0) for k in _USAGE_KEYS}
    acc["cache_creation"] = None  # Will be dict or None
    return acc

def _extract_usage(ev: Dict[str, Any]) -> Dict[str, Any] | None:
    u = ev.get("usage")
    if not isinstance(u, dict):
        return None

    out = {}
    for k in _USAGE_KEYS:
        v = u.get(k)

        # Special handling for cache_creation (can be a dict)
        if k == "cache_creation":
            if isinstance(v, dict):
                # Anthropic: preserve nested structure
                out[k] = dict(v)  # shallow copy
            else:
                # Not present or not a dict
                out[k] = None
            continue

        # Regular numeric fields
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
            # Don't sum cache_creation dict, it's handled separately in _accumulate_compact
            continue
        elif k == "cost_usd":
            acc["cost_usd"] = float(acc.get("cost_usd", 0.0)) + float(usage.get("cost_usd", 0.0))
        elif k == "audio_seconds":
            acc[k] = float(acc.get(k, 0.0)) + float(usage.get(k, 0.0))
        else:
            acc[k] = int(acc.get(k, 0)) + int(usage.get(k, 0))

def _finalize_cost(acc: Dict[str, Any]) -> None:
    # If cost stayed 0.0 across all, keep it 0.0 (not None) for clarity.
    pass

def _group_key(ev: Dict[str, Any], group_by: List[str]) -> Tuple[Any, ...]:
    ctx = ev.get("context") or {}
    # allow grouping by "date" (YYYY-MM-DD) extracted from timestamp
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
        out.append(ev.get(g) if g in ev else ctx.get(g))
    return tuple(out)


# ----
# Cost Calculation
# ----
def price_table():
    """
    Enhanced price table with separate cache type pricing.

    For Anthropic models:
    - cache_pricing.5m: Standard 5-minute cache (write = input price, read = 10% of input)
    - cache_pricing.1h: Extended 1-hour cache (write = 1.25x input, read = 10% of input)

    For OpenAI models:
    - Automatic caching, use legacy cache_write/cache_read fields
    """
    sonnet_45 = "claude-sonnet-4-5-20250929"
    haiku_4 = "claude-haiku-4-5-20251001"

    return {
        "llm": [
            {
                "model": sonnet_45,
                "provider": "anthropic",
                "input_tokens_1M": 3.00,
                "output_tokens_1M": 15.00,
                # Detailed cache pricing by type
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 3.00,   # Same as input
                        "read_tokens_1M": 0.30      # 90% discount
                    },
                    "1h": {
                        "write_tokens_1M": 3.75,   # 25% premium
                        "read_tokens_1M": 0.30      # 90% discount (same as 5m)
                    }
                },
                # Legacy fallback (for backward compatibility)
                "cache_write_tokens_1M": 3.00,
                "cache_read_tokens_1M": 0.30
            },
            {
                "model": haiku_4,
                "provider": "anthropic",
                "input_tokens_1M": 1,
                "output_tokens_1M": 5,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 1,
                        "read_tokens_1M": 0.1
                    },
                    "1h": {
                        "write_tokens_1M": 2,   # 25% premium
                        "read_tokens_1M": 0.1
                    }
                },
                "cache_write_tokens_1M": 2,
                "cache_read_tokens_1M": 0.1
            },
            {
                "model": "claude-3-5-haiku-20241022",
                "provider": "anthropic",
                "input_tokens_1M": 0.80,
                "output_tokens_1M": 4.00,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 0.80,
                        "read_tokens_1M": 0.08
                    },
                    "1h": {
                        "write_tokens_1M": 1.00,   # 25% premium
                        "read_tokens_1M": 0.08
                    }
                },
                "cache_write_tokens_1M": 0.80,
                "cache_read_tokens_1M": 0.08
            },
            {
                "model": "claude-3-haiku-20240307",
                "provider": "anthropic",
                "input_tokens_1M": 0.25,
                "output_tokens_1M": 1.25,
                "cache_pricing": {
                    "5m": {
                        "write_tokens_1M": 0.25,
                        "read_tokens_1M": 0.03
                    },
                    "1h": {
                        "write_tokens_1M": 0.30,   # 20% premium (older model)
                        "read_tokens_1M": 0.03
                    }
                },
                "cache_write_tokens_1M": 0.25,
                "cache_read_tokens_1M": 0.03
            },
            # OpenAI models - no detailed cache breakdown needed
            {
                "model": "gpt-4o",
                "provider": "openai",
                "input_tokens_1M": 2.50,
                "output_tokens_1M": 10.00,
                "cache_write_tokens_1M": 0.00,  # Automatic/free
                "cache_read_tokens_1M": 1.25     # 50% discount
            },
            {
                "model": "gpt-4o-mini",
                "provider": "openai",
                "input_tokens_1M": 0.15,
                "output_tokens_1M": 0.60,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 0.075
            },
            {
                "model": "o1",
                "provider": "openai",
                "input_tokens_1M": 15.00,
                "output_tokens_1M": 60.00,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 7.50
            },
            {
                "model": "o3-mini",
                "provider": "openai",
                "input_tokens_1M": 1.10,
                "output_tokens_1M": 4.40,
                "cache_write_tokens_1M": 0.00,
                "cache_read_tokens_1M": 0.55
            }
        ],
        "embedding": [
            {
                "model": "text-embedding-3-small",
                "provider": "openai",
                "tokens_1M": 0.02
            },
            {
                "model": "text-embedding-3-large",
                "provider": "openai",
                "tokens_1M": 0.13
            }
        ]
    }


def _calculate_agent_costs(
        agent_usage: Dict[str, List[Dict[str, Any]]],
        llm_pricelist: List[Dict[str, Any]],
        emb_pricelist: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """
    Calculate costs per agent.

    Returns:
      {
        "answer_generator": {
          "total_cost_usd": 0.045,
          "breakdown": [...],
          "tokens": {"input": 1000, "output": 500, ...}
        },
        ...
      }
    """
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

    agent_costs = {}

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
            cost_details = {}

            if service == "llm":
                pr = _find_llm_price(provider, model)
                if pr:
                    input_cost = (float(spent.get("input", 0)) / 1_000_000.0) * float(pr.get("input_tokens_1M", 0.0))
                    output_cost = (float(spent.get("output", 0)) / 1_000_000.0) * float(pr.get("output_tokens_1M", 0.0))
                    cache_read_cost = (float(spent.get("cache_read", 0)) / 1_000_000.0) * float(pr.get("cache_read_tokens_1M", 0.0))

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

                    # Update token summary
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
def example_1(tenant, project, bundle_id):

    kdcube_path = os.getenv("KDCUBE_STORAGE_PATH", "file:///tmp/kdcube_data")
    backend = create_storage_backend(kdcube_path)
    calc = RateCalculator(backend, base_path="accounting")
    user_id = os.getenv("TEST_USER_ID")

    # q = AccountingQuery(
    #     tenant_id=tenant,
    #     project_id=project,
    #     app_bundle_id=bundle_id,     # whatever you set in with_accounting(..., app_bundle_id=...)
    #     # session_id="abc123",
    #     date_from="2025-09-28",                    # inclusive
    #     date_to="2025-09-29",                      # inclusive
    #     service_types=["llm", "embedding"],        # or None for all
    #     hard_file_limit=1000,
    # )

    # res = calc.query_usage(q, group_by=["service_type", "model_or_service"])
    # print("Filters:", res["filters"])
    # print("Total:", res["total"])
    # print("Event count:", res.get("event_count", 0))
    # print("Groups:")
    # for k, v in res["groups"].items():
    #     print(" ", k, v)

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
    res = calc.query_usage(q, group_by=["service_type", "model_or_service"])
    print(f'Total: {res["total"]}, Event count: {res.get("event_count", 0)}')

    # 2) Per-session rollup for a bundle across a range
    q2 = AccountingQuery(
        tenant_id=tenant,
        project_id=project,
        app_bundle_id=bundle_id,
        date_from="2025-09-27",
        date_to="2025-09-28",
        user_id=user_id
    )
    res2 = calc.query_usage(q2, group_by=["session_id"])
    for key_tuple, grp in res2["groups"].items():
        print(key_tuple, grp)
    print()

    res = calc.rate_stats(
        AccountingQuery(
            tenant_id=tenant,
            project_id=project,
            app_bundle_id=bundle_id,
            date_from="2025-09-28",
            date_to="2025-09-28",
            user_id=user_id
        ),
        granularity="1m",
        group_by=["session_id"]
    )
    print()

async def example_2(tenant, project, bundle_id):

    turn_id = "turn_1760550498939_cr526c"
    kdcube_path = os.getenv("KDCUBE_STORAGE_PATH", "file:///tmp/kdcube_data")
    backend = create_storage_backend(kdcube_path)
    calc = RateCalculator(backend, base_path="accounting")

    from datetime import datetime
    tokens = await calc.tokens_for_turn(
        tenant_id=tenant,
        project_id=project,
        turn_id=turn_id,
        app_bundle_id=bundle_id,
        date_hint=datetime.utcnow().date().isoformat(),   # optional optimization
        service_types=["llm","embedding"],
    )
    print(f"Tokens: {tokens}")


async def example_grouped_calc(tenant, project, bundle_id):

    turn_id = "turn_1760612140724_splgg3" #" "turn_1760550498939_cr526c"
    conversation_id = "88f56ef9-36fc-4a4f-8f27-5d53ff19dd03"
    kdcube_path = os.getenv("KDCUBE_STORAGE_PATH", "file:///tmp/kdcube_data")
    backend = create_storage_backend(kdcube_path)
    calc = RateCalculator(backend, base_path="accounting")

    rollup = calc.usage_rollup_compact(
        AccountingQuery(
            tenant_id=tenant,
            project_id=project,
            app_bundle_id=bundle_id,
            date_from="2025-10-15",
            date_to="2025-10-15",
            service_types=["llm","embedding"],
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
        service_types=["llm","embedding"],
    )

    # Weighted LLM tokens (ignore provider for weighting):
    #   tokens = input_tokens * 0.4 + output_tokens * 1.0
    llm_input_sum = sum(int(item.get("spent", {}).get("input", 0)) for item in rollup if item.get("service") == "llm")
    llm_output_sum = sum(int(item.get("spent", {}).get("output", 0)) for item in rollup if item.get("service") == "llm")
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
                        (float(spent.get("input", 0)) / 1_000_000.0) * float(pr.get("input_tokens_1M", 0.0))
                        + (float(spent.get("output", 0)) / 1_000_000.0) * float(pr.get("output_tokens_1M", 0.0))
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

    import os, asyncio
    from kdcube_ai_app.storage.storage import create_storage_backend

    logging.basicConfig(level=logging.INFO)

    bundle_id = "with.codegen"
    tenant = os.getenv("DEFAULT_TENANT", "home")
    project = os.getenv("DEFAULT_PROJECT_NAME", "demo")

    asyncio.run(example_grouped_calc(tenant=tenant, project=project, bundle_id=bundle_id))
