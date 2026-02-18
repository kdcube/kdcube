# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# auth/sessions
import hashlib
import json
import time
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional, Dict, List

from redis import asyncio as aioredis

from kdcube_ai_app.auth.AuthManager import User
from kdcube_ai_app.infra.namespaces import REDIS, ns_key

logger = logging.getLogger(__name__)

def _auth_debug_enabled() -> bool:
    return os.getenv("AUTH_DEBUG", "").lower() in {"1", "true", "yes", "on"}
ATOMIC_SESSION_MERGE_OR_CREATE_AND_GET = r"""
-- Atomic merge-or-create + get
-- Shallow merge (level one) with presence-based semantics for user_data.
--
-- Semantics:
--   session key:  <SESSION_PREFIX>:<kind>:<id>
--   index key:    <SESSION_INDEX_PREFIX>:<session_id> -> session_key
--
-- KEYS[1] = session_key
--
-- ARGV[1] = new_session_json
-- ARGV[2] = ttl_sec
-- ARGV[3] = index_prefix
-- ARGV[4] = refresh_existing_ttl ("1" or "0") -- kept for compatibility
-- ARGV[5] = user_data_json (optional)
-- ARGV[6] = context_json (optional)          -- RequestContext as dict JSON
-- ARGV[7] = user_type_value (optional)      -- e.g. "privileged"

local session_key = KEYS[1]

local new_json = ARGV[1]
local ttl = tonumber(ARGV[2]) or 0
local index_prefix = ARGV[3]
local refresh = ARGV[4] or "0"
local user_data_json = ARGV[5]
local context_json = ARGV[6]
local user_type_value = ARGV[7]

-- Make empty Lua tables encode as JSON arrays [] instead of objects {}
if cjson.encode_empty_table_as_object then
    cjson.encode_empty_table_as_object(false)
end

local function set_with_ttl(key, val)
    if ttl > 0 then
        redis.call("SETEX", key, ttl, val)
    else
        redis.call("SET", key, val)
    end
end

local function expire_if_needed(key)
    if ttl > 0 then
        redis.call("EXPIRE", key, ttl)
    end
end

local function decode_json(s)
    if not s or s == "" then return nil end
    local ok, obj = pcall(cjson.decode, s)
    if ok then return obj end
    return nil
end

local function as_array(val)
    if type(val) ~= "table" then
        return nil
    end

    -- Distinguish array-like vs map-like:
    -- If any string key exists, treat as NOT an array.
    for k, _ in pairs(val) do
        if type(k) ~= "number" then
            return nil
        end
    end

    -- Even empty tables should be encoded as [] for list fields
    setmetatable(val, cjson.array_mt)
    return val
end

local function normalize_list_fields(obj)
    if not obj then return end
    if obj["roles"] ~= nil then
        as_array(obj["roles"])
    end
    if obj["permissions"] ~= nil then
        as_array(obj["permissions"])
    end
end

local function now_epoch()
    local t = redis.call("TIME")
    return tonumber(t[1]) + tonumber(t[2]) / 1000000
end

local function ensure_index_for(json_val)
    local obj = decode_json(json_val)
    if obj then
        local sid = obj["session_id"] or obj["id"]
        if sid and index_prefix then
            local index_key = index_prefix .. ":" .. sid
            -- Always write/refresh index
            set_with_ttl(index_key, session_key)
            return index_key
        end
    end
    return nil
end

local function merge_user_data(existing_obj, user_obj)
    if not existing_obj or not user_obj then return end

    -- Presence-based semantics
    if user_obj["roles"] ~= nil then
        existing_obj["roles"] = user_obj["roles"]
    end
    if user_obj["permissions"] ~= nil then
        existing_obj["permissions"] = user_obj["permissions"]
    end
    if user_obj["user_id"] ~= nil then
        existing_obj["user_id"] = user_obj["user_id"]
    end
    if user_obj["username"] ~= nil then
        existing_obj["username"] = user_obj["username"]
    end
    if user_obj["email"] ~= nil then
        existing_obj["email"] = user_obj["email"]
    end
end

local function apply_context(existing_obj, ctx_obj)
    if not existing_obj or not ctx_obj then return end

    -- Replace request_context at root (level-one update)
    existing_obj["request_context"] = ctx_obj

    -- Update root timezone only if provided and not null/empty
    local tz = ctx_obj["user_timezone"]
    if tz ~= nil and tz ~= cjson.null and tz ~= "" then
        existing_obj["timezone"] = tz
    end
end

-- EXISTING SESSION PATH
if redis.call("EXISTS", session_key) == 1 then
    local existing = redis.call("GET", session_key)
    local existing_obj = decode_json(existing)

    if not existing_obj then
        -- Can't safely merge; best-effort index repair + TTL refresh
        local idx0 = ensure_index_for(existing)
        if refresh == "1" and ttl > 0 then
            redis.call("EXPIRE", session_key, ttl)
            if idx0 then redis.call("EXPIRE", idx0, ttl) end
        end
        return {0, existing}
    end

    local user_obj = decode_json(user_data_json)
    local ctx_obj = decode_json(context_json)

    -- Update "touch" fields
    existing_obj["last_seen"] = now_epoch()

    if user_type_value and user_type_value ~= "" then
        existing_obj["user_type"] = user_type_value
    end

    -- Shallow merge selected user fields
    merge_user_data(existing_obj, user_obj)

    -- Shallow update context + derived timezone
    apply_context(existing_obj, ctx_obj)

    -- IMPORTANT: prevent [] -> {} regression on encode
    normalize_list_fields(existing_obj)

    local merged_json = cjson.encode(existing_obj)
    
    -- Persist merged session with TTL (this also refreshes TTL like your old _save_session)
    set_with_ttl(session_key, merged_json)

    -- Ensure/refresh index from merged JSON
    local idx = ensure_index_for(merged_json)
    if refresh == "1" and ttl > 0 then
        redis.call("EXPIRE", session_key, ttl)
        if idx then redis.call("EXPIRE", idx, ttl) end
    end

    return {0, merged_json}
end

-- CREATE SESSION PATH
local new_obj = decode_json(new_json)
if new_obj then
    local now = now_epoch()

    if not new_obj["created_at"] or new_obj["created_at"] == 0 then
        new_obj["created_at"] = now
    end
    new_obj["last_seen"] = now

    if user_type_value and user_type_value ~= "" then
        new_obj["user_type"] = user_type_value
    end

    local user_obj = decode_json(user_data_json)
    local ctx_obj = decode_json(context_json)

    -- apply the same merge semantics on create
    merge_user_data(new_obj, user_obj)

    apply_context(new_obj, ctx_obj)

    -- ensure empty lists stay JSON arrays
    normalize_list_fields(new_obj)

    new_json = cjson.encode(new_obj)
end

set_with_ttl(session_key, new_json)
ensure_index_for(new_json)

return {1, new_json}
"""

