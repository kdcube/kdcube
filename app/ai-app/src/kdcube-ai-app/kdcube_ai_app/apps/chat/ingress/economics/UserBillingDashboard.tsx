import React, { useState, useEffect, useMemo } from 'react';
import ReactDOM from 'react-dom/client';

// =============================================================================
// Type Definitions
// =============================================================================

interface AppSettings {
    baseUrl: string;
    accessToken: string | null;
    idToken: string | null;
    idTokenHeader: string;
    defaultTenant: string;
    defaultProject: string;
    defaultAppBundleId: string;
    stripeDashboardBaseUrl: string;
}

interface SubscriptionPlan {
    tenant: string;
    project: string;
    plan_id: string;
    provider: string;
    stripe_price_id: string | null;
    monthly_price_cents: number;
    active: boolean;
    created_at: string;
    notes: string | null;
}

interface Subscription {
    tenant: string;
    project: string;
    user_id: string;
    plan_id: string | null;
    status: string;
    monthly_price_cents: number;
    started_at: string;
    next_charge_at: string | null;
    last_charged_at: string | null;
    provider: string;
    stripe_customer_id: string | null;
    stripe_subscription_id: string | null;
}

interface QuotaBreakdown {
    user_id: string;
    role?: string | null;
    plan_id: string;
    plan_source?: string | null;
    effective_policy: {
        max_concurrent: number | null;
        requests_per_day: number | null;
        requests_per_month: number | null;
        tokens_per_hour: number | null;
        tokens_per_day: number | null;
        tokens_per_month: number | null;
        usd_per_hour?: number | null;
        usd_per_day?: number | null;
        usd_per_month?: number | null;
    };
    current_usage: {
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
    };
    reset_windows?: {
        bundle_id?: string | null;
        hour_reset_at?: string | null;
        day_started_at?: string | null;
        day_reset_at?: string | null;
        month_started_at?: string | null;
        month_reset_at?: string | null;
    } | null;
    remaining: {
        requests_today: number | null;
        requests_this_month: number | null;
        tokens_this_hour: number | null;
        tokens_today: number | null;
        tokens_this_month: number | null;
        tokens_this_hour_usd?: number | null;
        tokens_today_usd?: number | null;
        tokens_this_month_usd?: number | null;
        percentage_used: number | null;
    };
    lifetime_credits: {
        has_lifetime_credits: boolean;
        purchased_usd: number;
        spent_usd: number;
        reserved_usd: number;
        available_usd: number;
        lifetime_usd_purchased?: number | null;
    } | null;
    subscription_balance?: {
        has_subscription?: boolean;
        active?: boolean;
        plan_id?: string | null;
        status?: string | null;
        period_start?: string | null;
        period_end?: string | null;
        available_usd: number;
        available_tokens?: number;
        reserved_usd?: number;
        reserved_tokens?: number;
        spent_usd?: number;
    } | null;
}

// =============================================================================
// Settings Manager
// =============================================================================

