# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# sdk/rate_limit/rl_reset.py
#
# Reset (delete) Redis rate-limit state for a given bundle+subject.
# Works with keys created by sdk/rate_limit/limiter.py

import argparse
import asyncio
import sys
from typing import List

from redis.asyncio import Redis


PATTERNS_CURRENT = [
    "tokens:day:*",
    "tokens:hour:*",
    "rpm:*",
    "rps:*",
    "locks",
]
# Older/experimental patterns (harmless if none exist)
PATTERNS_LEGACY = [
    "reqs:day:*",
    "reqs:month:*",
    "reqs:total",
    "toks:hour:*",
    "toks:day:*",
    "toks:month:*",
    "last:tokens",
    "last:at",
]

async def _gather_keys(r: Redis, base_prefix: str, patterns: List[str]) -> List[str]:
    """SCAN all keys under base_prefix + pattern list."""
    out: List[str] = []
    for pat in patterns:
        match = f"{base_prefix}{pat}"
        # SCAN is incremental; scan_iter wraps it
        async for k in r.scan_iter(match=match, count=500):
            out.append(k.decode() if isinstance(k, (bytes, bytearray)) else str(k))
    return sorted(set(out))

async def main():
    p = argparse.ArgumentParser(
        description="Reset rate-limit records for a given bundle+subject in Redis."
    )
    # redis connection
    p.add_argument("--redis-url", help="redis URL (e.g. redis://localhost:6379/0). Overrides host/port/db.")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=6379)
    p.add_argument("--db", type=int, default=0)
    p.add_argument("--username")
    p.add_argument("--password")

    # targeting
    p.add_argument("--namespace", default="rl", help="Namespace prefix (default: rl)")
    p.add_argument("--bundle", required=True, help="Bundle ID (e.g. kdcube.codegen.orchestrator)")
    p.add_argument("--subject", required=True,
                   help="Subject id (e.g. user_id or user_id:session_id). Supports globbing (*, ?).")

    # behavior
    p.add_argument("--include-legacy", action="store_true", help="Also remove legacy counters (reqs:*, toks:*, last:*).")
    p.add_argument("--dry-run", action="store_true", help="List keys but do not delete.")
    p.add_argument("--verbose", "-v", action="store_true", help="Print keys as they are found.")
    args = p.parse_args()

    # connect
    if args.redis_url:
        r = Redis.from_url(args.redis_url)
    else:
        r = Redis(
            host=args.host,
            port=args.port,
            db=args.db,
            username=args.username,
            password=args.password,
            decode_responses=False,   # we decode per-key above
        )

    # base prefix like: rl:{bundle}:{subject}:
    # subject may be a glob; we keep the trailing colon so per-patterns append cleanly
    base_prefix = f"{args.namespace}:{args.bundle}:{args.subject}:"

    try:
        patterns = list(PATTERNS_CURRENT)
        if args.include_legacy:
            patterns += PATTERNS_LEGACY

        keys = await _gather_keys(r, base_prefix, patterns)

        if args.verbose or args.dry_run:
            print(f"# prefix: {base_prefix}")
            for k in keys:
                print(k)

        if not keys:
            print("no matching keys found.")
            return

        if args.dry_run:
            print(f"\nDRY-RUN: {len(keys)} keys would be removed.")
            return

        # Prefer UNLINK (non-blocking); fall back to DEL if unavailable
        try:
            removed = int(await r.unlink(*keys))
        except Exception:
            removed = int(await r.delete(*keys))

        print(f"removed {removed} keys.")
    finally:
        await r.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)

"""
# basic (URL)
python tools/rl_reset.py --redis-url redis://localhost:6379/0 \
  --bundle kdcube.codegen.orchestrator \
  --subject user-123

# same but show what would be deleted (no changes)
python tools/rl_reset.py --redis-url redis://localhost:6379/0 \
  --bundle kdcube.codegen.orchestrator \
  --subject user-123 --dry-run -v

# wildcard reset (all sessions for a user)
python tools/rl_reset.py --redis-url redis://localhost:6379/0 \
  --bundle kdcube.codegen.orchestrator \
  --subject "user-123:*"

# also scrub legacy counters
python tools/rl_reset.py --redis-url redis://localhost:6379/0 \
  --bundle kdcube.codegen.orchestrator \
  --subject user-123 --include-legacy
"""