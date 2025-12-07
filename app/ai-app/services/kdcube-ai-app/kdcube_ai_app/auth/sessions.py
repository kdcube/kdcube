# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# auth/sessions
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional, Dict, List

from redis import asyncio as aioredis

from kdcube_ai_app.auth.AuthManager import User
from kdcube_ai_app.infra.namespaces import REDIS, ns_key


@dataclass
class RequestContext:
    """Framework-agnostic request context"""
    client_ip: str
    user_agent: str
    authorization_header: Optional[str] = None
    id_token: Optional[str] = None

    def get_fingerprint(self) -> str:
        """Generate user fingerprint"""
        fingerprint_data = f"{self.client_ip}:{self.user_agent}"
        return hashlib.sha256(fingerprint_data.encode()).hexdigest()[:16]

class UserType(Enum):
    ANONYMOUS = "anonymous"
    REGISTERED = "registered"
    PRIVILEGED = "privileged"
    PAYED = "payed"


@dataclass
class UserSession:
    """Simple user session data"""
    session_id: str
    user_type: UserType
    fingerprint: str
    user_id: Optional[str] = None
    username: Optional[str] = None
    roles: List[str] = None
    permissions: List[str] = None
    created_at: float = 0
    last_seen: float = 0
    email: Optional[str] = None
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
        if self.created_at == 0:
            self.created_at = time.time()
        self.last_seen = time.time()

    def to_user(self) -> User:
        """Convert session to User object for auth validation"""
        return User(
            username=self.username or self.fingerprint,
            email=self.email,
            name=self.username,
            roles=self.roles,
            permissions=self.permissions
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

    async def get_or_create_session(self,
                                    context: RequestContext,
                                    user_type: UserType,
                                    user_data: Optional[Dict] = None) -> UserSession:
        """Get existing session or create new one"""
        await self.init_redis()

        fingerprint = context.get_fingerprint()

        if user_type in [UserType.REGISTERED, UserType.PRIVILEGED] and user_data:
            session_key = f"{self.SESSION_PREFIX}:registered:{user_data['user_id']}"
        else:
            session_key = f"{self.SESSION_PREFIX}:anonymous:{fingerprint}"

        # Try to get existing session
        session_data = await self.redis.get(session_key)
        if session_data:
            try:
                session_dict = json.loads(session_data)
                # session_dict["user_type"] = UserType(session_dict["user_type"])

                session = UserSession(**session_dict)

                # update this from IDP info!
                session.last_seen = time.time()
                session.user_type = user_type

                if user_data:
                    session.roles = user_data.get('roles', [])
                    session.permissions = user_data.get('permissions', [])
                session.request_context = context
                await self._save_session(session_key, session)
                return session
            except Exception as e:
                import traceback
                print(traceback.format_exc())
                raise e

        # Create new session
        session = UserSession(
            session_id=str(uuid.uuid4()),
            user_type=user_type,
            fingerprint=fingerprint,
            user_id=user_data.get("user_id") if user_data else None,
            username=user_data.get("username") if user_data else None,
            roles=user_data.get("roles", []) if user_data else [],
            permissions=user_data.get("permissions", []) if user_data else [],
            request_context=context
        )

        await self._save_session(session_key, session)
        return session

    async def _save_session(self, session_key: str, session: UserSession):
        """Save session to Redis"""
        session_data = json.dumps(asdict(session), default=str, ensure_ascii=False)
        await self.redis.setex(session_key, self.SESSION_TTL, session_data)

        index_key = f"{self.SESSION_INDEX_PREFIX}:{session.session_id}"
        await self.redis.setex(index_key, self.SESSION_TTL, session_key)

    async def get_session_by_id(self, session_id: str) -> Optional[UserSession]:
        """Retrieve a session by its session_id"""
        await self.init_redis()
        index_key = f"{self.SESSION_INDEX_PREFIX}:{session_id}"
        session_key = await self.redis.get(index_key)
        if not session_key:
            return None
        session_data = await self.redis.get(session_key)
        if not session_data:
            return None
        session_dict = json.loads(session_data)
        return UserSession(**session_dict)