ATOMIC_SESSION_GET_OR_CREATE = r"""
-- Atomic get-or-create for any session key.
-- Semantics:
--   session key:  <SESSION_PREFIX>:<kind>:<id>
--   index key:    <SESSION_INDEX_PREFIX>:<session_id> -> session_key

-- KEYS[1] = session_key

-- ARGV[1] = new_session_json
-- ARGV[2] = ttl_sec (number)
-- ARGV[3] = index_prefix (string)
-- ARGV[4] = refresh_existing_ttl ("1" or "0")  -- optional

local session_key = KEYS[1]

local new_json = ARGV[1]
local ttl = tonumber(ARGV[2]) or 0
local index_prefix = ARGV[3]
local refresh = ARGV[4] or "0"

local function set_with_ttl(key, val)
    if ttl > 0 then
        redis.call("SETEX", key, ttl, val)
    else
        redis.call("SET", key, val)
    end
end

local function expire_if_needed(key)
    if ttl > 0 then
        redis.call("EXPIRE", key, ttl)
    end
end

local function ensure_index_for(json_val)
    local ok, obj = pcall(cjson.decode, json_val)
    if ok and obj then
        local sid = obj["session_id"] or obj["id"]
        if sid and index_prefix then
            local index_key = index_prefix .. ":" .. sid

            if redis.call("EXISTS", index_key) == 0 then
                set_with_ttl(index_key, session_key)
            elseif refresh == "1" then
                expire_if_needed(index_key)
            end

            return index_key
        end
    end
    return nil
end

-- If session exists: return it (and best-effort repair index)
if redis.call("EXISTS", session_key) == 1 then
    local existing = redis.call("GET", session_key)

    local idx = ensure_index_for(existing)

    if refresh == "1" and ttl > 0 then
        redis.call("EXPIRE", session_key, ttl)
        if idx then redis.call("EXPIRE", idx, ttl) end
    end

    return {0, existing}
end

-- Create new session
set_with_ttl(session_key, new_json)

-- Create corresponding session_id -> session_key index
ensure_index_for(new_json)

return {1, new_json}
"""

