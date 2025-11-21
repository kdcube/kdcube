# OPEX Aggregations

This module provides **daily and monthly usage aggregates** for the accounting API.
Aggregations are:

* computed **automatically on a schedule** (via a background task), and
* can be **backfilled manually** for arbitrary date ranges via an admin endpoint.

The aggregates are used by `/accounting/opex` endpoints (e.g. `/total`) to avoid rescanning raw logs on every request.

---

## 1. What runs on a scheduled basis?

The scheduler lives in:

* `kdcube_ai_app.apps.chat.api.opex.routines`
* `aggregation_scheduler_loop()`

It is started via the OPEX router lifespan:

```python
router = APIRouter(lifespan=opex_lifespan)
```

### Schedule

* The loop is time-zone aware and uses `Europe/Berlin` (`ACCOUNTING_TZ`).
* It runs according to aggregation schedule defined by `OPEX_AGG_CRON` env var. 
* At each run it computes aggregates for **yesterday** (Berlin date).

Roughly, the loop does this forever:

1. Compute the next `03:00` in `Europe/Berlin`.
2. `asyncio.sleep()` until then.
3. Determine `run_date = yesterday` in Berlin time.
4. Call:

   ```python
   await _run_daily_and_monthly_for_date(run_date)
   ```

### What exactly is computed?

For each `run_date`:

1. **Daily aggregate** (for that exact date only):

   ```python
   await agg.aggregate_daily_range_for_project(
       tenant_id=tenant,
       project_id=project,
       date_from=date_str,
       date_to=date_str,
       skip_existing=True,  # safe to re-run
   )
   ```

2. **Monthly aggregate** for that date’s month:

   ```python
   await agg.aggregate_monthly_from_daily(
       tenant_id=tenant,
       project_id=project,
       year=run_date.year,
       month=run_date.month,
       require_full_coverage=False,
   )
   ```

So:

* Each nightly run:

    * **recomputes the month** corresponding to `run_date` (based on all daily buckets)
    * **adds daily aggregates** for `run_date` (or skips if already present, due to `skip_existing=True`)

### Tenant and project scope

The scheduler uses environment variables:

* `DEFAULT_TENANT` (defaults to `"home"`)
* `DEFAULT_PROJECT_NAME` (defaults to `"demo"`)

All scheduled aggregation is done for this single `(tenant, project)` pair.

### Distributed locking (Redis)

To avoid multiple API instances doing the same work, the job uses a **Redis-based lock**:

* Redis is configured via `REDIS_URL` (e.g. `redis://redis:6379/0`).

* Lock key format:

  ```text
  acct:agg:{tenant}:{project}:{run_date_iso}
  ```

* The lock is held for up to **4 hours** (`lock_ttl_seconds = 4 * 3600`).

* If another instance holds the lock, the current instance logs and **skips** aggregation for that date.

If `REDIS_URL` is not set:

* Aggregation still runs, but **without distributed locking** (each instance will attempt to aggregate).

---

## 2. How to trigger aggregation for selected dates (backfill)

For backfilling or manual runs, there is an admin endpoint:

```http
POST /accounting/opex/admin/run-aggregation-range
```

### Parameters

* `start_date` (required): `YYYY-MM-DD`
* `end_date` (optional): `YYYY-MM-DD`, inclusive

Behaviour:

* If `end_date` is omitted, it defaults to **yesterday** in `Europe/Berlin`.
* If `end_date < start_date`, the API returns `400`.

Example signatures (as implemented):

```python
@router.post("/admin/run-aggregation-range")
async def admin_run_aggregation_range(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(
        None,
        description="End date (YYYY-MM-DD, inclusive). "
                    "If omitted, defaults to yesterday in Europe/Berlin."
    ),
    session: UserSession = Depends(auth_without_pressure())
):
    ...
    await routines.run_aggregation_range(start, end)
```

The helper `run_aggregation_range(start, end)` (in `routines`) will:

* Iterate date-by-date from `start` to `end` (inclusive)
* For each date call `_run_daily_and_monthly_for_date(run_date)`
* Use the **same Redis locking** as the scheduler, so:

    * Safe to call on multiple instances at once
    * Safe to rerun the same range (daily uses `skip_existing=True`)

### Example usage

Backfill from a specific start date up to yesterday:

```bash
curl -X POST \
  "http://localhost:8010/accounting/opex/admin/run-aggregation-range?start_date=2025-01-01"
```

Backfill a specific closed range:

```bash
curl -X POST \
  "http://localhost:8010/accounting/opex/admin/run-aggregation-range?start_date=2025-01-01&end_date=2025-01-10"
```

Expected response:

```json
{
  "status": "ok",
  "start_date": "2025-01-01",
  "end_date": "2025-01-10",
  "message": "Aggregation triggered for date range"
}
```

---

## 3. Configuration summary

Environment variables used by the aggregation system:

* `STORAGE_PATH`
  Path/URL for the underlying storage backend (`AccountingAggregator` + `RateCalculator`).

* `DEFAULT_TENANT`
  Tenant ID for scheduled aggregation (default: `"home"`).

* `DEFAULT_PROJECT_NAME`
  Project ID for scheduled aggregation (default: `"demo"`).

* `REDIS_URL`
  Redis connection URL for distributed locks.
  If unset, aggregation still runs, but **without** cross-instance locking.

* Aggregation schedule `OPEX_AGG_CRON`.
  I.e. OPEX_AGG_CRON="0 3 * * *"

* (Logging) `LOG_LEVEL`, etc.
  Normal logging config – useful to tune verbosity of `OPEX.Routines` and related modules.

