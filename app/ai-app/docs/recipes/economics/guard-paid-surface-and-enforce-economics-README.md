---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/economics/guard-paid-surface-and-enforce-economics-README.md
title: "Guard a Paid Surface and Enforce Economics"
summary: "Verify quota and funding, reserve, run, and settle a paid service call on any application surface — chat turn, standalone UI/API, background job — using the economics enforcement engine and the search model-service facade."
status: current
tags: ["recipe", "economics", "enforcement", "guard", "accounting", "search"]
updated_at: 2026-07-13
keywords:
  [
    "guard paid surface",
    "enforce economics",
    "EconomicsGuard",
    "economic_preflight",
    "EconomicsSubject",
    "search_model_service",
    "embed_search_query",
    "reservation",
    "settlement",
    "degrade to lexical",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-enforcement-engine-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/economics/tracked-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/accounting/accounting-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economics-events-README.md
---

# Recipe: Guard a Paid Surface and Enforce Economics

Use this recipe when an application runs a **paid service** — an LLM call, an
embedding, a web search, or a [self-tracked service](./tracked-service-README.md)
of your own — and you want the platform to decide, **before the call runs**,
whether this user can afford it, to reserve the estimated cost, and to settle the
actual cost afterward.

A *paid surface* is any place that reaches a paid service: a chat turn, a
standalone search box, a REST operation, a background job, a cron routine. The
economics model (roles, plans, funding split, reservations, settlement) is the
same on every one of them; this recipe shows how to put a paid call under that
model without reimplementing any of it.

The chat entrypoint already does this for chat turns. Everything below is for the
**other** surfaces — accountable work that runs on a user's behalf without a chat
turn — and for nested paid calls inside a turn.

> Accounting answers *what did this cost?* Enforcement answers *may this run, and
> who pays?* This recipe is about enforcement. To make a **new** service type
> emit usage that enforcement can price, see the companion recipe,
> [Implement a Self-Tracked Service](./tracked-service-README.md).

## 1. Be an economics-enabled application

The engine reuses the runtime primitives an economics-enabled entrypoint already
owns (`cp_manager`, `rl`, `budget_limiter`, `run_accounting`, `comm`, `logger`).
Extend the economics base:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import (
    BaseEntrypointWithEconomics,
)


class MyEntrypoint(BaseEntrypointWithEconomics):
    ...
```

`BaseEntrypointWithEconomics` binds `cp_manager`, `rl`, and `budget_limiter` when
Redis and/or PostgreSQL are configured. When none are present, the runtime has no
economics and paid calls should run unmetered exactly as before — so guard code
must always tolerate a runtime **without** economics rather than hard-require it.

## 2. Pick the entry point by who settles the cost

The engine (`kdcube_ai_app/apps/chat/sdk/infra/economics/enforcement.py`) offers
two entry points. Both resolve role → plan → funding and raise
`EconomicsLimitException` when the flow is not feasible. They differ in whether
they reserve and settle:

| Entry point | Verifies | Reserves | Settles | Use when |
| --- | :---: | :---: | :---: | --- |
| `EconomicsGuard` | ✅ | ✅ | ✅ | the accounted work runs *inside* the guard and you want it metered and charged here |
| `economic_preflight` | ✅ | — | — | you only need a feasibility gate — the cost is metered elsewhere (e.g. the parent chat turn), or the caller degrades gracefully on denial |

## 3. Resolve the economics subject (who pays)

Every flow carries an `EconomicsSubject` — the resolved identity funding is
charged to. It is **not** a `user_type` lane; funding, subscription, and plan
access come from economics state and explicit authority fields.

```python
from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import EconomicsSubject

subject = EconomicsSubject(
    tenant="acme",
    project="main",
    user_id="u-123",
    roles=("kdcube:role:registered",),
    permissions=(),
    budget_bypass=None,          # let economics decide unless authority says otherwise
    is_anonymous=False,
    timezone="Europe/Kyiv",      # optional; anchors quota periods where configured
)
```

For linked or delegated identities — a channel actor, a delegated external
client, a background job — project the subject from the carried authority context
instead of hand-building it, so roles, permissions, and any explicit budget
bypass travel with the request:

```python
from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
    economics_subject_from_authority_context,
)

