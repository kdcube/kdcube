---
id: repo:kdcube-ai-app/app/ai-app/docs/economics/eco-test-README.md
title: "Eco Test"
summary: "QA test plan for the economics subsystem and expected behaviors."
tags: ["economics", "testing", "qa", "budgets", "plans"]
keywords: ["test plan", "wallets", "shortfall", "rate limits", "admin UI"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/economics/eco-admin-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/eco-kickoff-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-README.md
---
# Economics QA Test Guide

This document is for QA. It explains expected economics behavior (as designed), how to validate it using the Admin UI, and a short test plan for tomorrow.

## 1) Short, Concrete Behavior Summary

Use these definitions while testing:

- **Role** decides funding access (`registered`, `paid`, `privileged`).
- **Plan** decides quota limits (`free`, `wallet`, `beta-30`, `beta-50`, etc.).
- **Funding split**: every request is one split — the primary source covers
  `plan_part = min(R, Q, P)`, the wallet covers the over‑quota remainder. No paid lane, no switch.
- **Funding sources**: primary is `subscription` or `project`; the `wallet` is overflow only.
- **Important**: subscription and wallet never go negative. **Only** project budget can go negative (absorption).

### A) Free user (no wallet, no subscription)

- **Plan**: `free`
- **Split**: project funds the plan part; no wallet, so quota exhaustion denies
- **Funding**: project budget only
- **Limits**: free plan quotas apply (requests/tokens)
- **Absorption**: any actual spend beyond reservation is absorbed by **project budget**.
- **Absorption tag**: `shortfall:free_plan`.
- **Expected**: once quota exhausted → request denied (no paid fallback)

### B) Plan user (subscription, no wallet)

- **Plan**: subscription plan (e.g., `beta-30`)
- **Split**: subscription funds the plan part; no wallet, so exhaustion denies
- **Funding**: subscription budget only
- **Limits**: plan quotas apply
- **Absorption**: any actual spend beyond reservation is absorbed by **project budget** (`shortfall:subscription_overage`).
- **Expected**: when subscription budget is exhausted → request denied (unless wallet exists)

### C) Wallet user (wallet, no subscription)

- **Plan**: stays `free`
- **Split**: project funds the plan part within quota; the wallet covers the over‑quota remainder
- **Funding**:
  - First: free plan quota and project budget cover the maximum plan-funded portion
  - Overflow: wallet covers remaining usage; wallet-paid tokens do **not** consume free plan quota
  - If wallet runs out: project budget absorbs the remainder (shortfall), and any remaining free plan quota is consumed by that fallback
- **Limits**:
  - **Service limits** (requests/concurrency) from `wallet`
  - **Token limits** from `free`
- **Absorption**: wallet shortfall is absorbed by **project budget** (`shortfall:wallet_plan`).
- **Expected**: wallet decreases; if wallet is insufficient, project budget absorbs shortfall and logs a shortfall note

### D) Hybrid user (subscription + wallet)

- **Plan**: subscription plan
- **Split**: subscription funds the plan part; the wallet covers the over‑quota remainder
- **Funding**:
  - Subscription quota/funding covers the maximum plan-funded portion (cannot go negative)
  - Wallet covers overflow (cannot go negative); wallet-paid tokens do **not** consume subscription plan quota
  - If both are insufficient → project budget absorbs remainder (shortfall), and any remaining subscription plan quota is consumed by that fallback
- **Limits**:
  - If subscription funds any portion → subscription plan quotas apply
  - If subscription funds **zero** and wallet covers the full request → **wallet** quotas apply
- **Absorption**: wallet shortfall is absorbed by **project budget** (`shortfall:wallet_subscription`).

### E) Privileged user

- **Plan**: `admin`
- **Split**: budget bypass — project charged after run
- **Funding**: budget bypass (no pre‑check)
- **Absorption**: all spend is absorbed by **project budget** (can go negative).
- **Limits**: `admin` plan quotas (usually very high)

## 2) QA UI Verification (No DB Access Needed)

All checks below are done in the **Economics Admin UI**.

### A) Budget absorption report

UI: **App Budget → Budget absorption report**

Use this to confirm when project budget absorbed **wallet/plan shortfalls**.

Report fields:
- **Total absorbed**
- **Subscription shortfall** (`shortfall:wallet_subscription`)
- **Wallet plan shortfall** (`shortfall:wallet_plan`)
- **Subscription overage** (`shortfall:subscription_overage`)
- **Free plan overage** (`shortfall:free_plan`)

Filters:
- **Period**: day / month
- **Group by**: none / user / bundle
- **Export CSV**: uses the same data as JSON

This is your primary “who caused project budget to go negative” view.

### B) Request lineage

UI: **App Budget → Request lineage**

Paste a chat `turn_id` or another top-level accountable `request_id` from runtime logs. The UI shows:

- Project budget reservations + ledger entries
- Subscription reservations + ledger entries
- Wallet reservations

This is the per‑request truth for **who paid** and where any shortfall went.

### C) User budget breakdown

UI: **User Budget Breakdown**

Shows:
- Plan resolution (plan_id)
- Wallet balance
- Subscription balance
- Active reservations
- Last usage and quota insight

Use this to confirm:
- plan and auto‑resolved role are correct (role is read from user session)
- wallet and subscription balances update after requests

## 3) Test Plan (QA, ~1.5–2 hours)

### Prep (10 min)

1. Confirm bundle prop `economics.reservation_amount_dollars` is set (e.g., 2.0).
2. Ensure plans exist: `free`, `wallet`, `beta-30`, `beta-50`, `admin`.
3. Confirm plan quota policies are loaded in the admin UI.

### Test A — Free user (15 min)

1. Use a registered user with no wallet and no subscription.
2. Run a few small requests.
3. Verify:
   - Requests are allowed until quota is hit.
   - After quota hit: request denied.
   - Project budget decreases.
4. Check:
   - No wallet usage.
   - Absorption report has **no shortfall** for this user.

### Test B — Pay‑as‑you‑go user (20 min)

1. Give a user wallet credits, no subscription.
2. Run requests until free quota is exhausted.
3. Verify:
   - Plan remains `free`.
   - Wallet starts covering overflow.
   - If wallet runs low, a shortfall is absorbed by project budget.
4. Check:
   - Absorption report shows `shortfall:wallet_plan`.
   - Request lineage shows project + wallet split.

### Test C — External subscription only (20 min)

1. Create an external (Stripe) subscription and activate for a user.
2. Fund the period (Stripe webhook, or **Subscription Balance Admin** top‑up).
3. Run requests.
4. Verify:
   - Subscription balance decreases.
   - No wallet or project budget usage.
   - Absorption report remains empty.

### Test C2 — Internal plan (project by quota) (15 min)

1. Create an internal subscription (e.g., `beta-30`) and activate for a user.
2. Run requests.
3. Verify:
   - No subscription period budget is created or used.
   - Project budget decreases, bounded by the plan quota.
   - **Reset quota** re-anchors the month + day windows and clears hour buckets; all rolling counters start fresh.
   - Top‑up on this user returns `400` (internal has no balance).

### Test D — External subscription + Wallet (20 min)

1. Give an external subscriber some wallet credits.
2. Drain subscription to near zero.
3. Run a large request.
4. Verify:
   - Subscription covers available portion.
   - Wallet covers overflow.
   - If wallet is insufficient, project absorbs shortfall.
5. Check absorption report for `shortfall:wallet_subscription`.

### Test E — Privileged admin (10 min)

1. Use `privileged` user.
2. Run large requests.
3. Verify:
   - Requests always allowed (within admin plan quotas).
   - Project budget can go negative (bypass).

### Test F — Lineage spot‑check (10 min)

1. Take a chat `turn_id` or top-level accountable `request_id` from any of the above runs.
2. Open **Request lineage** in UI.
3. Confirm the split matches expectation for that scenario.

---

If any result differs from this guide, record:
- user_id
- plan_id
- role
- request_id (chat uses turn_id)
- expected vs observed funding
- screenshots from Absorption Report and Request Lineage
