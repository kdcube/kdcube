# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# sdk/infra/economics/limiter.py
from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis

from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy
from kdcube_ai_app.infra.namespaces import REDIS

# --------- helpers (keys / time) ---------
def _k(ns: str, bundle: str, subject: str, *parts: str) -> str:
    """
    Build Redis key for user rate limiting.

    Format: {namespace}:{bundle}:{subject}:{parts}
    Example: kdcube:economics:rl:kdcube.codegen.orchestrator:tenant-a:project-x:user123:locks
    """
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
# --------- Tier token reservation (atomic) ---------
# Reservation metadata format in HSET:
#   "<amt>|<k_tok_h_resv>|<k_tok_d_resv>|<k_tok_m_resv>"
#
# KEYS:
#  1  locks_zset
#  2  req_day
#  3  req_month
#  4  req_total
#  5  tok_hour
#  6  tok_day
#  7  tok_month
#  8  tok_hour_resv
#  9  tok_day_resv
# 10  tok_month_resv
# 11  resv_index_zset
# 12  resv_data_hash
#
# ARGV:
#  1  now_ts
#  2  lock_id
#  3  max_concurrent (0 disables)
#  4  lock_exp_ts
#  5  req_per_day_limit   (-1 = None)
#  6  req_per_month_limit (-1 = None)
#  7  total_req_limit     (-1 = None)
#  8  tok_per_hour_limit  (-1 = None)
#  9  tok_per_day_limit   (-1 = None)
# 10  tok_per_month_limit (-1 = None)
# 11  reserve_req_tokens  (desired reservation)
# 12  resv_id             (reservation id; empty disables)
# 13  resv_exp_ts         (reservation expiry)
# 14  exp_day
# 15  exp_mon
# 16  exp_hour
_LUA_ADMIT_LOCK_AND_RESERVE = r"""
local locks = KEYS[1]
local req_d_k = KEYS[2]
local req_m_k = KEYS[3]
local req_t_k = KEYS[4]
local tok_h_k = KEYS[5]
local tok_d_k = KEYS[6]
local tok_m_k = KEYS[7]
local tok_hr_k = KEYS[8]
local tok_dr_k = KEYS[9]
local tok_mr_k = KEYS[10]
local resv_idx = KEYS[11]
local resv_map = KEYS[12]

local now = tonumber(ARGV[1])
local lock_id = ARGV[2]
local maxc = tonumber(ARGV[3])
local lock_exp = tonumber(ARGV[4])

local lim_req_d = tonumber(ARGV[5])
local lim_req_m = tonumber(ARGV[6])
local lim_req_t = tonumber(ARGV[7])
local lim_tok_h = tonumber(ARGV[8])
local lim_tok_d = tonumber(ARGV[9])
local lim_tok_m = tonumber(ARGV[10])

local want_resv = tonumber(ARGV[11])
local resv_id = ARGV[12]
local resv_exp = tonumber(ARGV[13])

local exp_day = tonumber(ARGV[14])
local exp_mon = tonumber(ARGV[15])
local exp_hour = tonumber(ARGV[16])

local function parse_meta(meta)
  if not meta then return 0, nil, nil, nil end
  local a, k1, k2, k3 = string.match(meta, "^(%d+)|([^|]*)|([^|]*)|([^|]*)$")
  return tonumber(a) or 0, k1, k2, k3
end

local function decr_if_exists(k, amt)
  if (not k) or k == "" then return end
  local cur = redis.call("GET", k)
  if not cur then return end
  local nv = redis.call("INCRBY", k, -amt)
  if tonumber(nv) <= 0 then
    redis.call("DEL", k)
  end
end

-- Purge expired reservations (best-effort, BOUNDED)
local MAX_PURGE = 200
local expired = redis.call("ZRANGEBYSCORE", resv_idx, "-inf", now, "LIMIT", 0, MAX_PURGE)
for i = 1, #expired do
  local rid = expired[i]
  local meta = redis.call("HGET", resv_map, rid)
  if meta then
    local amt, k1, k2, k3 = parse_meta(meta)
    if amt > 0 then
      decr_if_exists(k1, amt)
      decr_if_exists(k2, amt)
      decr_if_exists(k3, amt)
    end
    redis.call("HDEL", resv_map, rid)
  end
  redis.call("ZREM", resv_idx, rid)
end

-- Purge expired concurrency holders
redis.call("ZREMRANGEBYSCORE", locks, "-inf", now)

-- Read committed counters
local req_d = tonumber(redis.call("GET", req_d_k) or "0")
local req_m = tonumber(redis.call("GET", req_m_k) or "0")
local req_t = tonumber(redis.call("GET", req_t_k) or "0")

local tok_h = tonumber(redis.call("GET", tok_h_k) or "0")
local tok_d = tonumber(redis.call("GET", tok_d_k) or "0")
local tok_m = tonumber(redis.call("GET", tok_m_k) or "0")

-- Read reserved counters
local tok_hr = tonumber(redis.call("GET", tok_hr_k) or "0")
local tok_dr = tonumber(redis.call("GET", tok_dr_k) or "0")
local tok_mr = tonumber(redis.call("GET", tok_mr_k) or "0")

-- Effective usage for checks = committed + reserved
local tok_h_eff = tok_h + tok_hr
local tok_d_eff = tok_d + tok_dr
local tok_m_eff = tok_m + tok_mr

local reason = ""

local function add_violation(v)
  if reason == "" then reason = v else reason = reason .. "|" .. v end
end

if lim_req_d >= 0 and req_d >= lim_req_d then add_violation("requests_per_day") end
if lim_req_m >= 0 and req_m >= lim_req_m then add_violation("requests_per_month") end
if lim_req_t >= 0 and req_t >= lim_req_t then add_violation("total_requests") end

if lim_tok_h >= 0 and tok_h_eff >= lim_tok_h then add_violation("tokens_per_hour") end
if lim_tok_d >= 0 and tok_d_eff >= lim_tok_d then add_violation("tokens_per_day") end
if lim_tok_m >= 0 and tok_m_eff >= lim_tok_m then add_violation("tokens_per_month") end

if reason ~= "" then
  return {0, reason, req_d, req_m, req_t, tok_h_eff, tok_d_eff, tok_m_eff, 0, 0}
end

-- Concurrency
local in_flight = 0
if maxc and maxc > 0 then
  local current = redis.call("ZCARD", locks)
  local existing = redis.call("ZSCORE", locks, lock_id)

  if existing then
    -- idempotent retry: refresh expiry only
    redis.call("ZADD", locks, lock_exp, lock_id)

    -- align with _LUA_TRY_LOCK: do NOT shorten; TTL == -1 => keep no-expiry
    local ttl = redis.call("TTL", locks)
    if ttl > 0 then
      local cur_exp = now + ttl
      if cur_exp < lock_exp then redis.call("EXPIREAT", locks, lock_exp) end
    elseif ttl == -1 then
      -- no expiry -> leave it
    else
      redis.call("EXPIREAT", locks, lock_exp)
    end

    in_flight = current
  else
    if current >= maxc then
      return {0, "concurrency", req_d, req_m, req_t, tok_h_eff, tok_d_eff, tok_m_eff, current, 0}
    end
    redis.call("ZADD", locks, lock_exp, lock_id)

    local ttl = redis.call("TTL", locks)
    if ttl > 0 then
      local cur_exp = now + ttl
      if cur_exp < lock_exp then redis.call("EXPIREAT", locks, lock_exp) end
    elseif ttl == -1 then
      -- no expiry -> leave it
    else
      redis.call("EXPIREAT", locks, lock_exp)
    end

    in_flight = current + 1
  end
end

-- Token reservation amount = min(want_resv, remaining across configured token windows)
local reserved = 0
if want_resv and want_resv > 0 and resv_id and resv_id ~= "" then
  -- Idempotency: if already reserved under resv_id, reuse it
  local existing = redis.call("HGET", resv_map, resv_id)
  if existing then
    local amt, k1, k2, k3 = parse_meta(existing)
    reserved = amt
        
    -- IMPORTANT FIX: refresh reservation expiry on idempotent reuse
    redis.call("ZADD", resv_idx, resv_exp, resv_id)
    
    -- keep reservation containers alive (you already do this in create-branch)
    redis.call("EXPIREAT", resv_idx, exp_mon)
    redis.call("EXPIREAT", resv_map, exp_mon)
    
    -- optional: refresh reserved-counter key expiries (safe even if key absent)
    if k1 and k1 ~= "" then redis.call("EXPIREAT", k1, exp_hour) end
    if k2 and k2 ~= "" then redis.call("EXPIREAT", k2, exp_day)  end
    if k3 and k3 ~= "" then redis.call("EXPIREAT", k3, exp_mon)  end
  else
    local r = want_resv

    if lim_tok_h >= 0 then
      local rem = lim_tok_h - tok_h_eff
      if rem < r then r = rem end
    end
    if lim_tok_d >= 0 then
      local rem = lim_tok_d - tok_d_eff
      if rem < r then r = rem end
    end
    if lim_tok_m >= 0 then
      local rem = lim_tok_m - tok_m_eff
      if rem < r then r = rem end
    end

    if r < 0 then r = 0 end
    reserved = r

    if reserved > 0 then
      -- Reserve into each configured window (only if that window is limited)
      if lim_tok_h >= 0 then redis.call("INCRBY", tok_hr_k, reserved); redis.call("EXPIREAT", tok_hr_k, exp_hour) end
      if lim_tok_d >= 0 then redis.call("INCRBY", tok_dr_k, reserved); redis.call("EXPIREAT", tok_dr_k, exp_day)  end
      if lim_tok_m >= 0 then redis.call("INCRBY", tok_mr_k, reserved); redis.call("EXPIREAT", tok_mr_k, exp_mon)  end

      local meta = tostring(reserved) .. "|" .. tok_hr_k .. "|" .. tok_dr_k .. "|" .. tok_mr_k
      redis.call("HSET", resv_map, resv_id, meta)
      redis.call("ZADD", resv_idx, resv_exp, resv_id)

      -- Keep reservation containers alive until month end (cheap + avoids stale keys)
      redis.call("EXPIREAT", resv_idx, exp_mon)
      redis.call("EXPIREAT", resv_map, exp_mon)

      -- Update effective usage values after reservation
      tok_h_eff = tok_h_eff + (lim_tok_h >= 0 and reserved or 0)
      tok_d_eff = tok_d_eff + (lim_tok_d >= 0 and reserved or 0)
      tok_m_eff = tok_m_eff + (lim_tok_m >= 0 and reserved or 0)
    end
  end
end

return {1, "", req_d, req_m, req_t, tok_h_eff, tok_d_eff, tok_m_eff, in_flight, reserved}
"""

