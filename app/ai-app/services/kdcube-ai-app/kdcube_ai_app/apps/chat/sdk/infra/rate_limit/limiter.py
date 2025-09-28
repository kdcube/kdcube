# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# sdk/rate_limit/limiter.py
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis

from kdcube_ai_app.apps.chat.sdk.infra.rate_limit.policy import QuotaPolicy


# --------- helpers (keys / time) ---------
def _k(ns: str, bundle: str, subject: str, *parts: str) -> str:
    return ":".join([ns, bundle, subject, *parts])

def _ymd(dt: datetime) -> str:  return dt.strftime("%Y%m%d")
def _ym(dt: datetime) -> str:   return dt.strftime("%Y%m")
def _ymdh(dt: datetime) -> str: return dt.strftime("%Y%m%d%H")

def _eod(dt: datetime) -> int:
    end = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc) + timedelta(days=1)
    return int(end.timestamp())

def _eom(dt: datetime) -> int:
    if dt.month == 12:
        nxt = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)
    return int(nxt.timestamp())

def _eoh(dt: datetime) -> int:
    end = datetime(dt.year, dt.month, dt.day, dt.hour, tzinfo=timezone.utc) + timedelta(hours=1)
    return int(end.timestamp())

def _strs(*items) -> list[str]:
    return [str(x) for x in items]

# --------- Lua scripts ---------
# ZSET lock with per-member expiry
# KEYS[1] = locks_zset
# ARGV = [now_ts, lock_id, max_concurrent, expire_ts]
_LUA_TRY_LOCK = r"""
local z = KEYS[1]
local now = tonumber(ARGV[1])
local lock_id = ARGV[2]
local maxc = tonumber(ARGV[3])
local exp  = tonumber(ARGV[4])

-- purge expired holders
redis.call('ZREMRANGEBYSCORE', z, '-inf', now)

local current = redis.call('ZCARD', z)
if current >= maxc then
  return {0, current, maxc}
end

redis.call('ZADD', z, exp, lock_id)
redis.call('EXPIREAT', z, exp)  -- clean up if idle
return {1, current + 1, maxc}
"""

# Atomic commit: +1 request, +tokens into hour/day/month, write last_turn_*, release lock
# KEYS: d_reqs, m_reqs, t_reqs, h_toks, d_toks, m_toks, last_tok, last_at, locks_zset
# ARGV: inc_req, inc_tokens, exp_day, exp_mon, exp_hour, now_ts, lock_id
_LUA_COMMIT = r"""
local d_reqs = KEYS[1]
local m_reqs = KEYS[2]
local t_reqs = KEYS[3]
local h_toks = KEYS[4]
local d_toks = KEYS[5]
local m_toks = KEYS[6]
local last_t = KEYS[7]
local last_a = KEYS[8]
local locks  = KEYS[9]

local inc_req  = tonumber(ARGV[1])
local inc_tok  = tonumber(ARGV[2])
local exp_day  = tonumber(ARGV[3])
local exp_mon  = tonumber(ARGV[4])
local exp_hour = tonumber(ARGV[5])
local now_ts   = tonumber(ARGV[6])
local lock_id  = ARGV[7]

if inc_req > 0 then
  redis.call('INCRBY', d_reqs, inc_req); redis.call('EXPIREAT', d_reqs, exp_day)
  redis.call('INCRBY', m_reqs, inc_req); redis.call('EXPIREAT', m_reqs, exp_mon)
  redis.call('INCRBY', t_reqs, inc_req)
end

if inc_tok > 0 then
  redis.call('INCRBY', h_toks, inc_tok); redis.call('EXPIREAT', h_toks, exp_hour)
  redis.call('INCRBY', d_toks, inc_tok); redis.call('EXPIREAT', d_toks, exp_day)
  redis.call('INCRBY', m_toks, inc_tok); redis.call('EXPIREAT', m_toks, exp_mon)
end

redis.call('SET', last_t, tostring(inc_tok))
redis.call('SET', last_a, tostring(now_ts))

if lock_id and lock_id ~= '' then
  redis.call('ZREM', locks, lock_id)
end
return 1
"""