@dataclass
class RequestContext:
    """Framework-agnostic request context"""
    client_ip: str
    user_agent: str
    authorization_header: Optional[str] = None
    id_token: Optional[str] = None

    user_timezone: Optional[str] = None
    user_utc_offset_min: Optional[int] = None

    def get_fingerprint(self) -> str:
        """Generate user fingerprint"""
        fingerprint_data = f"{self.client_ip}:{self.user_agent}"
        return hashlib.sha256(fingerprint_data.encode()).hexdigest()[:16]

class UserType(Enum):
    ANONYMOUS = "anonymous"
    REGISTERED = "registered"
    PRIVILEGED = "privileged"
    PAID = "paid"

@dataclass
class UserSession:
    """Simple user session data"""
    session_id: str
    user_type: UserType
    fingerprint: Optional[str] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    roles: List[str] = None
    permissions: List[str] = None
    created_at: float = 0
    last_seen: float = 0
    email: Optional[str] = None
    timezone: Optional[str] = None
    request_context: Optional[RequestContext] = None

    def __post_init__(self):
        if isinstance(self.user_type, str):
            # in case someone passed "REGISTERED" or "UserType.REGISTERED"
            token = self.user_type.split(".")[-1]
            try:
                self.user_type = UserType[token]      # by NAME
            except KeyError:
                self.user_type = UserType(token.lower())
        if isinstance(self.request_context, dict):
            try:
                self.request_context = RequestContext(**self.request_context)
            except Exception:
                self.request_context = None

        if self.roles is None:
            self.roles = []
        if self.permissions is None:
            self.permissions = []
        if self.roles is None or not isinstance(self.roles, list):
            self.roles = []
        if self.permissions is None or not isinstance(self.permissions, list):
            self.permissions = []
        if self.created_at == 0:
            self.created_at = time.time()
        self.last_seen = time.time()

    def to_user(self) -> User:
        """Convert session to User object for auth validation"""
        user_type_val = None
        if self.user_type is not None:
            user_type_val = self.user_type.value if isinstance(self.user_type, UserType) else str(self.user_type)
        return User(
            username=self.username or self.fingerprint,
            email=self.email,
            name=self.username,
            roles=self.roles,
            permissions=self.permissions,
            user_type=user_type_val,
        )

    def serialize_to_dict(self):
        session_dict=asdict(self)
        if isinstance(self.user_type, UserType):
            session_dict["user_type"] = self.user_type.value
        return session_dict

