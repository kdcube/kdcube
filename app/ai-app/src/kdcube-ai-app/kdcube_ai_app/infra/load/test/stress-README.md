## How to run

Prereqs

AUTH_PROVIDER=simple
IDP_DB_PATH=/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/
chat/ingress/idp_users.json
GATEWAY_CONFIG_JSON='{"tenant":"<TENANT_ID>","project":"<PROJECT_ID>"}'

Note: `burst_sse_load.py` targets **ingress** (`/sse/*` and `/monitoring/system`).
It will enqueue chat tasks, so **proc is exercised indirectly** if it is running.
It does not hit the proc integrations API. For proc/integrations load, use a separate test.

Note: `guarded_bypass_load.py` targets **REST** endpoints on ingress and validates
rate limiting behavior (429 vs no‑429) based on gateway config.

### Case 1: 15 registered, 1 message each (no SSE streams)

python /Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/infra/load/test/burst_sse_load.py \
--base-url http://localhost:8010 \
--registered 15 --admin 0 \
--messages-per-user 1 \
--concurrency 10 \
--monitor

### Case 2: 15 registered + 5 admin, 2 messages each (with SSE)

python /Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/infra/load/test/burst_sse_load.py \
--base-url http://localhost:8010 \
--registered 15 --admin 5 \
--messages-per-user 2 \
--concurrency 10 \
--open-sse \
--monitor

### Case 3: Stress queue (burst)

python /Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/infra/load/test/burst_sse_load.py \
--base-url http://localhost:8010 \
--registered 15 --admin 0 \
--messages-per-user 4 \
--concurrency 30 \
--monitor

### Case 4: Guarded vs bypass throttling (429 vs no‑429)

Make sure gateway config includes the endpoint lists:
- `guarded_rest_patterns` includes `^/api/cb/resources/by-rn$`
- `bypass_throttling_patterns` includes `^/api/admin/control-plane/webhooks/stripe$`

python /Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/infra/load/test/guarded_bypass_load.py \
--base-url http://localhost:8010 \
--user-role anonymous \
--requests 50 \
--concurrency 10 \
--idp-path /Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/idp_users.json

Expected: guarded endpoint shows 429; bypass endpoint shows **0** 429
(other 4xx/5xx can still happen if payload/signature is invalid).

———

## How to profile during the test

Open Gateway Monitoring and watch:

- Queue Analytics
- Queue Utilization
- Throttling (Recent)

Logs:

- enqueue_chat_task_atomic result
- acquired task
- Starting task

Redis Browser:

- Queue keys: <tenant>:<project>:kdcube:chat:prompt:queue:*
- Heartbeats: <tenant>:<project>:kdcube:heartbeat:process:*