# KEYS: resv_index_zset, resv_data_hash
# ARGV: now_ts, resv_id
_LUA_RELEASE_RESERVATION = r"""
local resv_idx = KEYS[1]
local resv_map = KEYS[2]
local now = tonumber(ARGV[1])
local resv_id = ARGV[2]

local function parse_meta(meta)
  if not meta then return 0, nil, nil, nil end
  local a, k1, k2, k3 = string.match(meta, "^(%d+)|([^|]*)|([^|]*)|([^|]*)$")
  return tonumber(a) or 0, k1, k2, k3
end

local function decr_if_exists(k, amt)
  if (not k) or k == "" then return end
  local cur = redis.call("GET", k)
  if not cur then return end
  local nv = redis.call("INCRBY", k, -amt)
  if tonumber(nv) <= 0 then redis.call("DEL", k) end
end

-- purge expired reservations (avoid leaks)
local expired = redis.call("ZRANGEBYSCORE", resv_idx, "-inf", now)
for i = 1, #expired do
  local rid = expired[i]
  local meta = redis.call("HGET", resv_map, rid)
  if meta then
    local amt, k1, k2, k3 = parse_meta(meta)
    if amt > 0 then
      decr_if_exists(k1, amt)
      decr_if_exists(k2, amt)
      decr_if_exists(k3, amt)
    end
    redis.call("HDEL", resv_map, rid)
  end
  redis.call("ZREM", resv_idx, rid)
end

if (not resv_id) or resv_id == "" then return 0 end
local meta = redis.call("HGET", resv_map, resv_id)
if not meta then
  redis.call("ZREM", resv_idx, resv_id)
  return 0
end

local amt, k1, k2, k3 = parse_meta(meta)
if amt > 0 then
  decr_if_exists(k1, amt)
  decr_if_exists(k2, amt)
  decr_if_exists(k3, amt)
end
redis.call("HDEL", resv_map, resv_id)
redis.call("ZREM", resv_idx, resv_id)
return 1
"""