subject = await economics_subject_from_authority_context(
    tenant=tenant,
    project=project,
    identity_authority=bundle_call_context.get("identity_authority"),
    actor_user_id="telegram_100200300",
    fallback_user_id="telegram_100200300",
    timezone="Europe/Kyiv",
)
```

An actor verified only by an external integration, with no platform or grantor
projection, carries the runtime label `external` and has **no** platform
economics subject: it may do low-authority channel work but cannot consume
registered/free platform quota or admin bypass.

## 4. Guard a standalone paid flow (verify → reserve → run → settle)

`EconomicsGuard` is an async context manager around one accountable flow. On
enter it resolves plan and funding, admits against quota, reserves the estimate,
and binds accounting to the flow's `scope_id`. On exit it aggregates the flow's
accounting events for that `scope_id` and settles the actual cost — committing or
releasing the reservations. It never suppresses exceptions.

```python
from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
    EconomicsGuard, EconomicsEstimate, FlowPolicy,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException

try:
    async with EconomicsGuard(
        self,                                   # the economics-enabled entrypoint
        subject=subject,
        scope_id="report_render_42",            # stable, unique accountable request id
        flow="reports.render",                  # label for logs / events / lineage
        estimate=EconomicsEstimate(reservation_usd=0.05),
        policy=FlowPolicy(enforce_concurrency=False, emit_user_events=False),
    ) as decision:
        result = await do_the_paid_work()       # accounted model/service calls run here
except EconomicsLimitException as exc:
    # Not feasible — nothing ran, no hold left behind. Inspect exc.code / exc.data.
    return degraded_response(exc)
```

Inside the block, run your paid work through a tracked service so the guard has
usage to settle. Any `@track_llm` / `@track_embedding` / `@track_web_search` call
— or your own [self-tracked service](./tracked-service-README.md) — that runs
here emits into the accounting context the guard bound under `scope_id`; on exit
the guard reads those events back and settles the real cost.

The `scope_id` is the flow's **accountable request id**: pick a stable, unique,
self-describing value (`<flow>_<entity_id>`). Reservations, ledger entries, and
spend are recorded under it and are readable with
`GET /economics/request-lineage?request_id=<scope_id>`.

`EconomicsEstimate` sizes the reservation. The primary lever is
`reservation_usd`; most non-chat flows cost pennies, so a small fixed USD figure
is right. When you cannot estimate USD directly, drive it from tokens with
`input_text` / `output_budget_tokens` (floored by `min_tokens`, default 500):

```python
@dataclass
class EconomicsEstimate:
    reservation_usd: Optional[float] = None
    input_text: Optional[str] = None
    output_budget_tokens: Optional[int] = None
    min_tokens: int = 500
```

`FlowPolicy` carries the per-flow knobs — the defaults suit background flows:

| Field | Default | Meaning |
| --- | --- | --- |
| `enforce_concurrency` | `False` | take a concurrency slot (chat-only; keep off for background flows) |
| `reservation_ttl_sec` | `900` | reservation hold lifetime |
| `lock_ttl_sec` | `180` | admit lock lifetime |
| `emit_user_events` | `False` | emit `rate_limit.*` SSE events on denial (needs a `comm` channel) |
| `enforce_quota_lock` | `False` | serialize the admit→reserve window per user with a distributed lock |
| `quota_lock_wait_sec` | `5.0` | how long to wait for that lock before denying as `quota_lock_timeout` |

The block yields an `EconomicsDecision` carrying the resolved `plan_id`,
`funding_source` (`subscription` / `project` / `wallet` / `none`),
`funding_available_usd`, `est_turn_usd`, `budget_bypass`, `nested`, and
`scope_id` — useful for logging the decision that was made.

## 5. Gate only, then meter elsewhere (`economic_preflight`)

When you only need to decide whether a flow may start — and the cost is metered
by something else, or the caller degrades gracefully on denial — use
`economic_preflight`. It runs the same admit and funding resolution with **no
reservation and no settlement**.

```python
from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
    economic_preflight, EconomicsEstimate, FlowPolicy,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException

