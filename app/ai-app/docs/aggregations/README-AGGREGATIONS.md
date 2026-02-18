# OPEX Aggregations

This module provides **pre-computed accounting aggregates** that sit on top of the raw `accounting` events and are used by the OPEX API to avoid rescanning logs on every request.

We currently materialize:

* **Daily totals** (+ per-user and per-agent)
* **Hourly totals** (per day)
* **Monthly totals**
* **Yearly totals**

These aggregates live under the `analytics` tree and are consumed by `RateCalculator` (in `infra/accounting/calculator.py`) and the OPEX endpoints in `apps/chat/api/opex/opex.py`.

---

## 1. Storage layout

### 1.1 Raw events

Raw usage events are written under `accounting/`:

* **New layout (preferred):**

  ```text
  accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>/<service_type>/[group]/file.json
  ```

* **Legacy layout (still supported):**

  ```text
  accounting/<tenant>/<project>/<YYYY>/<MM>/<DD>/<service_type>/[group]/file.json
  ```

The aggregator reads *only* from `accounting/...` (never from `analytics/...`).

---

### 1.2 Aggregated products (analytics tree)

All aggregates are stored under:

```text
analytics/<tenant>/<project>/accounting/
```

#### Daily totals

**Path:**

```text
analytics/<tenant>/<project>/accounting/daily/<YYYY>/<MM>/<DD>/total.json
```

**Shape (simplified):**

```json
{
  "tenant_id": "home",
  "project_id": "demo",
  "level": "daily",
  "year": 2025,
  "month": 11,
  "day": 2,
  "hour": null,
  "bucket_start": "2025-11-02T00:00:00Z",
  "bucket_end": "2025-11-03T00:00:00Z",

  "total": { /* full usage accumulator */ },
  "rollup": [
    {
      "service": "llm" | "embedding" | ...,
      "provider": "anthropic" | "openai" | ...,
      "model": "claude-sonnet-..." | "gpt-4o" | ...,
      "spent": { /* per-service compact metrics, e.g. tokens */ }
    }
    ...
  ],
  "event_count": 71,
  "user_ids": ["admin-user-1", "..."],
  "aggregated_at": "2025-11-21T16:58:09.118100Z"
}
```

**Dimensions present:**

* **Time**: day (UTC bucket with `bucket_start` / `bucket_end`)
* **Service breakdown** (inside `rollup`):

    * `service` (llm, embedding, …)
    * `provider`
    * `model`
* **Users present in the bucket**: `user_ids[]`

---

#### Daily per-user aggregates

**Path:**

```text
analytics/<tenant>/<project>/accounting/daily/<YYYY>/<MM>/<DD>/users.json
```

**Shape:**

```json
{
  "tenant_id": "...",
  "project_id": "...",
  "level": "daily",
  "dimension": "user",
  "year": 2025,
  "month": 11,
  "day": 2,
  "hour": null,
  "bucket_start": "...",
  "bucket_end": "...",
  "users": [
    {
      "user_id": "admin-user-1",
      "event_count": 42,
      "total": { /* full usage accumulator for this user */ },
      "rollup": [
        {
          "service": "llm",
          "provider": "anthropic",
          "model": "claude-sonnet-...",
          "spent": { /* compact per-service tokens/cost basis */ }
        }
      ]
    },
    ...
  ],
  "aggregated_at": "..."
}
```

**Dimensions:**

* Time: **day**
* Dimension: **user**
* Nested service breakdown per user: `(service, provider, model)` via `rollup`.

These are the backing data for **per-user queries** when we use aggregates.

---

#### Daily per-agent aggregates

**Path:**

```text
analytics/<tenant>/<project>/accounting/daily/<YYYY>/<MM>/<DD>/agents.json
```

**Shape:**