# KEYS:
#  1..9  same as _LUA_COMMIT (req/tok/last/locks)
#  10    resv_index_zset
#  11    resv_data_hash
#  12    commit_dedupe_ke
# ARGV:
#  1 inc_req
#  2 inc_tokens
#  3 exp_day
#  4 exp_mon
#  5 exp_hour
#  6 now_ts
#  7 lock_id
#  8 resv_id
_LUA_COMMIT_WITH_RESERVATION = r"""
local d_reqs = KEYS[1]
local m_reqs = KEYS[2]
local t_reqs = KEYS[3]
local h_toks = KEYS[4]
local d_toks = KEYS[5]
local m_toks = KEYS[6]
local last_t = KEYS[7]
local last_a = KEYS[8]
local locks  = KEYS[9]
local resv_idx = KEYS[10]
local resv_map = KEYS[11]
local dedupe = KEYS[12]

local inc_req  = tonumber(ARGV[1])
local inc_tok  = tonumber(ARGV[2])
local exp_day  = tonumber(ARGV[3])
local exp_mon  = tonumber(ARGV[4])
local exp_hour = tonumber(ARGV[5])
local now_ts   = tonumber(ARGV[6])
local lock_id  = ARGV[7]
local resv_id  = ARGV[8]

local function redis_now()
  local t = redis.call('TIME')
  return tonumber(t[1])
end
local srv_now = redis_now()

local function parse_meta(meta)
  if not meta then return 0, nil, nil, nil end
  local a, k1, k2, k3 = string.match(meta, "^(%d+)|([^|]*)|([^|]*)|([^|]*)$")
  return tonumber(a) or 0, k1, k2, k3
end

local function decr_if_exists(k, amt)
  if (not k) or k == "" then return end
  local cur = redis.call("GET", k)
  if not cur then return end
  local nv = redis.call("INCRBY", k, -amt)
  if tonumber(nv) <= 0 then redis.call("DEL", k) end
end

-- purge expired reservations (BOUNDED)
local MAX_PURGE = 200
local expired = redis.call("ZRANGEBYSCORE", resv_idx, "-inf", now_ts, "LIMIT", 0, MAX_PURGE)
for i = 1, #expired do
  local rid = expired[i]
  local meta = redis.call("HGET", resv_map, rid)
  if meta then
    local amt, k1, k2, k3 = parse_meta(meta)
    if amt > 0 then
      decr_if_exists(k1, amt)
      decr_if_exists(k2, amt)
      decr_if_exists(k3, amt)
    end
    redis.call("HDEL", resv_map, rid)
  end
  redis.call("ZREM", resv_idx, rid)
end

-- release the reservation for THIS turn (if exists)
if resv_id and resv_id ~= "" then
  local meta = redis.call("HGET", resv_map, resv_id)
  if meta then
    local amt, k1, k2, k3 = parse_meta(meta)
    if amt > 0 then
      decr_if_exists(k1, amt)
      decr_if_exists(k2, amt)
      decr_if_exists(k3, amt)
    end
    redis.call("HDEL", resv_map, resv_id)
  end
  redis.call("ZREM", resv_idx, resv_id)
end

-- COMMIT IDEMPOTENCY:
-- Keep dedupe key at least 48h from *server time* to survive month rollover.
if dedupe and dedupe ~= '' then
  local first = redis.call('SETNX', dedupe, '1')
  if first == 0 then
    if lock_id and lock_id ~= "" then
      redis.call("ZREM", locks, lock_id)
    end
    return 0
  end

  local keep_sec = 172800
  local exp = exp_mon
  if exp < (srv_now + keep_sec) then
    exp = srv_now + keep_sec
  end
  redis.call('EXPIREAT', dedupe, exp)
end

-- commit request counters
if inc_req and inc_req > 0 then
  redis.call("INCRBY", d_reqs, inc_req); redis.call("EXPIREAT", d_reqs, exp_day)
  redis.call("INCRBY", m_reqs, inc_req); redis.call("EXPIREAT", m_reqs, exp_mon)
  redis.call("INCRBY", t_reqs, inc_req)
end

-- commit token counters
if inc_tok and inc_tok > 0 then
  redis.call("INCRBY", h_toks, inc_tok); redis.call("EXPIREAT", h_toks, exp_hour)
  redis.call("INCRBY", d_toks, inc_tok); redis.call("EXPIREAT", d_toks, exp_day)
  redis.call("INCRBY", m_toks, inc_tok); redis.call("EXPIREAT", m_toks, exp_mon)
end

redis.call("SET", last_t, tostring(inc_tok or 0))
redis.call("SET", last_a, tostring(now_ts))

-- release concurrency slot
if lock_id and lock_id ~= "" then
  redis.call("ZREM", locks, lock_id)
end

return 1
"""

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

-- IDENTITY / IDEMPOTENCY: if caller already holds lock_id, just refresh
local existing = redis.call('ZSCORE', z, lock_id)
if existing then
  redis.call('ZADD', z, exp, lock_id)

  -- do NOT shorten key TTL; only extend
  local ttl = redis.call('TTL', z)
  if ttl > 0 then
    local cur_exp = now + ttl
    if cur_exp < exp then redis.call('EXPIREAT', z, exp) end
  elseif ttl == -1 then
    -- no expiry -> leave it
  else
    -- ttl == -2 shouldn't happen if z exists, but ignore
    redis.call('EXPIREAT', z, exp)
  end

  return {1, current, maxc}
end

-- normal admission
if current >= maxc then
  return {0, current, maxc}
end

redis.call('ZADD', z, exp, lock_id)

