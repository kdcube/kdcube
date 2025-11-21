# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/accounting/aggregator.py

"""
Accounting aggregation job.

This module reads *raw* accounting events from the `accounting` tree and
writes pre-aggregated usage summaries into time-partitioned folders:

    accounting/<tenant>/<project>/<YYYY>.yearly/aggregate.json
    accounting/<tenant>/<project>/<YYYY>.<MM>.monthly/aggregate.json
    accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>.daily/aggregate.json
    accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>.<HH>.hourly/aggregate.json

Each aggregate file has the shape:

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
  "rollup": [
    {
      "service": "llm",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5-20250929",
      "spent": {
        "input": 1234,
        "output": 5678,
        "cache_creation": 0,
        "cache_5m_write": 0,
        "cache_1h_write": 0,
        "cache_read": 0,
        "tokens": 0
      }
    },
    ...
  ],
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
    Aggregates raw accounting events into pre-computed buckets:

    - yearly:
        accounting/<tenant>/<project>/<YYYY>.yearly/aggregate.json
    - monthly:
        accounting/<tenant>/<project>/<YYYY>.<MM>.monthly/aggregate.json
    - daily:
        accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>.daily/aggregate.json
    - hourly:
        accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>.<HH>.hourly/aggregate.json
    """

    def __init__(self, storage_backend: IStorageBackend, *, base_path: str = "accounting"):
        self.fs = storage_backend
        self.base = base_path.strip("/")

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

          <base>/<tenant>/<project>/<YYYY>.<MM>.<DD>/
        and legacy:
          <base>/<tenant>/<project>/<YYYY>/<MM>/<DD>/

        NOTE: we deliberately do *not* look under *.daily / *.hourly folders,
        so we won't accidentally re-aggregate aggregates.
        """
        dot_label = f"{d.year:04d}.{d.month:02d}.{d.day:02d}"
        roots = [
            f"{self.base}/{tenant_id}/{project_id}/{dot_label}",
            f"{self.base}/{tenant_id}/{project_id}/{d.year:04d}/{d.month:02d}/{d.day:02d}",
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

          accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>.daily/aggregate.json

        Additionally, writes hourly aggregates for each hour that has
        at least one event:

          accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>.<HH>.hourly/aggregate.json

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
                user_ids_daily.add(str(uid))

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
            f"{self.base}/{tenant_id}/{project_id}/"
            f"{day.year:04d}.{day.month:02d}.{day.day:02d}.daily"
        )
        daily_path = f"{daily_folder}/aggregate.json"
        await self.fs.write_text_a(daily_path, json.dumps(daily_payload, ensure_ascii=False))
        logger.info(
            "[aggregate_daily_for_project] Aggregated %d events into %s",
            event_count_daily,
            daily_path,
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
                f"{self.base}/{tenant_id}/{project_id}/"
                f"{day.year:04d}.{day.month:02d}.{day.day:02d}.{hour:02d}.hourly"
            )
            path_hour = f"{folder_hour}/aggregate.json"
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
            day_label = f"{cur.year:04d}.{cur.month:02d}.{cur.day:02d}.daily"
            folder = f"{self.base}/{tenant_id}/{project_id}/{day_label}"
            agg_path = f"{folder}/aggregate.json"

            if skip_existing:
                try:
                    if await self.fs.exists_a(agg_path):
                        logger.info(
                            "[aggregate_daily_range_for_project] Skipping %s (already exists)",
                            agg_path,
                        )
                        cur += timedelta(days=1)
                        continue
                except Exception:
                    # If exists() fails, we'll just try to re-write
                    pass

            await self.aggregate_daily_for_project(
                tenant_id=tenant_id,
                project_id=project_id,
                day=cur,
            )
            cur += timedelta(days=1)

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

        If require_full_coverage=True, returns None (and does not write anything)
        when at least one day in the month is missing a daily aggregate.
        """
        from calendar import monthrange

        _, ndays = monthrange(year, month)
        total = _new_usage_acc()
        rollup_map: Dict[Tuple[str, str, str], Dict[str, int]] = {}
        user_ids: Set[str] = set()
        event_count = 0
        used_days = 0
        missing_days = 0

        for day in range(1, ndays + 1):
            bname = f"{year:04d}.{month:02d}.{day:02d}.daily"
            folder = f"{self.base}/{tenant_id}/{project_id}/{bname}"
            path = f"{folder}/aggregate.json"

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

        folder = f"{self.base}/{tenant_id}/{project_id}/{year:04d}.{month:02d}.monthly"
        path = f"{folder}/aggregate.json"
        await self.fs.write_text_a(path, json.dumps(payload, ensure_ascii=False))

        logger.info(
            "[aggregate_monthly_from_daily] Aggregated %d days (missing=%d) into %s",
            used_days,
            missing_days,
            path,
        )
        return payload

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

        If require_full_coverage=True, returns None (and does not write anything)
        when at least one month in the year is missing a monthly aggregate.
        """
        total = _new_usage_acc()
        rollup_map: Dict[Tuple[str, str, str], Dict[str, int]] = {}
        user_ids: Set[str] = set()
        event_count = 0
        used_months = 0
        missing_months = 0

        for month in range(1, 13):
            bname = f"{year:04d}.{month:02d}.monthly"
            folder = f"{self.base}/{tenant_id}/{project_id}/{bname}"
            path = f"{folder}/aggregate.json"

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

        folder = f"{self.base}/{tenant_id}/{project_id}/{year:04d}.yearly"
        path = f"{folder}/aggregate.json"
        await self.fs.write_text_a(path, json.dumps(payload, ensure_ascii=False))

        logger.info(
            "[aggregate_yearly_from_monthly] Aggregated %d months (missing=%d) into %s",
            used_months,
            missing_months,
            path,
        )
        return payload


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
    agg = AccountingAggregator(backend, base_path="accounting")

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