```json
{
  "tenant_id": "...",
  "project_id": "...",
  "level": "daily",
  "dimension": "agent",
  "year": 2025,
  "month": 11,
  "day": 2,
  "hour": null,
  "bucket_start": "...",
  "bucket_end": "...",
  "agents": [
    {
      "agent_name": "answer_generator",
      "event_count": 27,
      "total": { /* full usage accumulator */ },
      "rollup": [
        {
          "service": "llm",
          "provider": "anthropic",
          "model": "claude-haiku-...",
          "spent": { /* compact tokens */ }
        }
      ]
    },
    ...
  ],
  "aggregated_at": "..."
}
```

**Dimensions:**

* Time: **day**
* Dimension: **agent**
* Nested service breakdown per agent.

These are the backing data for **per-agent queries** when we use aggregates.

---

#### Hourly totals

**Path:**

```text
analytics/<tenant>/<project>/accounting/hourly/<YYYY>/<MM>/<DD>/<HH>/total.json
```

**Shape (simplified):**

```json
{
  "tenant_id": "...",
  "project_id": "...",
  "level": "hourly",
  "year": 2025,
  "month": 11,
  "day": 2,
  "hour": 13,
  "bucket_start": "2025-11-02T13:00:00Z",
  "bucket_end": "2025-11-02T14:00:00Z",
  "total": { /* usage */ },
  "rollup": [ ... same format as daily rollup ... ],
  "event_count": 10,
  "user_ids": ["admin-user-1", "..."],
  "aggregated_at": "..."
}
```

**Dimensions:**

* Time: **hour**
* Service breakdown: `(service, provider, model)` via `rollup`.

Currently, the OPEX HTTP API does not directly expose hourly endpoints, but the data is there for future time-series or rate-based features.

---

#### Monthly totals

**Path:**

```text
analytics/<tenant>/<project>/accounting/monthly/<YYYY>/<MM>/total.json
```

**Shape:**

```json
{
  "tenant_id": "...",
  "project_id": "...",
  "level": "monthly",
  "year": 2025,
  "month": 11,
  "day": null,
  "hour": null,
  "bucket_start": "2025-11-01T00:00:00Z",
  "bucket_end": "2025-12-01T00:00:00Z",

  "total": { /* sum of daily totals in that month */ },
  "rollup": [ /* merged rollup across all days */ ],
  "event_count": 1234,
  "user_ids": ["admin-user-1", "..."],
  "aggregated_at": "...",

  "days_covered": 29,
  "days_missing": 1,
  "days_in_month": 30
}
```

**Dimensions:**

* Time: **month**
* Service breakdown: `(service, provider, model)`
* Coverage metadata: which days in the month contributed.

---

#### Yearly totals

**Path:**

```text
analytics/<tenant>/<project>/accounting/yearly/<YYYY>/total.json
```

**Shape:**

```json
{
  "tenant_id": "...",
  "project_id": "...",
  "level": "yearly",
  "year": 2025,
  "month": null,
  "day": null,
  "hour": null,
  "bucket_start": "2025-01-01T00:00:00Z",
  "bucket_end": "2026-01-01T00:00:00Z",

  "total": { /* sum of monthly totals in that year */ },
  "rollup": [ /* merged monthly rollup */ ],
  "event_count": 9999,
  "user_ids": ["..."],
  "aggregated_at": "...",

  "months_covered": 10,
  "months_missing": 2
}
```

**Dimensions:**

* Time: **year**
* Service breakdown: `(service, provider, model)`
* Coverage metadata: which months contributed.

> Note: currently, the scheduler focuses on daily + monthly; yearly aggregates are primarily built by the CLI helper in `aggregator.py` (`_run_cli`) and can be wired into a periodic job if needed.

---

## 2. How aggregates are computed

### 2.1 Aggregation engine

Core logic lives in:

* `kdcube_ai_app.infra.accounting.aggregator.AccountingAggregator`

Key methods:

* `aggregate_daily_for_project(...)`

    * Reads raw events for a specific date.
    * Writes:

        * daily total: `daily/YYYY/MM/DD/total.json`
        * daily per-user: `daily/YYYY/MM/DD/users.json`
        * daily per-agent: `daily/YYYY/MM/DD/agents.json`
        * hourly totals: `hourly/YYYY/MM/DD/HH/total.json` (for hours that have at least one event)

