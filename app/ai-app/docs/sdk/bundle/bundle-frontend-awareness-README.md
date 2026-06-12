---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-frontend-awareness-README.md
title: "Bundle Frontend Awareness"
summary: "Frontend operating guidance for bundle UIs against scaled ingress and proc backends: retries, drain behavior, rate limiting, backpressure, shared widget source, and multi-tab or session coordination."
tags: ["sdk", "bundle", "frontend", "sse", "retries", "rate-limits", "backpressure", "scaling", "widget"]
keywords: ["scaled frontend behavior", "sse retry strategy", "drain and shutdown behavior", "rate limit handling", "backpressure handling", "multi tab coordination", "ingress and proc awareness", "shared sdk widget source", "bundle ui resilience"]
updated_at: 2026-05-19
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-ui-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-chat-stream-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/README-monitoring-observability.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
---
# Bundle Frontend Awareness Guide (Ingress/Proc, Rate Limits, and Serverless Behavior)

This guide is for **frontend developers** integrating with the chat platform.
The backend is **multi‑replica** and **serverless‑like**: instances can scale up/down at any time.

## Quick Takeaways

- **Never assume sticky connections.** Any request can land on any replica.
- **Expect drains / restarts.** SSE streams can close with a `server_shutdown` event.
- **A started turn is not auto-retried.** If proc dies after bundle execution started, the client may see partial output and then an interruption signal.
- **Honor rate‑limits and backpressure.** Use exponential backoff and jitter.
- **Reduce request bursts.** Coalesce calls on page load and across tabs.

---

## A. Connection & Lifecycle Rules

| Area | What You’ll See | What You Must Do |
| --- | --- | --- |
| **SSE stream closes** | `server_shutdown` event, or connection drop | Reconnect with **backoff + jitter** (start 1–2s, cap 30s). |
| **Draining instance** | HTTP `503` with `{status:"draining"}` | Retry after short delay (1–3s + jitter). Don’t hammer. |
| **Scaled down instance** | SSE closes, no more events | Reconnect; don’t treat as fatal. |
| **Turn interrupted after start** | `conv_status` with `data.state="error"` and `data.completion="interrupted"`, plus `chat_error` with `data.error_type="turn_interrupted"` | Keep partial output visible, mark the turn as interrupted/failed, and let the user retry manually. Do not auto-resubmit the same message. |
| **No stickiness** | Requests land on different replicas | Keep **session_id** and **auth tokens** consistent per user. |
| **Multiple tabs** | Higher burst risk | Use shared storage and **coordinate** so only one tab does polling. |

---

## B. Rate Limits & Backpressure (Concrete)

### Typical errors

| Status | Meaning | What to do |
| --- | --- | --- |
| `429` | Rate limit exceeded | Backoff (2–5s) + jitter. Reduce burst. |
| `503` | Backpressure or draining | Backoff (1–3s) + jitter. Don’t retry immediately. |
| `401/403` | Auth missing/invalid | Refresh auth tokens / redirect to login. |

### Burst guidance

- **Page load** should not trigger more than 10–15 requests in <10s.
- **Chat send** should be serialized; avoid concurrent send bursts.
- **Polling** (if used) should be 5–10s minimum; prefer SSE.

---

## C. SSE (Streaming) Best Practices

### When opening SSE

- **Use a single SSE stream per user session**, not per component.
- If multiple tabs are open, **elect a leader tab** to keep SSE alive.

### On `server_shutdown`

- Treat it as **expected**.
- Close stream immediately.
- Reconnect after a short delay (1–2s + jitter).

### On turn interruption

- This is different from `server_shutdown`.
- It means proc had already started the turn, and the client may already have rendered `chat_delta` chunks.
- Preserve the partial output that was already shown.
- Mark the active turn as interrupted/failed when you receive:
  - `conv_status` with `data.completion = "interrupted"`
  - `chat_error` with `data.error_type = "turn_interrupted"`
- Offer a visible retry/resubmit action, but do not auto-replay the original request.

### Keepalive handling

- The server emits `: keepalive` when idle. Ignore it.
- Don’t assume keepalive means new data.

---

## D. API Endpoints & Base URLs

