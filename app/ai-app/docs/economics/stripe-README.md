# Stripe Integration Guide

This document explains how Stripe is integrated into the economics subsystem and how to operate it safely.
It is written for the engineer who will wire Stripe in production.

## What Stripe Is Used For

We use Stripe for two things:

1. **Subscriptions** (monthly recurring charges)
2. **Wallet refunds** (refund lifetime credit purchases)

We do **not** top up project budget directly from Stripe.
Project budget is increased only by admin actions or by subscription rollovers.

## Stripe Components in Code

Key modules:

- [stripe.py](services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/stripe.py)
- [control_plane.py](services/kdcube-ai-app/kdcube_ai_app/apps/chat/api/control_plane/control_plane.py)

Current routing (implementation detail):

- Stripe HTTP endpoints are defined in [control_plane.py](services/kdcube-ai-app/kdcube_ai_app/apps/chat/api/control_plane/control_plane.py).

TODO (planned refactor):

- Move Stripe routes into a dedicated `economics/` route module near the control plane package\n  and keep `control_plane.py` as the top‑level router only.

Webhook endpoint:

- `POST /webhooks/stripe`

Admin endpoints:

- `POST /subscriptions/create` (internal or stripe)
- `POST /wallet/refund`
- `POST /subscriptions/cancel`
- `POST /stripe/reconcile`
- `GET /stripe/pending`

## Stripe Objects We Rely On

The system expects Stripe objects to carry metadata:

- `tenant`
- `project`
- `user_id`
- `tier`
- `kdcube_invoice_kind=subscription`

These are set when creating subscriptions via the admin endpoint.
If metadata is missing, the webhook handler will attempt fallback lookups.

## Webhook Events Handled

The webhook handler (`StripeEconomicsWebhookHandler`) processes:

- `payment_intent.succeeded`
- `invoice.paid`
- `refund.created` / `refund.updated`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Idempotency and audit are enforced through `external_economics_events`.

## Subscription Flow (Stripe)

1. Admin calls `POST /subscriptions/create` with:
   - `provider=stripe`
   - `stripe_price_id`
   - `user_id`, `tier`
   - optional `stripe_customer_id`

2. Stripe subscription is created with metadata.
3. We write a best‑effort snapshot to `user_subscriptions`.
4. On each invoice payment (`invoice.paid`), the webhook:
   - updates `user_subscriptions` (status, next_charge_at, last_charged_at)
   - tops up the **subscription period budget** for that billing period

The top‑up is idempotent per billing period, using `external_economics_events`.

## Subscription Cancel Flow

Admin calls `POST /subscriptions/cancel`.

Behavior:

- We request `cancel_at_period_end` in Stripe.
- The current period balance remains usable until period end.
- We record an internal pending event (`external_economics_events`, `source='internal'`, `kind='subscription_cancel'`).
- Stripe webhook updates status to `canceled` when Stripe confirms.

If webhooks are missed, `/stripe/reconcile` will query Stripe and finalize pending cancels.

## Wallet Refund Flow

Admin calls `POST /wallet/refund` with a Stripe `payment_intent_id`.

Behavior:

1. **Immediate local debit** of lifetime credits (prevents further usage).
2. Create internal pending event: `source='internal'`, `kind='wallet_refund'`.
3. Create Stripe refund via API.
4. Webhook (`refund.updated`) marks the event as applied on success.
5. If refund fails, credits are restored and the event is marked failed.

All refund operations send admin email notifications.

## Reconcile Pending Stripe Requests

`POST /stripe/reconcile` will:

- Scan internal pending events (refunds and subscription cancels).
- Query Stripe to resolve their final status.
- Apply the same logic as webhook handlers.

This is used when webhook delivery was missed or delayed.

## Email Notifications

The Stripe admin service sends email notifications for:

- refund requested / completed / failed
- subscription cancel requested / completed / failed
- reconcile results

Email sender is implemented in [email.py](services/kdcube-ai-app/kdcube_ai_app/infra/channel/email.py).

## Configuration Required

Environment variables required for Stripe:

- `STRIPE_SECRET_KEY` (or `STRIPE_API_KEY`)
- `STRIPE_WEBHOOK_SECRET`

If `STRIPE_WEBHOOK_SECRET` is missing, webhooks are accepted without signature verification (not recommended).

Email configuration (for refund/cancel alerts):

- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_USER`
- `EMAIL_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`
- `EMAIL_USE_TLS`
- `EMAIL_ENABLED`

See the service configuration doc for details:

- [service-config-README.md](docs/service/service-config-README.md)

## Operational Checklist (Stripe)

- Create Stripe products/prices and store `stripe_price_id`.
- Configure webhook URL to `POST /api/v1/webhooks/stripe`.
- Set webhook signing secret in `STRIPE_WEBHOOK_SECRET`.
- Ensure admin email settings are correct.
- Run `/stripe/reconcile` periodically or after outages.

## Notes for Integrator

- Subscriptions are mapped to **per‑period budgets**; do not carry balances across months.
- Unused subscription balance is rolled into project budget at period close.
- Project budget is never topped up by Stripe events directly.
- Idempotency is enforced via `external_economics_events` and Stripe event IDs.

For schema and operational procedures, see `operational-README.md` in this folder.
