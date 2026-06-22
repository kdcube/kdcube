/**
 * Compact KDCube-style usage card.
 *
 * Renders the registered user's combined budget across all apps in the
 * current workspace, scoped to the active hourly / daily / monthly quota
 * buckets that the server reports. Subscription / Stripe checkout flow
 * is intentionally hidden; the relevant fields exist on the response but
 * are not surfaced here yet.
 *
 * Refresh paths:
 *   - On first mount once settings are ready (parent runtime config or
 *     /api/cp-frontend-config).
 *   - On `kdcube.surface.command(action="refresh")` postMessage from the
 *     host scene after `accounting.usage` events.
 *   - On an explicit user click of the small refresh control.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  bindComponentEventSubscriptions,
  createSceneEventTransport,
} from '@kdcube/components-core/events';
import { getBudgetBreakdown, getProfile, UsageCardApiError } from './api/client';
import { settings } from './api/settings';
import type { ProfileResponse, QuotaBreakdown } from './api/types';

const REFRESH_DEBOUNCE_MS = 600;
const SURFACE_COMMAND_MESSAGE_TYPE = 'kdcube.surface.command';

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

function formatTokens(value: number | null | undefined): string {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '0';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, '')}K`;
  return String(Math.round(n));
}

interface UsageBlockDef {
  label: string;
  resetAt?: string | null;
  costUsed: number | null | undefined;
  costQuota: number | null | undefined;
  tokUsed: number | null | undefined;
  tokQuota: number | null | undefined;
}

// One time-window in the super-compact view: dollar spent / quota as the
// headline (colored by how close it is to the cap), tokens spent / quota
// alongside, and when the window resets. Quota reads ∞ on an unlimited plan.
const UsageBlock: React.FC<UsageBlockDef> = ({ label, resetAt, costUsed, costQuota, tokUsed, tokQuota }) => {
  const reset = formatRelativeReset(resetAt);
  const hasCostQuota = costQuota != null && costQuota > 0;
  const pct = hasCostQuota
    ? Math.min(100, Math.max(0, Math.round((Number(costUsed || 0) / (costQuota as number)) * 100)))
    : 0;
  const state = pct >= 100 ? 'full' : pct >= 80 ? 'high' : 'normal';
  const hasTokQuota = tokQuota != null && tokQuota > 0;
  return (
    <section className="uc-blk">
      <div className="uc-blk-head">
        <span className="uc-blk-label">{label}</span>
        {reset ? <span className="uc-blk-reset">resets in {reset}</span> : null}
      </div>
      <div className="uc-blk-row">
        <span className="uc-blk-cost money" data-pct={state}>
          {formatUsd(costUsed)} <span className="uc-q">/ {hasCostQuota ? formatUsd(costQuota) : '∞'}</span>
        </span>
        <span className="uc-blk-tok money">
          {formatTokens(tokUsed)} <span className="uc-q">/ {hasTokQuota ? formatTokens(tokQuota) : '∞'}</span>
          <span className="uc-u">tokens</span>
        </span>
      </div>
    </section>
  );
};

function accountIdentity(profile: ProfileResponse | null): string | null {
  if (!profile) return null;
  const candidate = profile.email || profile.username || profile.user_id;
  if (!candidate) return null;
  const trimmed = String(candidate).trim();
  return trimmed.length ? trimmed : null;
}

function initialCompact(): boolean {
  try {
    const p = new URLSearchParams(window.location.search || '');
    const v = (p.get('view') || p.get('mode') || '').trim().toLowerCase();
    return v === 'compact' || (p.get('compact') || '').trim() === '1';
  } catch {
    return false;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object';
}

function isUsageRefreshMessage(value: unknown): boolean {
  if (!isRecord(value)) return false;
  if (value.type === SURFACE_COMMAND_MESSAGE_TYPE) {
    const action = typeof value.action === 'string' ? value.action.trim().toLowerCase() : '';
    const target = typeof value.target_surface === 'string' ? value.target_surface.trim().toLowerCase() : '';
    return action === 'refresh' && (!target || target === 'sdk.usage.card');
  }

  const nestedData = value.data;
  if (isRecord(nestedData) && (nestedData.type === 'accounting.usage' || nestedData.name === 'accounting.usage')) {
    return true;
  }

  const event = value.event;
  if (isRecord(event) && (event.type === 'accounting.usage' || event.name === 'accounting.usage' || event.step === 'accounting')) {
    return true;
  }

  const payload = value.payload;
  if (isRecord(payload) && (payload.type === 'accounting.usage' || payload.name === 'accounting.usage')) {
    return true;
  }

  return false;
}

function refreshReason(value: unknown): string {
  if (!isRecord(value)) return 'host-refresh';
  const reason = value.reason;
  if (typeof reason === 'string' && reason.trim()) return reason.trim();
  if (value.type === SURFACE_COMMAND_MESSAGE_TYPE) return 'surface-command';
  if (value.type === 'accounting.usage') return 'accounting.usage';
  const nestedData = value.data;
  if (isRecord(nestedData) && (nestedData.type === 'accounting.usage' || nestedData.name === 'accounting.usage')) return 'accounting.usage';
  const event = value.event;
  if (isRecord(event) && (event.type === 'accounting.usage' || event.name === 'accounting.usage' || event.step === 'accounting')) return 'accounting.usage';
  return typeof value.type === 'string' ? value.type : 'host-refresh';
}

function notifyHost(message: Record<string, unknown>): void {
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage(message, '*');
    }
  } catch {
    // Host diagnostics are best-effort only.
  }
}

function subscribeToSceneUsageEvents(): () => void {
  console.info('[kdcube.usage-card] scene subscription claim', { event: 'accounting.usage' });
  return bindComponentEventSubscriptions({
    component: 'usage_card',
    transportMode: 'scene',
    transports: {
      scene: createSceneEventTransport({ logger: console }),
    },
    logger: console,
    subscriptions: [
      {
        id: 'usage-card-accounting-refresh',
        source: 'sse',
        events: ['accounting.usage'],
        channels: ['chat_service', 'chat_step', 'accounting.usage', 'message'],
        forward: {
          type: SURFACE_COMMAND_MESSAGE_TYPE,
          target_surface: 'sdk.usage.card',
          action: 'refresh',
        },
        forwardType: SURFACE_COMMAND_MESSAGE_TYPE,
        reason: 'accounting.usage',
        debounceMs: 800,
      },
    ],
  });
}

export const App: React.FC = () => {
  const [compact, setCompact] = useState<boolean>(initialCompact);
  const [breakdown, setBreakdown] = useState<QuotaBreakdown | null>(null);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [status, setStatus] = useState<CardStatus>({ loading: false, error: null, ready: false });
  const inFlightRef = useRef<Promise<void> | null>(null);
  const pendingRefreshRef = useRef(false);
  const debounceRef = useRef<number | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const sceneSubscriptionCleanupRef = useRef<(() => void) | null>(null);

  const load = useCallback(async (reason = 'load') => {
    if (inFlightRef.current) {
      pendingRefreshRef.current = true;
      console.info('[kdcube.usage-card] refresh queued while load in flight', { reason });
      return inFlightRef.current;
    }
    const task = (async () => {
      setStatus((prev) => ({ ...prev, loading: true, error: null }));
      try {
        try {
          const nextBreakdown = await getBudgetBreakdown();
          setBreakdown(nextBreakdown);
          setStatus({ loading: false, error: null, ready: true });
          try {
            setProfile(await getProfile());
          } catch {
            setProfile(null);
          }
        } catch (err) {
          const message = err instanceof UsageCardApiError ? err.message : (err as Error)?.message ?? 'load failed';
          setStatus({ loading: false, error: message, ready: true });
          setProfile(null);
        }
      } finally {
        inFlightRef.current = null;
        if (pendingRefreshRef.current) {
          pendingRefreshRef.current = false;
          window.setTimeout(() => {
            void load('queued-refresh');
          }, 0);
        }
      }
    })();
    inFlightRef.current = task;
    return task;
  }, []);

  const scheduleRefresh = useCallback((reason = 'host-refresh') => {
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      debounceRef.current = null;
      void load(reason);
    }, REFRESH_DEBOUNCE_MS);
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    settings.setupParentListener().then((ready) => {
      if (cancelled || !ready) return;
      console.info('[kdcube.usage-card] host config ready', {
        hostedByScene: settings.isHostedByScene(),
        liveEventsTransport: settings.getLiveEventsTransport() || '',
      });
      if (settings.isHostedByScene() && settings.getLiveEventsTransport() === 'scene') {
        sceneSubscriptionCleanupRef.current = subscribeToSceneUsageEvents();
      } else {
        console.info('[kdcube.usage-card] scene subscription skipped', {
          hostedByScene: settings.isHostedByScene(),
          liveEventsTransport: settings.getLiveEventsTransport() || '',
        });
      }
      void load();
    });
    return () => {
      cancelled = true;
      sceneSubscriptionCleanupRef.current?.();
      sceneSubscriptionCleanupRef.current = null;
    };
  }, [load]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === 'kdcube-set-view') {
        if (data.view === 'expanded') setCompact(false);
        if (data.view === 'compact') setCompact(true);
        return;
      }
      if (!isUsageRefreshMessage(data)) return;
      const reason = refreshReason(data);
      console.info('[kdcube.usage-card] refresh requested', { reason });
      notifyHost({
        type: 'kdcube.surface.command.ack',
        target_surface: 'sdk.usage.card',
        action: 'refresh',
        reason,
        ts: new Date().toISOString(),
      });
      scheduleRefresh(reason);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [scheduleRefresh]);

  // Report the rendered content height so a summoned compact panel fits to
  // content (no empty space or cutoff). Mirrors the memory/tasks widgets;
  // measuring the lowest child bottom is correct even when the shell fills
  // the iframe.
  useEffect(() => {
    const el = shellRef.current;
    if (!el || typeof window === 'undefined' || window.parent === window) return;
    let raf = 0;
    const measure = () => {
      raf = 0;
      const top = el.getBoundingClientRect().top;
      let bottom = top;
      Array.from(el.children).forEach((child) => {
        const r = (child as HTMLElement).getBoundingClientRect();
        if (r.bottom > bottom) bottom = r.bottom;
      });
      const height = Math.max(0, Math.ceil(bottom - top) + 12);
      window.parent.postMessage({ type: 'kdcube-usage-resize', widget: 'usage_card', height, compact }, '*');
    };
    const schedule = () => { if (!raf) raf = window.requestAnimationFrame(measure); };
    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    const mo = new MutationObserver(schedule);
    mo.observe(el, { childList: true, subtree: true });
    schedule();
    return () => { ro.disconnect(); mo.disconnect(); if (raf) window.cancelAnimationFrame(raf); };
  }, [compact, breakdown]);

  return (
    <div ref={shellRef} className={`usage-card-shell${compact ? ' usage-compact' : ''}${status.loading ? ' is-loading' : ''}`}>
      <header className="usage-card-header">
        {compact ? (
          <p className="uc-plan-line" title={`${breakdown?.plan_id || 'Plan'}${accountIdentity(profile) ? ' · ' + accountIdentity(profile) : ''}`}>
            <span className="uc-plan-label">Plan:</span>{' '}
            <span className="uc-plan-name">{breakdown?.plan_id ? breakdown.plan_id : 'Plan'}</span>
            {accountIdentity(profile) ? (
              <>
                {' '}<span className="uc-plan-sep">·</span>{' '}
                <span className="uc-plan-acct">{accountIdentity(profile)}</span>
              </>
            ) : null}
          </p>
        ) : (
          <div className="usage-card-identity">
            <p className="eyebrow">Plan</p>
            <h2 title={breakdown?.plan_id || undefined}>
              {breakdown?.plan_id ? breakdown.plan_id : 'Plan'}
            </h2>
            {accountIdentity(profile) ? (
              <p className="usage-card-account" title={accountIdentity(profile) || undefined}>
                {accountIdentity(profile)}
              </p>
            ) : null}
          </div>
        )}
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
        compact ? (
          <div className="usage-body usage-compact-body">
            <UsageBlock
              label="Last hour"
              resetAt={breakdown.reset_windows?.hour_reset_at}
              costUsed={breakdown.current_usage.tokens_this_hour_usd}
              costQuota={breakdown.effective_policy.usd_per_hour}
              tokUsed={breakdown.current_usage.tokens_this_hour}
              tokQuota={breakdown.effective_policy.tokens_per_hour}
            />
            <UsageBlock
              label="Current 24h"
              resetAt={breakdown.reset_windows?.day_reset_at}
              costUsed={breakdown.current_usage.tokens_today_usd}
              costQuota={breakdown.effective_policy.usd_per_day}
              tokUsed={breakdown.current_usage.tokens_today}
              tokQuota={breakdown.effective_policy.tokens_per_day}
            />
            <UsageBlock
              label="Current 30d"
              resetAt={breakdown.reset_windows?.month_reset_at}
              costUsed={breakdown.current_usage.tokens_this_month_usd}
              costQuota={breakdown.effective_policy.usd_per_month}
              tokUsed={breakdown.current_usage.tokens_this_month}
              tokQuota={breakdown.effective_policy.tokens_per_month}
            />
          </div>
        ) : (
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
          <UsageWindow title="Current 24h quota period" resetAt={breakdown.reset_windows?.day_reset_at}>
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
          <UsageWindow title="Current 30-day quota period" resetAt={breakdown.reset_windows?.month_reset_at}>
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
        )
      ) : null}
    </div>
  );
};

export default App;