try:
    decision = await economic_preflight(
        self,
        subject=subject,
        estimate=EconomicsEstimate(reservation_usd=0.01),
        flow="reports.preview",
        policy=FlowPolicy(enforce_concurrency=False, emit_user_events=False),
    )
except EconomicsLimitException:
    return use_cheaper_path()      # skip the expensive call, serve a lighter result
```

`economic_preflight` takes no reservation, so it ignores `enforce_quota_lock`.

## 6. Searchable components: use the model-service facade

Semantic search is the most common paid surface, and it has a purpose-built
facade so components never touch the guard directly. An economics-enabled
entrypoint exposes it:

```python
model_service = self.search_model_service(flow="reports.search", subject=subject)
```

The facade (`EconomicSearchModelService`) gives a component **one** dependency
with two methods that differ deliberately in how they fail:

- `await model_service.embed_texts([...])` — document/index embeddings.
  Exceptions **propagate**: write-side index correctness must not silently skip
  material.
- `await model_service.embed_search_query(query, flow="reports.search")` — the
  query embedding, wrapped in `EconomicsGuard`. It returns **`None`** on
  economics denial so the query caller can **degrade to lexical search** instead
  of failing the request.

A searchable component reads the facade and downgrades on `None`:

```python
async def _embed_query(self, query: str) -> list[float] | None:
    embed_search_query = getattr(self.model_service, "embed_search_query", None)
    if callable(embed_search_query):
        vec = await embed_search_query(query, flow="reports.search")
        if vec is None:
            return None                # economics denied → caller falls back to lexical
        return vec
    # Runtime without the facade: embed directly (unmetered path).
    embed_texts = getattr(self.model_service, "embed_texts", None)
    return (await embed_texts([query]))[0] if callable(embed_texts) else None
```

The caller treats `None` as "semantic unavailable" and ranks lexically (BM25 /
keyword), so search **never hard-fails on economics** — it just gets cheaper.
Concrete platform examples degrade exactly this way: memory search
(`flow="memory.search"`), canvas pin search (`flow="canvas.pins.search"`), and
task search (`flow="task_tracker.issue.search"`).

A user-scoped search surface wires the subject and downgrade together:

```python
async def _search_embed_or_downgrade(self, query: str, subject) -> list[float] | None:
    from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
    normalized = str(query or "").strip()
    if not normalized:
        return None
    try:
        svc = self.search_model_service(flow="reports.search", subject=subject)
        embed_query = getattr(svc, "embed_search_query", None)
        if callable(embed_query):
            return await embed_query(normalized, flow="reports.search")
    except EconomicsLimitException:
        return None                    # degrade to keyword / BM25
    except Exception:
        return None                    # metered embedder unavailable → degrade
    return None
```

## 7. Nested inside a chat turn: guards degrade to verify-only

`BaseEntrypointWithEconomics.run(...)` marks the chat turn as the **active parent
economics scope** while it executes the bundle core. Any `EconomicsGuard` (or the
search facade) entered *inside* that scope automatically degrades to
**verify-only**: it checks feasibility but leaves the tracked usage event in the
parent turn, and the turn settles it. This is what keeps a search embedded in a
chat turn from being charged twice.

You do not wire this — it is automatic through a context variable the parent scope
binds. The rule to remember: **the same work is never settled twice.** A guard
outside any active parent scope creates and settles its own operation scope (for
example `memory_search_<id>`); the same guard inside a turn only verifies, and the
`EconomicsDecision.nested` flag reports which happened.

## 8. Background and detached flows

A job that runs later on a worker has no live request context, so persist the
`identity_authority` snapshot in the job envelope at enqueue time and restore it
into the subject when the job runs — privileged/admin bypass must come from that
authority projection, never reconstructed from the dispatch queue. Reserving
flows should turn on the quota lock to close the read-remaining-quota → reserve
race between concurrent requests of the same user:

```python
guard = EconomicsGuard(
    self,
    subject=subject,
    scope_id=f"reconcile_{job_id}",
    flow="reports.reconcile",
    estimate=EconomicsEstimate(reservation_usd=0.02),
    policy=FlowPolicy(enforce_concurrency=False, enforce_quota_lock=True),
)