* `aggregate_daily_range_for_project(...)`

    * Loops from `date_from` to `date_to` (inclusive).
    * For each day:

        * Checks if `daily/.../total.json` exists.
        * If `skip_existing=True` and `total.json` already exists → **skips** that day entirely
          (so it *does not* recompute `users.json` / `agents.json` for that day unless you change this logic).

* `aggregate_monthly_from_daily(...)`

    * Reads `daily/.../total.json` for each day in the month.
    * Sums them into `monthly/YYYY/MM/total.json`.
    * Tracks `days_covered` / `days_missing`.

* `aggregate_yearly_from_monthly(...)`

    * Reads `monthly/.../total.json` for each month.
    * Sums them into `yearly/YYYY/total.json`.
    * Tracks `months_covered` / `months_missing`.

---

### 2.2 Scheduler (nightly aggregates)

The scheduler logic is implemented in:

* `kdcube_ai_app.apps.chat.api.opex.routines`

It is started via the OPEX router lifespan:

```python
router = APIRouter(lifespan=opex_lifespan)
```

**High-level behavior:**

* Uses `ACCOUNTING_TZ = Europe/Berlin`.
* Runs once per day (driven by `OPEX_AGG_CRON`, e.g. `0 3 * * *`).
* On each run (at ~03:00 Berlin time), it:

    1. Computes `run_date = yesterday` (Berlin local date).
    2. For that `run_date`, triggers:

        * daily aggregation (`aggregate_daily_range_for_project` with `date_from=date_to=run_date`)
        * monthly aggregation for the corresponding month (`aggregate_monthly_from_daily`).

**Tenant / project scope:**

* Controlled by env vars:

    * `DEFAULT_TENANT` (default `"home"`)
    * `DEFAULT_PROJECT_NAME` (default `"demo"`)

All scheduled aggregates are for this single `(tenant, project)` pair.

---

### 2.3 Redis-based locking

To avoid duplicate work across multiple API instances, aggregator runs use a **Redis lock**:

* `REDIS_URL` points to the Redis instance (e.g. `redis://redis:6379/0`).

* Lock key pattern:

  ```text
  acct:agg:{tenant}:{project}:{YYYY-MM-DD}
  ```

* Lock TTL is **4 hours**.

* If a lock is already held:

    * The current instance logs and **skips** aggregation for that date.

If `REDIS_URL` is unset:

* Aggregations still run, but **without cross-instance protection**.

---

### 2.4 Manual backfill

Admin endpoint:

```http
POST /accounting/opex/admin/run-aggregation-range
```

**Query params:**

* `start_date` – required, `YYYY-MM-DD`
* `end_date` – optional, `YYYY-MM-DD` (inclusive)

    * If omitted: defaults to **yesterday** in `Europe/Berlin`.

**Behavior:**

* Validates `start_date` / `end_date` (must satisfy `end >= start`).

* Calls:

  ```python
  await routines.run_aggregation_range(start, end)
  ```

* `run_aggregation_range`:

    * Iterates day-by-day.
    * For each date, does the same work as the scheduler (daily + monthly).
    * Uses the same Redis locks (safe across multiple instances, safe to re-run).

**Examples:**

Backfill from a date to yesterday:

```bash
curl -X POST \
  "http://localhost:8010/accounting/opex/admin/run-aggregation-range?start_date=2025-01-01"
```

Backfill an explicit range:

```bash
curl -X POST \
  "http://localhost:8010/accounting/opex/admin/run-aggregation-range?start_date=2025-01-01&end_date=2025-01-10"
```

Response:

```json
{
  "status": "ok",
  "start_date": "2025-01-01",
  "end_date": "2025-01-10",
  "message": "Aggregation triggered for date range"
}
```

---

## 3. Queries that leverage aggregates

Aggregates are consumed by `RateCalculator` in `infra/accounting/calculator.py`.

Below is a summary of **which methods use aggregates** vs raw scanning.

### 3.1 Global totals – `usage_all_users`

