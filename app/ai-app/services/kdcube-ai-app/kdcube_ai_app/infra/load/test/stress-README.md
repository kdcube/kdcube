## How to run

Prereqs

AUTH_PROVIDER=simple
IDP_DB_PATH=/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/services/kdcube-ai-app/kdcube_ai_app/apps/
chat/api/idp_users.json

### Case 1: 15 registered, 1 message each (no SSE streams)

python /Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/services/kdcube-ai-app/kdcube_ai_app/infra/load/test/burst_sse_load.py \
--base-url http://localhost:8010 \
--registered 15 --admin 0 \
--messages-per-user 1 \
--concurrency 10 \
--monitor

### Case 2: 15 registered + 5 admin, 2 messages each (with SSE)

python /Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/services/kdcube-ai-app/kdcube_ai_app/infra/load/test/burst_sse_load.py \
--base-url http://localhost:8010 \
--registered 15 --admin 5 \
--messages-per-user 2 \
--concurrency 10 \
--open-sse \
--monitor

### Case 3: Stress queue (burst)

python /Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/services/kdcube-ai-app/kdcube_ai_app/infra/load/test/burst_sse_load.py \
--base-url http://localhost:8010 \
--registered 15 --admin 0 \
--messages-per-user 4 \
--concurrency 30 \
--monitor

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