decision = None
try:
    try:
        decision = await guard.__aenter__()      # verify, reserve, bind accounting
    except EconomicsLimitException as exc:
        await mark_denied(job, exc)              # nothing ran; record and stop
        return
    await do_the_paid_work()
finally:
    if decision is not None:
        await guard.__aexit__(None, None, None)  # run accounting, settle, release
```

Prefer `async with` when the whole flow fits one block; the explicit
`__aenter__` / `__aexit__` form above is for job runners that separate admission
from settlement. `enforce_quota_lock=True` is a no-op when Redis is unavailable,
so it is safe to leave on.

## 9. Handle denial

`EconomicsLimitException` is raised **before any work runs** — there is no hold to
clean up. Its `code` tells you why and `data` carries the snapshot:

```python
class EconomicsLimitException(RuntimeError):
    def __init__(self, message: str, *, code: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.data = data or {}
```

| `code` | Meaning |
| --- | --- |
| `rate_limited` | quota exhausted (requests/tokens/concurrency) |
| `no_funding_source` | no eligible funding for this subject (e.g. anonymous with `funding_source = "none"`) |
| `quota_lock_timeout` | the per-user admit→reserve lock was contended past `quota_lock_wait_sec` |
| `<funding_source>_reservation_failed` | the funding hold could not be placed (e.g. `project_reservation_failed`) |
| `subscription_unavailable` | subscription funding was expected but is not available |

Choose the reaction that fits the surface: a query layer returns `None` and ranks
lexically; a preflight gate serves a cheaper result; a foreground operation
surfaces the message; a background job records the denial and stops. When
`emit_user_events=True` and a `comm` channel is present, denials also emit the
`rate_limit.*` SSE events (see
[economics-events-README.md](../../economics/economics-events-README.md)) for a
chat-style client to render.

## 10. What the economics endpoints are (and are not)

The `/economics/*` REST endpoints — `me/budget-breakdown`, `request-lineage`, and
the admin surfaces — **read and manage** economics state (quota meters, budgets,
subscriptions, lineage). They are gated by session/role
(`require_auth(RequireUser())` and admin roles). They do **not** enforce paid
work: enforcement happens at the service call site through the guard engine in
this recipe. Use the endpoints to observe and administer; use the guard to
decide.

## Diagnostics

Every guard and facade path emits a centralized runtime log with the marker
`[economics.enforcement]`, tagged with `flow`, `scope_id`, `subject_id`,
tenant/project, and stage (`plan_resolved`, `admit`, `reserve_ok`,
`accounting_bound`, `settle`, `deny`, `deny_cleanup`). Grep that marker with a
flow name to watch a surface admit, reserve, settle, deny, or fall back:

```bash
grep '\[economics.enforcement\]' <proc-logs> | grep 'reports.search'
```

Then trace the money for one flow:

```text
GET /economics/request-lineage?request_id=<scope_id>
```

It returns the ledger and reservation rows recorded under that `scope_id`. For a
single user's quota state, run the diagnostics script
`kdcube_ai_app/apps/chat/sdk/infra/economics/profile_user_economics.py` inside a
container that shares the runtime's Redis and PostgreSQL.

## Related Documentation

- [Economics Enforcement Engine](../../economics/economic-enforcement-engine-README.md) — the authoritative engine reference (contracts, stages, behavior notes).
- [Economics Model](../../economics/economic-README.md) — roles, plans, funding split, reservation and settlement semantics.
- [Implement a Self-Tracked Service](./tracked-service-README.md) — define a new accountable service type so enforcement has usage to price.
- [Accounting & Usage Tracking](../../accounting/accounting-README.md) — how usage events are captured, stored, and priced per turn.
- [Economics Rate-Limit SSE Events](../../economics/economics-events-README.md) — the `rate_limit.*` payloads emitted on denial when user events are on.