```python
async def usage_all_users(...):
    # uses _usage_all_users_with_aggregates(...) when possible
```

**Uses aggregates when:**

* `app_bundle_id is None`
* `service_types is None` or empty
* `hard_file_limit is None`

In this case:

* `_usage_all_users_with_aggregates`:

    * Checks for presence of **daily total** files:

        * `analytics/.../daily/YYYY/MM/DD/total.json`
    * Splits the requested date range into segments of:

        * days **with** daily aggregates
        * days **without** daily aggregates
    * For each segment:

        * **Aggregated segment**: reads daily totals and merges them.
        * **Non-aggregated segment**: falls back to a *bounded* raw scan only for that subrange.

So for a generic date range `[date_from, date_to]`:

* You always get a correct total.
* Wherever daily aggregates exist, they are used.
* Gaps are patched via raw scanning, but **only for gap days**.

**If `require_aggregates=True`:**

* `usage_all_users` will **not** fall back to the old “full raw scan” path if the aggregate-aware helper fails.
* Note: it still uses raw for gap segments *inside* `_usage_all_users_with_aggregates`. That’s intentional.

**Backed by aggregates:**

* `daily/.../total.json` only (monthly/yearly are not yet used here).

---

### 3.2 Per-user totals – `usage_by_user`

```python
async def usage_by_user(...):
    # tries _usage_by_user_with_aggregates(...)
```

**Aggregate path used when:**

* `app_bundle_id is None`
* `service_types` is `None` or empty
* `hard_file_limit is None`

In this case:

* `_usage_by_user_with_aggregates`:

    * For every day in `[date_from, date_to]`, expects:

        * `analytics/.../daily/YYYY/MM/DD/users.json`
    * If **any** day is missing `users.json`:

        * Returns `None` → caller falls back to raw scan.
    * Otherwise:

        * Sums per-user totals and rollups across all days.
        * Returns a map:

            * `user_id -> { total, rollup, event_count }`.

**Backed by aggregates:**

* `daily/.../users.json`.

If any day in the requested range does not have per-user aggregates, the whole call falls back to the raw-scan implementation (which is still optimized by filename prefixes, but not aggregate-based).

---

### 3.3 Per-agent totals – `usage_by_agent`

```python
async def usage_by_agent(...):
    # tries _usage_by_agent_with_aggregates(...)
```

**Aggregate path used when:**

* Global scope:

    * `user_id is None`
    * `conversation_id is None`
    * `turn_id is None`
* And:

    * `app_bundle_id is None`
    * `service_types` is `None` or empty
    * `hard_file_limit is None`

In this case:

* `_usage_by_agent_with_aggregates`:

    * For every day in `[date_from, date_to]`, expects:

        * `analytics/.../daily/YYYY/MM/DD/agents.json`
    * If any day missing → returns `None` → caller falls back to raw.
    * Otherwise:

        * Sums per-agent totals and rollups across all days.
        * Returns a map:

            * `agent_name -> { total, rollup, event_count }`.

**Backed by aggregates:**

* `daily/.../agents.json`.

---

### 3.4 Conversation / turn / time-series queries

The following are **raw-based only** (no aggregates used):

* `usage_user_conversation(...)` (per-conversation)
* `query_turn_usage(...)` (per-turn)
* `turn_usage_rollup_compact(...)`
* `turn_usage_by_agent(...)`
* `time_series(...)`
* `rate_stats(...)`

However, they are highly optimized:

* Use the **conversation-based filename structure**:

  ```text
  cb|<user_id>|<conversation_id>|<turn_id>|<agent_name>|<timestamp>.json
  ```

* Build efficient prefixes like:

  ```text
  cb|user-123|
  cb|user-123|conv-xxx|
  cb|user-123|conv-xxx|turn-001|
  cb|user-123|conv-xxx|turn-001|answer_generator|
  ```

So even though they’re raw-based, they do **not** blindly scan the entire bucket.

---

### 3.5 OPEX HTTP endpoints

In `apps/chat/api/opex/opex.py`:

