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

    def query_turn_usage(
            self,
            *,
            tenant_id: str,
            project_id: str,
            turn_id: str,
            app_bundle_id: Optional[str] = None,
            date_hint: Optional[str] = None,          # "YYYY-MM-DD" if you know it; else we scan 2 recent days
            service_types: Optional[List[str]] = None, # e.g. ["llm","embedding"]
            hard_file_limit: Optional[int] = 5000,
    ) -> Dict[str, Any]:
        q = AccountingQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            app_bundle_id=app_bundle_id,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )

        # If we know the day, read only that day. Otherwise scan today & yesterday.
        if date_hint:
            q.date_from = date_hint
            q.date_to = date_hint
            paths = self._iter_event_paths(q)
        else:
            from datetime import datetime, timedelta
            today = datetime.utcnow().date()
            yday = today - timedelta(days=1)
            candidates = []
            for d in (yday.isoformat(), today.isoformat()):
                q.date_from, q.date_to = d, d
                candidates.extend(list(self._iter_event_paths(q)))
            paths = candidates

        totals = _new_usage_acc()
        count = 0
        evs: List[Dict[str, Any]] = []

        for p in paths:
            try:
                ev = json.loads(self.fs.read_text(p))
            except Exception:
                continue

            # require same tenant/project and (optional) app bundle id; service_type already filtered by q
            if app_bundle_id and (ev.get("app_bundle_id") or (ev.get("context") or {}).get("app_bundle_id")) != app_bundle_id:
                # some old events may only have it in context
                ctx_ab = (ev.get("context") or {}).get("app_bundle_id")
                if ctx_ab != app_bundle_id:
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

    def tokens_for_turn(
            self,
            *,
            tenant_id: str,
            project_id: str,
            turn_id: str,
            app_bundle_id: Optional[str] = None,
            date_hint: Optional[str] = None,
            service_types: Optional[List[str]] = None,
            hard_file_limit: Optional[int] = 5000,
    ) -> int:
        r = self.query_turn_usage(
            tenant_id=tenant_id,
            project_id=project_id,
            turn_id=turn_id,
            app_bundle_id=app_bundle_id,
            date_hint=date_hint,
            service_types=service_types,
            hard_file_limit=hard_file_limit,
        )
        return int(r.get("tokens") or 0)
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
    "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens",
    "total_tokens", "embedding_tokens", "embedding_dimensions", "search_queries",
    "search_results", "image_count", "image_pixels", "audio_seconds", "requests",
    "cost_usd",
]

def _new_usage_acc() -> Dict[str, Any]:
    return {k: (0.0 if k in ("audio_seconds", "cost_usd") else 0) for k in _USAGE_KEYS}

def _extract_usage(ev: Dict[str, Any]) -> Dict[str, Any] | None:
    u = ev.get("usage")
    if not isinstance(u, dict):
        return None
    # include only known numeric fields, defaulting to 0
    out = {}
    for k in _USAGE_KEYS:
        v = u.get(k)
        if v is None:
            out[k] = 0.0 if k in ("audio_seconds", "cost_usd") else 0
        else:
            out[k] = float(v) if k in ("audio_seconds", "cost_usd") else int(v) if isinstance(v, (int, float)) and k != "audio_seconds" and k != "cost_usd" else v
    return out

def _accumulate(acc: Dict[str, Any], usage: Dict[str, Any]) -> None:
    for k in _USAGE_KEYS:
        if k == "cost_usd":
            # sum only when present (>=0 or >0). None already normalized to 0.0
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


if __name__ == "__main__":
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    import os
    from kdcube_ai_app.storage.storage import create_storage_backend

    logging.basicConfig(level=logging.INFO)

    bundle_id = "with.codegen"
    tenant = os.getenv("DEFAULT_TENANT", "home")
    project = os.getenv("DEFAULT_PROJECT_NAME", "demo")
    user_id = os.getenv("TEST_USER_ID")

    kdcube_path = os.getenv("KDCUBE_STORAGE_PATH", "file:///tmp/kdcube_data")
    backend = create_storage_backend(kdcube_path)
    calc = RateCalculator(backend, base_path="accounting")

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