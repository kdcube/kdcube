---
id: ks:docs/economics/rate-limit-simulation-playbook.md
title: "Rate Limit Event Simulation Playbook"
summary: "Step-by-step scenarios to trigger each rate_limit.* SSE event in local dev."
tags: ["economics", "rate-limit", "sse", "testing", "playbook"]
keywords: ["rate_limit.warning", "rate_limit.denied", "SSE", "quota", "throttling"]
see_also:
  - ks:docs/economics/economics-events-README.md
  - ks:docs/sdk/bundle/bundle-sse-events-README.md
  - ks:docs/economics/eco-test-README.md
  - ks:docs/economics/operational-README.md
---
# Rate Limit Event Simulation Playbook

Use this playbook to manually trigger each `rate_limit.*` SSE event in a local dev environment and verify the client handles it correctly.

**Base assumptions:**
- Local stack is running (`docker compose up`)
- Admin token: `test-admin-token-123` (from `idp_users.json`)
- Test users
- All quota changes take effect immediately (no service restart required)
- After each scenario — restore the original policy or use a fresh user

---

## Scenario 1 — `rate_limit.warning` (1 message remaining)

### Prerequisites
- A `free` plan user with no prior requests today
- Use `chat-user-0001`

### Plan
1. Set `requests_per_day: 2` on the free plan:
2. Send the **first** request from `chat-user-0001` — any message.
3. Wait for the response to fully complete.

### Expected result
- **After the first request completes** (post-run): `rate_limit.warning` arrives on `chat_service`.
- Client shows a **yellow warning** notification: `"You have 1 message remaining in your current quota."`
- The user is in the **idle state** when the warning appears — input is not blocked.
- The second request executes normally.
- After the second request completes: a second warning fires — `"You've used your last message. Your quota resets today at <time>."`
- The third request is blocked by `rate_limit.denied`.

---

## Scenario 2 — `rate_limit.warning` (low tokens)

### Prerequisites
- A `free` plan user with no requests today
- Use `chat-user-0002`

### Plan
1. Set `tokens_per_hour` on a `free` low enough that a single short request leaves less than `est_turn_tokens` (~4 000) remaining:

2. Send any short request from `chat-user-0002` (e.g. `"Write a short paragraph about AI"`).
   This uses ~500–700 tokens, leaving ~1 800 remaining.
3. Wait for the response to fully complete.
   Post-run check: `total_token_remaining ≈ 1 800 < est_turn_tokens ≈ 4 200` → warning fires.

### Expected result
- **After the first request completes** (post-run): `rate_limit.warning` fires.
- Client shows: `"You're running low on tokens (~1K remaining). Consider upgrading."`
- Input remains **unblocked** (warning type).

---

## Scenario 3 — `rate_limit.denied` (tokens_per_hour exceeded)

### Prerequisites
- A `free` plan user with no requests today
- Use `chat-user-0003`

### Plan
1. Set `tokens_per_hour: 500` on a `free`
2. Send any request from `chat-user-0003`.

### Expected result
- `rate_limit.denied` fires immediately (at admit).
- Client shows a **red error** notification: `"You've reached your usage limit. Your quota resets today at 9:32 PM."`
- Input is **blocked** until notification is dismissed.

---

## Scenario 4 — `rate_limit.denied` (requests_per_day exceeded)

### Prerequisites
- A `free` plan user with no requests today
- Use `chat-user-0004`

### Plan
1. Set `requests_per_day: 2` on a `free`
2. Send 2 requests from `chat-user-0004` (both complete successfully).
3. Send a **third** request.

### Expected result
- `rate_limit.denied` fires on the third request.
- Client shows error notification with reset time.

---

## Scenario 5 — `rate_limit.denied` (quota_lock_timeout)

### Prerequisites
- `max_concurrent: 1` on the free plan
- Use `chat-user-0005`
- Two browser tabs logged in as the same user

### Plan 
1. Set `max_concurrent: 1` on a `free`
2. In **tab 1**: send a long request (e.g. "Write a very long story...") — it starts processing.
3. Immediately in **tab 2**: send another request from the same user.
4. Tab 2 waits 5 seconds trying to acquire the Redis quota lock.

### Expected result
- Client shows error notification with message: `You have too many requests running at once. Please wait for one to complete.`

---

## Scenario 6 — `rate_limit.post_run_exceeded`

### Prerequisites
- A `free` plan user with no requests today
- Use `chat-user-0006`

### Plan
1. Set `tokens_per_hour` to just above the floor estimate (2000) but low enough that a real response exceeds it on a `free`

2. Send a request that generates a long response from `chat-user-0006`:
   > "Write a detailed 500-word explanation of how neural networks work."
3. Admit passes (estimated tokens ~2000 < 2200).
4. LLM responds with >2200 tokens total.

### Expected result
- Request **completes normally** — the full response is streamed.
- After completion: `rate_limit.post_run_exceeded` fires on `chat_service`.
- `data.rate_limit.notification_type = "warning"` (not error — request already succeeded)
- Client shows a **yellow warning** notification: `"You've reached your usage limit. Your quota resets today at ..."`
- Input remains **unblocked** (warning type does not block).
- **Next request** will be blocked by `rate_limit.denied` (hour bucket is now over limit).

---

## Scenario 7 — `rate_limit.project_exhausted`

### Prerequisites
- A `free` plan user without wallet credits and without active subscription
- Use `chat-user-0007`
- Project budget must be near zero

### Plan
1. Check current project budget balance.
2. Make sure that the project budget is near zero.
3. Send a request from `chat-user-0007`.

### Expected result
- `rate_limit.project_exhausted` fires.
- Client shows: `"Project budget exhausted. Please contact your administrator to add funds."`

---

## Scenario 8 — `rate_limit.subscription_exhausted`

### Prerequisites
- A user with an active subscription but zero (or near-zero) subscription balance
- No wallet credits
- Use `chat-user-0008`

### Plan
1. Create a test subscription for `chat-user-0008` with a very small budget via admin API.
2. Make enough requests to exhaust the subscription balance.
3. Send another request.

### Expected result
- `rate_limit.subscription_exhausted` fires.
- `data.user_message = "Your subscription balance is exhausted. Please top up your subscription to continue."`
- Client shows a **red error** notification with that message.
- Input is **blocked**.