* `/total`

    * Calls `calc.usage_all_users(..., require_aggregates=True)`
    * Uses daily totals + raw gap-filling as described above.
    * Adds cost estimates via `_compute_cost_estimate` (based on `price_table()`).

* `/users`

    * Intended to call `calc.usage_by_user(...)` and compute per-user cost.
    * When re-enabled, will automatically use daily `users.json` aggregates when available.

* `/agents`

    * Intended to call `calc.usage_by_agent(...)`, using daily `agents.json` where available.

* `/conversation`, `/turn`, `/turn/by-agent`

    * Use the raw, prefix-optimized methods on `RateCalculator`.
    * Do *not* depend on daily/monthly/yearly aggregates.

---

## 4. Budget-style questions: “remaining budget” for the app vs per-service

> **Question:**
> Can I use our API to decide, at any point in time, *“what is the remaining budget of the entire application”* (vs per-service budget)?

**Short answer:**
Yes, the OPEX API gives you **accurate usage and cost so far**; you can derive “remaining budget” in your own logic by subtracting from whatever budgets you define. The accounting layer itself does **not** store or enforce budgets; it just meters and prices.

### 4.1 Global “entire application” budget

To compute “how much of the overall budget is already spent” for a billing period (e.g. monthly):

1. Decide your billing window, e.g.:

    * `date_from = first day of current month`
    * `date_to = today` (or yesterday, depending on how you want to handle partial days).

2. Call:

   ```http
   GET /accounting/opex/total
       ?tenant=<tenant>
       &project=<project>
       &date_from=2025-11-01
       &date_to=2025-11-21
   ```

3. The response includes:

   ```json
   {
     "total": { ... },
     "rollup": [ ... per (service, provider, model) ... ],
     "user_count": ...,
     "event_count": ...,
     "cost_estimate": {
       "total_cost_usd": 123.45,
       "breakdown": [
         { "service": "llm", "provider": "anthropic", "model": "claude-sonnet-...", "cost_usd": 78.90 },
         { "service": "embedding", "provider": "openai", "model": "text-embedding-3-small", "cost_usd": 44.55 }
       ]
     }
   }
   ```

4. If your **global budget** is, say, `200 USD` per month, you can compute:

   ```text
   remaining_global_budget = 200.0 - cost_estimate.total_cost_usd
   ```

This works because `/total` already aggregates across **all services** and prices them via the shared `price_table()`.

> Note: for dates that already have daily aggregates, this is aggregate-backed; for today or gap days it uses raw events for those days only, but the result is still a consistent “spent so far”.

---

### 4.2 Per-service (LLM vs embedding vs …) budgets

If you want **separate budgets per service** (e.g. LLM vs embedding):

* Use the same `/total` endpoint.
* Look at `cost_estimate.breakdown`, which is per `(service, provider, model)`.

You can aggregate in your own logic:

* “LLM budget” → sum all rows where `"service": "llm"`.
* “Embedding budget” → sum all `"service": "embedding"` rows.
* Or per provider / per model if you have more granular budgets.

Then:

```text
remaining_llm_budget = llm_budget_cap_usd - llm_cost_so_far_usd
remaining_embedding_budget = emb_budget_cap_usd - emb_cost_so_far_usd
...
```

Again, the OPEX layer just tells you **how much you’ve spent per dimension**; you decide the caps and what to do when you’re close or over (alert, rate-limit, block, etc.).

---

### 4.3 What the system does *not* do (by design)

* It does **not** store:

    * budget caps,
    * per-tenant or per-app budget configs,
    * or “remaining budget” counters.
* It does **not** enforce:

    * hard budget limits,
    * throttling or blocking when a budget is exceeded.

All of that is meant to be implemented by:

* the calling application / control plane, using:

    * `/total` for global spend,
    * `/users` / `/agents` (once re-enabled) for per-user/agent spend,
    * and the cost estimate breakdowns.

The current API is entirely suitable as the **metering + pricing** foundation for both:

* “How much has the whole app spent this month?” and
* “How much has each service spent vs its budget?”

…as long as you layer your own budget config and simple arithmetic on top.
