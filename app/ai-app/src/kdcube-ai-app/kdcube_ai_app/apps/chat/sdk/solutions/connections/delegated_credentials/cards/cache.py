# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Redis live projection of committed card authority.

One key per card holds exactly one of three states:

    card       the latest committed non-secret authority projection
    updating   a short fail-closed marker owned by one in-flight mutation
    revoked    a short negative cache after a committed revocation

The projection's TTL is the card's remaining authorization lifetime, so the
live entry expires with the authority it describes. Markers carry their own
short residency and never act as authority.

Every install is a compare-and-transition: a delayed read-through may not
displace a newer revision, an updating marker, or a revoked tombstone, and a
mutation finalizer may replace only the marker carrying its own mutation id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_io import (
    decode_cache_value,
    encode_cache_value,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CardAuthority,
    CardRecordError,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    validated_access_id,
    validated_subject_hash,
)

_LOGGER = logging.getLogger("connection_hub.delegated_cards.cache")

CARD_CACHE_KIND_CARD = "card"
CARD_CACHE_KIND_UPDATING = "updating"
CARD_CACHE_KIND_REVOKED = "revoked"

# Claim the card transition. Refused when another mutation owns the key, when a
# revoked tombstone is present, or when the live revision is not the one the
# caller read.
_INSTALL_MARKER_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
  local ok, decoded = pcall(cjson.decode, existing)
  if ok and type(decoded) == 'table' then
    local kind = decoded['kind']
    -- A revoked tombstone is displaced only by a writer whose expected
    -- revision IS the revoked one: a re-consent commits the next revision of
    -- the same id, while a stale writer still carries an older number.
    if kind ~= 'card' and kind ~= 'revoked' then
      return 0
    end
    if tonumber(decoded['card_revision']) ~= tonumber(ARGV[2]) then
      return 0
    end
  end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
return 1
"""

# Finalize a mutation. Replaces only this mutation's own marker, or an absent
# key left behind when that marker expired.
_FINALIZE_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
  local ok, decoded = pcall(cjson.decode, existing)
  if not ok or type(decoded) ~= 'table' then
    return 0
  end
  if decoded['kind'] ~= 'updating' or decoded['mutation_id'] ~= ARGV[2] then
    return 0
  end
end
if ARGV[1] == '' then
  redis.call('DEL', KEYS[1])
else
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
end
return 1
"""

# Read-through repair. Installs only over an absent key or a strictly older
# ordinary projection.
_RESTORE_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
  local ok, decoded = pcall(cjson.decode, existing)
  if ok and type(decoded) == 'table' then
    if decoded['kind'] ~= 'card' then
      return 0
    end
    if tonumber(decoded['card_revision']) >= tonumber(ARGV[2]) then
      return 0
    end
  end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
return 1
"""


class CardCacheUnusable(RuntimeError):
    """A cached card value exists but cannot be trusted.

    Distinct from absence: absence is a definitive answer about authority,
    while unusable data means the answer could not be determined. Callers with
    a durable source repair it; callers without one report unavailability
    rather than denying as if the card were gone.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CardCacheEntry:
    kind: str
    authority: CardAuthority | None = None
    card_revision: int = 0
    mutation_id: str = ""

    @property
    def is_card(self) -> bool:
        return self.kind == CARD_CACHE_KIND_CARD

    @property
    def is_updating(self) -> bool:
        return self.kind == CARD_CACHE_KIND_UPDATING

    @property
    def is_revoked(self) -> bool:
        return self.kind == CARD_CACHE_KIND_REVOKED


