# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Server-side burst load generator for SSE chat.
This runs from the backend host (not the browser), so it bypasses browser SSE limits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx


@dataclass
class LoadUser:
    token: str
    role: str
    user_id: Optional[str] = None
    username: Optional[str] = None


@dataclass
class SSESession:
    user: LoadUser
    stream_id: str
    task: Optional[asyncio.Task]


def _load_idp_users(path: str) -> Dict[str, Dict]:
    with open(path, "r") as f:
        return json.load(f)


def _group_tokens(users: Dict[str, Dict]) -> Dict[str, List[LoadUser]]:
    groups: Dict[str, List[LoadUser]] = {"admin": [], "registered": [], "paid": []}
    for token, user in (users or {}).items():
        roles = set(user.get("roles") or [])
        entry = LoadUser(
            token=token,
            role="registered",
            user_id=user.get("sub") or user.get("user_id"),
            username=user.get("username"),
        )
        if "kdcube:role:super-admin" in roles:
            entry.role = "admin"
            groups["admin"].append(entry)
        elif "kdcube:role:paid" in roles:
            entry.role = "paid"
            groups["paid"].append(entry)
        else:
            groups["registered"].append(entry)
    return groups


def _select_users(groups: Dict[str, List[LoadUser]],
                  admin_count: int,
                  registered_count: int,
                  paid_count: int) -> List[LoadUser]:
    selected: List[LoadUser] = []
    selected.extend(groups.get("admin", [])[:admin_count])
    selected.extend(groups.get("registered", [])[:registered_count])
    selected.extend(groups.get("paid", [])[:paid_count])
    return selected


async def _open_sse_stream(
    client: httpx.AsyncClient,
    base_url: str,
    user: LoadUser,
    stream_id: str,
    tenant: Optional[str],
    project: Optional[str],
    stop_event: asyncio.Event,
) -> None:
    params = {
        "stream_id": stream_id,
        "bearer_token": user.token,
    }
    if tenant:
        params["tenant"] = tenant
    if project:
        params["project"] = project

    url = f"{base_url}/sse/stream"
    try:
        async with client.stream("GET", url, params=params, timeout=None) as resp:
            if resp.status_code != 200:
                return
            async for _line in resp.aiter_lines():
                if stop_event.is_set():
                    break
    except Exception:
        return


async def _send_chat(
    client: httpx.AsyncClient,
    base_url: str,
    user: LoadUser,
    stream_id: str,
    i: int,
    message: str,
    tenant: Optional[str],
    project: Optional[str],
    bundle_id: Optional[str],
) -> Tuple[int, int, Optional[str]]:
    url = f"{base_url}/sse/chat"
    params = {"stream_id": stream_id}
    turn_id = f"turn_{uuid.uuid4().hex[:8]}"
    conv_id = f"burst-{stream_id}-{i}"

    msg = {
        "text": message,
        "conversation_id": conv_id,
        "turn_id": turn_id,
    }
    if tenant:
        msg["tenant"] = tenant
    if project:
        msg["project"] = project
    if bundle_id:
        msg["bundle_id"] = bundle_id

    payload = {"message": msg}
    headers = {"Authorization": f"Bearer {user.token}", "Content-Type": "application/json"}
    t0 = time.monotonic()
    try:
        resp = await client.post(url, params=params, json=payload, headers=headers)
        ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code >= 400:
            try:
                return resp.status_code, ms, resp.text
            except Exception:
                return resp.status_code, ms, None
        return resp.status_code, ms, None
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        return 0, ms, str(e)


