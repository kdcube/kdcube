# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import os
import redis
from dotenv import load_dotenv, find_dotenv
from kdcube_ai_app.apps.chat.sdk.config import get_settings

load_dotenv(find_dotenv())

# ── CONFIGURE ──────────────────────────────────────────────────────────────────
REDIS_URL = get_settings().REDIS_URL
r = redis.Redis.from_url(REDIS_URL)

# ── INSPECTION FUNCTIONS ───────────────────────────────────────────────────────
def inspect_keys():
    print("\n=== ALL KEYS & TYPES ===")
    for raw_key in r.scan_iter(match="*"):
        key = raw_key.decode()
        typ = r.type(key).decode()
        line = f"{key!r}: type={typ}"

        if typ == "list":
            length = r.llen(key)
            sample = r.lrange(key, 0, min(length, 5) - 1)
            line += f", length={length}, sample={sample}"
        elif typ == "stream":
            info = r.xinfo_stream(key)
            line += f", entries={info['length']}"
        elif typ == "string":
            line += f", value={r.get(key)}"
        print(line)

def inspect_stream(key):
    print(f"\n=== STREAM {key!r} INFO ===")
    try:
        info = r.xinfo_stream(key)
        print(json.dumps(info, indent=2))
        entries = r.xrange(key, min='-', max='+', count=10)
        print(f"First 10 entries:\n{entries}")
    except redis.exceptions.ResponseError:
        print(f"No stream named {key!r}")

def inspect_pubsub():
    print("\n\n=== PUB/SUB ===")
    chans = r.pubsub_channels()
    print(" Channels:", [c.decode() for c in chans])
    counts = r.pubsub_numsub(*chans) if chans else []
    print(" Subscriber counts:", {c.decode(): n for c,n in counts})

import os
import json
from redis import Redis

# Copy these from your dramatiq_simple_multiprocess.py
REDIS_URL = get_settings().REDIS_URL


def delete_keys_by_pattern(pattern: str):
    r = Redis.from_url(REDIS_URL)
    # scan_iter yields matching keys without blocking Redis
    keys = list(r.scan_iter(match=pattern))
    if not keys:
        print("No keys found matching:", pattern)
        return
    # delete or, for large volumes, use unlink for non-blocking deletion
    deleted = r.delete(*keys)
    print(f"Deleted {deleted} keys")

def inspect_queue(QUEUE_NAME):
    # Connect
    r = Redis.from_url(REDIS_URL)

    # The dramatiq list key is always: dramatiq:<namespace>.<queue>
    # By default the namespace is "default"
    # key = f"dramatiq:default.{QUEUE_NAME}"
    key = f"dramatiq:{QUEUE_NAME}"


    # How many messages?
    length = r.llen(key)
    print(f"{key} length:", length)

    # Peek at each element
    items = r.lrange(key, 0, -1)
    print("Raw items (bytes):")
    for idx, item in enumerate(items):
        print(f" {idx}:", item)

    # If you want to decode JSON:
    print("\nDecoded messages:")
    for idx, item in enumerate(items):
        try:
            msg = json.loads(item)
        except Exception as e:
            msg = f"<non-JSON: {e}>"
        print(f" {idx}:", msg)

# ── MAIN ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # inspect_keys()
    # if you know a queue is a stream, you can call inspect_stream("celery")
    # inspect_stream("celery")
    # inspect_pubsub()
    QUEUE_NAME = "kdcube_orch_low_priority"
    # inspect_queue(QUEUE_NAME)
    # keys = [f"dramatiq.*", "dramatiq:default:kdcube_orch_low_priority","dramatiq.kdcube_orch_low_priority"]
    # keys = "kdcube:system:ratelimit:*"
    keys = "kdcube:session:anonymous:*"

    keys = "kdcube:throttling:*"
    # keys = "_kombu.binding.*"
    keys = "kdcube:economics:rl:*"
    delete_keys_by_pattern(keys)
    inspect_keys()
