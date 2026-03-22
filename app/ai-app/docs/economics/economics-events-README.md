---
id: ks:docs/economics/economics-events-README.md
title: "Economics Rate-Limit SSE Events"
summary: "Reference for all rate_limit.* server-sent events: when they fire, what payload they carry, and where user-facing messages are defined."
tags: ["economics", "rate-limit", "sse", "events", "client"]
keywords: ["rate_limit.warning", "rate_limit.denied", "rate_limit.no_funding", "rate_limit.subscription_exhausted", "rate_limit.project_exhausted", "user_message", "events_resources"]
see_also:
  - ks:docs/economics/economic-README.md
  - ks:docs/clients/sse-events-README.md
---
# Economics Rate-Limit SSE Events

This document describes the `rate_limit.*` server-sent events emitted by
`entrypoint_with_economic.py` and received by the client.

All user-facing text (`user_message` field) is defined in one place:

> **[`kdcube_ai_app/apps/chat/sdk/infra/economics/events_resources.py`](../../services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/events_resources.py)**

Edit that file to change what users see — no entrypoint logic needs to change.

---

## Event reference

| Event type | `notification_type` | `user_message` resource | When it fires |
|---|---|---|---|
| `rate_limit.warning` | `warning` | `MSG_WARNING_*` / `msg_warning_*` (selected at runtime based on remaining quota — see table below) | After a request completes and the post-run snapshot shows ≤ 1 message remaining **or** token balance below one estimated turn. Fires only when no hard violation occurred. |
| `rate_limit.denied` | `error` | `msg_denied_quota_reset(reset_text)` · `MSG_DENIED_LOCK_TIMEOUT` · `MSG_DENIED_CONCURRENCY` · `MSG_DENIED_TOKEN_LIMIT` · `MSG_DENIED_REQUEST_LIMIT` · `MSG_DENIED_GENERIC` (selected by `reason`) | Request is rejected before execution: quota exhausted, concurrency limit hit, or quota-lock timeout. Includes `retry_after_sec` and `reset_text` when the reset time is known. |
| `rate_limit.no_funding` | `error` | `MSG_NO_FUNDING` | User's account type is not in the allowed funding sources (e.g. anonymous users with `funding_source = "none"`). Request is rejected immediately. |
| `rate_limit.subscription_exhausted` | `error` | `MSG_SUBSCRIPTION_EXHAUSTED` | The user's subscription budget is exhausted and the user has no personal credits to cover the turn. Request is rejected. |
| `rate_limit.project_exhausted` | `error` | `MSG_PROJECT_EXHAUSTED` | The project budget is exhausted and the user has no personal credits to cover the turn. Request is rejected. |
| `rate_limit.snapshot` | *(info, not shown to user)* | — | Emitted after every completed request with the current usage snapshot. Used for client-side display of quota meters. |
| `rate_limit.lane_switch` | *(info, not shown to user)* | — | Emitted when the request switches from plan lane to paid (wallet) lane mid-flight. |
| `economics.user_underfunded_absorbed` | *(info, not shown to user)* | — | Emitted when the project absorbs the cost because the user's personal budget was short; logged for accounting. |

### `rate_limit.warning` message selection

| Condition | Resource |
|---|---|
| `messages_remaining == 0` and reset time known | `msg_warning_last_msg_reset(reset_text)` |
| `messages_remaining == 0` and reset time unknown | `MSG_WARNING_LAST_MSG_SOON` |
| `messages_remaining == 1` and request quota is binding | `MSG_WARNING_ONE_REQUEST_REMAINING` |
| `messages_remaining == 1` and token quota is binding | `msg_warning_low_tokens(tokens_k)` |
| `messages_remaining > 1` and token balance low | `msg_warning_low_tokens(tokens_k)` |
| fallback | `MSG_WARNING_APPROACHING` |

---

## Payload fields common to blocking events

All blocking events (`rate_limit.denied`, `rate_limit.no_funding`,
`rate_limit.subscription_exhausted`, `rate_limit.project_exhausted`) carry:

| Field | Type | Description |
|---|---|---|
| `user_message` | `string` | Human-readable message to display in the UI. |
| `notification_type` | `"error"` | Always `"error"` for blocking events. |
| `reason` | `string` | Machine-readable reason code (e.g. `requests_per_day`, `subscription_budget_exhausted`). |
| `bundle_id` | `string` | Request bundle identifier. |
| `subject_id` | `string` | User/subject identifier. |
| `user_type` | `string` | User type as resolved by `AuthManager` (e.g. `registered`, `anonymous`). |

`rate_limit.denied` additionally includes:

| Field | Type | Description |
|---|---|---|
| `retry_after_sec` | `int \| null` | Seconds until quota resets; `null` when unknown. |
| `reset_text` | `string \| null` | Human-readable reset time (e.g. `"tomorrow at 9:00 AM"`). |

---

## Warning event payload

`rate_limit.warning` carries the same fields as blocking events plus the full
`QuotaInsight` snapshot flattened at the top level:

| Field | Type | Description |
|---|---|---|
| `user_message` | `string` | Human-readable warning text. |
| `notification_type` | `"warning"` | Always `"warning"`. |
| `messages_remaining` | `int \| null` | How many messages the user can still send under the current quota. |
| `total_token_remaining` | `int \| null` | Estimated token budget remaining. |
| `remaining` | `object` | Per-dimension remaining counts (`requests_per_day`, `tokens_per_hour`, etc.). |