# --------- API ---------
@dataclass
class AdmitResult:
    allowed: bool
    reason: Optional[str]
    lock_id: Optional[str]
    # snapshot after admission (remaining or current readings)
    snapshot: Dict[str, int]     # {req_day, req_month, req_total, tok_hour, tok_day, tok_month, in_flight}


class RateLimiter:
    """
    Redis-backed, atomic admission & accounting:
      - Concurrency via ZSET (+ per-holder expiry)
      - Request quotas: daily / monthly / total
      - Token budgets: hour / day / month (post-paid; checked at admit based on *previous* commits)
    Keys:
      rl:{bundle}:{subject}:locks
      rl:{bundle}:{subject}:reqs:day:{YYYYMMDD}
      rl:{bundle}:{subject}:reqs:month:{YYYYMM}
      rl:{bundle}:{subject}:reqs:total
      rl:{bundle}:{subject}:toks:hour:{YYYYMMDDHH}
      rl:{bundle}:{subject}:toks:day:{YYYYMMDD}
      rl:{bundle}:{subject}:toks:month:{YYYYMM}
      rl:{bundle}:{subject}:last_turn_tokens
      rl:{bundle}:{subject}:last_turn_at
    """
    def __init__(self, redis: Redis, *, namespace: str = "rl"):
        self.r = redis
        self.ns = namespace

    async def admit(
        self,
        *,
        bundle_id: str,
        subject_id: str,
        policy: QuotaPolicy,
        lock_id: str,
        lock_ttl_sec: int = 120,
        now: Optional[datetime] = None,
    ) -> AdmitResult:
        """
        Check request & token quotas (based on *already committed* usage),
        then (if allowed) acquire a concurrency slot.
        """
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        ymd, ym, ymdh = _ymd(now), _ym(now), _ymdh(now)

        # ---- keys
        k_locks = _k(self.ns, bundle_id, subject_id, "locks")

        k_req_d = _k(self.ns, bundle_id, subject_id, "reqs:day", ymd)
        k_req_m = _k(self.ns, bundle_id, subject_id, "reqs:month", ym)
        k_req_t = _k(self.ns, bundle_id, subject_id, "reqs:total")

        k_tok_h = _k(self.ns, bundle_id, subject_id, "toks:hour", ymdh)
        k_tok_d = _k(self.ns, bundle_id, subject_id, "toks:day", ymd)
        k_tok_m = _k(self.ns, bundle_id, subject_id, "toks:month", ym)

        # ---- read current counters
        vals = await self.r.mget(k_req_d, k_req_m, k_req_t, k_tok_h, k_tok_d, k_tok_m)
        req_d = int(vals[0] or 0); req_m = int(vals[1] or 0); req_t = int(vals[2] or 0)
        tok_h = int(vals[3] or 0); tok_d = int(vals[4] or 0); tok_m = int(vals[5] or 0)

        # ---- policy checks (post-paid tokens)
        violations = []
        if policy.requests_per_day   is not None and req_d >= policy.requests_per_day:   violations.append("requests_per_day")
        if policy.requests_per_month is not None and req_m >= policy.requests_per_month: violations.append("requests_per_month")
        if policy.total_requests     is not None and req_t >= policy.total_requests:     violations.append("total_requests")
        if policy.tokens_per_hour    is not None and tok_h >= policy.tokens_per_hour:    violations.append("tokens_per_hour")
        if policy.tokens_per_day     is not None and tok_d >= policy.tokens_per_day:     violations.append("tokens_per_day")
        if policy.tokens_per_month   is not None and tok_m >= policy.tokens_per_month:   violations.append("tokens_per_month")

        if violations:
            return AdmitResult(
                allowed=False,
                reason="|".join(violations),
                lock_id=None,
                snapshot={
                    "req_day": req_d, "req_month": req_m, "req_total": req_t,
                    "tok_hour": tok_h, "tok_day": tok_d, "tok_month": tok_m,
                    "in_flight": 0,
                },
            )

        # ---- concurrency lock (if configured)
        in_flight = 0
        if policy.max_concurrent and policy.max_concurrent > 0:
            res = await self.r.eval(
                _LUA_TRY_LOCK,
                1,
                *_strs(k_locks),
                *_strs(
                    int(now.timestamp()),          # now (secs)
                    lock_id,                              # member id
                    int(policy.max_concurrent),           # max
                    int(now.timestamp()) + int(lock_ttl_sec),  # expire (secs)
                )
            )

            ok = bool(int(res[0]))
            in_flight = int(res[1]) if ok else int(res[1])  # res[1]=current after purge
            if not ok:
                return AdmitResult(
                    allowed=False,
                    reason="concurrency",
                    lock_id=None,
                    snapshot={
                        "req_day": req_d, "req_month": req_m, "req_total": req_t,
                        "tok_hour": tok_h, "tok_day": tok_d, "tok_month": tok_m,
                        "in_flight": in_flight,
                    },
                )

        return AdmitResult(
            allowed=True,
            reason=None,
            lock_id=lock_id,
            snapshot={
                "req_day": req_d, "req_month": req_m, "req_total": req_t,
                "tok_hour": tok_h, "tok_day": tok_d, "tok_month": tok_m,
                "in_flight": in_flight,
            },
        )

    async def commit(
        self,
        *,
        bundle_id: str,
        subject_id: str,
        tokens: int,
        lock_id: Optional[str],
        now: Optional[datetime] = None,
    ) -> None:
        """
        End-of-turn/accounting commit:
          - +1 request (day/month/total)
          - +tokens (hour/day/month)
          - last_turn_tokens / last_turn_at
          - release concurrency (if lock_id provided)
        """
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        ymd, ym, ymdh = _ymd(now), _ym(now), _ymdh(now)

        k_req_d = _k(self.ns, bundle_id, subject_id, "reqs:day", ymd)
        k_req_m = _k(self.ns, bundle_id, subject_id, "reqs:month", ym)
        k_req_t = _k(self.ns, bundle_id, subject_id, "reqs:total")

        k_tok_h = _k(self.ns, bundle_id, subject_id, "toks:hour", ymdh)
        k_tok_d = _k(self.ns, bundle_id, subject_id, "toks:day", ymd)
        k_tok_m = _k(self.ns, bundle_id, subject_id, "toks:month", ym)

        k_last_t = _k(self.ns, bundle_id, subject_id, "last_turn_tokens")
        k_last_a = _k(self.ns, bundle_id, subject_id, "last_turn_at")
        k_locks  = _k(self.ns, bundle_id, subject_id, "locks")

        await self.r.eval(
            _LUA_COMMIT,
            9,
            *_strs(
                k_req_d, k_req_m, k_req_t,
                k_tok_h, k_tok_d, k_tok_m,
                k_last_t, k_last_a, k_locks,
            ),
            *_strs(
                1,                   # +1 request
                int(tokens or 0),           # +tokens
                _eod(now),                  # day EXPIREAT
                _eom(now),                  # month EXPIREAT
                _eoh(now),                  # hour EXPIREAT
                int(now.timestamp()),       # last_at
                lock_id or "",              # release this member
            ),
        )

    async def release(self, *, bundle_id: str, subject_id: str, lock_id: str) -> int:
        """Force-release a concurrency slot (use in error/abort paths)."""
        k_locks = _k(self.ns, bundle_id, subject_id, "locks")
        return int(await self.r.zrem(k_locks, lock_id))

def subject_id_of(tenant: str, project: str, user_id: str, session_id: Optional[str] = None) -> str:
    return f"{tenant}:{project}:{user_id}" if not session_id else f"{tenant}:{project}:{user_id}:{session_id}"