function parseJwt(token: string) {
    try {
        const base64Url = token.split('.')[1];
        const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
        const jsonPayload = decodeURIComponent(atob(base64).split('').map(function(c) {
            return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
        return JSON.parse(jsonPayload);
    } catch (e) {
        return null;
    }
}

class SettingsManager {
    private readonly PLACEHOLDER_BASE_URL = '{{' + 'CHAT_BASE_URL' + '}}';
    private readonly PLACEHOLDER_ACCESS_TOKEN = '{{' + 'ACCESS_TOKEN' + '}}';
    private readonly PLACEHOLDER_ID_TOKEN = '{{' + 'ID_TOKEN' + '}}';
    private readonly PLACEHOLDER_ID_TOKEN_HEADER = '{{' + 'ID_TOKEN_HEADER' + '}}';
    private readonly PLACEHOLDER_TENANT = '{{' + 'DEFAULT_TENANT' + '}}';
    private readonly PLACEHOLDER_PROJECT = '{{' + 'DEFAULT_PROJECT' + '}}';
    private readonly PLACEHOLDER_BUNDLE_ID = '{{' + 'DEFAULT_APP_BUNDLE_ID' + '}}';

    private settings: AppSettings = {
        baseUrl: '{{CHAT_BASE_URL}}',
        accessToken: '{{ACCESS_TOKEN}}',
        idToken: '{{ID_TOKEN}}',
        idTokenHeader: '{{ID_TOKEN_HEADER}}',
        defaultTenant: '{{DEFAULT_TENANT}}',
        defaultProject: '{{DEFAULT_PROJECT}}',
        defaultAppBundleId: '{{DEFAULT_APP_BUNDLE_ID}}',
        stripeDashboardBaseUrl: '',
    };

    private configReceivedCallback: (() => void) | null = null;

    getBaseUrl(): string {
        if (this.settings.baseUrl === this.PLACEHOLDER_BASE_URL) {
            return window.location.origin;
        }
        try {
            const url = new URL(this.settings.baseUrl);
            if (url.port === 'None' || url.hostname.includes('None')) return window.location.origin;
            const trimmed = this.settings.baseUrl.replace(/\/+$/, '');
            return trimmed.endsWith('/api') ? trimmed.slice(0, -4) : trimmed;
        } catch (e) {
            return window.location.origin;
        }
    }

    getAccessToken(): string | null {
        if (this.settings.accessToken === this.PLACEHOLDER_ACCESS_TOKEN || !this.settings.accessToken) {
            return null;
        }
        return this.settings.accessToken;
    }

    getIdToken(): string | null {
        if (this.settings.idToken === this.PLACEHOLDER_ID_TOKEN || !this.settings.idToken) {
            return null;
        }
        return this.settings.idToken;
    }

    getIdTokenHeader(): string {
        return this.settings.idTokenHeader === this.PLACEHOLDER_ID_TOKEN_HEADER ? 'X-ID-Token' : this.settings.idTokenHeader;
    }

    getDefaultTenant(): string {
        return this.settings.defaultTenant === this.PLACEHOLDER_TENANT ? 'home' : this.settings.defaultTenant;
    }

    getDefaultProject(): string {
        return this.settings.defaultProject === this.PLACEHOLDER_PROJECT ? 'demo' : this.settings.defaultProject;
    }

    hasPlaceholderSettings(): boolean {
        return this.settings.baseUrl === this.PLACEHOLDER_BASE_URL;
    }

    updateSettings(partial: Partial<AppSettings>): void {
        this.settings = { ...this.settings, ...partial };
    }

    onConfigReceived(callback: () => void): void {
        this.configReceivedCallback = callback;
    }

    private applyRuntimeConfig(config: any, options: { notify?: boolean } = {}): boolean {
        const tenant = config.defaultTenant || config.tenant || config.tenant_id;
        const project = config.defaultProject || config.project || config.project_id;
        const idTokenHeader = config.idTokenHeader || config.idTokenHeaderName || config.auth?.idTokenHeaderName;
        const updates: Partial<AppSettings> = {};
        if (config.baseUrl && typeof config.baseUrl === 'string') updates.baseUrl = config.baseUrl;
        if (config.accessToken !== undefined) updates.accessToken = config.accessToken;
        if (config.idToken !== undefined) updates.idToken = config.idToken;
        if (idTokenHeader) updates.idTokenHeader = idTokenHeader;
        if (tenant) updates.defaultTenant = tenant;
        if (project) updates.defaultProject = project;
        if (config.defaultAppBundleId) updates.defaultAppBundleId = config.defaultAppBundleId;
        if (config.stripeDashboardBaseUrl) updates.stripeDashboardBaseUrl = config.stripeDashboardBaseUrl;
        if (Object.keys(updates).length === 0) return false;
        this.updateSettings(updates);
        if (options.notify !== false) this.configReceivedCallback?.();
        return true;
    }

    private async loadFrontendConfig(): Promise<boolean> {
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 1000);
        try {
            const response = await fetch(`${this.getBaseUrl()}/api/cp-frontend-config`, {
                method: 'GET',
                credentials: 'include',
                cache: 'no-store',
                headers: {Accept: 'application/json'},
                signal: controller.signal,
            });
            if (!response.ok) return false;
            const config = await response.json();
            if (!config || typeof config !== 'object') return false;
            return this.applyRuntimeConfig(config, {notify: false});
        } catch {
            return false;
        } finally {
            window.clearTimeout(timeout);
        }
    }

    setupParentListener(): Promise<boolean> {
        const identity = "CONTROL_PLANE_ADMIN";

        window.addEventListener('message', (event: MessageEvent) => {
            if (event.data.type === 'CONN_RESPONSE' || event.data.type === 'CONFIG_RESPONSE') {
                const requestedIdentity = event.data.identity;
                if (requestedIdentity !== identity) return;

                console.log('[UserBilling] RECEIVED CONFIG:', event.data.config);

                if (event.data.config) {
                    if (this.applyRuntimeConfig(event.data.config)) {
                        console.log('[UserBilling] Applying updates to settings');
                    }
                }
            }
        });

        if (this.hasPlaceholderSettings()) {
            return new Promise<boolean>((resolve) => {
                let resolved = false;
                const finish = (ready: boolean) => {
                    if (resolved) return;
                    resolved = true;
                    resolve(ready);
                };
                const requestParentConfig = () => {
                    window.parent.postMessage({
                        type: 'CONFIG_REQUEST',
                        data: {
                            requestedFields: [
                                'baseUrl', 'accessToken', 'idToken', 'idTokenHeader',
                                'defaultTenant', 'defaultProject', 'defaultAppBundleId'
                            ],
                            identity: identity
                        }
                    }, '*');
                    const timeout = window.setTimeout(() => {
                        console.warn('[UserBilling] Config request timeout - proceeding with current settings');
                        finish(false);
                    }, 3000);
                    const originalCallback = this.configReceivedCallback;
                    this.onConfigReceived(() => {
                        window.clearTimeout(timeout);
                        if (originalCallback) originalCallback();
                        finish(true);
                    });
                };
                void this.loadFrontendConfig().then((loaded) => {
                    if (loaded) {
                        finish(true);
                    } else {
                        requestParentConfig();
                    }
                });
            });
        }

        return Promise.resolve(!this.hasPlaceholderSettings());
    }
}

const settings = new SettingsManager();

function makeAuthHeaders(base?: HeadersInit): Headers {
    const headers = new Headers(base);
    const accessToken = settings.getAccessToken();
    const idToken = settings.getIdToken();
    const idTokenHeader = settings.getIdTokenHeader();

    if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
    if (idToken) headers.set(idTokenHeader, idToken);
    return headers;
}

// =============================================================================
// API Client
// =============================================================================

class BillingAPI {
    private getMeUrl(path: string): string { return `${settings.getBaseUrl()}/api/economics/me${path}`; }
    private getStripeCheckoutUrl(path: string): string { return `${settings.getBaseUrl()}/api/economics/stripe/checkout${path}`; }

    private async fetchWithAuth(url: string, options: RequestInit = {}): Promise<Response> {
        const headers = makeAuthHeaders(options.headers);
        const response = await fetch(url, { ...options, headers });
        if (!response.ok) {
            const errorText = await response.text().catch(() => response.statusText);
            throw new Error(`API error: ${response.status} - ${errorText}`);
        }
        return response;
    }

    async getBudgetBreakdown(): Promise<{ status: string; } & QuotaBreakdown> {
        const response = await this.fetchWithAuth(this.getMeUrl('/budget-breakdown'));
        return response.json();
    }

    async listSubscriptionPlans(): Promise<{ plans: SubscriptionPlan[] }> {
        const response = await this.fetchWithAuth(this.getMeUrl('/subscription-plans'));
        return response.json();
    }

    async getSubscription(): Promise<{ subscription: Subscription | null }> {
        const response = await this.fetchWithAuth(this.getMeUrl('/subscription'));
        return response.json();
    }

    async openCustomerPortal(): Promise<{ portal_url: string }> {
        const returnUrl = currentFrameReturnUrl();
        const response = await this.fetchWithAuth(
            `${this.getMeUrl('/stripe/customer-portal')}?return_url=${encodeURIComponent(returnUrl)}`,
            { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }
        );
        return response.json();
    }

    async cancelSubscription(): Promise<{ status: string; action: string; message: string }> {
        const response = await this.fetchWithAuth(this.getMeUrl('/subscription/cancel'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}'
        });
        return response.json();
    }

    async createCheckoutTopup(amountUsd: number, successUrl: string, cancelUrl: string): Promise<{ session_id: string, checkout_url: string }> {
        const response = await this.fetchWithAuth(this.getStripeCheckoutUrl('/topup'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount_usd: amountUsd, success_url: successUrl, cancel_url: cancelUrl })
        });
        return response.json();
    }

    async createCheckoutSubscription(planId: string, successUrl: string, cancelUrl: string): Promise<{ session_id: string, checkout_url: string }> {
        const response = await this.fetchWithAuth(this.getStripeCheckoutUrl('/subscription'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plan_id: planId, success_url: successUrl, cancel_url: cancelUrl })
        });
        return response.json();
    }
}

