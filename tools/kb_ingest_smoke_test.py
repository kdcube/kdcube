#!/usr/bin/env python
"""
End-to-end KB ingest smoke test.

What it does:
  1) Uploads a small in-memory document via   POST /api/kb/{project}/upload
  2) Dispatches processing via                POST /api/kb/{project}/upload/process
  3) Polls                                    GET  /api/kb/{project}/resources
     until the resource shows search_indexing=True, or until --timeout expires.
  4) Reports stages observed.

It does NOT touch Postgres directly — the goal is to prove the full HTTP path
(proxy -> kb -> dramatiq -> embedding -> retrieval_segment upsert) works.

Usage (from the repo root):
    python tools/kb_ingest_smoke_test.py
    python tools/kb_ingest_smoke_test.py --base http://localhost:5174 --token test-admin-token-123
    python tools/kb_ingest_smoke_test.py --file path/to/doc.pdf --project default --timeout 180

Auth: defaults to the dev "test-admin-token-123" admin bearer token (matches
what the SimpleIDP accepts in the all_in_one_kdcube stack). Override with
--token if you have a real id token / use --id-token-header to set the header
name (default Authorization: Bearer ...).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import requests

DEFAULT_DOC = b"""# Smoke Test Document

This is a tiny markdown document used to verify that the KB ingestion pipeline
works end to end. It contains a few named entities so retrieval can match on
something specific:

- Product: KDCube Advanced RAG
- Library: SearchIndexingModule
- Database: PostgreSQL with pgvector
- Identifier: SMOKE-TEST-2026-04-26
- Acronym: KB
"""


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def step(msg: str) -> None:
    print(flush=True)
    log(f"=== {msg} ===")


def upload(base: str, project: str, file_bytes: bytes, file_name: str, headers: dict) -> dict:
    step(f"Step 1/3 — uploading {file_name} ({len(file_bytes)} bytes)")
    files = {"file": (file_name, io.BytesIO(file_bytes), "text/markdown")}
    r = requests.post(
        f"{base}/api/kb/{project}/upload",
        files=files,
        headers=headers,
        timeout=120,
    )
    log(f"  HTTP {r.status_code} from {r.request.url}")
    if not r.ok:
        log(f"  body: {r.text[:500]}")
        raise SystemExit(2)
    body = r.json()
    log(f"  resource_id={body.get('resource_id')}  version={body.get('resource_metadata', {}).get('version')}")
    return body


def dispatch(base: str, project: str, resource_meta: dict, headers: dict) -> dict:
    step("Step 2/3 — dispatching processing pipeline")
    payload = {
        "resource_metadata": resource_meta,
        "socket_id": "",  # we poll for status; no socket consumer
        "processing_mode": "retrieval_only",
    }
    r = requests.post(
        f"{base}/api/kb/{project}/upload/process",
        json=payload,
        headers={**headers, "Content-Type": "application/json"},
        timeout=60,
    )
    log(f"  HTTP {r.status_code}")
    if not r.ok:
        log(f"  body: {r.text[:500]}")
        raise SystemExit(3)
    body = r.json()
    log(f"  task_id={body.get('task_id')}  status={body.get('status')}")
    return body


def poll_until_indexed(
        base: str, project: str, resource_id: str, headers: dict, timeout_s: int, interval_s: int = 3,
) -> dict | None:
    step(f"Step 3/3 — polling resources every {interval_s}s for up to {timeout_s}s")
    deadline = time.time() + timeout_s
    last_stages: dict | None = None
    last_log = 0.0
    while time.time() < deadline:
        r = requests.get(
            f"{base}/api/kb/{project}/resources",
            headers=headers,
            timeout=120,
        )
        if not r.ok:
            log(f"  poll: HTTP {r.status_code} {r.text[:200]}")
            time.sleep(interval_s)
            continue
        body = r.json()
        items = body if isinstance(body, list) else (body.get("resources") or [])
        match = next((x for x in items if str(x.get("id")) == str(resource_id)), None)
        if not match:
            log(f"  poll: resource {resource_id} not present yet (have {len(items)})")
        else:
            stages = match.get("processing_status") or {}
            if stages != last_stages:
                last_stages = dict(stages)
                done_keys = sorted(k for k, v in stages.items() if v)
                log(f"  poll: stages done -> {done_keys or '(none)'}")
            elif time.time() - last_log > 15:
                last_log = time.time()
                log("  poll: still working...")
            if stages.get("search_indexing"):
                step("Pipeline COMPLETE — search_indexing=True")
                return match
        time.sleep(interval_s)
    log("Timeout reached without search_indexing=True")
    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:5174", help="proxy base URL")
    p.add_argument("--project", default="default", help="project id")
    p.add_argument("--token", default="test-admin-token-123", help="bearer token")
    p.add_argument("--id-token-header", default=None,
                   help="if set, send token via this header instead of Authorization: Bearer")
    p.add_argument("--file", default=None, help="path to a file to upload (default: an in-memory MD doc)")
    p.add_argument("--timeout", type=int, default=240, help="poll deadline seconds")
    args = p.parse_args()

    if args.file:
        path = Path(args.file)
        file_bytes = path.read_bytes()
        file_name = path.name
    else:
        file_bytes = DEFAULT_DOC
        file_name = "smoke_test.md"

    headers: dict = {}
    if args.id_token_header:
        headers[args.id_token_header] = args.token
    else:
        headers["Authorization"] = f"Bearer {args.token}"

    log(f"base={args.base}  project={args.project}  auth={'header:'+args.id_token_header if args.id_token_header else 'Bearer'}")

    up = upload(args.base, args.project, file_bytes, file_name, headers)
    meta = up.get("resource_metadata") or {}
    rid = up.get("resource_id") or meta.get("id")
    if not rid:
        log("Upload response missing resource_id; aborting.")
        log(json.dumps(up, indent=2)[:1000])
        return 4

    dispatch(args.base, args.project, meta, headers)

    final = poll_until_indexed(args.base, args.project, str(rid), headers, args.timeout)
    if final is None:
        log("FAIL: pipeline did not finish in time. Resource is in the KB but never reached search_indexing.")
        log("Check dramatiq logs:  docker logs --tail 100 all_in_one_kdcube-dramatiq-1")
        return 5

    print()
    log("OK — resource fully indexed:")
    print(json.dumps({
        "id": final.get("id"),
        "version": final.get("version"),
        "title": final.get("title"),
        "processing_status": final.get("processing_status"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