class DelegatedCardRuntimeCache:
    """Async Redis projection of committed delegated-card authority."""

    def __init__(self, redis: Any, *, tenant: str, project: str) -> None:
        self._redis = redis
        self._tenant = str(tenant or "").strip()
        self._project = str(project or "").strip()

    def card_key(self, access_id: str) -> str:
        return (
            f"{self._tenant}:{self._project}:kdcube:delegated-access:"
            f"card:{validated_access_id(access_id)}"
        )

    async def read(self, access_id: str) -> CardCacheEntry | None:
        """The cached state, or ``None`` when the key is absent.

        Raises ``CardCacheUnusable`` when a value is present but cannot be
        parsed, so a damaged projection is never mistaken for a revoked card.
        """
        raw = await self._redis.get(self.card_key(access_id))
        if raw is None:
            return None
        payload = decode_cache_value(raw)
        if payload is None:
            raise CardCacheUnusable("cached_card_not_decodable")
        kind = str(payload.get("kind") or "").strip()
        if kind == CARD_CACHE_KIND_UPDATING:
            return CardCacheEntry(
                kind=kind,
                card_revision=int(payload.get("card_revision") or 0),
                mutation_id=str(payload.get("mutation_id") or ""),
            )
        if kind == CARD_CACHE_KIND_REVOKED:
            return CardCacheEntry(
                kind=kind, card_revision=int(payload.get("card_revision") or 0)
            )
        if kind != CARD_CACHE_KIND_CARD:
            raise CardCacheUnusable("cached_card_kind_unknown")
        try:
            authority = CardAuthority.from_mapping(payload.get("authority"))
        except CardRecordError as exc:
            _LOGGER.warning(
                "[connection-hub.delegated-cards] unusable cached card %s: %s",
                access_id, exc.reason,
            )
            raise CardCacheUnusable("cached_card_invalid") from exc
        return CardCacheEntry(
            kind=kind, authority=authority, card_revision=authority.card_revision
        )

    async def claim_transition(
        self,
        access_id: str,
        *,
        mutation_id: str,
        expected_revision: int,
        ttl_seconds: int,
    ) -> bool:
        """Install the updating marker so requests fail closed during a mutation."""
        marker = {
            "kind": CARD_CACHE_KIND_UPDATING,
            "mutation_id": str(mutation_id),
            "card_revision": int(expected_revision),
        }
        return await self._eval_bool(
            _INSTALL_MARKER_LUA,
            access_id,
            encode_cache_value(marker),
            str(int(expected_revision)),
            str(max(1, int(ttl_seconds))),
        )

    async def commit_projection(
        self,
        authority: CardAuthority,
        *,
        mutation_id: str,
        ttl_seconds: int,
    ) -> bool:
        """Replace this mutation's marker with the committed live projection."""
        if ttl_seconds <= 0:
            return await self.finalize_removal(
                authority.access_id, mutation_id=mutation_id
            )
        payload = {
            "kind": CARD_CACHE_KIND_CARD,
            "card_revision": authority.card_revision,
            "authority": authority.to_dict(),
        }
        return await self._eval_bool(
            _FINALIZE_LUA,
            authority.access_id,
            encode_cache_value(payload),
            str(mutation_id),
            str(max(1, int(ttl_seconds))),
        )

    async def commit_tombstone(
        self,
        access_id: str,
        *,
        card_revision: int,
        mutation_id: str,
        ttl_seconds: int,
    ) -> bool:
        """Replace this mutation's marker with the short revoked tombstone."""
        payload = {
            "kind": CARD_CACHE_KIND_REVOKED,
            "card_revision": int(card_revision),
        }
        return await self._eval_bool(
            _FINALIZE_LUA,
            access_id,
            encode_cache_value(payload),
            str(mutation_id),
            str(max(1, int(ttl_seconds))),
        )

    async def finalize_removal(self, access_id: str, *, mutation_id: str) -> bool:
        """Drop this mutation's marker without leaving a live projection."""
        return await self._eval_bool(_FINALIZE_LUA, access_id, "", str(mutation_id), "1")

    async def restore_projection(
        self, authority: CardAuthority, *, ttl_seconds: int
    ) -> bool:
        """Repopulate after a miss. Never displaces a marker, tombstone, or
        newer revision, so a delayed restoration cannot revive old authority."""
        if ttl_seconds <= 0:
            return False
        payload = {
            "kind": CARD_CACHE_KIND_CARD,
            "card_revision": authority.card_revision,
            "authority": authority.to_dict(),
        }
        return await self._eval_bool(
            _RESTORE_LUA,
            authority.access_id,
            encode_cache_value(payload),
            str(int(authority.card_revision)),
            str(max(1, int(ttl_seconds))),
        )

    # -- per-grantor discovery index ------------------------------------------
    #
    # A sorted set scored by each card's own expires_at. There is no whole-key
    # TTL, so one card's lifetime cannot shorten another card's discoverability.
    # The index is never authority: every listed member is still resolved
    # through the card cache/store.

    def grantor_index_key(self, subject_hash: str) -> str:
        return (
            f"{self._tenant}:{self._project}:kdcube:delegated-access:"
            f"cards-by-grantor:{validated_subject_hash(subject_hash)}"
        )

    async def index_add(self, *, subject_hash: str, access_id: str, expires_at: int) -> None:
        await self._redis.zadd(
            self.grantor_index_key(subject_hash),
            {validated_access_id(access_id): float(int(expires_at))},
        )

    async def index_remove(self, *, subject_hash: str, access_id: str) -> None:
        await self._redis.zrem(
            self.grantor_index_key(subject_hash), validated_access_id(access_id)
        )

    async def index_members(self, *, subject_hash: str, now: int) -> list[str]:
        """Unexpired member ids, pruning those whose own expiry has passed."""
        key = self.grantor_index_key(subject_hash)
        await self._redis.zremrangebyscore(key, "-inf", int(now))
        raw = await self._redis.zrangebyscore(key, f"({int(now)}", "+inf")
        members: list[str] = []
        for item in raw or []:
            value = item.decode("utf-8") if isinstance(item, (bytes, bytearray)) else str(item)
            members.append(value)
        return members

    async def _eval_bool(self, script: str, access_id: str, *args: str) -> bool:
        result = await self._redis.eval(script, 1, self.card_key(access_id), *args)
        return bool(int(result or 0))


__all__ = [
    "CARD_CACHE_KIND_CARD",
    "CARD_CACHE_KIND_REVOKED",
    "CARD_CACHE_KIND_UPDATING",
    "CardCacheEntry",
    "CardCacheUnusable",
    "DelegatedCardRuntimeCache",
]
