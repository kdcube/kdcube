# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# utils/sql_dt_utils.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any, List, Tuple, Union, Optional, Iterable

# -------- basics --------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def is_date_only(x: Any) -> bool:
    return isinstance(x, str) and "T" not in x and len(x) >= 10 and x[4] == "-" and x[7] == "-"

def _parse_utc_instant(val: str | None) -> datetime | None:
    """Parse ISO-8601 or YYYY-MM-DD to a tz-aware UTC datetime (seconds precision)."""
    if not val:
        return None
    s = str(val).strip()
    try:
        # date-only
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            d = datetime.fromisoformat(s).date()
            return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
        # normalize 'Z'
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0)
    except Exception:
        return None

def to_utc_dt(x: Union[str, datetime]) -> datetime:
    """tz-aware UTC datetime from datetime or ISO string (supports trailing Z)."""
    if isinstance(x, datetime):
        return (x if x.tzinfo else x.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    s = str(x).strip().replace(" ", "T")
    if is_date_only(s):
        d = datetime.fromisoformat(s[:10]).date()
        return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def iso_utc_text(x: Union[str, datetime]) -> str:
    """Canonical ISO-8601 UTC (seconds precision, trailing Z)."""
    dt = to_utc_dt(x)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

def day_bounds_dt(day: Union[str, datetime]) -> tuple[datetime, datetime]:
    """[start, next_day) as UTC datetimes for YYYY-MM-DD (or any input coerced to that day)."""
    d = to_utc_dt(day).date() if isinstance(day, datetime) else datetime.fromisoformat(str(day)[:10]).date()
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)

def day_bounds_iso(day: Union[str, datetime]) -> tuple[str, str]:
    """[start, next_day) as ISO Z strings."""
    lo, hi = day_bounds_dt(day)
    return (iso_utc_text(lo), iso_utc_text(hi))

def parse_ts_safe(x: Any) -> datetime:
    """Best-effort parse for sorting; returns datetime.min on failure."""
    try:
        if isinstance(x, datetime):
            return to_utc_dt(x)
        if isinstance(x, str) and x:
            return datetime.fromisoformat(x.replace("Z", "+00:00"))
    except Exception:
        pass
    return datetime.min

# -------- SQL helper for time filters --------

def build_temporal_filters(
    *,
    col_expr: str,          # the SQL expression (TEXT or TIMESTAMPTZ-capable)
    mode: str = "text",     # "text" | "timestamptz"
    on: Optional[Union[str, datetime]] = None,
    after: Optional[Union[str, datetime]] = None,
    before: Optional[Union[str, datetime]] = None,
    placeholder: str = "$%s",
) -> tuple[List[str], List[Any]]:
    """
    Build WHERE clauses and params for time windows.
    - TEXT mode: returns ISO strings; lexicographic compare on canonical ISO Z.
    - TIMESTAMPTZ mode: returns tz-aware datetimes; compare with ::timestamptz.
    Semantics:
      on -> [day, day+1)
      after -> >= instant (if date-only: start-of-day)
      before -> < instant (if date-only: start-of-day)
    """
    clauses: List[str] = []
    params:  List[Any] = []

    if mode not in ("text", "timestamptz"):
        raise ValueError("mode must be 'text' or 'timestamptz'")

    if mode == "text":
        col = col_expr
        to_point = iso_utc_text
        start_of = lambda v: day_bounds_iso(v)[0]
        range_of = day_bounds_iso
        cast = ""
    else:
        # prefer casting the COLUMN so params can stay "unknown" (asyncpg accepts str or dt),
        # but we will pass real datetimes anyway for clarity/perf.
        col_cast = "::timestamptz"
        col = f"{col_expr}{col_cast}"
        to_point = to_utc_dt
        start_of = lambda v: day_bounds_dt(v)[0]
        range_of = day_bounds_dt
        cast = "::timestamptz"

    if on:
        lo, hi = range_of(on)
        clauses.append(f"{col} >= {placeholder}{cast} AND {col} < {placeholder}{cast}")
        params.extend([lo, hi])

    if after:
        v = start_of(after) if is_date_only(after) else to_point(after)
        clauses.append(f"{col} >= {placeholder}{cast}")
        params.append(v)

    if before:
        v = start_of(before) if is_date_only(before) else to_point(before)
        clauses.append(f"{col} < {placeholder}{cast}")
        params.append(v)

    return clauses, params

def patch_placeholders(clauses: Iterable[str], start_index: int) -> tuple[List[str], int]:
    """
    Replace each '$%s' token with sequential $N starting at start_index+1.
    Returns (patched_clauses, last_index).
    """
    idx = start_index
    patched: list[str] = []
    for clause in clauses:
        # replace one placeholder at a time so each gets a unique index
        while "$%s" in clause:
            idx += 1
            clause = clause.replace("$%s", f"${idx}", 1)
        patched.append(clause)
    return patched, idx
