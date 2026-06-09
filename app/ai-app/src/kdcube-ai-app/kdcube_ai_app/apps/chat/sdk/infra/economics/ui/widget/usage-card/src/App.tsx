/**
 * Compact KDCube-style usage card.
 *
 * Renders the registered user's combined budget across all apps in the
 * current workspace, scoped to the rolling hourly / daily / monthly
 * windows that the server reports. Subscription / Stripe checkout flow
 * is intentionally hidden; the relevant fields exist on the response but
 * are not surfaced here yet.
 *
 * Refresh paths:
 *   - On first mount once settings are ready (parent runtime config or
 *     /api/cp-frontend-config).
 *   - On `kdcube-usage-card-refresh` postMessage from the host (the scene
 *     posts this on each `accounting.usage` event).
 *   - On an explicit user click of the small refresh control.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { getBudgetBreakdown, UsageCardApiError } from './api/client';
import { settings } from './api/settings';
import type { QuotaBreakdown } from './api/types';

const REFRESH_DEBOUNCE_MS = 600;
const REFRESH_MESSAGE_TYPE = 'kdcube-usage-card-refresh';

interface CardStatus {
  loading: boolean;
  error: string | null;
  ready: boolean;
}

function formatCount(value: number | null | undefined): string {
  if (value == null) return '∞';
  return value.toLocaleString();
}

function formatUsd(value: number | null | undefined): string {
  const amount = Number(value || 0);
  if (!Number.isFinite(amount)) return '$0.00';
  return `$${amount.toFixed(2)}`;
}

function formatUsdLimit(value: number | null | undefined): string {
  if (value == null) return '∞';
  return formatUsd(value);
}

function formatRelativeReset(iso: string | null | undefined): string {
  if (!iso) return '';
  const at = new Date(iso).getTime();
  if (!Number.isFinite(at)) return '';
  const diffMs = at - Date.now();
  if (diffMs <= 0) return 'soon';
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h`;
  const days = Math.round(hours / 24);
  return `${days}d`;
}

interface UsageRowProps {
  label: string;
  used: number;
  limit: number | null;
  remaining: number | null;
  usedUsd?: number | null;
  limitUsd?: number | null;
}

const UsageRow: React.FC<UsageRowProps> = ({
  label,
  used,
  limit,
  remaining,
  usedUsd,
  limitUsd,
}) => {
  const hasUsd = usedUsd != null || limitUsd != null;
  const percent = (() => {
    if (limit == null || limit <= 0) return 0;
    return Math.min(100, Math.max(0, Math.round((used / limit) * 100)));
  })();
  const remainingText = remaining == null ? '∞' : formatCount(remaining);
  return (
    <div className="usage-row" data-pct={percent >= 100 ? 'full' : percent >= 80 ? 'high' : 'normal'}>
      <div className="usage-row-head">
        <span className="usage-row-label">{label}</span>
        <span className="usage-row-value">
          {hasUsd ? `${formatUsd(usedUsd)} / ${formatUsdLimit(limitUsd)}` : `${formatCount(used)} / ${formatCount(limit)}`}
        </span>
      </div>
      <div className="usage-row-bar" aria-hidden>
        <div className="usage-row-bar-fill" style={{ width: `${percent}%` }} />
      </div>
      <div className="usage-row-meta">
        <span>remaining {remainingText}</span>
      </div>
    </div>
  );
};

interface UsageWindowProps {
  title: string;
  resetAt?: string | null;
  children: React.ReactNode;
}

const UsageWindow: React.FC<UsageWindowProps> = ({ title, resetAt, children }) => {
  const resetLabel = formatRelativeReset(resetAt);
  return (
    <section className="usage-window">
      <header className="usage-window-head">
        <h3>{title}</h3>
        {resetLabel ? <span className="usage-window-reset">resets in {resetLabel}</span> : null}
      </header>
      <div className="usage-window-body">{children}</div>
    </section>
  );
};

export const App: React.FC = () => {
  const [breakdown, setBreakdown] = useState<QuotaBreakdown | null>(null);
  const [status, setStatus] = useState<CardStatus>({ loading: false, error: null, ready: false });
  const inFlightRef = useRef<Promise<void> | null>(null);
  const debounceRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    if (inFlightRef.current) return inFlightRef.current;
    const task = (async () => {
      setStatus((prev) => ({ ...prev, loading: true, error: null }));
      try {
        const result = await getBudgetBreakdown();
        setBreakdown(result);
        setStatus({ loading: false, error: null, ready: true });
      } catch (err) {
        const message = err instanceof UsageCardApiError ? err.message : (err as Error)?.message ?? 'load failed';
        setStatus({ loading: false, error: message, ready: true });
      } finally {
        inFlightRef.current = null;
      }
    })();
    inFlightRef.current = task;
    return task;
  }, []);

  const scheduleRefresh = useCallback(() => {
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      debounceRef.current = null;
      void load();
    }, REFRESH_DEBOUNCE_MS);
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    settings.setupParentListener().then((ready) => {
      if (cancelled || !ready) return;
      void load();
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.type !== REFRESH_MESSAGE_TYPE) return;
      scheduleRefresh();
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [scheduleRefresh]);

  return (
    <div className={`usage-card-shell${status.loading ? ' is-loading' : ''}`}>
      <header className="usage-card-header">
        <div>
          <p className="eyebrow">Usage</p>
          <h2>{breakdown?.plan_id ? breakdown.plan_id : 'Plan'}</h2>
        </div>
        <button
          type="button"
          className="usage-refresh"
          onClick={() => void load()}
          disabled={status.loading}
          aria-label="Refresh usage"
          title="Refresh"
        >
          ↻
        </button>
      </header>
      {status.error ? (
        <div className="usage-error" role="alert">
          {status.error}
        </div>
      ) : null}
      {!breakdown && !status.error && !status.ready ? (
        <div className="usage-empty">Loading…</div>
      ) : null}
      {breakdown ? (
        <div className="usage-body">
          <UsageWindow title="Last 60 minutes" resetAt={breakdown.reset_windows?.hour_reset_at}>
            <UsageRow
              label="Tokens"
              used={breakdown.current_usage.tokens_this_hour}
              limit={breakdown.effective_policy.tokens_per_hour}
              remaining={breakdown.remaining.tokens_this_hour}
              usedUsd={breakdown.current_usage.tokens_this_hour_usd}
              limitUsd={breakdown.effective_policy.usd_per_hour}
            />
          </UsageWindow>
          <UsageWindow title="Last 24 hours">
            <UsageRow
              label="Requests"
              used={breakdown.current_usage.requests_today}
              limit={breakdown.effective_policy.requests_per_day}
              remaining={breakdown.remaining.requests_today}
            />
            <UsageRow
              label="Tokens"
              used={breakdown.current_usage.tokens_today}
              limit={breakdown.effective_policy.tokens_per_day}
              remaining={breakdown.remaining.tokens_today}
              usedUsd={breakdown.current_usage.tokens_today_usd}
              limitUsd={breakdown.effective_policy.usd_per_day}
            />
          </UsageWindow>
          <UsageWindow title="Rolling 30 days" resetAt={breakdown.reset_windows?.month_reset_at}>
            <UsageRow
              label="Requests"
              used={breakdown.current_usage.requests_this_month}
              limit={breakdown.effective_policy.requests_per_month}
              remaining={breakdown.remaining.requests_this_month}
            />
            <UsageRow
              label="Tokens"
              used={breakdown.current_usage.tokens_this_month}
              limit={breakdown.effective_policy.tokens_per_month}
              remaining={breakdown.remaining.tokens_this_month}
              usedUsd={breakdown.current_usage.tokens_this_month_usd}
              limitUsd={breakdown.effective_policy.usd_per_month}
            />
          </UsageWindow>
          {(breakdown.current_usage.tokens_reserved ?? 0) > 0 ? (
            <div className="usage-reserved">
              <span className="usage-reserved-label">Reserved</span>
              <span className="usage-reserved-value">
                {formatUsd(breakdown.current_usage.tokens_reserved_usd)} ·{' '}
                {formatCount(breakdown.current_usage.tokens_reserved)} tokens
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

export default App;
