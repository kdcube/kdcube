---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/economics/tracked-service-README.md
title: "Implement a Self-Tracked Service"
summary: "Make an application's own paid operation emit accountable usage — a decorator, four extractors, and a ServiceUsage — so its cost is recorded, priced, and available for economics settlement alongside LLM, embedding, and web-search usage."
status: current
tags: ["recipe", "economics", "accounting", "usage", "tracking"]
updated_at: 2026-07-13
keywords:
  [
    "self tracked service",
    "accounted service",
    "AccountingTracker",
    "track_llm",
    "ServiceUsage",
    "cost_usd",
    "price table",
    "with_accounting",
    "service type",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/accounting/accounting-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/economics/guard-paid-surface-and-enforce-economics-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-enforcement-engine-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/economics-descriptor-README.md
---

# Recipe: Implement a Self-Tracked Service

Use this recipe when an application does paid work of its own — calls a paid
external API, runs a metered model, performs an expensive operation — and you want
that work to show up as **usage**: an accountable event with a provider, a unit
count, and a dollar cost, recorded the same way the platform records LLM,
embedding, and web-search calls.

Three layers stack here, and it helps to keep them apart:

| Layer | Question | Mechanism |
| --- | --- | --- |
| **Tracking** | *what did this call use?* | a tracker decorator emits an `AccountingEvent` |
| **Pricing** | *what did that usage cost?* | the price table (or a self-reported `cost_usd`) turns usage into USD |
| **Enforcement** | *may it run, and who pays?* | the guard reserves and settles that cost — see the [guard recipe](./guard-paid-surface-and-enforce-economics-README.md) |

This recipe covers the first two. It is the "define the service" half; the
["guard a paid surface"](./guard-paid-surface-and-enforce-economics-README.md)
recipe is the "run it under a budget" half.

## 1. The anatomy of a tracked call

A tracked call is an ordinary function wearing a tracker decorator. When the
function runs inside a bound accounting context, the decorator wraps it, reads
four **extractors** off the result and arguments, builds a `ServiceUsage`, and
writes one `AccountingEvent`. The core primitive is `AccountingTracker`; the
built-ins (`track_llm`, `track_embedding`, `track_web_search`) are thin factories
over it that fix the `service_type`.

```python
class AccountingTracker:
    def __init__(self,
                 service_type: ServiceType,
                 provider_extractor=None,   # (result, *args, **kwargs) -> str
                 model_extractor=None,      # (result, *args, **kwargs) -> str
                 usage_extractor=None,      # (result, *args, **kwargs) -> ServiceUsage
                 metadata_extractor=None):  # (result, *args, **kwargs) -> dict
        ...
```

Two facts shape everything below:

- **The event is emitted automatically, but only when accounting storage is
  bound.** Outside a bound context the decorator is a transparent no-op — the
  function just runs. A chat turn binds accounting in the processor; a standalone
  or background flow binds it through `EconomicsGuard` (the `accounting_bound`
  stage). So a tracked call and a guard compose: the guard binds the scope, the
  tracked call emits into it, the guard settles it.
- **The event carries a context snapshot** taken from the async-local
  `AccountingContext`, so who/where/which-turn is attached without threading it
  through call arguments.

## 2. Reuse a built-in tracker when the shape fits

If your paid call is genuinely an LLM, an embedding, or a web search to some
provider, do not invent a new type — decorate with the matching built-in and
supply extractors. This is the whole surface:

```python
from typing import Annotated
from kdcube_ai_app.infra.accounting import track_embedding, ServiceUsage


def _emb_provider(result, *args, **kwargs) -> str:
    return kwargs.get("provider", "acme-embeddings")


def _emb_model(result, *args, **kwargs) -> str:
    return kwargs.get("model", "acme-embed-v1")


def _emb_usage(result, *args, **kwargs) -> ServiceUsage:
    # `result` is your provider's response; count the units it actually billed.
    return ServiceUsage(
        embedding_tokens=int(result.get("tokens", 0)),
        embedding_dimensions=int(result.get("dims", 0)),
        requests=1,
    )


@track_embedding(
    provider_extractor=_emb_provider,
    model_extractor=_emb_model,
    usage_extractor=_emb_usage,
)
async def embed_with_acme(text: str, *, provider="acme-embeddings", model="acme-embed-v1") -> dict:
    return await call_acme_embeddings(text, model=model)
```

## 3. Define your own tracker for a new service type

When the work is not one of the built-in shapes, make a thin factory over
`AccountingTracker` and pick the `service_type` closest to what it is. The
`ServiceType` enum is fixed — `llm`, `embedding`, `web_search`,
`image_generation`, `speech_to_text`, `text_to_speech`, `vision`, `other` — so
choose the nearest, or `other`:

```python
from typing import Any, Dict
from kdcube_ai_app.infra.accounting import AccountingTracker, ServiceType, ServiceUsage


def track_document_ocr(provider_extractor=None, model_extractor=None,
                       usage_extractor=None, metadata_extractor=None):
    """Tracker for a paid document-OCR service."""
    return AccountingTracker(
        ServiceType.VISION,          # the nearest fixed type; use OTHER if none fits
        provider_extractor, model_extractor,
        usage_extractor, metadata_extractor,
    )
```

Each extractor receives `(result, *args, **kwargs)` — the wrapped call's return
value followed by its arguments — and returns one field of the event. Compute the
billed units from whatever the provider tells you:

```python
def _ocr_provider(result, *args, **kwargs) -> str:
    return kwargs.get("provider", "acme-ocr")


def _ocr_model(result, *args, **kwargs) -> str:
    return kwargs.get("model", "ocr-v2")


def _ocr_usage(result, *args, **kwargs) -> ServiceUsage:
    pages = int(result.get("pages_processed", 0)) if isinstance(result, dict) else 0
    return ServiceUsage(
        document_pages=pages,
        document_count=1,
        requests=1,
        # cost_usd is the pricing lever — see §4.
        cost_usd=round(pages * 0.01, 6),   # $0.01 per page, computed from the bill
    )


def _ocr_meta(result, *args, **kwargs) -> Dict[str, Any]:
    return {"doc_type": kwargs.get("doc_type", "pdf")}


@track_document_ocr(
    provider_extractor=_ocr_provider,
    model_extractor=_ocr_model,
    usage_extractor=_ocr_usage,
    metadata_extractor=_ocr_meta,
)
async def ocr_document(path: str, *, provider="acme-ocr", model="ocr-v2", doc_type="pdf") -> dict:
    return await call_acme_ocr(path, model=model)
```

`ServiceUsage` has a field for most billable units — `input_tokens`,
`output_tokens`, `embedding_tokens`, `search_queries`, `search_results`,
`document_pages`, `document_tokens`, `image_count`, `image_tokens`,
`audio_seconds`, `requests` — plus `cost_usd`. Fill the ones your provider bills
on; leave the rest at their defaults.

## 4. Make it charge dollars

Tracking records the usage; **pricing** turns it into a dollar cost the turn
calculator sums, and that dollar total is what economics reserves and settles.
There are two bundle-only ways to price a tracked call — no platform edits:

**A. Report the cost directly (`cost_usd`).** Set `ServiceUsage.cost_usd` in your
usage extractor to the amount the provider billed (as `ocr_document` does above).
The per-turn calculator honors a self-reported `cost_usd` as the charge.

**B. Price from the economics descriptor price table.** The economics descriptor
`economics.yaml` carries a `price_tables:` section, read **live** (never
DB-seeded) by `price_table()`. It is a **whole-table replacement** of the in-code
baseline — not a merge — and its `llm:` section **must** include the token-economy
reference model, or the entire block is treated as invalid and the baseline is
used in full. So to add a priced provider/model, replace the table and keep the
reference model in place:

```yaml
# economics.yaml — per tenant/project; read live for reservation, price table,
# and reference model. See the Economics Descriptor doc.
price_tables:
  llm:
    # MUST carry the reference model, or the whole price_tables block is ignored.
    - { provider: anthropic, model: claude-sonnet-4-5-20250929, input_tokens_1M: 3.0, output_tokens_1M: 15.0 }
  embedding:
    - { provider: openai, model: text-embedding-3-small, tokens_1M: 0.02 }
    - { provider: acme-embeddings, model: acme-embed-v1, tokens_1M: 0.02 }   # your entry
```

This is an operator-level edit (the descriptor), so for a bundle that just wants
its own paid call to charge, path A — a reported `cost_usd` — is usually the
lighter move.

> **One caveat, stated plainly.** The per-turn cost calculator prices `llm`,
> `embedding`, and `web_search` today — each of those honors a self-reported
> `cost_usd`. A tracked call under a *new* service type (`vision`, `other`, …) is
> recorded and shows up in usage analytics and OPEX, but the turn calculator
> prices it at **$0** and it does not move budgets. To make a custom paid service
> actually charge the user today, emit it under one of the priced service types —
> pick the closest (`embedding` for an embedding-like API, `llm` for a
> token-billed generation) and carry the real `cost_usd`. Extending the calculator
> to price a brand-new service type natively is a platform change, not a bundle
> change.

For reference, the platform anchors its token-denominated quota to a reference
model (`llm_reference_service()`), converting each event's USD into reference
tokens; you never compute that — reporting an accurate `cost_usd` (or a correct
price-table entry) is enough.

## 5. Attribute the usage (bind context)

An event's `who / where / which-turn` comes from the `AccountingContext`. Inside a
chat turn the processor has already bound `tenant_id`, `project_id`, `user_id`,
`request_id`, and the like. For your own attribution — a component name, a phase,
an agent id — overlay the context around the call with `with_accounting`:

```python
from kdcube_ai_app.infra.accounting import with_accounting

async with with_accounting("reports.ocr", doc_id=doc_id, metadata={"stage": "ingest"}):
    result = await ocr_document(path, provider="acme-ocr")
```

`with_accounting(component, **context)` sets the component and overlays context
keys for every event emitted inside the block. A fixed set of keys is flattened to
the event root (`user_id`, `session_id`, `user_type`, `project_id`, `tenant_id`,
`request_id`, `component`, `app_bundle_id`, `timezone`, `agent_id`); expose more
with `register_context_keys("doc_id")`. Use `set_context(**kwargs)` when you need
to set context without a `with` block.

## 6. Enforce it (run under a budget)

Tracking + pricing make the cost *visible*; enforcement makes it *authorized and
paid*. Run the tracked call inside an `EconomicsGuard` so the platform verifies
the user can afford it, reserves the estimate, and settles the actual cost — the
`cost_usd` your tracker reported — when the block exits:

```python
from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
    EconomicsGuard, EconomicsEstimate, FlowPolicy,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException

try:
    async with EconomicsGuard(
        self, subject=subject,
        scope_id=f"ocr_{doc_id}", flow="reports.ocr",
        estimate=EconomicsEstimate(reservation_usd=0.20),
        policy=FlowPolicy(enforce_concurrency=False),
    ):
        async with with_accounting("reports.ocr", doc_id=doc_id):
            result = await ocr_document(path, provider="acme-ocr")   # emits into the scope
except EconomicsLimitException:
    return degraded_response()      # not affordable — nothing ran
```

The guard binds accounting under `scope_id`, so the tracked event lands in that
scope and is settled on exit — no double counting, one ledger entry. See the
[guard recipe](./guard-paid-surface-and-enforce-economics-README.md) for subject
resolution, preflight-only gating, search facades, and background flows.

## 7. Verify

- **The event is on disk.** Raw events are written under
  `accounting/<tenant>/<project>/<date>/<service_type>/…`; your new
  `service_type` appears as its own folder. OPEX aggregates land under
  `analytics/<tenant>/<project>/accounting/`.
- **The cost settled.** Trace one flow with
  `GET /economics/request-lineage?request_id=<scope_id>` — the ledger and
  reservation rows show the dollars your tracker reported.
- **The enforcement path.** Grep the runtime logs for `[economics.enforcement]`
  with your `flow` to see admit → reserve → accounting_bound → settle.

## Diagnostics

If a tracked call records no event, the accounting context is not bound — confirm
it runs inside a chat turn, an `EconomicsGuard`, or another binder. If it records
an event but charges `$0`, check §4: the service type must be one the calculator
prices, and either a matching price-table entry or a reported `cost_usd` must be
present.

## Related Documentation

- [Accounting & Usage Tracking](../../accounting/accounting-README.md) — the tracker internals, context propagation, storage layout, and the `RateCalculator`.
- [Guard a Paid Surface and Enforce Economics](./guard-paid-surface-and-enforce-economics-README.md) — reserve and settle a tracked call under the economics model.
- [Economics Model](../../economics/economic-README.md) — how per-turn USD becomes reservations, quota, and settlement.
- [Economics Enforcement Engine](../../economics/economic-enforcement-engine-README.md) — the guard/preflight API a tracked service runs under.
- [Economics Descriptor](../../configuration/economics-descriptor-README.md) — the `economics.yaml` `price_tables` and reference-model sections read live at runtime.