-- only extend, never shrink
local ttl = redis.call('TTL', z)
if ttl > 0 then
  local cur_exp = now + ttl
  if cur_exp < exp then redis.call('EXPIREAT', z, exp) end
else
  redis.call('EXPIREAT', z, exp)
end

return {1, current + 1, maxc}
"""

# Atomic commit: +1 request, +tokens into hour/day/month, write last_turn_*, release lock
# KEYS: d_reqs, m_reqs, t_reqs, h_toks, d_toks, m_toks, last_tok, last_at, locks_zset, commit_dedupe_key
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
local dedupe = KEYS[10]

local inc_req  = tonumber(ARGV[1])
local inc_tok  = tonumber(ARGV[2])
local exp_day  = tonumber(ARGV[3])
local exp_mon  = tonumber(ARGV[4])
local exp_hour = tonumber(ARGV[5])
local now_ts   = tonumber(ARGV[6])
local lock_id  = ARGV[7]

local function redis_now()
  local t = redis.call('TIME')
  return tonumber(t[1])
end
local srv_now = redis_now()

-- COMMIT IDEMPOTENCY:
-- Keep dedupe key at least 48h from *server time* to survive month rollover.
if dedupe and dedupe ~= '' then
  local first = redis.call('SETNX', dedupe, '1')
  if first == 0 then
    if lock_id and lock_id ~= '' then
      redis.call('ZREM', locks, lock_id)
    end
    return 0
  end

  local keep_sec = 172800
  local exp = exp_mon
  if exp < (srv_now + keep_sec) then
    exp = srv_now + keep_sec
  end
  redis.call('EXPIREAT', dedupe, exp)
end

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

# --------- Tier Balance Helpers ---------
def _merge_policy_with_tier_balance(
        base_policy: QuotaPolicy,
        tier_balance: Optional['UserTierBalance']
) -> QuotaPolicy:
    """
    Apply tier balance OVERRIDE (not additive).

    If tier_balance exists and has tier override: Use override limits where set, else fall back to base tier.

    Args:
        base_policy: Base tier policy
        tier_balance: User's tier balance (contains tier override + lifetime budget)

    Returns:
        Merged QuotaPolicy with overrides applied
    """

    # Only apply when the override is ACTIVE (active flag + has override + not expired)
    if not tier_balance or not tier_balance.tier_override_is_active():
        return base_policy

    # Apply OVERRIDE semantics (tier_balance fields override base_policy)
    return QuotaPolicy(
        max_concurrent=(
            tier_balance.max_concurrent
            if tier_balance.max_concurrent is not None
            else base_policy.max_concurrent
        ),
        requests_per_day=(
            tier_balance.requests_per_day
            if tier_balance.requests_per_day is not None
            else base_policy.requests_per_day
        ),
        requests_per_month=(
            tier_balance.requests_per_month
            if tier_balance.requests_per_month is not None
            else base_policy.requests_per_month
        ),
        total_requests=(
            tier_balance.total_requests
            if tier_balance.total_requests is not None
            else base_policy.total_requests
        ),
        tokens_per_hour=(
            tier_balance.tokens_per_hour
            if tier_balance.tokens_per_hour is not None
            else base_policy.tokens_per_hour
        ),
        tokens_per_day=(
            tier_balance.tokens_per_day
            if tier_balance.tokens_per_day is not None
            else base_policy.tokens_per_day
        ),
        tokens_per_month=(
            tier_balance.tokens_per_month
            if tier_balance.tokens_per_month is not None
            else base_policy.tokens_per_month
        ),
    )

# --------- API ---------
@dataclass
class AdmitResult:
    allowed: bool
    reason: Optional[str]
    lock_id: Optional[str]
    # snapshot after admission (remaining or current readings)
    snapshot: Dict[str, int]     # {req_day, req_month, req_total, tok_hour, tok_day, tok_month, in_flight}
    # tier balance info (for transparency)
    used_tier_override: bool = False
    effective_policy: Optional[Dict[str, Any]] = None  # Merged policy used for admission

    reserved_tokens: int = 0
    reservation_id: Optional[str] = None


class UserEconomicsRateLimiter:
    """
    Redis-backed, atomic admission & accounting for user-level rate limiting.

    Supports tier balance - tier overrides and lifetime token budgets purchased or granted to users
    above their base policy limits.

    Tracks:
      - Concurrency via ZSET (+ per-holder expiry)
      - Request quotas: daily / monthly / total
      - Token budgets: hour / day / month (post-paid; checked at admit based on *previous* commits)

    Redis Keys (bundle-scoped with namespace prefix):
      kdcube:economics:rl:{bundle}:{subject}:locks
      kdcube:economics:rl:{bundle}:{subject}:reqs:day:{YYYYMMDD}
      kdcube:economics:rl:{bundle}:{subject}:reqs:month:{YYYYMM}
      kdcube:economics:rl:{bundle}:{subject}:reqs:total
      kdcube:economics:rl:{bundle}:{subject}:toks:hour:{YYYYMMDDHH}
      kdcube:economics:rl:{bundle}:{subject}:toks:day:{YYYYMMDD}
      kdcube:economics:rl:{bundle}:{subject}:toks:month:{YYYYMM}
      kdcube:economics:rl:{bundle}:{subject}:last_turn_tokens
      kdcube:economics:rl:{bundle}:{subject}:last_turn_at

    Where:
      - bundle = Bundle ID (e.g., "kdcube.codegen.orchestrator")
      - subject = {tenant}:{project}:{user_id} or {tenant}:{project}:{user_id}:{session_id}

    Example:
      kdcube:economics:rl:kdcube.codegen.orchestrator:tenant-a:project-x:user123:locks
      kdcube:economics:rl:kdcube.codegen.orchestrator:tenant-a:project-x:user123:reqs:day:20250515
      kdcube:economics:rl:kdcube.codegen.orchestrator:tenant-a:project-x:user123:toks:hour:2025051514
    """

    def __init__(
        self,
        redis: Redis,
        *,
        namespace: str = REDIS.ECONOMICS.RATE_LIMIT,
        user_balance_snapshot_mgr: Optional['UserTierBalanceSnapshotManager'] = None
    ):
        """
        Initialize RateLimiter.

        Args:
            redis: Redis client
            namespace: Namespace prefix (default: "kdcube:rl")
            user_balance_snapshot_mgr: Manager for querying user balances
        """
        self.r = redis
        self.ns = namespace
        self.user_balance_snapshot_mgr = user_balance_snapshot_mgr

    async def admit(
        self,
        *,
        bundle_id: str,
        subject_id: str,
        policy: QuotaPolicy,
        lock_id: str,
        lock_ttl_sec: int = 120,
        now: Optional[datetime] = None,
        apply_tier_override: bool = True,

        reserve_tokens: int = 0,
        reservation_id: Optional[str] = None,
        reservation_ttl_sec: int = 1800,
    ) -> AdmitResult:
        """
        Check request & token quotas (based on *already committed* usage),
        then (if allowed) acquire a concurrency slot.

        If user_balance_snapshot_mgr is configured, fetches and applies tier overrides
        purchased or granted to the user.

        Args:
            bundle_id: Bundle identifier
            subject_id: Subject (format: {tenant}:{project}:{user_id})
            policy: Base QuotaPolicy with limits
            lock_id: Unique lock identifier (usually turn_id)
            lock_ttl_sec: Lock TTL in seconds (default: 120)
            now: Current time (for testing)

        Returns:
            AdmitResult with allowed status, snapshot, and tier balance info
        """
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        ymd, ym, ymdh = _ymd(now), _ym(now), _ymdh(now)

        # Parse subject_id to get tenant, project, user_id
        subject_parts = subject_id.split(":")
        tenant = subject_parts[0] if len(subject_parts) > 0 else None
        project = subject_parts[1] if len(subject_parts) > 1 else None
        user_id = subject_parts[2] if len(subject_parts) > 2 else None

        # Fetch tier balance and merge with base policy
        tier_balance = None
        used_tier_override = False
        effective_policy = policy

        if apply_tier_override and self.user_balance_snapshot_mgr and tenant and project and user_id:
            try:
                tier_balance = await self.user_balance_snapshot_mgr.get_user_tier_balance(
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                )

                if tier_balance and tier_balance.tier_override_is_active():
                    effective_policy = _merge_policy_with_tier_balance(policy, tier_balance)
                    used_tier_override = True
            except Exception as e:
                # Log but don't fail admission on tier balance errors
                import logging
                logging.warning(f"Failed to fetch tier balance for {subject_id}: {e}")

        # ---- Build keys using namespace prefix
        k_locks = _k(self.ns, bundle_id, subject_id, "locks")

        k_req_d = _k(self.ns, bundle_id, subject_id, "reqs:day", ymd)
        k_req_m = _k(self.ns, bundle_id, subject_id, "reqs:month", ym)
        k_req_t = _k(self.ns, bundle_id, subject_id, "reqs:total")

        k_tok_h = _k(self.ns, bundle_id, subject_id, "toks:hour", ymdh)
        k_tok_d = _k(self.ns, bundle_id, subject_id, "toks:day", ymd)
        k_tok_m = _k(self.ns, bundle_id, subject_id, "toks:month", ym)

        # Reserved keys
        k_tok_hr = _k(self.ns, bundle_id, subject_id, "toks_resv:hour", ymdh)
        k_tok_dr = _k(self.ns, bundle_id, subject_id, "toks_resv:day", ymd)
        k_tok_mr = _k(self.ns, bundle_id, subject_id, "toks_resv:month", ym)

        k_resv_idx = _k(self.ns, bundle_id, subject_id, "toks_resv:index")
        k_resv_map = _k(self.ns, bundle_id, subject_id, "toks_resv:data")


        def _lim(x: Optional[int]) -> int:
            return int(x) if x is not None else -1

        if int(reserve_tokens or 0) > 0:
            resv_id = str(reservation_id or lock_id)
            now_ts = int(now.timestamp())

            out = await self.r.eval(
                _LUA_ADMIT_LOCK_AND_RESERVE,
                12,
                *_strs(
                    k_locks,
                    k_req_d, k_req_m, k_req_t,
                    k_tok_h, k_tok_d, k_tok_m,
                    k_tok_hr, k_tok_dr, k_tok_mr,
                    k_resv_idx, k_resv_map,
                ),
                *_strs(
                    now_ts,
                    lock_id,
                    int(effective_policy.max_concurrent or 0),
                    now_ts + int(lock_ttl_sec),
                    _lim(effective_policy.requests_per_day),
                    _lim(effective_policy.requests_per_month),
                    _lim(effective_policy.total_requests),
                    _lim(effective_policy.tokens_per_hour),
                    _lim(effective_policy.tokens_per_day),
                    _lim(effective_policy.tokens_per_month),
                    int(reserve_tokens),
                    resv_id,
                    now_ts + int(reservation_ttl_sec),
                    _eod(now),
                    _eom(now),
                    _eoh(now),
                    )
            )

            allowed = bool(int(out[0] or 0))
            reason = out[1].decode() if isinstance(out[1], (bytes, bytearray)) else (str(out[1]) if out[1] else None)
            req_d = int(out[2] or 0); req_m = int(out[3] or 0); req_t = int(out[4] or 0)
            tok_h_eff = int(out[5] or 0); tok_d_eff = int(out[6] or 0); tok_m_eff = int(out[7] or 0)
            in_flight = int(out[8] or 0)
            reserved = int(out[9] or 0)

            return AdmitResult(
                allowed=allowed,
                reason=(reason or None) if not allowed else None,
                lock_id=(lock_id if allowed else None),
                snapshot={
                    "req_day": req_d, "req_month": req_m, "req_total": req_t,
                    # IMPORTANT: snapshot includes committed+reserved (effective usage)
                    "tok_hour": tok_h_eff, "tok_day": tok_d_eff, "tok_month": tok_m_eff,
                    "in_flight": in_flight,
                },
                used_tier_override=used_tier_override,
                effective_policy=asdict(effective_policy) if used_tier_override else None,
                reserved_tokens=reserved,
                reservation_id=(resv_id if reserved > 0 else None),
            )

        # ---- read current counters
        vals = await self.r.mget(k_req_d, k_req_m, k_req_t, k_tok_h, k_tok_d, k_tok_m)
        req_d = int(vals[0] or 0); req_m = int(vals[1] or 0); req_t = int(vals[2] or 0)
        tok_h = int(vals[3] or 0); tok_d = int(vals[4] or 0); tok_m = int(vals[5] or 0)

        # ---- policy checks using EFFECTIVE policy (base + tier override)
        violations = []
        if effective_policy.requests_per_day   is not None and req_d >= effective_policy.requests_per_day:   violations.append("requests_per_day")
        if effective_policy.requests_per_month is not None and req_m >= effective_policy.requests_per_month: violations.append("requests_per_month")
        if effective_policy.total_requests     is not None and req_t >= effective_policy.total_requests:     violations.append("total_requests")
        if effective_policy.tokens_per_hour    is not None and tok_h >= effective_policy.tokens_per_hour:    violations.append("tokens_per_hour")
        if effective_policy.tokens_per_day     is not None and tok_d >= effective_policy.tokens_per_day:     violations.append("tokens_per_day")
        if effective_policy.tokens_per_month   is not None and tok_m >= effective_policy.tokens_per_month:   violations.append("tokens_per_month")

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
                used_tier_override=used_tier_override,
                effective_policy=asdict(effective_policy) if used_tier_override else None,
            )

        # ---- concurrency lock (if configured)
        in_flight = 0
        if effective_policy.max_concurrent and effective_policy.max_concurrent > 0:
            res = await self.r.eval(
                _LUA_TRY_LOCK,
                1,
                *_strs(k_locks),
                *_strs(
                    int(now.timestamp()),                      # now (secs)
                    lock_id,                                   # member id
                    int(effective_policy.max_concurrent),      # max (using effective policy!)
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
                    used_tier_override=used_tier_override,
                    effective_policy=asdict(effective_policy) if used_tier_override else None,
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
            used_tier_override=used_tier_override,
            effective_policy=asdict(effective_policy) if used_tier_override else None,
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

        Args:
            bundle_id: Bundle identifier
            subject_id: Subject (format: {tenant}:{project}:{user_id})
            tokens: Number of tokens to commit
            lock_id: Lock identifier to release
            now: Current time (for testing)
        """
        if not lock_id:
            raise ValueError("UserEconomicsRateLimiter.commit(): lock_id is required (dedupe safety).")

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

        # Dedupe key MUST include a non-empty identifier.
        k_commit = _k(self.ns, bundle_id, subject_id, "commit", str(lock_id))

        await self.r.eval(
            _LUA_COMMIT,
            10,
            *_strs(
                k_req_d, k_req_m, k_req_t,
                k_tok_h, k_tok_d, k_tok_m,
                k_last_t, k_last_a, k_locks,
                k_commit,
            ),
            *_strs(
                1,                      # +1 request
                int(tokens or 0),       # +tokens
                _eod(now),              # day EXPIREAT
                _eom(now),              # month EXPIREAT
                _eoh(now),              # hour EXPIREAT
                int(now.timestamp()),   # last_at
                str(lock_id),           # release this member
            ),
        )

    async def release(self, *, bundle_id: str, subject_id: str, lock_id: str) -> int:
        """
        Force-release a concurrency slot (use in error/abort paths).

        Args:
            bundle_id: Bundle identifier
            subject_id: Subject (format: {tenant}:{project}:{user_id})
            lock_id: Lock identifier to release

        Returns:
            Number of locks removed (0 or 1)
        """
        k_locks = _k(self.ns, bundle_id, subject_id, "locks")
        return int(await self.r.zrem(k_locks, lock_id))

    async def breakdown(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            bundle_ids: Optional[List[str]] = None,
            now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Get usage breakdown for a user across bundles.

        Args:
            tenant: Tenant ID
            project: Project ID
            user_id: User ID
            bundle_ids: List of bundle IDs, or ["*"] for all bundles
            now: Current time (for testing)

        Returns:
            {
                "bundles": {
                    "bundle_id": {
                        "requests_today": int,
                        "requests_this_month": int,
                        "requests_total": int,
                        "tokens_today": int,
                        "tokens_this_month": int,
                        "concurrent": int,
                    }
                },
                "totals": {
                    "requests_today": int,
                    "requests_this_month": int,
                    "requests_total": int,
                    "tokens_today": int,
                    "tokens_this_month": int,
                }
            }
        """
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        ymd = _ymd(now)
        ym = _ym(now)

        subject_id = subject_id_of(tenant, project, user_id)

        # Find all bundles for this user if not specified
        if not bundle_ids or (len(bundle_ids) == 1 and bundle_ids[0] == "*"):
            # Scan Redis for all bundle keys
            pattern = f"{self.ns}:*:{subject_id}:reqs:total"
            cursor = 0
            found_bundles = set()
            ns_prefix = f"{self.ns}:"

            while True:
                cursor, keys = await self.r.scan(cursor, match=pattern, count=100)
                for key in keys:
                    # Extract bundle_id from key pattern: kdcube:economics:rl:{bundle}:{subject}:reqs:total
                    key_str = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
                    if not key_str.startswith(ns_prefix):
                        continue
                    # key: "{ns}:{bundle}:{subject}:reqs:total"
                    rest = key_str[len(ns_prefix):]          # "{bundle}:{subject}:reqs:total"
                    bundle_id = rest.split(":", 1)[0]        # "{bundle}"
                    if bundle_id:
                        found_bundles.add(bundle_id)

                if cursor == 0:
                    break

            bundle_ids = list(found_bundles)

        if not bundle_ids:
            return {"bundles": {}, "totals": {
                "requests_today": 0,
                "requests_this_month": 0,
                "requests_total": 0,
                "tokens_today": 0,
                "tokens_this_month": 0,
            }}

        # Collect usage for each bundle
        bundles = {}
        totals = {
            "requests_today": 0,
            "requests_this_month": 0,
            "requests_total": 0,
            "tokens_today": 0,
            "tokens_this_month": 0,
        }

        for bundle_id in bundle_ids:
            k_req_d = _k(self.ns, bundle_id, subject_id, "reqs:day", ymd)
            k_req_m = _k(self.ns, bundle_id, subject_id, "reqs:month", ym)
            k_req_t = _k(self.ns, bundle_id, subject_id, "reqs:total")
            k_tok_d = _k(self.ns, bundle_id, subject_id, "toks:day", ymd)
            k_tok_m = _k(self.ns, bundle_id, subject_id, "toks:month", ym)
            k_locks = _k(self.ns, bundle_id, subject_id, "locks")

            vals = await self.r.mget(k_req_d, k_req_m, k_req_t, k_tok_d, k_tok_m)
            req_d = int(vals[0] or 0)
            req_m = int(vals[1] or 0)
            req_t = int(vals[2] or 0)
            tok_d = int(vals[3] or 0)
            tok_m = int(vals[4] or 0)
            concurrent = await self.r.zcard(k_locks)

            bundles[bundle_id] = {
                "requests_today": req_d,
                "requests_this_month": req_m,
                "requests_total": req_t,
                "tokens_today": tok_d,
                "tokens_this_month": tok_m,
                "concurrent": concurrent,
            }

            # Aggregate totals
            totals["requests_today"] += req_d
            totals["requests_this_month"] += req_m
            totals["requests_total"] += req_t
            totals["tokens_today"] += tok_d
            totals["tokens_this_month"] += tok_m

        return {"bundles": bundles, "totals": totals}

    async def release_token_reservation(
            self,
            *,
            bundle_id: str,
            subject_id: str,
            reservation_id: str,
            now: Optional[datetime] = None,
    ) -> int:
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        k_resv_idx = _k(self.ns, bundle_id, subject_id, "toks_resv:index")
        k_resv_map = _k(self.ns, bundle_id, subject_id, "toks_resv:data")
        out = await self.r.eval(
            _LUA_RELEASE_RESERVATION,
            2,
            *_strs(k_resv_idx, k_resv_map),
            *_strs(int(now.timestamp()), str(reservation_id)),
        )
        return int(out or 0)

    async def commit_with_reservation(
            self,
            *,
            bundle_id: str,
            subject_id: str,
            tokens: int,
            lock_id: Optional[str],
            reservation_id: Optional[str],
            now: Optional[datetime] = None,
            inc_request: int = 1,
    ) -> None:
        """
        Atomic commit + reservation release.

        IMPORTANT:
          - At least one of (lock_id, reservation_id) MUST be present,
            otherwise dedupe key becomes unsafe.
        """
        if not (lock_id or reservation_id):
            raise ValueError("commit_with_reservation(): lock_id or reservation_id is required (dedupe safety).")

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

        k_resv_idx = _k(self.ns, bundle_id, subject_id, "toks_resv:index")
        k_resv_map = _k(self.ns, bundle_id, subject_id, "toks_resv:data")

        # Dedupe must be stable and non-empty.
        commit_id = str(lock_id or reservation_id)
        k_commit  = _k(self.ns, bundle_id, subject_id, "commit", commit_id)

        await self.r.eval(
            _LUA_COMMIT_WITH_RESERVATION,
            12,
            *_strs(
                k_req_d, k_req_m, k_req_t,
                k_tok_h, k_tok_d, k_tok_m,
                k_last_t, k_last_a, k_locks,
                k_resv_idx, k_resv_map,
                k_commit
            ),
            *_strs(
                int(inc_request or 0),
                int(tokens or 0),
                _eod(now),
                _eom(now),
                _eoh(now),
                int(now.timestamp()),
                lock_id or "",
                str(reservation_id or ""),
                ),
        )

def subject_id_of(tenant: str, project: str, user_id: str, session_id: Optional[str] = None) -> str:
    """
    Build subject ID from tenant, project, and user.

    Format: {tenant}:{project}:{user_id} or {tenant}:{project}:{user_id}:{session_id}

    Args:
        tenant: Tenant ID
        project: Project ID
        user_id: User ID
        session_id: Optional session ID for session-level limiting

    Returns:
        Subject ID string

    Examples:
        >>> subject_id_of("tenant-a", "project-x", "user123")
        "tenant-a:project-x:user123"

        >>> subject_id_of("tenant-a", "project-x", "user123", "session456")
        "tenant-a:project-x:user123:session456"
    """
    return f"{tenant}:{project}:{user_id}" if not session_id else f"{tenant}:{project}:{user_id}:{session_id}"


@dataclass(frozen=True)
class QuotaInsight:
    """
    Infra-level view of quotas & current usage.

    Pure numbers / machine-friendly; no UI strings.
    """
    limits: Dict[str, Optional[int]]
    remaining: Dict[str, Optional[int]]
    violations: List[str]
    messages_remaining: Optional[int]
    retry_after_sec: Optional[int]
    retry_scope: Optional[str]   # "hour" | "day" | "month" | None
    used_tier_override: bool = False  # Whether tier override was applied
    total_token_remaining: Optional[int] = None
    usage_percentage: Optional[float] = 0.0
    approaching_limit_type: Optional[str] = None


def _remaining_from_policy(policy: QuotaPolicy, snapshot: Dict[str, int]) -> Dict[str, Optional[int]]:
    """
    Compute remaining budget for each quota dimension from policy + snapshot.

    snapshot keys:
      req_day, req_month, req_total, tok_hour, tok_day, tok_month, in_flight
    """
    def rem(limit: Optional[int], used: int) -> Optional[int]:
        if limit is None:
            return None
        return max(limit - int(used or 0), 0)

    return {
        "requests_per_day": rem(policy.requests_per_day, snapshot.get("req_day", 0)),
        "requests_per_month": rem(policy.requests_per_month, snapshot.get("req_month", 0)),
        "total_requests": rem(policy.total_requests, snapshot.get("req_total", 0)),
        "tokens_per_hour": rem(policy.tokens_per_hour, snapshot.get("tok_hour", 0)),
        "tokens_per_day": rem(policy.tokens_per_day, snapshot.get("tok_day", 0)),
        "tokens_per_month": rem(policy.tokens_per_month, snapshot.get("tok_month", 0)),
    }


def _messages_remaining_from_remaining(remaining: Dict[str, Optional[int]]) -> Optional[int]:
    """
    Single "messages remaining" number.

    We take the minimum across all *request* quotas that are configured:
      - daily
      - monthly
      - total_requests (if used)

    That's the tightest bound on "how many more requests can I safely send?".
    """
    candidates = [
        remaining.get("requests_per_day"),
        remaining.get("requests_per_month"),
        remaining.get("total_requests"),
    ]
    candidates = [v for v in candidates if v is not None]
    if not candidates:
        return None
    return min(candidates)


def _retry_after_from_violations(violations: List[str], *, now: Optional[datetime] = None) -> Tuple[Optional[int], Optional[str]]:
    """
    Given violated quota names (matching the strings from RateLimiter.admit),
    compute TTL until the user is allowed again.

    If multiple windows are violated (e.g. tokens_per_hour + tokens_per_day),
    you must wait for *all* of them, so we take the MAX TTL.

    Returns: (retry_after_sec, scope) where scope ∈ {"hour","day","month"} or None.
    """
    if not violations:
        return None, None

    now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
    now_ts = int(now.timestamp())
    candidates: List[Tuple[str, int]] = []

    for v in violations:
        if v in ("requests_per_day", "tokens_per_day"):
            ttl = max(_eod(now) - now_ts, 0)
            candidates.append(("day", ttl))
        elif v in ("requests_per_month", "tokens_per_month"):
            ttl = max(_eom(now) - now_ts, 0)
            candidates.append(("month", ttl))
        elif v == "tokens_per_hour":
            ttl = max(_eoh(now) - now_ts, 0)
            candidates.append(("hour", ttl))
        # total_requests has no reset; concurrency is not a quota window → ignore

    if not candidates:
        return None, None

    scope, ttl = max(candidates, key=lambda it: it[1])
    return ttl, scope

def _calculate_usage_percentage(limits, remaining) -> Optional[float]:
    """Calculate the tightest usage percentage from insight."""
    if not limits:
        return None

    percentages = []
    for key in ["requests_per_day", "requests_per_month", "tokens_per_day", "tokens_per_month"]:
        limit = limits.get(key)
        rem_val = remaining.get(key)

        if limit is not None and limit > 0 and rem_val is not None:
            used = limit - rem_val
            pct = (used / limit) * 100
            percentages.append(pct)

    return max(percentages) if percentages else None

def _get_approaching_limit_type(limits, remaining) -> Optional[str]:
    """Identify which limit is being approached."""
    if not limits:
        return None

    closest_key = None
    closest_pct = 0.0

    for key in ["requests_per_day", "requests_per_month", "tokens_per_day", "tokens_per_month"]:
        limit = limits.get(key)
        rem_val = remaining.get(key)

        if limit is not None and limit > 0 and rem_val is not None:
            used = limit - rem_val
            pct = (used / limit) * 100
            if pct > closest_pct:
                closest_pct = pct
                closest_key = key

    return closest_key

def _first_token_scope(policy: QuotaPolicy) -> Optional[str]:
    # "first set rule"
    if getattr(policy, "tokens_per_hour", None) is not None:
        return "hour"
    if getattr(policy, "tokens_per_day", None) is not None:
        return "day"
    if getattr(policy, "tokens_per_month", None) is not None:
        return "month"
    return None


def _tier_token_remaining_first_rule(policy: QuotaPolicy, remaining: Dict[str, Optional[int]]) -> Optional[int]:
    scope = _first_token_scope(policy)
    if not scope:
        return None
    return remaining.get(f"tokens_per_{scope}")


def compute_quota_insight(
        *,
        policy: QuotaPolicy,
        snapshot: Dict[str, int],
        reason: Optional[str],
        user_budget_tokens: Optional[int] = None,  # User's purchased token balance
        used_tier_override: bool = False,
        now: Optional[datetime] = None,
        est_tokens_per_turn = 133_333
) -> QuotaInsight:
    """
    Compute quota insight considering BOTH tier limits AND user token budget.

    Early warning should trigger when user is close to running out of EITHER:
    - Request quotas (tier)
    - Token budget (tier + purchased)

    Args:
        policy: Effective QuotaPolicy (already merged with tier override if applicable)
        snapshot: Current usage snapshot
        reason: Violation reason if denied
        user_budget_tokens: User's purchased lifetime token balance
        used_tier_override: Whether tier override was applied
        now: Current time (for testing)

    Returns:
        QuotaInsight with limits, remaining, violations, and recommendations
    """
    limits = asdict(policy)
    remaining = _remaining_from_policy(policy, snapshot)
    violations: List[str] = (reason or "").split("|") if reason else []

    retry_after_sec, retry_scope = _retry_after_from_violations(violations, now=now)

    # Calculate messages_remaining from REQUEST quotas
    request_remaining = _messages_remaining_from_remaining(remaining)

    # Tier tokens remaining: "first set rule" (same semantics as the limiter/reservation plan)
    tier_token_remaining = _tier_token_remaining_first_rule(policy, remaining)

    ub = int(user_budget_tokens or 0)
    total_token_remaining = None
    if tier_token_remaining is not None:
        total_token_remaining = int(tier_token_remaining) + ub


    # Estimate messages from token budget (assuming ~100K tokens per request)
    token_based_messages = None
    if total_token_remaining is not None:
        token_based_messages = max(int(total_token_remaining) // int(est_tokens_per_turn), 0)

    messages_remaining = request_remaining
    if token_based_messages is not None:
        messages_remaining = token_based_messages if messages_remaining is None else min(messages_remaining, token_based_messages)

    usage_percentage = _calculate_usage_percentage(limits, remaining)
    approaching_limit_type = _get_approaching_limit_type(limits, remaining)
    qi = QuotaInsight(
        limits=limits,
        remaining=remaining,
        violations=violations,
        messages_remaining=messages_remaining,
        retry_after_sec=retry_after_sec,
        retry_scope=retry_scope,
        used_tier_override=used_tier_override,
        total_token_remaining=total_token_remaining,
        usage_percentage=usage_percentage,
        approaching_limit_type=approaching_limit_type
    )

    return qi