| API | Base URL | Notes |
| --- | --- | --- |
| **Ingress (SSE + REST)** | `.../sse/*`, `.../api/chat/*` | Session + gateway checks |
| **Proc (Integrations)** | `.../api/integrations/*` | Long‑running bundle ops |

Frontends should support **two base URLs** (ingress/proc) in dev.
In prod, an Nginx or API gateway typically hides this and exposes a unified base.

---

## E. Retry & Backoff Recipes

### SSE reconnect

```
delay = min(30s, 2^attempt + jitter(0..1s))
```

### HTTP 429/503 retry

```
delay = base(1–3s) * 2^attempt + jitter(0..1s)
max_attempts = 5
```

Stop retrying after a few attempts and surface a UI message.

---

## F. Multi‑Tab Coordination

Recommended pattern:

1. Use `localStorage` or `BroadcastChannel` to elect a leader tab.
2. Only the leader maintains SSE.
3. Followers read updates from shared storage (or request on demand).

This avoids duplicate SSE streams and rate‑limit bursts.

---

## G. Auth Notes

- Non‑simple auth requires **access token + ID token**.
- SSE accepts auth tokens via **query params** if headers aren’t available.
- REST/integrations may also carry the connected peer id via the configured stream-id header
  (default `KDC-Stream-ID`) when the client wants bundle-originated events targeted back to
  one connected peer instead of broadcast to the whole session.
- If using a proxylogin service, the browser should not handle raw tokens.

See [bundle-client-communication-README.md](bundle-client-communication-README.md) for the full header/cookie/query-param contract.

---

## H. Shared Widget Components

SDK-owned shared UI, such as the User Memory widget or Telegram admin/channels
panels, is frontend code compiled into the host widget through
`shared_sources`. It does not change the runtime safety rules:

- shared source or `ui.widgets.<alias>` config does not declare a widget
  surface; an inherited or local `@ui_widget(alias="<alias>")` must exist
- suppress inherited widgets with `enabled.widget.<alias>: false`, not by
  disabling only `ui.widgets.<alias>`
- the host widget still owns config handshake and operation URL construction
- the host widget should keep one API client and inject it into shared panels
- backend APIs still enforce KDCube roles, Telegram `initData`, and registry
  roles; hiding a tab is only UX, not authorization
- regular users should see only non-admin surfaces; admin panels should appear
  only when the backend payload says they are allowed

See [bundle-widget-integration-README.md#shared-ui-source-materialization](bundle-widget-integration-README.md#shared-ui-source-materialization).

## I. Data Bus Awareness

If the frontend uses Socket.IO for both chat and Data Bus, keep the UI state
machines separate.

| Action | Send through | UI expectation |
| --- | --- | --- |
| user prompt, followup, steer, chat context | `chat_message` / `/sse/chat` with `external_events[]` | chat admission, timeline/turn events, ReAct may consume |
| durable bundle state mutation | `data_bus.publish` with `messages[]` | stream admission ack first, handler result later through `chat_service` or refetch |

Data Bus retries and conflicts are domain-state concerns. Do not auto-replay a
state mutation unless the idempotency key and current object revision make that
safe. For collaborative objects, the UI should keep the latest revision visible
and be prepared to show a conflict when the server rejects a stale
`base_revision`.

Read the design boundary in
[Conversation Event Bus And Data Bus](../../service/comm/conversation-event-bus-and-data-bus-README.md).

---

## J. Do/Don’t Summary

| ✅ Do | ❌ Don’t |
| --- | --- |
| Backoff + jitter on 429/503 | Fire retries in tight loops |
| Handle `server_shutdown` event | Treat it as a fatal error |
| Share SSE across tabs | Open SSE per widget or per tab |
| Use two base URLs in dev | Hardcode a single base forever |
| Inject one host API client into shared panels | Let copied UI build its own tenant/project/bundle constants |

---

## K. Troubleshooting Checklist

- 429 spikes? → reduce burst, add backoff, coalesce requests.
- 503 spikes? → capacity pressure, wait and retry.
- SSE drops? → check for `server_shutdown` and reconnect.
- Partial answer then abrupt failure? → look for `conv_status` completion `interrupted` and `chat_error.error_type = turn_interrupted`; keep the partial output and let the user retry.
- No SSE events? → verify stream is open and user/session is valid.