// =============================================================================
// UI Components
// =============================================================================

const Card: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => (
    <div className={`bg-white rounded-2xl shadow-sm border border-gray-200/70 overflow-hidden ${className}`}>
        {children}
    </div>
);

const Button: React.FC<{
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    variant?: 'primary' | 'secondary';
    className?: string;
}> = ({ children, onClick, disabled = false, variant = 'primary', className = '' }) => {
    const variants = {
        primary: 'bg-gray-900 hover:bg-gray-800 text-white',
        secondary: 'bg-white hover:bg-gray-50 text-gray-900 border border-gray-200',
    };
    return (
        <button
            onClick={onClick}
            disabled={disabled}
            className={`px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${variants[variant]} ${className}`}
        >
            {children}
        </button>
    );
};

const LoadingSpinner = () => (
    <div className="flex justify-center py-10">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-gray-200 border-t-gray-900"></div>
    </div>
);

function formatCount(value: number | null | undefined): string {
    if (value == null) return '∞';
    return value.toLocaleString();
}

function formatUsd(value: number | null | undefined): string {
    const amount = Number(value || 0);
    return `$${amount.toFixed(2)}`;
}

function formatUsdLimit(value: number | null | undefined): string {
    if (value == null) return '∞';
    return formatUsd(value);
}

