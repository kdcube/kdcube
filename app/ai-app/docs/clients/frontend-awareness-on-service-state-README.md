---
id: ks:docs/clients/frontend-awareness-on-service-state-README.md
title: "Frontend Awareness On Service State"
summary: "Frontend guidance for multi‑replica, serverless‑like backend: SSE lifecycle, retries, rate limits, and multi‑tab coordination."
tags: ["clients", "frontend", "sse", "retries", "rate-limits", "backpressure", "scaling"]
keywords: ["serverless behavior", "multi-replica", "backoff", "jitter", "draining", "http-429", "http-503", "multi-tab", "ingress", "proc"]
see_also:
  - ks:docs/clients/README.md
  - ks:docs/clients/client-communication-README.md
  - ks:docs/clients/sse-events-README.md
  - ks:docs/service/README-monitoring-observability.md
  - ks:docs/service/auth/auth-README.md
---
# Frontend Awareness Guide (Ingress/Proc, Rate Limits, and Serverless Behavior)

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

See [client-communication-README.md](client-communication-README.md) for the full header/cookie/query-param contract.

---

## H. Do/Don’t Summary

| ✅ Do | ❌ Don’t |
| --- | --- |
| Backoff + jitter on 429/503 | Fire retries in tight loops |
| Handle `server_shutdown` event | Treat it as a fatal error |
| Share SSE across tabs | Open SSE per widget or per tab |
| Use two base URLs in dev | Hardcode a single base forever |

---

## I. Troubleshooting Checklist

- 429 spikes? → reduce burst, add backoff, coalesce requests.
- 503 spikes? → capacity pressure, wait and retry.
- SSE drops? → check for `server_shutdown` and reconnect.
- Partial answer then abrupt failure? → look for `conv_status` completion `interrupted` and `chat_error.error_type = turn_interrupted`; keep the partial output and let the user retry.
- No SSE events? → verify stream is open and user/session is valid.