class SessionManager:
    """Simple session management"""

    def __init__(self,
                 redis_url: str,
                 tenant: str,
                 project: str,
                 session_ttl: int = 86400):
        self.redis_url = redis_url
        self.redis = None
        self.tenant = tenant
        self.project = project
        self.SESSION_PREFIX = self.ns(REDIS.SESSION)
        self.SESSION_TTL = session_ttl
        self.SESSION_INDEX_PREFIX = f"{self.SESSION_PREFIX}:index"

    def ns(self, base: str) -> str:
        return ns_key(base, tenant=self.tenant, project=self.project)

    async def init_redis(self):
        if not self.redis:
            self.redis = aioredis.from_url(self.redis_url)

    async def get_or_create_session(
            self,
            context: RequestContext,
            user_type: UserType,
            user_data: Optional[Dict] = None
    ) -> UserSession:
        """Get existing session or create new one (atomic for all types)."""
        await self.init_redis()

        fingerprint = context.get_fingerprint()

        # Decide key exactly as before
        if user_type in [UserType.REGISTERED, UserType.PRIVILEGED] and user_data:
            session_key = f"{self.SESSION_PREFIX}:registered:{user_data['user_id']}"
        else:
            session_key = f"{self.SESSION_PREFIX}:anonymous:{fingerprint}"

        # Build candidate (GUID session_id) exactly as your semantics
        session = UserSession(
            session_id=str(uuid.uuid4()),
            user_type=user_type,
            fingerprint=fingerprint,
            user_id=user_data.get("user_id") if user_data else None,
            username=user_data.get("username") if user_data else None,
            roles=user_data.get("roles", []) if user_data else [],
            permissions=user_data.get("permissions", []) if user_data else [],
            email=user_data.get("email") if user_data else None,
            request_context=context,
            timezone=context.user_timezone
        )
        if _auth_debug_enabled():
            logger.info(
                "Session merge: type=%s user_id=%s roles=%s perms=%s",
                user_type.value if isinstance(user_type, UserType) else user_type,
                user_data.get("user_id") if user_data else None,
                len(user_data.get("roles") or []) if user_data else 0,
                len(user_data.get("permissions") or []) if user_data else 0,
            )

        payload = json.dumps(session.serialize_to_dict(), ensure_ascii=False)
        user_data_json = json.dumps(user_data, ensure_ascii=False) if user_data else ""
        context_json = json.dumps(asdict(context), ensure_ascii=False)

        # Atomic get-or-create + index repair/create
        result = await self.redis.eval(
            ATOMIC_SESSION_MERGE_OR_CREATE_AND_GET,
            1,
            session_key,
            payload,
            str(self.SESSION_TTL),
            self.SESSION_INDEX_PREFIX,
            "1",
            user_data_json,
            context_json,
            user_type.value,
        )

        created_flag = result[0]
        session_json = result[1]

        if isinstance(created_flag, (bytes, bytearray)):
            created_flag = int(created_flag.decode("utf-8"))
        else:
            created_flag = int(created_flag)

        if isinstance(session_json, (bytes, bytearray)):
            session_json = session_json.decode("utf-8")

        stored = UserSession(**json.loads(session_json))
        # Optional: keep runtime object aligned with current request instance
        # (does not persist, but helpful for the caller)
        stored.request_context = context

        return stored

    async def _save_session(self, session_key: str, session: UserSession, *, write_index: bool = True):

        await self.init_redis()
        session_data = json.dumps(session.serialize_to_dict(), ensure_ascii=False)
        # Store session JSON with TTL
        await self.redis.setex(session_key, self.SESSION_TTL, session_data)

        if write_index:
            # Store index: session_id -> session_key
            index_key = f"{self.SESSION_INDEX_PREFIX}:{session.session_id}"
            await self.redis.setex(index_key, self.SESSION_TTL, session_key)

    async def get_session_by_id(self, session_id: str) -> Optional[UserSession]:
        """Retrieve a session by its session_id"""
        await self.init_redis()
        index_key = f"{self.SESSION_INDEX_PREFIX}:{session_id}"
        session_key_raw = await self.redis.get(index_key)
        session_key = self._as_str(session_key_raw)
        if not session_key:
            return None

        session_data_raw = await self.redis.get(session_key)
        session_dict = self._loads_json(session_data_raw)
        if not session_dict:
            return None

        try:
            return UserSession(**session_dict)
        except Exception:
            # If schema changed or stored data is partial/corrupt
            return None

    def _as_str(self, v: Optional[object]) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray)):
            try:
                return v.decode("utf-8")
            except Exception:
                # last-resort decode; should be rare
                return v.decode("utf-8", errors="replace")
        return str(v)

    def _loads_json(self, s: Optional[object]) -> Optional[Dict]:
        text = self._as_str(s)
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None