function formatDateTime(value: string | null | undefined): string {
    if (!value) return 'Not available';
    return new Date(value).toLocaleString();
}

function currentFrameReturnUrl(): string {
    try {
        return window.parent !== window ? window.parent.location.href : window.location.href;
    } catch {
        return window.location.href;
    }
}

function navigateTopLevel(url: string): void {
    try {
        window.top!.location.href = url;
    } catch {
        window.location.href = url;
    }
}

const MetricRow: React.FC<{
    label: string;
    used: number;
    limit: number | null | undefined;
    remaining: number | null | undefined;
    usedUsd?: number | null;
    limitUsd?: number | null;
    remainingUsd?: number | null;
}> = ({ label, used, limit, remaining, usedUsd, limitUsd, remainingUsd }) => {
    const hasUsd = usedUsd != null || limitUsd != null || remainingUsd != null;
    return (
        <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3">
            <div className="flex items-center justify-between gap-3 text-sm">
                <span className="text-gray-500">{label}</span>
                <span className="font-semibold text-gray-900">
                    {hasUsd ? `${formatUsd(usedUsd)} / ${formatUsdLimit(limitUsd)}` : `${formatCount(used)} / ${formatCount(limit)}`}
                </span>
            </div>
            <div className="mt-1 flex items-center justify-between gap-3 text-xs text-gray-500">
                <span>
                    Remaining: {hasUsd ? formatUsdLimit(remainingUsd) : formatCount(remaining)}
                </span>
            </div>
            {hasUsd && (
                <div className="mt-1 text-xs text-gray-400">
                    Tokens: {formatCount(used)} / {formatCount(limit)} · remaining {formatCount(remaining)}
                </div>
            )}
        </div>
    );
};

const PlanReservationMetric: React.FC<{
    tokens: number;
    usd?: number | null;
}> = ({ tokens, usd }) => (
    <div className="rounded-xl border border-amber-100 bg-amber-50 px-4 py-3">
        <div className="flex items-center justify-between gap-3 text-sm">
            <span className="text-amber-800">Plan reserved</span>
            <span className="font-semibold text-amber-950">{formatUsd(usd)}</span>
        </div>
        <div className="mt-1 text-xs text-amber-800">
            {formatCount(tokens)} tokens held by in-flight requests
        </div>
    </div>
);

