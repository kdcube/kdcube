# Economics QA Test Guide

This document is for QA. It explains expected economics behavior (as designed), how to validate it using the Admin UI, and a short test plan for tomorrow.

## 1) Short, Concrete Behavior Summary

Use these definitions while testing:

- **Role** decides funding access (`registered`, `paid`, `privileged`).
- **Plan** decides quota limits (`free`, `payasyougo`, `beta-30`, `beta-50`, etc.).
- **Lane** is only:
  - `plan lane`
  - `paid lane` (wallet‑only lane)
- **Funding sources** are `subscription`, `project`, `wallet`.
- **Important**: subscription and wallet never go negative. **Only** project budget can go negative (absorption).

### A) Free user (no wallet, no subscription)

- **Plan**: `free`
- **Lane**: plan lane
- **Funding**: project budget only
- **Limits**: free plan quotas apply (requests/tokens)
- **Absorption**: any actual spend beyond reservation is absorbed by **project budget**.
- **Absorption tag**: `shortfall:free_plan`.
- **Expected**: once quota exhausted → request denied (no paid fallback)

### B) Plan user (subscription, no wallet)

- **Plan**: subscription plan (e.g., `beta-30`)
- **Lane**: plan lane
- **Funding**: subscription budget only
- **Limits**: plan quotas apply
- **Absorption**: any actual spend beyond reservation is absorbed by **project budget** (`shortfall:subscription_overage`).
- **Expected**: when subscription budget is exhausted → request denied (unless wallet exists)

### C) Pay‑as‑you‑go user (wallet, no subscription)

- **Plan**: stays `free`
- **Lane**: plan lane unless plan quota is exceeded; can switch to paid lane if needed
- **Funding**:
  - First: project budget covers **free plan portion**
  - Overflow: wallet covers remaining
  - If wallet runs out: project budget absorbs the remainder (shortfall)
- **Limits**:
  - **Service limits** (requests/concurrency) from `payasyougo`
  - **Token limits** from `free`
- **Absorption**: wallet shortfall is absorbed by **project budget** (`shortfall:wallet_plan`).
- **Expected**: wallet decreases; if wallet is insufficient, project budget absorbs shortfall and logs a shortfall note

### D) Hybrid user (subscription + wallet)

- **Plan**: subscription plan
- **Lane**: plan lane while subscription funds the turn; switches to paid lane if subscription funds **zero**
- **Funding**:
  - Subscription covers up to available (cannot go negative)
  - Wallet covers overflow (cannot go negative)
  - If both are insufficient → project budget absorbs remainder (shortfall)
- **Limits**:
  - If subscription funds any portion → subscription plan quotas apply
  - If subscription funds **zero** and wallet covers the full request → **payasyougo** quotas apply
- **Absorption**: wallet shortfall is absorbed by **project budget** (`shortfall:wallet_subscription`).

### E) Privileged user

- **Plan**: `admin`
- **Lane**: plan lane
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
- **Wallet paid shortfall** (`shortfall:wallet_paid`)
- **Wallet plan shortfall** (`shortfall:wallet_plan`)
- **Subscription overage** (`shortfall:subscription_overage`)
- **Free plan overage** (`shortfall:free_plan`)

Filters:
- **Period**: day / month
- **Group by**: none / user / bundle
- **Export CSV**: uses the same data as JSON

This is your primary “who caused project budget to go negative” view.

### B) Request lineage (turn_id = request_id)

UI: **App Budget → Request lineage**

Paste a `turn_id` from runtime logs. The UI shows:

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
- plan / role is correct
- wallet and subscription balances update after requests

## 3) Test Plan (QA, ~1.5–2 hours)

### Prep (10 min)

1. Confirm bundle prop `economics.reservation_amount_dollars` is set (e.g., 2.0).
2. Ensure plans exist: `free`, `payasyougo`, `beta-30`, `beta-50`, `admin`.
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

### Test C — Subscription only (20 min)

1. Create an internal subscription plan (e.g., `beta-30`) and activate for a user.
2. Top up the period.
3. Run requests.
4. Verify:
   - Subscription balance decreases.
   - No wallet or project budget usage.
   - Absorption report remains empty.

### Test D — Subscription + Wallet (20 min)

1. Give a subscribed user some wallet credits.
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

1. Take a `turn_id` from any of the above runs.
2. Open **Request lineage** in UI.
3. Confirm the split matches expectation for that scenario.

---

If any result differs from this guide, record:
- user_id
- plan_id
- role
- turn_id
- expected vs observed funding
- screenshots from Absorption Report and Request Lineage
