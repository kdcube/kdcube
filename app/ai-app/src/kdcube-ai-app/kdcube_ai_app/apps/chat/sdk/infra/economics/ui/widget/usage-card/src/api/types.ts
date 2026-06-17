/**
 * Wire shape for the GET /api/economics/me/budget-breakdown endpoint
 * (see apps/chat/ingress/economics/me.py).
 *
 * Subscription + Stripe checkout fields are intentionally omitted from
 * this widget's read path; the pay-flow surface is hidden in the UI for
 * now and the breakdown payload alone covers everything the compact card
 * needs.
 */

export interface BudgetEffectivePolicy {
  max_concurrent: number | null;
  requests_per_day: number | null;
  requests_per_month: number | null;
  tokens_per_hour: number | null;
  tokens_per_day: number | null;
  tokens_per_month: number | null;
  usd_per_hour?: number | null;
  usd_per_day?: number | null;
  usd_per_month?: number | null;
}

export interface BudgetCurrentUsage {
  requests_today: number;
  requests_this_month: number;
  tokens_this_hour: number;
  tokens_today: number;
  tokens_this_month: number;
  tokens_reserved?: number;
  tokens_this_hour_usd?: number | null;
  tokens_today_usd?: number | null;
  tokens_this_month_usd?: number | null;
  tokens_reserved_usd?: number | null;
}

export interface BudgetRemaining {
  requests_today: number | null;
  requests_this_month: number | null;
  tokens_this_hour: number | null;
  tokens_today: number | null;
  tokens_this_month: number | null;
  tokens_this_hour_usd?: number | null;
  tokens_today_usd?: number | null;
  tokens_this_month_usd?: number | null;
}

export interface BudgetResetWindows {
  bundle_id?: string | null;
  hour_reset_at?: string | null;
  day_started_at?: string | null;
  day_reset_at?: string | null;
  month_started_at?: string | null;
  month_reset_at?: string | null;
}

export interface BudgetLifetimeCredits {
  tokens_available?: number;
  tokens_consumed?: number;
  available_usd?: number;
}

export interface QuotaBreakdown {
  user_id: string;
  role?: string | null;
  plan_id: string;
  plan_source?: string | null;
  effective_policy: BudgetEffectivePolicy;
  current_usage: BudgetCurrentUsage;
  remaining: BudgetRemaining;
  reset_windows?: BudgetResetWindows | null;
  lifetime_credits?: BudgetLifetimeCredits | null;
  // Subscription / wallet fields exist on the server response but are not
  // consumed by the compact card; the pay flow is hidden for now.
}

export interface BudgetBreakdownResponse extends QuotaBreakdown {
  status?: string;
}

/** Wire shape for GET /profile. Email is reported via `email` when the IdP
 *  provided one; for federated identities the `username` is typically the
 *  email itself, so the widget falls back to it. */
export interface ProfileResponse {
  user_type: string;
  username?: string | null;
  email?: string | null;
  user_id?: string | null;
  roles?: string[] | null;
  session_id?: string | null;
}