const WalletMetric: React.FC<{
    label: string;
    value: string;
    hint?: string;
}> = ({ label, value, hint }) => (
    <div className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3">
        <div className="text-xs font-semibold uppercase tracking-wider text-emerald-700">{label}</div>
        <div className="mt-1 text-sm font-semibold text-emerald-950">{value}</div>
        {hint && <div className="mt-1 text-xs text-emerald-800">{hint}</div>}
    </div>
);

// =============================================================================
// Main Component
// =============================================================================

const UserBillingDashboard: React.FC = () => {
    const api = useMemo(() => new BillingAPI(), []);
    const [configStatus, setConfigStatus] = useState<'initializing' | 'ready' | 'error'>('initializing');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [breakdown, setBreakdown] = useState<QuotaBreakdown | null>(null);
    const [plans, setPlans] = useState<SubscriptionPlan[]>([]);
    const [subscription, setSubscription] = useState<Subscription | null>(null);

    const [topupAmount, setTopupAmount] = useState<string>('10');
    const [cancelConfirm, setCancelConfirm] = useState(false);

    useEffect(() => {
        settings.setupParentListener().then(() => {
            setConfigStatus('ready');
        }).catch(() => setConfigStatus('error'));
    }, []);

    useEffect(() => {
        if (configStatus === 'ready') {
            loadData();
        }
    }, [configStatus]);

    const loadData = async () => {
        setLoading(true);
        setError(null);
        try {
            const [brk, pData, subData] = await Promise.all([
                api.getBudgetBreakdown().catch(() => null),
                api.listSubscriptionPlans().catch(() => ({ plans: [] })),
                api.getSubscription().catch(() => ({ subscription: null }))
            ]);
            if (brk) setBreakdown(brk);
            if (pData) setPlans(pData.plans);
            if (subData) setSubscription(subData.subscription);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleTopup = async () => {
        const amt = parseFloat(topupAmount);
        if (isNaN(amt) || amt < 0.5) {
            setError("Amount must be at least $0.50");
            return;
        }
        try {
            setLoading(true);
            const returnUrl = currentFrameReturnUrl();
            const res = await api.createCheckoutTopup(amt, returnUrl, returnUrl);
            if (res.checkout_url) navigateTopLevel(res.checkout_url);
        } catch (err: any) {
            setError(err.message);
            setLoading(false);
        }
    };

    const handleSubscribe = async (planId: string) => {
        try {
            setLoading(true);
            const returnUrl = currentFrameReturnUrl();
            const res = await api.createCheckoutSubscription(planId, returnUrl, returnUrl);
            if (res.checkout_url) navigateTopLevel(res.checkout_url);
        } catch (err: any) {
            setError(err.message);
            setLoading(false);
        }
    };

    const handleCustomerPortal = async () => {
        try {
            setLoading(true);
            const res = await api.openCustomerPortal();
            if (res.portal_url) navigateTopLevel(res.portal_url);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleCancelSubscription = async () => {
        if (!cancelConfirm) { setCancelConfirm(true); return; }
        try {
            setLoading(true);
            setCancelConfirm(false);
            await api.cancelSubscription();
            await loadData();
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    if (configStatus === 'initializing') return <LoadingSpinner />;

    const currentPlanId = breakdown?.plan_id || subscription?.plan_id || 'free';
    const personalCredits = breakdown?.lifetime_credits;
    const availablePersonalCreditsUsd = personalCredits?.available_usd || 0;
    const hasPersonalOverflowCover = availablePersonalCreditsUsd > 0;

    return (
        <div className="h-screen overflow-hidden bg-gray-50/50 p-3 md:p-4 font-sans text-gray-900">
            <div className="mx-auto flex h-full max-w-5xl flex-col gap-4 overflow-hidden">
                
                <div className="flex shrink-0 items-start justify-between gap-4">
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight">Billing & Plans</h1>
                        <p className="mt-1 text-sm text-gray-500">Manage your plan quota and personal wallet.</p>
                    </div>
                    <div className="shrink-0 flex items-center gap-2">
                        <button
                            onClick={loadData}
                            disabled={loading}
                            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-gray-100 text-gray-600 border border-gray-200 hover:bg-gray-200 disabled:opacity-50"
                        >
                            <svg xmlns="http://www.w3.org/2000/svg" className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/>
                                <path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>
                            </svg>
                            Refresh
                        </button>
                        {subscription?.stripe_customer_id && (
                            <button
                                onClick={handleCustomerPortal}
                                disabled={loading}
                                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 disabled:opacity-50"
                            >
                                Manage Billing ↗
                            </button>
                        )}
                    </div>
                </div>

                {error && (
                    <div className="p-4 bg-rose-50 border border-rose-200 text-rose-800 rounded-xl">
                        {error}
                    </div>
                )}

                {loading && !breakdown && <LoadingSpinner />}

                {!loading && breakdown && (
                    <div className="min-h-0 overflow-y-auto pr-1">
                        <div className="flex flex-col gap-4">
                            {/* Current Plan Overview */}
                            <Card className="p-4">
                                <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500">Plan quota</h3>
                                <div className="text-xl font-bold capitalize">{currentPlanId}</div>
                                {subscription?.status === 'active' && (
                                    <div className="mt-2 inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-800">
                                        Active Subscription
                                    </div>
                                )}
                                <div className="mt-4 rounded-xl border border-sky-100 bg-sky-50 px-4 py-3 text-sm text-sky-900">
                                    <div className="font-semibold">Active quota buckets</div>
                                    <p className="mt-1 text-sky-800">
                                        The numbers below reflect your combined usage across all apps in this workspace. Hourly usage is the last 60 minutes; daily and monthly usage are the current quota periods since their last reset.
                                    </p>
                                </div>
                                <div className="mt-4 space-y-3">
                                    {subscription?.started_at && (
                                        <div className="flex justify-between text-sm">
                                            <span className="text-gray-500">Started</span>
                                            <span className="font-medium">{new Date(subscription.started_at).toLocaleString()}</span>
                                        </div>
                                    )}
                                    {subscription?.next_charge_at && (
                                        <div className="flex justify-between text-sm">
                                            <span className="text-gray-500">Next renewal</span>
                                            <span className="font-medium">{new Date(subscription.next_charge_at).toLocaleString()}</span>
                                        </div>
                                    )}
                                    <div className="border-t border-gray-100 pt-3">
                                        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Last 60 minutes</div>
                                        <MetricRow
                                            label="Tokens"
                                            used={breakdown.current_usage.tokens_this_hour}
                                            limit={breakdown.effective_policy.tokens_per_hour}
                                            remaining={breakdown.remaining.tokens_this_hour}
                                            usedUsd={breakdown.current_usage.tokens_this_hour_usd}
                                            limitUsd={breakdown.effective_policy.usd_per_hour}
                                            remainingUsd={breakdown.remaining.tokens_this_hour_usd}
                                        />
                                        {breakdown.reset_windows?.hour_reset_at && (
                                            <div className="mt-2 text-xs text-gray-500">
                                                Hourly window resets at {formatDateTime(breakdown.reset_windows.hour_reset_at)}
                                            </div>
                                        )}
                                    </div>
                                    <div className="border-t border-gray-100 pt-3">
                                        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Current 24h quota period</div>
                                        <MetricRow
                                            label="Requests"
                                            used={breakdown.current_usage.requests_today}
                                            limit={breakdown.effective_policy.requests_per_day}
                                            remaining={breakdown.remaining.requests_today}
                                        />
                                        <div className="mt-2">
                                            <MetricRow
                                                label="Tokens"
                                                used={breakdown.current_usage.tokens_today}
                                                limit={breakdown.effective_policy.tokens_per_day}
                                                remaining={breakdown.remaining.tokens_today}
                                                usedUsd={breakdown.current_usage.tokens_today_usd}
                                                limitUsd={breakdown.effective_policy.usd_per_day}
                                                remainingUsd={breakdown.remaining.tokens_today_usd}
                                            />
                                        </div>
                                        {breakdown.reset_windows?.day_reset_at && (
                                            <div className="mt-2 text-xs text-gray-500">
                                                Daily quota resets at {formatDateTime(breakdown.reset_windows.day_reset_at)}
                                            </div>
                                        )}
                                    </div>
                                    <div className="border-t border-gray-100 pt-3">
                                        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Current 30-day quota period</div>
                                        <MetricRow
                                            label="Requests"
                                            used={breakdown.current_usage.requests_this_month}
                                            limit={breakdown.effective_policy.requests_per_month}
                                            remaining={breakdown.remaining.requests_this_month}
                                        />
                                        <div className="mt-2">
                                            <MetricRow
                                                label="Tokens"
                                                used={breakdown.current_usage.tokens_this_month}
                                                limit={breakdown.effective_policy.tokens_per_month}
                                                remaining={breakdown.remaining.tokens_this_month}
                                                usedUsd={breakdown.current_usage.tokens_this_month_usd}
                                                limitUsd={breakdown.effective_policy.usd_per_month}
                                                remainingUsd={breakdown.remaining.tokens_this_month_usd}
                                            />
                                        </div>
                                        {breakdown.reset_windows?.month_reset_at && (
                                            <div className="mt-2 text-xs text-gray-500">
                                                Monthly quota resets at {formatDateTime(breakdown.reset_windows.month_reset_at)}
                                            </div>
                                        )}
                                    </div>
                                    <PlanReservationMetric
                                        tokens={breakdown.current_usage.tokens_reserved || 0}
                                        usd={breakdown.current_usage.tokens_reserved_usd}
                                    />
                                    <div className={`rounded-xl border px-4 py-3 text-sm ${hasPersonalOverflowCover ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>
                                        <div className="font-semibold">If one request is larger than your remaining plan tokens</div>
                                        <p className="mt-1">
                                            The part above your remaining plan quota is covered by personal credits. You currently have {formatUsd(availablePersonalCreditsUsd)} available.
                                        </p>
                                        {!hasPersonalOverflowCover && (
                                            <p className="mt-1">
                                                If a request is larger than the remaining plan quota shown above, you will need to wait for the rolling window reset or add funds.
                                            </p>
                                        )}
                                    </div>
                                </div>
                                {subscription?.status === 'active' && subscription?.provider === 'stripe' && (
                                    <div className="mt-6 pt-4 border-t border-gray-100">
                                        {cancelConfirm ? (
                                            <div className="space-y-2">
                                                <p className="text-xs text-gray-500">Cancel at end of current billing period?</p>
                                                <div className="flex gap-2">
                                                    <button
                                                        onClick={handleCancelSubscription}
                                                        disabled={loading}
                                                        className="flex-1 py-1.5 text-xs font-semibold text-white bg-rose-600 hover:bg-rose-700 rounded-lg disabled:opacity-50"
                                                    >
                                                        Yes, cancel
                                                    </button>
                                                    <button
                                                        onClick={() => setCancelConfirm(false)}
                                                        className="flex-1 py-1.5 text-xs font-semibold text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg"
                                                    >
                                                        Keep plan
                                                    </button>
                                                </div>
                                            </div>
                                        ) : (
                                            <button
                                                onClick={handleCancelSubscription}
                                                disabled={loading}
                                                className="text-xs text-rose-600 hover:text-rose-800 font-medium disabled:opacity-50"
                                            >
                                                Cancel subscription
                                            </button>
                                        )}
                                    </div>
                                )}
                            </Card>

                            {/* Wallet Overview */}
                            <Card className="p-4">
                                <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500">Wallet / Personal Credits</h3>
                                <div className="text-xl font-bold">
                                    ${(breakdown.lifetime_credits?.available_usd || 0).toFixed(2)}
                                </div>
                                <div className="mb-4 text-sm text-gray-500">available</div>
                                <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-4">
                                    <WalletMetric
                                        label="Purchased"
                                        value={formatUsd(personalCredits?.purchased_usd || 0)}
                                    />
                                    <WalletMetric
                                        label="Spent"
                                        value={formatUsd(personalCredits?.spent_usd || 0)}
                                    />
                                    <WalletMetric
                                        label="Reserved"
                                        value={formatUsd(personalCredits?.reserved_usd || 0)}
                                    />
                                    <WalletMetric
                                        label="Available"
                                        value={formatUsd(personalCredits?.available_usd || 0)}
                                    />
                                </div>
                                
                                <div className="rounded-xl border border-gray-100 bg-gray-50 p-3">
                                    <label className="block text-xs font-semibold text-gray-700 mb-2">Top up balance (USD)</label>
                                    <div className="flex gap-2">
                                        <div className="relative flex-1">
                                            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">$</span>
                                            <input 
                                                type="number" 
                                                value={topupAmount}
                                                onChange={e => setTopupAmount(e.target.value)}
                                                className="w-full pl-7 pr-4 py-2 bg-white border border-gray-200 rounded-lg focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300 outline-none"
                                            />
                                        </div>
                                        <Button onClick={handleTopup} disabled={loading}>Add Funds</Button>
                                    </div>
                                </div>
                            </Card>
                            {breakdown.subscription_balance?.has_subscription && (
                                <Card className="p-4">
                                    <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500">Subscription Balance</h3>
                                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
                                        <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3">
                                            <div className="text-xs font-semibold uppercase tracking-wider text-gray-400">Available</div>
                                            <div className="mt-1 text-sm font-medium text-gray-900">
                                                {formatUsd(breakdown.subscription_balance.available_usd)}
                                            </div>
                                        </div>
                                        <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3">
                                            <div className="text-xs font-semibold uppercase tracking-wider text-gray-400">Reserved</div>
                                            <div className="mt-1 text-sm font-medium text-gray-900">
                                                {formatUsd(breakdown.subscription_balance.reserved_usd || 0)}
                                            </div>
                                        </div>
                                        <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3">
                                            <div className="text-xs font-semibold uppercase tracking-wider text-gray-400">Spent This Period</div>
                                            <div className="mt-1 text-sm font-medium text-gray-900">
                                                {formatUsd(breakdown.subscription_balance.spent_usd)}
                                            </div>
                                        </div>
                                        <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3">
                                            <div className="text-xs font-semibold uppercase tracking-wider text-gray-400">Period Ends</div>
                                            <div className="mt-1 text-sm font-medium text-gray-900">
                                                {formatDateTime(breakdown.subscription_balance.period_end)}
                                            </div>
                                        </div>
                                    </div>
                                </Card>
                            )}
                        </div>

                        {/* Available Plans */}
                        <div className="mt-4">
                            <h3 className="mb-3 text-lg font-bold">Available Subscriptions</h3>
                            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                                {plans.map(plan => {
                                    const isCurrent = plan.plan_id === currentPlanId;
                                    return (
                                        <Card key={plan.plan_id} className={`flex flex-col p-4 ${isCurrent ? 'ring-2 ring-gray-900' : ''}`}>
                                            {isCurrent && (
                                                <span className="self-start px-2 py-1 bg-gray-900 text-white text-xs font-bold rounded mb-4">CURRENT</span>
                                            )}
                                            <div className="text-lg font-bold capitalize mb-1">{plan.plan_id}</div>
                                            <div className="text-2xl font-bold mb-4">${(plan.monthly_price_cents / 100).toFixed(2)}<span className="text-sm font-normal text-gray-500">/mo</span></div>
                                            {plan.notes && <p className="text-sm text-gray-600 mb-6 flex-1">{plan.notes}</p>}
                                            {!isCurrent && (
                                                <Button 
                                                    className="w-full mt-auto" 
                                                    onClick={() => handleSubscribe(plan.plan_id)}
                                                    disabled={loading}
                                                >
                                                    Subscribe
                                                </Button>
                                            )}
                                            {isCurrent && (
                                                <Button variant="secondary" className="w-full mt-auto" disabled>
                                                    Active
                                                </Button>
                                            )}
                                        </Card>
                                    );
                                })}
                                {plans.length === 0 && (
                                    <div className="col-span-full text-center py-10 text-gray-500 border-2 border-dashed border-gray-200 rounded-2xl">
                                        No subscription plans available right now.
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                )}

            </div>
        </div>
    );
};

const root = ReactDOM.createRoot(document.getElementById('root') as HTMLElement);
root.render(<UserBillingDashboard />);
