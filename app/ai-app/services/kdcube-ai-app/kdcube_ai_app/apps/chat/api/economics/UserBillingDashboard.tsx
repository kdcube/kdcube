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
    };
    current_usage: {
        requests_today: number;
        requests_this_month: number;
        tokens_this_hour: number;
        tokens_today: number;
        tokens_this_month: number;
    };
    remaining: {
        requests_today: number | null;
        requests_this_month: number | null;
        tokens_this_hour: number | null;
        tokens_today: number | null;
        tokens_this_month: number | null;
        percentage_used: number | null;
    };
    lifetime_credits: {
        has_lifetime_credits: boolean;
        tokens_purchased: number;
        tokens_consumed: number;
        tokens_available: number;
        available_usd: number;
    } | null;
    subscription_balance?: {
        has_subscription?: boolean;
        active?: boolean;
        plan_id?: string | null;
        period_start?: string | null;
        period_end?: string | null;
        available_usd: number;
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
            return 'http://localhost:8010';
        }
        try {
            const url = new URL(this.settings.baseUrl);
            if (url.port === 'None' || url.hostname.includes('None')) return 'http://localhost:8010';
            return this.settings.baseUrl;
        } catch (e) {
            return 'http://localhost:8010';
        }
    }

    getAccessToken(): string | null {
        return this.settings.accessToken === this.PLACEHOLDER_ACCESS_TOKEN ? null : this.settings.accessToken;
    }

    getIdToken(): string | null {
        return this.settings.idToken === this.PLACEHOLDER_ID_TOKEN ? null : this.settings.idToken;
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

    setupParentListener(): Promise<boolean> {
        const identity = "CONTROL_PLANE_ADMIN";
        const inIframe = window.parent && window.parent !== window;

        console.log('[UserBilling] Initializing setupParentListener. In iframe:', inIframe);

        window.addEventListener('message', (event: MessageEvent) => {
            if (event.data.type === 'CONN_RESPONSE' || event.data.type === 'CONFIG_RESPONSE') {
                const requestedIdentity = event.data.identity;
                if (requestedIdentity !== identity) return;

                console.log('[UserBilling] RECEIVED CONFIG:', event.data.config);

                if (event.data.config) {
                    const config = event.data.config;
                    const updates: Partial<AppSettings> = {};

                    if (config.baseUrl && typeof config.baseUrl === 'string') updates.baseUrl = config.baseUrl;
                    if (config.accessToken !== undefined) updates.accessToken = config.accessToken;
                    if (config.idToken !== undefined) updates.idToken = config.idToken;
                    if (config.idTokenHeader) updates.idTokenHeader = config.idTokenHeader;

                    if (Object.keys(updates).length > 0) {
                        console.log('[UserBilling] Applying updates to settings:', Object.keys(updates));
                        this.updateSettings(updates);
                        if (this.configReceivedCallback) this.configReceivedCallback();
                    }
                }
            }
        });

        // If we are in an iframe, we ALWAYS want to ask the parent for the latest tokens/ID
        if (inIframe) {
            console.log('[UserBilling] In iframe, sending CONFIG_REQUEST...');
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

            return new Promise<boolean>((resolve) => {
                const timeout = setTimeout(() => {
                    console.warn('[UserBilling] Config request timeout - proceeding with current settings');
                    resolve(false);
                }, 3000);
                const originalCallback = this.configReceivedCallback;
                this.onConfigReceived(() => {
                    clearTimeout(timeout);
                    if (originalCallback) originalCallback();
                    resolve(true);
                });
            });
        } else {
            console.log('[UserBilling] Not in iframe, using placeholder/patched settings');
            return Promise.resolve(!this.hasPlaceholderSettings());
        }
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
        const returnUrl = window.parent !== window ? window.parent.location.href : window.location.href;
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
            const returnUrl = window.parent !== window ? window.parent.location.href : window.location.href;
            const res = await api.createCheckoutTopup(amt, returnUrl, returnUrl);
            if (res.checkout_url) window.top!.location.href = res.checkout_url;
        } catch (err: any) {
            setError(err.message);
            setLoading(false);
        }
    };

    const handleSubscribe = async (planId: string) => {
        try {
            setLoading(true);
            const returnUrl = window.parent !== window ? window.parent.location.href : window.location.href;
            const res = await api.createCheckoutSubscription(planId, returnUrl, returnUrl);
            if (res.checkout_url) window.top!.location.href = res.checkout_url;
        } catch (err: any) {
            setError(err.message);
            setLoading(false);
        }
    };

    const handleCustomerPortal = async () => {
        try {
            setLoading(true);
            const res = await api.openCustomerPortal();
            if (res.portal_url) window.top!.location.href = res.portal_url;
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
    if (!settings.getAccessToken()) return <div className="p-6 text-center text-gray-500">Not authenticated. Please log in.</div>;

    const currentPlanId = breakdown?.plan_id || subscription?.plan_id || 'free';

    return (
        <div className="min-h-screen bg-gray-50/50 p-4 md:p-8 font-sans text-gray-900">
            <div className="max-w-4xl mx-auto space-y-6">
                
                <div className="mb-8 flex items-start justify-between gap-4">
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight">Billing & Plans</h1>
                        <p className="text-gray-500 mt-2">Manage your subscription and lifetime token balance.</p>
                    </div>
                    {subscription?.stripe_customer_id && (
                        <button
                            onClick={handleCustomerPortal}
                            disabled={loading}
                            className="shrink-0 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 disabled:opacity-50"
                        >
                            Manage Billing ↗
                        </button>
                    )}
                </div>

                {error && (
                    <div className="p-4 bg-rose-50 border border-rose-200 text-rose-800 rounded-xl">
                        {error}
                    </div>
                )}

                {loading && !breakdown && <LoadingSpinner />}

                {!loading && breakdown && (
                    <>
                        <div className="flex flex-col gap-6">
                            {/* Current Plan Overview */}
                            <Card className="p-6">
                                <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Current Plan</h3>
                                <div className="text-2xl font-bold mb-2 capitalize">{currentPlanId}</div>
                                {subscription?.status === 'active' && (
                                    <div className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-800 border border-emerald-200">
                                        Active Subscription
                                    </div>
                                )}
                                <div className="mt-6 space-y-3">
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
                                        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Hourly</div>
                                        <div className="flex justify-between text-sm">
                                            <span className="text-gray-500">Tokens</span>
                                            <span className="font-medium">{breakdown.current_usage.tokens_this_hour.toLocaleString()} / {breakdown.effective_policy.tokens_per_hour?.toLocaleString() || '∞'}</span>
                                        </div>
                                    </div>
                                    <div className="border-t border-gray-100 pt-3">
                                        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Daily</div>
                                        <div className="flex justify-between text-sm mb-1.5">
                                            <span className="text-gray-500">Requests</span>
                                            <span className="font-medium">{breakdown.current_usage.requests_today} / {breakdown.effective_policy.requests_per_day || '∞'}</span>
                                        </div>
                                        <div className="flex justify-between text-sm">
                                            <span className="text-gray-500">Tokens</span>
                                            <span className="font-medium">{breakdown.current_usage.tokens_today.toLocaleString()} / {breakdown.effective_policy.tokens_per_day?.toLocaleString() || '∞'}</span>
                                        </div>
                                    </div>
                                    <div className="border-t border-gray-100 pt-3">
                                        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Monthly</div>
                                        <div className="flex justify-between text-sm mb-1.5">
                                            <span className="text-gray-500">Requests</span>
                                            <span className="font-medium">{breakdown.current_usage.requests_this_month} / {breakdown.effective_policy.requests_per_month || '∞'}</span>
                                        </div>
                                        <div className="flex justify-between text-sm">
                                            <span className="text-gray-500">Tokens</span>
                                            <span className="font-medium">{breakdown.current_usage.tokens_this_month.toLocaleString()} / {breakdown.effective_policy.tokens_per_month?.toLocaleString() || '∞'}</span>
                                        </div>
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

                            {/* Lifetime Tokens Overview */}
                            <Card className="p-6">
                                <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Lifetime Tokens</h3>
                                <div className="text-2xl font-bold mb-2">
                                    ${(breakdown.lifetime_credits?.available_usd || 0).toFixed(2)}
                                </div>
                                <div className="text-sm text-gray-500 mb-6">
                                    {breakdown.lifetime_credits?.tokens_available.toLocaleString() || 0} tokens available
                                </div>
                                
                                <div className="bg-gray-50 rounded-xl p-4 border border-gray-100">
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
                        </div>

                        {/* Available Plans */}
                        <div className="mt-10">
                            <h3 className="text-xl font-bold mb-4">Available Subscriptions</h3>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                                {plans.map(plan => {
                                    const isCurrent = plan.plan_id === currentPlanId;
                                    return (
                                        <Card key={plan.plan_id} className={`p-6 flex flex-col ${isCurrent ? 'ring-2 ring-gray-900' : ''}`}>
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
                    </>
                )}

            </div>
        </div>
    );
};

const root = ReactDOM.createRoot(document.getElementById('root') as HTMLElement);
root.render(<UserBillingDashboard />);