async def _get_system_status(client: httpx.AsyncClient, base_url: str, token: str) -> Optional[Dict]:
    try:
        resp = await client.get(
            f"{base_url}/monitoring/system",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        return None
    return None


async def run_load(args: argparse.Namespace) -> int:
    idp_path = args.idp_path or os.getenv("IDP_DB_PATH") or "./idp_users.json"
    users_raw = _load_idp_users(idp_path)
    groups = _group_tokens(users_raw)
    selected = _select_users(groups, args.admin, args.registered, args.paid)

    if not selected:
        print("No users selected. Check IDP_DB_PATH and counts.")
        return 1

    base_url = args.base_url.rstrip("/")
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # baseline stats (optional)
        monitor_token = (groups.get("admin") or groups.get("registered") or groups.get("paid") or [None])[0]
        monitor_token_val = monitor_token.token if monitor_token else None
        if args.monitor and monitor_token_val:
            before = await _get_system_status(client, base_url, monitor_token_val)
            if before:
                queue = (before.get("queue_stats") or {}).get("total", 0)
                print(f"[monitor] before: queue_total={queue}")

        # Optionally open SSE streams
        stop_event = asyncio.Event()
        sessions: List[SSESession] = []
        if args.open_sse:
            for idx, user in enumerate(selected):
                stream_id = f"burst-{user.role}-{idx}-{int(time.time() * 1000)}"
                task = asyncio.create_task(
                    _open_sse_stream(client, base_url, user, stream_id, args.tenant, args.project, stop_event),
                    name=f"sse:{stream_id}",
                )
                sessions.append(SSESession(user=user, stream_id=stream_id, task=task))

            if args.warmup_s > 0:
                await asyncio.sleep(args.warmup_s)
        else:
            for idx, user in enumerate(selected):
                stream_id = f"burst-{user.role}-{idx}-{int(time.time() * 1000)}"
                sessions.append(SSESession(user=user, stream_id=stream_id, task=None))

        # Build all chat tasks
        tasks: List[asyncio.Task] = []
        sem = asyncio.Semaphore(max(1, args.concurrency))
        results: List[Tuple[int, int, Optional[str]]] = []

        async def _run_one(u: LoadUser, stream_id: str, i: int):
            async with sem:
                res = await _send_chat(
                    client, base_url, u, stream_id, i,
                    args.message, args.tenant, args.project, args.bundle_id
                )
                results.append(res)

        for sess in sessions:
            for i in range(args.messages_per_user):
                tasks.append(asyncio.create_task(_run_one(sess.user, sess.stream_id, i)))

        t_start = time.monotonic()
        await asyncio.gather(*tasks, return_exceptions=True)
        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        # Close SSE streams
        stop_event.set()
        for sess in sessions:
            if sess.task:
                sess.task.cancel()
        if sessions and args.open_sse:
            await asyncio.sleep(0.1)

        # summarize
        ok = sum(1 for code, _, _ in results if code and code < 400)
        errors = [r for r in results if (r[0] == 0 or r[0] >= 400)]
        print(f"Sent {len(results)} messages in {elapsed_ms}ms. ok={ok} errors={len(errors)}")
        if errors:
            # Show a few errors
            for code, ms, detail in errors[:5]:
                print(f"  error code={code} ms={ms} detail={detail}")

        if args.monitor and monitor_token_val:
            after = await _get_system_status(client, base_url, monitor_token_val)
            if after:
                queue = (after.get("queue_stats") or {}).get("total", 0)
                throttled = (after.get("throttling_stats") or {}).get("total_throttled", 0)
                print(f"[monitor] after: queue_total={queue} throttled={throttled}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SSE chat burst load generator (server-side)")
    p.add_argument("--base-url", default=os.getenv("CHAT_BASE_URL", "http://localhost:8010"))
    p.add_argument("--tenant", default=os.getenv("TENANT_ID"))
    p.add_argument("--project", default=os.getenv("PROJECT_ID"))
    p.add_argument("--idp-path", default=os.getenv("IDP_DB_PATH"))
    p.add_argument("--admin", type=int, default=5, help="Number of admin users")
    p.add_argument("--registered", type=int, default=10, help="Number of registered users")
    p.add_argument("--paid", type=int, default=0, help="Number of paid users")
    p.add_argument("--messages-per-user", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--message", default="ping")
    p.add_argument("--bundle-id", default=None)
    p.add_argument("--open-sse", action="store_true", help="Open SSE streams per user")
    p.add_argument("--warmup-s", type=float, default=0.5, help="Warmup seconds before sending messages")
    p.add_argument("--monitor", action="store_true", help="Fetch /monitoring/system before/after")
    p.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(run_load(args))


if __name__ == "__main__":
    raise SystemExit(main())
