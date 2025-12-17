# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/accounting/aggregator.py

"""
Accounting aggregation job.

This module reads *raw* accounting events from the `accounting` tree and
writes pre-aggregated usage summaries into an `analytics` tree:

RAW EVENTS (unchanged):
    accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>/
    accounting/<tenant>/<project>/<YYYY>/<MM>/<DD>/

AGGREGATES (new layout):
    analytics/<tenant>/<project>/accounting/
        daily/<YYYY>/<MM>/<DD>/total.json
        hourly/<YYYY>/<MM>/<DD>/<HH>/total.json
        monthly/<YYYY>/<MM>/total.json
        yearly/<YYYY>/total.json

Each *total.json* has the shape:

{
  "tenant_id": "...",
  "project_id": "...",
  "level": "daily" | "monthly" | "yearly" | "hourly",
  "year": 2025,
  "month": 11,
  "day": 20,
  "hour": 1,
  "bucket_start": "2025-11-20T01:00:00Z",
  "bucket_end": "2025-11-20T02:00:00Z",
  "total": { ... full usage counters ... },
  "rollup": [ { service, provider, model, spent }, ... ],
  "event_count": 42,
  "user_ids": ["admin-user-1", "..."],
  "aggregated_at": "2025-11-20T03:17:00Z"
}

NOTES:
- Aggregates are *additive* across buckets: you can safely sum totals and
  rollups across different daily buckets without double-counting, as long
  as the time ranges do not overlap.
- Raw event files are left untouched. The calculator is updated separately
  to *optionally* use these aggregates when they fully cover a requested range.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Set

from kdcube_ai_app.storage.storage import IStorageBackend
from kdcube_ai_app.infra.accounting.calculator import (
    _new_usage_acc,
    _extract_usage,
    _accumulate,
    _spent_seed,
    _accumulate_compact,
)

logger = logging.getLogger("AccountingAggregator")


class AccountingAggregator:
    """
    Aggregates raw accounting events into pre-computed buckets.

    RAW:
        accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>/...

    AGGREGATES:
        analytics/<tenant>/<project>/accounting/
            daily/<YYYY>/<MM>/<DD>/total.json
            hourly/<YYYY>/<MM>/<DD>/<HH>/total.json
            monthly/<YYYY>/<MM>/total.json
            yearly/<YYYY>/total.json
    """

    def __init__(
        self,
        storage_backend: IStorageBackend,
        *,
        raw_base: str = "accounting",
        agg_base: str = "analytics",
    ):
        self.fs = storage_backend
        self.raw_base = raw_base.strip("/")
        self.agg_base = agg_base.strip("/")

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    async def _safe_listdir(self, path: str) -> List[str]:
        try:
            return await self.fs.list_dir_a(path)
        except Exception:
            logger.debug("list_dir failed for %s", path, exc_info=True)
            return []

    async def _iter_raw_event_paths_for_day(
        self,
        tenant_id: str,
        project_id: str,
        d: date,
    ) -> List[str]:
        """
        Collect all JSON *raw* event file paths for a given calendar day
        under both:

          <raw_base>/<tenant>/<project>/<YYYY>.<MM>.<DD>/
        and legacy:
          <raw_base>/<tenant>/<project>/<YYYY>/<MM>/<DD>/

        NOTE: we deliberately do *not* look under aggregate folders.
        """
        dot_label = f"{d.year:04d}.{d.month:02d}.{d.day:02d}"
        roots = [
            f"{self.raw_base}/{tenant_id}/{project_id}/{dot_label}",
            f"{self.raw_base}/{tenant_id}/{project_id}/{d.year:04d}/{d.month:02d}/{d.day:02d}",
        ]

        all_paths: List[str] = []

        for date_root in roots:
            stypes = await self._safe_listdir(date_root)
            if not stypes:
                continue

            for st in sorted(stypes):
                st_root = f"{date_root}/{st}"
                groups = await self._safe_listdir(st_root)
                if not groups:
                    continue

                # Flat: JSON files directly under service-type directory
                has_json_files = any(name.endswith(".json") for name in groups)

                if has_json_files:
                    for fname in groups:
                        if fname.endswith(".json"):
                            all_paths.append(f"{st_root}/{fname}")
                else:
                    # Grouped: <date>/<service>/<group>/*.json
                    for g in groups:
                        g_root = f"{st_root}/{g}"
                        files = await self._safe_listdir(g_root)
                        for fname in files:
                            if fname.endswith(".json"):
                                all_paths.append(f"{g_root}/{fname}")

        return all_paths

    # ... bucket_meta helpers and _rollup_to_list stay unchanged ...

    @staticmethod
    def _bucket_meta_daily(day: date) -> Dict[str, Any]:
        start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        return {
            "level": "daily",
            "year": day.year,
            "month": day.month,
            "day": day.day,
            "hour": None,
            "bucket_start": start.isoformat().replace("+00:00", "Z"),
            "bucket_end": end.isoformat().replace("+00:00", "Z"),
        }

    @staticmethod
    def _bucket_meta_hourly(day: date, hour: int) -> Dict[str, Any]:
        start = datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        return {
            "level": "hourly",
            "year": day.year,
            "month": day.month,
            "day": day.day,
            "hour": hour,
            "bucket_start": start.isoformat().replace("+00:00", "Z"),
            "bucket_end": end.isoformat().replace("+00:00", "Z"),
        }

    @staticmethod
    def _bucket_meta_monthly(year: int, month: int) -> Dict[str, Any]:
        from calendar import monthrange

        _, ndays = monthrange(year, month)
        start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
        # first of next month
        if month == 12:
            end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        return {
            "level": "monthly",
            "year": year,
            "month": month,
            "day": None,
            "hour": None,
            "bucket_start": start.isoformat().replace("+00:00", "Z"),
            "bucket_end": end.isoformat().replace("+00:00", "Z"),
            "days_in_month": ndays,
        }

    @staticmethod
    def _bucket_meta_yearly(year: int) -> Dict[str, Any]:
        start = datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        return {
            "level": "yearly",
            "year": year,
            "month": None,
            "day": None,
            "hour": None,
            "bucket_start": start.isoformat().replace("+00:00", "Z"),
            "bucket_end": end.isoformat().replace("+00:00", "Z"),
        }

    @staticmethod
    def _rollup_to_list(
            rollup_map: Dict[Tuple[str, str, str], Dict[str, int]]
    ) -> List[Dict[str, Any]]:
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
        return rollup_list

    # -------------------------------------------------------------------------
    # Daily + hourly aggregation from raw events
    # -------------------------------------------------------------------------

    async def aggregate_daily_for_project(
        self,
        *,
        tenant_id: str,
        project_id: str,
        day: date,
    ) -> Optional[Dict[str, Any]]:
        """
        Compute a daily aggregate for (tenant_id, project_id, day) and
        write it to:

          analytics/<tenant>/<project>/accounting/
              daily/<YYYY>/<MM>/<DD>/total.json

        Additionally, writes hourly aggregates for each hour that has
        at least one event:

          analytics/<tenant>/<project>/accounting/
              hourly/<YYYY>/<MM>/<DD>/<HH>/total.json

        Returns the *daily* aggregate payload, or None if there were no events.
        """
        paths = await self._iter_raw_event_paths_for_day(tenant_id, project_id, day)
        if not paths:
            logger.info(
                "[aggregate_daily_for_project] No events for %s/%s on %s",
                tenant_id,
                project_id,
                day.isoformat(),
            )
            return None

        # Daily accumulator
        total_daily = _new_usage_acc()
        rollup_daily: Dict[Tuple[str, str, str], Dict[str, int]] = {}
        user_ids_daily: Set[str] = set()
        event_count_daily = 0

        # Hourly accumulators: hour -> (total, rollup, user_ids, count)
        hourly_totals: Dict[int, Dict[str, Any]] = {}
        hourly_rollups: Dict[int, Dict[Tuple[str, str, str], Dict[str, int]]] = {}
        hourly_user_ids: Dict[int, Set[str]] = {}
        hourly_counts: Dict[int, int] = {}

        # Daily accumulator
        total_daily = _new_usage_acc()
        rollup_daily: Dict[Tuple[str, str, str], Dict[str, int]] = {}
        user_ids_daily: Set[str] = set()
        event_count_daily = 0

        # Per-user daily
        user_totals: Dict[str, Dict[str, Any]] = {}
        user_rollups: Dict[str, Dict[Tuple[str, str, str], Dict[str, int]]] = {}
        user_event_counts: Dict[str, int] = {}

        # Per-agent daily
        agent_totals: Dict[str, Dict[str, Any]] = {}
        agent_rollups: Dict[str, Dict[Tuple[str, str, str], Dict[str, int]]] = {}
        agent_event_counts: Dict[str, int] = {}

        for p in paths:
            try:
                raw = await self.fs.read_text_a(p)
                ev = json.loads(raw)
            except Exception:
                logger.debug("Skipping unreadable event %s", p, exc_info=True)
                continue

            usage = _extract_usage(ev)
            # Even if usage is missing, we still count the event
            event_count_daily += 1

            if not usage:
                continue

            # ------------ daily ------------
            _accumulate(total_daily, usage)

            service = str(ev.get("service_type") or "").strip()
            if service:
                ctx = ev.get("context") or {}
                provider = str(ev.get("provider") or ctx.get("provider") or "").strip()
                model = str(ev.get("model_or_service") or ctx.get("model_or_service") or "").strip()

                key = (service, provider, model)
                spent = rollup_daily.get(key)
                if not spent:
                    spent = _spent_seed(service)
                    rollup_daily[key] = spent

                _accumulate_compact(spent, usage, service)

            uid = ev.get("user_id") or (ev.get("context") or {}).get("user_id")
            if uid:
                uid = str(uid)
                user_ids_daily.add(uid)

                # ---- per-user totals / rollups ----
                if uid not in user_totals:
                    user_totals[uid] = _new_usage_acc()
                    user_rollups[uid] = {}
                    user_event_counts[uid] = 0

                _accumulate(user_totals[uid], usage)
                user_event_counts[uid] += 1

                if service:
                    key = (service, provider, model)
                    u_spent = user_rollups[uid].get(key)
                    if not u_spent:
                        u_spent = _spent_seed(service)
                        user_rollups[uid][key] = u_spent
                    _accumulate_compact(u_spent, usage, service)

            # ---- per-agent totals / rollups ----
            agent_name = self._extract_agent_name(ev)
            if agent_name not in agent_totals:
                agent_totals[agent_name] = _new_usage_acc()
                agent_rollups[agent_name] = {}
                agent_event_counts[agent_name] = 0

            _accumulate(agent_totals[agent_name], usage)
            agent_event_counts[agent_name] += 1

            if service:
                key = (service, provider, model)
                a_spent = agent_rollups[agent_name].get(key)
                if not a_spent:
                    a_spent = _spent_seed(service)
                    agent_rollups[agent_name][key] = a_spent
                _accumulate_compact(a_spent, usage, service)

            # ------------ hourly ------------
            ts_raw = ev.get("timestamp") or ""
            hour: Optional[int] = None
            try:
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                # Only bucket into this day; if mismatch, skip hourly bucketing
                if dt.date() == day:
                    hour = dt.hour
            except Exception:
                hour = None

            if hour is not None:
                if hour not in hourly_totals:
                    hourly_totals[hour] = _new_usage_acc()
                    hourly_rollups[hour] = {}
                    hourly_user_ids[hour] = set()
                    hourly_counts[hour] = 0

                _accumulate(hourly_totals[hour], usage)

                if service:
                    ctx = ev.get("context") or {}
                    provider = str(ev.get("provider") or ctx.get("provider") or "").strip()
                    model = str(ev.get("model_or_service") or ctx.get("model_or_service") or "").strip()

                    key = (service, provider, model)
                    spent = hourly_rollups[hour].get(key)
                    if not spent:
                        spent = _spent_seed(service)
                        hourly_rollups[hour][key] = spent
                    _accumulate_compact(spent, usage, service)

                hourly_counts[hour] += 1
                if uid:
                    hourly_user_ids[hour].add(str(uid))

        if event_count_daily == 0:
            logger.info(
                "[aggregate_daily_for_project] No usable events for %s/%s on %s",
                tenant_id,
                project_id,
                day.isoformat(),
            )
            return None

        # --------- write DAILY aggregate ---------
        daily_meta = self._bucket_meta_daily(day)
        daily_payload: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "level": daily_meta["level"],
            "year": daily_meta["year"],
            "month": daily_meta["month"],
            "day": daily_meta["day"],
            "hour": daily_meta["hour"],
            "bucket_start": daily_meta["bucket_start"],
            "bucket_end": daily_meta["bucket_end"],
            "total": total_daily,
            "rollup": self._rollup_to_list(rollup_daily),
            "event_count": event_count_daily,
            "user_ids": sorted(user_ids_daily),
            "aggregated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        daily_folder = (
            f"{self.agg_base}/{tenant_id}/{project_id}/"
            f"accounting/daily/{day.year:04d}/{day.month:02d}/{day.day:02d}"
        )
        daily_path = f"{daily_folder}/total.json"
        await self.fs.write_text_a(daily_path, json.dumps(daily_payload, ensure_ascii=False))
        logger.info(
            "[aggregate_daily_for_project] Aggregated %d events into %s",
            event_count_daily,
            daily_path,
        )
        # --------- write PER-USER daily aggregates ---------
        users_items = []
        for uid in sorted(user_totals.keys()):
            users_items.append(
                {
                    "user_id": uid,
                    "event_count": int(user_event_counts.get(uid, 0)),
                    "total": user_totals[uid],
                    "rollup": self._rollup_to_list(user_rollups.get(uid, {})),
                }
            )

        users_payload: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "level": daily_meta["level"],
            "dimension": "user",
            "year": daily_meta["year"],
            "month": daily_meta["month"],
            "day": daily_meta["day"],
            "hour": None,
            "bucket_start": daily_meta["bucket_start"],
            "bucket_end": daily_meta["bucket_end"],
            "users": users_items,
            "aggregated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        users_path = f"{daily_folder}/users.json"
        await self.fs.write_text_a(users_path, json.dumps(users_payload, ensure_ascii=False))
        logger.info(
            "[aggregate_daily_for_project] Wrote per-user aggregates (%d users) into %s",
            len(users_items),
            users_path,
        )

        # --------- write PER-AGENT daily aggregates ---------
        agents_items = []
        for agent_name in sorted(agent_totals.keys()):
            agents_items.append(
                {
                    "agent_name": agent_name,
                    "event_count": int(agent_event_counts.get(agent_name, 0)),
                    "total": agent_totals[agent_name],
                    "rollup": self._rollup_to_list(agent_rollups.get(agent_name, {})),
                }
            )

        agents_payload: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "level": daily_meta["level"],
            "dimension": "agent",
            "year": daily_meta["year"],
            "month": daily_meta["month"],
            "day": daily_meta["day"],
            "hour": None,
            "bucket_start": daily_meta["bucket_start"],
            "bucket_end": daily_meta["bucket_end"],
            "agents": agents_items,
            "aggregated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        agents_path = f"{daily_folder}/agents.json"
        await self.fs.write_text_a(agents_path, json.dumps(agents_payload, ensure_ascii=False))
        logger.info(
            "[aggregate_daily_for_project] Wrote per-agent aggregates (%d agents) into %s",
            len(agents_items),
            agents_path,
        )

        # --------- write HOURLY aggregates ---------
        for hour, total_hour in sorted(hourly_totals.items()):
            meta = self._bucket_meta_hourly(day, hour)
            payload_hour: Dict[str, Any] = {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "level": meta["level"],
                "year": meta["year"],
                "month": meta["month"],
                "day": meta["day"],
                "hour": meta["hour"],
                "bucket_start": meta["bucket_start"],
                "bucket_end": meta["bucket_end"],
                "total": total_hour,
                "rollup": self._rollup_to_list(hourly_rollups[hour]),
                "event_count": int(hourly_counts.get(hour, 0)),
                "user_ids": sorted(hourly_user_ids.get(hour, set())),
                "aggregated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }

            folder_hour = (
                f"{self.agg_base}/{tenant_id}/{project_id}/"
                f"accounting/hourly/{day.year:04d}/{day.month:02d}/{day.day:02d}/{hour:02d}"
            )
            path_hour = f"{folder_hour}/total.json"
            await self.fs.write_text_a(path_hour, json.dumps(payload_hour, ensure_ascii=False))
            logger.info(
                "[aggregate_daily_for_project] Aggregated hour=%02d (%d events) into %s",
                hour,
                payload_hour["event_count"],
                path_hour,
            )

        return daily_payload

    async def aggregate_daily_range_for_project(
        self,
        *,
        tenant_id: str,
        project_id: str,
        date_from: str,
        date_to: str,
        skip_existing: bool = True,
    ) -> None:
        """
        Aggregate a whole range of dates (inclusive) to daily + hourly aggregates.
        """
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d").date()
            dt = datetime.strptime(date_to, "%Y-%m-%d").date()
        except Exception as e:
            raise ValueError(f"Invalid date range {date_from}..{date_to}: {e}") from e

        if df > dt:
            raise ValueError(f"date_from {date_from} must be <= date_to {date_to}")

        cur = df
        while cur <= dt:
            daily_folder = (
                f"{self.agg_base}/{tenant_id}/{project_id}/"
                f"accounting/daily/{cur.year:04d}/{cur.month:02d}/{cur.day:02d}"
            )
            total_path = f"{daily_folder}/total.json"
            users_path = f"{daily_folder}/users.json"
            agents_path = f"{daily_folder}/agents.json"

            if skip_existing:
                try:
                    total_exists = await self.fs.exists_a(total_path)
                    users_exists = await self.fs.exists_a(users_path)
                    agents_exists = await self.fs.exists_a(agents_path)
                except Exception:
                    # If exists() fails for some path, treat as missing so we recompute
                    total_exists = users_exists = agents_exists = False

                if total_exists and users_exists and agents_exists:
                    logger.info(
                        "[aggregate_daily_range_for_project] Skipping %s (all daily aggregates present)",
                        daily_folder,
                    )
                    cur += timedelta(days=1)
                    continue

            await self.aggregate_daily_for_project(
                tenant_id=tenant_id,
                project_id=project_id,
                day=cur,
            )
            cur += timedelta(days=1)

    # -------------------------------------------------------------------------
    # Monthly from daily
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Monthly from daily
    # -------------------------------------------------------------------------

    async def aggregate_monthly_from_daily(
            self,
            *,
            tenant_id: str,
            project_id: str,
            year: int,
            month: int,
            require_full_coverage: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Aggregate all available daily buckets in a month into a single monthly bucket.

        Reads:
          analytics/<tenant>/<project>/accounting/daily/<YYYY>/<MM>/<DD>/total.json
          analytics/<tenant>/<project>/accounting/daily/<YYYY>/<MM>/<DD>/users.json
          analytics/<tenant>/<project>/accounting/daily/<YYYY>/<MM>/<DD>/agents.json

        Writes:
          analytics/<tenant>/<project>/accounting/monthly/<YYYY>/<MM>/total.json
          analytics/<tenant>/<project>/accounting/monthly/<YYYY>/<MM>/users.json
          analytics/<tenant>/<project>/accounting/monthly/<YYYY>/<MM>/agents.json
        """
        from calendar import monthrange

        _, ndays = monthrange(year, month)
        total = _new_usage_acc()
        rollup_map: Dict[Tuple[str, str, str], Dict[str, int]] = {}
        user_ids: Set[str] = set()
        event_count = 0
        used_days = 0
        missing_days = 0

        # per-user monthly
        per_user_totals: Dict[str, Dict[str, Any]] = {}
        per_user_rollups: Dict[str, Dict[Tuple[str, str, str], Dict[str, int]]] = {}
        per_user_events: Dict[str, int] = {}

        # per-agent monthly
        per_agent_totals: Dict[str, Dict[str, Any]] = {}
        per_agent_rollups: Dict[str, Dict[Tuple[str, str, str], Dict[str, int]]] = {}
        per_agent_events: Dict[str, int] = {}

        for day in range(1, ndays + 1):
            folder = (
                f"{self.agg_base}/{tenant_id}/{project_id}/"
                f"accounting/daily/{year:04d}/{month:02d}/{day:02d}"
            )
            path = f"{folder}/total.json"

            try:
                if not await self.fs.exists_a(path):
                    missing_days += 1
                    continue
                raw = await self.fs.read_text_a(path)
                payload = json.loads(raw)
            except Exception:
                missing_days += 1
                continue

            used_days += 1

            bucket_total = payload.get("total") or {}
            _accumulate(total, bucket_total)

            for item in payload.get("rollup", []):
                service = item.get("service")
                provider = item.get("provider") or ""
                model = item.get("model") or ""
                spent = item.get("spent") or {}
                key = (service, provider, model)

                existing = rollup_map.get(key)
                if not existing:
                    existing = _spent_seed(service or "")
                    rollup_map[key] = existing

                for k, v in spent.items():
                    existing[k] = int(existing.get(k, 0)) + int(v or 0)

            event_count += int(payload.get("event_count") or 0)
            for uid in payload.get("user_ids", []):
                if uid is not None:
                    user_ids.add(str(uid))

            # --------- per-user monthly from daily/users.json ---------
            users_path = f"{folder}/users.json"
            try:
                if await self.fs.exists_a(users_path):
                    raw_users = await self.fs.read_text_a(users_path)
                    users_payload = json.loads(raw_users)

                    for item in users_payload.get("users", []):
                        uid = item.get("user_id")
                        if not uid:
                            continue
                        uid = str(uid)

                        if uid not in per_user_totals:
                            per_user_totals[uid] = _new_usage_acc()
                            per_user_rollups[uid] = {}
                            per_user_events[uid] = 0

                        tot = item.get("total") or {}
                        _accumulate(per_user_totals[uid], tot)
                        per_user_events[uid] += int(item.get("event_count") or 0)

                        for r in item.get("rollup", []):
                            svc = r.get("service")
                            prov = r.get("provider") or ""
                            mdl = r.get("model") or ""
                            spent = r.get("spent") or {}
                            key = (svc, prov, mdl)

                            existing = per_user_rollups[uid].get(key)
                            if not existing:
                                existing = _spent_seed(svc or "")
                                per_user_rollups[uid][key] = existing

                            for k, v in spent.items():
                                existing[k] = int(existing.get(k, 0)) + int(v or 0)
            except Exception:
                logger.debug(
                    "[aggregate_monthly_from_daily] Failed to read %s", users_path, exc_info=True
                )

            # --------- per-agent monthly from daily/agents.json ---------
            agents_path = f"{folder}/agents.json"
            try:
                if await self.fs.exists_a(agents_path):
                    raw_agents = await self.fs.read_text_a(agents_path)
                    agents_payload = json.loads(raw_agents)

                    for item in agents_payload.get("agents", []):
                        agent_name = item.get("agent_name")
                        if not agent_name:
                            continue
                        agent_name = str(agent_name)

                        if agent_name not in per_agent_totals:
                            per_agent_totals[agent_name] = _new_usage_acc()
                            per_agent_rollups[agent_name] = {}
                            per_agent_events[agent_name] = 0

                        tot = item.get("total") or {}
                        _accumulate(per_agent_totals[agent_name], tot)
                        per_agent_events[agent_name] += int(item.get("event_count") or 0)

                        for r in item.get("rollup", []):
                            svc = r.get("service")
                            prov = r.get("provider") or ""
                            mdl = r.get("model") or ""
                            spent = r.get("spent") or {}
                            key = (svc, prov, mdl)

                            existing = per_agent_rollups[agent_name].get(key)
                            if not existing:
                                existing = _spent_seed(svc or "")
                                per_agent_rollups[agent_name][key] = existing

                            for k, v in spent.items():
                                existing[k] = int(existing.get(k, 0)) + int(v or 0)
            except Exception:
                logger.debug(
                    "[aggregate_monthly_from_daily] Failed to read %s", agents_path, exc_info=True
                )

        if used_days == 0:
            logger.info(
                "[aggregate_monthly_from_daily] No daily aggregates for %s/%s %04d-%02d",
                tenant_id,
                project_id,
                year,
                month,
            )
            return None

        if require_full_coverage and missing_days > 0:
            logger.info(
                "[aggregate_monthly_from_daily] Missing %d days for %s/%s %04d-%02d; skipping monthly",
                missing_days,
                tenant_id,
                project_id,
                year,
                month,
            )
            return None

        meta = self._bucket_meta_monthly(year, month)

        payload: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "level": meta["level"],
            "year": meta["year"],
            "month": meta["month"],
            "day": meta["day"],
            "hour": meta["hour"],
            "bucket_start": meta["bucket_start"],
            "bucket_end": meta["bucket_end"],
            "total": total,
            "rollup": self._rollup_to_list(rollup_map),
            "event_count": event_count,
            "user_ids": sorted(user_ids),
            "aggregated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "days_covered": used_days,
            "days_missing": missing_days,
            "days_in_month": meta["days_in_month"],
        }

        folder = (
            f"{self.agg_base}/{tenant_id}/{project_id}/"
            f"accounting/monthly/{year:04d}/{month:02d}"
        )
        path = f"{folder}/total.json"
        await self.fs.write_text_a(path, json.dumps(payload, ensure_ascii=False))

        logger.info(
            "[aggregate_monthly_from_daily] Aggregated %d days (missing=%d) into %s",
            used_days,
            missing_days,
            path,
        )

        # --------- write per-user monthly aggregates ---------
        if per_user_totals:
            users_items = []
            for uid in sorted(per_user_totals.keys()):
                users_items.append(
                    {
                        "user_id": uid,
                        "event_count": int(per_user_events.get(uid, 0)),
                        "total": per_user_totals[uid],
                        "rollup": self._rollup_to_list(per_user_rollups.get(uid, {})),
                    }
                )

            users_payload: Dict[str, Any] = {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "level": meta["level"],
                "dimension": "user",
                "year": meta["year"],
                "month": meta["month"],
                "day": None,
                "hour": None,
                "bucket_start": meta["bucket_start"],
                "bucket_end": meta["bucket_end"],
                "days_covered": used_days,
                "days_missing": missing_days,
                "days_in_month": meta["days_in_month"],
                "users": users_items,
                "aggregated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }

            users_path_out = f"{folder}/users.json"
            await self.fs.write_text_a(users_path_out, json.dumps(users_payload, ensure_ascii=False))
            logger.info(
                "[aggregate_monthly_from_daily] Wrote per-user monthly aggregates (%d users) into %s",
                len(users_items),
                users_path_out,
            )

        # --------- write per-agent monthly aggregates ---------
        if per_agent_totals:
            agents_items = []
            for agent_name in sorted(per_agent_totals.keys()):
                agents_items.append(
                    {
                        "agent_name": agent_name,
                        "event_count": int(per_agent_events.get(agent_name, 0)),
                        "total": per_agent_totals[agent_name],
                        "rollup": self._rollup_to_list(per_agent_rollups.get(agent_name, {})),
                    }
                )

            agents_payload: Dict[str, Any] = {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "level": meta["level"],
                "dimension": "agent",
                "year": meta["year"],
                "month": meta["month"],
                "day": None,
                "hour": None,
                "bucket_start": meta["bucket_start"],
                "bucket_end": meta["bucket_end"],
                "days_covered": used_days,
                "days_missing": missing_days,
                "days_in_month": meta["days_in_month"],
                "agents": agents_items,
                "aggregated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }

            agents_path_out = f"{folder}/agents.json"
            await self.fs.write_text_a(agents_path_out, json.dumps(agents_payload, ensure_ascii=False))
            logger.info(
                "[aggregate_monthly_from_daily] Wrote per-agent monthly aggregates (%d agents) into %s",
                len(agents_items),
                agents_path_out,
            )

        return payload

    # -------------------------------------------------------------------------
    # Yearly from monthly
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Yearly from monthly
    # -------------------------------------------------------------------------

    async def aggregate_yearly_from_monthly(
            self,
            *,
            tenant_id: str,
            project_id: str,
            year: int,
            require_full_coverage: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Aggregate all available monthly buckets in a year into a yearly bucket.

        Reads:
          analytics/<tenant>/<project>/accounting/monthly/<YYYY>/<MM>/total.json
          analytics/<tenant>/<project>/accounting/monthly/<YYYY>/<MM>/users.json
          analytics/<tenant>/<project>/accounting/monthly/<YYYY>/<MM>/agents.json

        Writes:
          analytics/<tenant>/<project>/accounting/yearly/<YYYY>/total.json
          analytics/<tenant>/<project>/accounting/yearly/<YYYY>/users.json
          analytics/<tenant>/<project>/accounting/yearly/<YYYY>/agents.json
        """
        total = _new_usage_acc()
        rollup_map: Dict[Tuple[str, str, str], Dict[str, int]] = {}
        user_ids: Set[str] = set()
        event_count = 0
        used_months = 0
        missing_months = 0

        # per-user yearly
        per_user_totals: Dict[str, Dict[str, Any]] = {}
        per_user_rollups: Dict[str, Dict[Tuple[str, str, str], Dict[str, int]]] = {}
        per_user_events: Dict[str, int] = {}

        # per-agent yearly
        per_agent_totals: Dict[str, Dict[str, Any]] = {}
        per_agent_rollups: Dict[str, Dict[Tuple[str, str, str], Dict[str, int]]] = {}
        per_agent_events: Dict[str, int] = {}

        for month in range(1, 13):
            folder = (
                f"{self.agg_base}/{tenant_id}/{project_id}/"
                f"accounting/monthly/{year:04d}/{month:02d}"
            )
            path = f"{folder}/total.json"

            try:
                if not await self.fs.exists_a(path):
                    missing_months += 1
                    continue
                raw = await self.fs.read_text_a(path)
                payload = json.loads(raw)
            except Exception:
                missing_months += 1
                continue

            used_months += 1

            bucket_total = payload.get("total") or {}
            _accumulate(total, bucket_total)

            for item in payload.get("rollup", []):
                service = item.get("service")
                provider = item.get("provider") or ""
                model = item.get("model") or ""
                spent = item.get("spent") or {}
                key = (service, provider, model)

                existing = rollup_map.get(key)
                if not existing:
                    existing = _spent_seed(service or "")
                    rollup_map[key] = existing

                for k, v in spent.items():
                    existing[k] = int(existing.get(k, 0)) + int(v or 0)

            event_count += int(payload.get("event_count") or 0)
            for uid in payload.get("user_ids", []):
                if uid is not None:
                    user_ids.add(str(uid))

            # --------- per-user yearly from monthly/users.json ---------
            users_path = f"{folder}/users.json"
            try:
                if await self.fs.exists_a(users_path):
                    raw_users = await self.fs.read_text_a(users_path)
                    users_payload = json.loads(raw_users)

                    for item in users_payload.get("users", []):
                        uid = item.get("user_id")
                        if not uid:
                            continue
                        uid = str(uid)

                        if uid not in per_user_totals:
                            per_user_totals[uid] = _new_usage_acc()
                            per_user_rollups[uid] = {}
                            per_user_events[uid] = 0

                        tot = item.get("total") or {}
                        _accumulate(per_user_totals[uid], tot)
                        per_user_events[uid] += int(item.get("event_count") or 0)

                        for r in item.get("rollup", []):
                            svc = r.get("service")
                            prov = r.get("provider") or ""
                            mdl = r.get("model") or ""
                            spent = r.get("spent") or {}
                            key = (svc, prov, mdl)

                            existing = per_user_rollups[uid].get(key)
                            if not existing:
                                existing = _spent_seed(svc or "")
                                per_user_rollups[uid][key] = existing

                            for k, v in spent.items():
                                existing[k] = int(existing.get(k, 0)) + int(v or 0)
            except Exception:
                logger.debug(
                    "[aggregate_yearly_from_monthly] Failed to read %s", users_path, exc_info=True
                )

            # --------- per-agent yearly from monthly/agents.json ---------
            agents_path = f"{folder}/agents.json"
            try:
                if await self.fs.exists_a(agents_path):
                    raw_agents = await self.fs.read_text_a(agents_path)
                    agents_payload = json.loads(raw_agents)

                    for item in agents_payload.get("agents", []):
                        agent_name = item.get("agent_name")
                        if not agent_name:
                            continue
                        agent_name = str(agent_name)

                        if agent_name not in per_agent_totals:
                            per_agent_totals[agent_name] = _new_usage_acc()
                            per_agent_rollups[agent_name] = {}
                            per_agent_events[agent_name] = 0

                        tot = item.get("total") or {}
                        _accumulate(per_agent_totals[agent_name], tot)
                        per_agent_events[agent_name] += int(item.get("event_count") or 0)

                        for r in item.get("rollup", []):
                            svc = r.get("service")
                            prov = r.get("provider") or ""
                            mdl = r.get("model") or ""
                            spent = r.get("spent") or {}
                            key = (svc, prov, mdl)

                            existing = per_agent_rollups[agent_name].get(key)
                            if not existing:
                                existing = _spent_seed(svc or "")
                                per_agent_rollups[agent_name][key] = existing

                            for k, v in spent.items():
                                existing[k] = int(existing.get(k, 0)) + int(v or 0)
            except Exception:
                logger.debug(
                    "[aggregate_yearly_from_monthly] Failed to read %s", agents_path, exc_info=True
                )

        if used_months == 0:
            logger.info(
                "[aggregate_yearly_from_monthly] No monthly aggregates for %s/%s %04d",
                tenant_id,
                project_id,
                year,
            )
            return None

        if require_full_coverage and missing_months > 0:
            logger.info(
                "[aggregate_yearly_from_monthly] Missing %d months for %s/%s %04d; skipping yearly",
                missing_months,
                tenant_id,
                project_id,
                year,
            )
            return None

        meta = self._bucket_meta_yearly(year)

        payload: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "level": meta["level"],
            "year": meta["year"],
            "month": meta["month"],
            "day": meta["day"],
            "hour": meta["hour"],
            "bucket_start": meta["bucket_start"],
            "bucket_end": meta["bucket_end"],
            "total": total,
            "rollup": self._rollup_to_list(rollup_map),
            "event_count": event_count,
            "user_ids": sorted(user_ids),
            "aggregated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "months_covered": used_months,
            "months_missing": missing_months,
        }

        folder = (
            f"{self.agg_base}/{tenant_id}/{project_id}/"
            f"accounting/yearly/{year:04d}"
        )
        path = f"{folder}/total.json"
        await self.fs.write_text_a(path, json.dumps(payload, ensure_ascii=False))

        logger.info(
            "[aggregate_yearly_from_monthly] Aggregated %d months (missing=%d) into %s",
            used_months,
            missing_months,
            path,
        )

        # --------- write per-user yearly aggregates ---------
        if per_user_totals:
            users_items = []
            for uid in sorted(per_user_totals.keys()):
                users_items.append(
                    {
                        "user_id": uid,
                        "event_count": int(per_user_events.get(uid, 0)),
                        "total": per_user_totals[uid],
                        "rollup": self._rollup_to_list(per_user_rollups.get(uid, {})),
                    }
                )

            users_payload: Dict[str, Any] = {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "level": meta["level"],
                "dimension": "user",
                "year": meta["year"],
                "month": None,
                "day": None,
                "hour": None,
                "bucket_start": meta["bucket_start"],
                "bucket_end": meta["bucket_end"],
                "months_covered": used_months,
                "months_missing": missing_months,
                "users": users_items,
                "aggregated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }

            users_path_out = f"{folder}/users.json"
            await self.fs.write_text_a(users_path_out, json.dumps(users_payload, ensure_ascii=False))
            logger.info(
                "[aggregate_yearly_from_monthly] Wrote per-user yearly aggregates (%d users) into %s",
                len(users_items),
                users_path_out,
            )

        # --------- write per-agent yearly aggregates ---------
        if per_agent_totals:
            agents_items = []
            for agent_name in sorted(per_agent_totals.keys()):
                agents_items.append(
                    {
                        "agent_name": agent_name,
                        "event_count": int(per_agent_events.get(agent_name, 0)),
                        "total": per_agent_totals[agent_name],
                        "rollup": self._rollup_to_list(per_agent_rollups.get(agent_name, {})),
                    }
                )

            agents_payload: Dict[str, Any] = {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "level": meta["level"],
                "dimension": "agent",
                "year": meta["year"],
                "month": None,
                "day": None,
                "hour": None,
                "bucket_start": meta["bucket_start"],
                "bucket_end": meta["bucket_end"],
                "months_covered": used_months,
                "months_missing": missing_months,
                "agents": agents_items,
                "aggregated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }

            agents_path_out = f"{folder}/agents.json"
            await self.fs.write_text_a(agents_path_out, json.dumps(agents_payload, ensure_ascii=False))
            logger.info(
                "[aggregate_yearly_from_monthly] Wrote per-agent yearly aggregates (%d agents) into %s",
                len(agents_items),
                agents_path_out,
            )

        return payload


    @staticmethod
    def _extract_agent_name(ev: Dict[str, Any]) -> str:
        """Best-effort agent name extraction, aligned with calculator logic."""
        ctx = ev.get("context") or {}
        metadata = ev.get("metadata") or {}
        agent = (
                metadata.get("agent")
                or ctx.get("agent")
                or ev.get("agent")
                or ev.get("agent_name")
        )
        if not agent:
            service_type = ev.get("service_type")
            provider = ev.get("provider")
            if service_type and provider:
                agent = f"{service_type}:{provider}"
        return str(agent) if agent is not None else "unknown"


# -------------------------------------------------------------------------
# Simple CLI entrypoint (optional, for manual runs)
# -------------------------------------------------------------------------

async def _run_cli() -> None:
    """
    Simple entrypoint to run from the command line, e.g.:

        python -m kdcube_ai_app.infra.accounting.aggregator

    Uses STORAGE_PATH / KDCUBE_STORAGE_PATH and aggregates a configured tenant/project.
    """
    import os
    from kdcube_ai_app.storage.storage import create_storage_backend

    storage_uri = (
            os.getenv("STORAGE_PATH")
            or os.getenv("KDCUBE_STORAGE_PATH")
            or "file:///tmp/kdcube_data"
    )
    tenant = os.getenv("DEFAULT_TENANT", "home")
    project = os.getenv("DEFAULT_PROJECT_NAME", "demo")
    date_from = os.getenv("AGG_DATE_FROM")  # e.g. "2025-11-01"
    date_to = os.getenv("AGG_DATE_TO")      # e.g. "2025-11-30"

    if not date_from or not date_to:
        raise SystemExit("Set AGG_DATE_FROM and AGG_DATE_TO (YYYY-MM-DD) env vars")

    backend = create_storage_backend(storage_uri)
    agg = AccountingAggregator(backend, raw_base="accounting")

    await agg.aggregate_daily_range_for_project(
        tenant_id=tenant,
        project_id=project,
        date_from=date_from,
        date_to=date_to,
        skip_existing=True,
    )

    # Optional: also build monthly/yearly for convenience
    df = datetime.strptime(date_from, "%Y-%m-%d").date()
    dt = datetime.strptime(date_to, "%Y-%m-%d").date()

    for year in range(df.year, dt.year + 1):
        for month in range(1, 13):
            # only months overlapping the requested range
            first_of_month = date(year, month, 1)
            last_of_month = (first_of_month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            if last_of_month < df or first_of_month > dt:
                continue

            await agg.aggregate_monthly_from_daily(
                tenant_id=tenant,
                project_id=project,
                year=year,
                month=month,
                require_full_coverage=False,
            )

        await agg.aggregate_yearly_from_monthly(
            tenant_id=tenant,
            project_id=project,
            year=year,
            require_full_coverage=False,
        )


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run_cli())
