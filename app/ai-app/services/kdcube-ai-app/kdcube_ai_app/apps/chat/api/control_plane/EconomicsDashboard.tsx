// COMPLETE PROFESSIONAL VERSION - Control Plane Admin React App (TypeScript)

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

interface QuotaPolicy {
    tenant: string;
    project: string;
    user_type: string;
    max_concurrent: number | null;
    requests_per_day: number | null;
    requests_per_month: number | null;
    tokens_per_hour: number | null;
    tokens_per_day: number | null;
    tokens_per_month: number | null;
    usd_per_hour?: number | null;
    usd_per_day?: number | null;
    usd_per_month?: number | null;
    reference_model?: string;
    notes: string | null;
}

interface BudgetPolicy {
    tenant: string;
    project: string;
    provider: string;
    usd_per_hour: number | null;
    usd_per_day: number | null;
    usd_per_month: number | null;
    notes: string | null;
}

interface TierOverride {
    requests_per_day: number | null;
    requests_per_month: number | null;
    tokens_per_hour: number | null;
    tokens_per_day: number | null;
    tokens_per_month: number | null;
    usd_per_hour?: number | null;
    usd_per_day?: number | null;
    usd_per_month?: number | null;
    max_concurrent: number | null;
    expires_at: string | null;
    notes: string | null;
    is_expired: boolean;
    reference_model?: string;
}

interface LifetimeBudget {
    tokens_purchased: number;
    tokens_consumed: number;
    tokens_gross_remaining: number;
    tokens_reserved: number;
    tokens_available: number;
    available_usd: number;
    purchase_amount_usd: number | null;
    reference_model?: string;
}

interface TierBalance {
    user_id: string;
    has_tier_override: boolean;
    has_lifetime_budget: boolean;
    tier_override: TierOverride | null;
    lifetime_budget: LifetimeBudget | null;
    message?: string;
}

interface LifetimeBalance {
    user_id: string;
    has_purchased_credits: boolean;
    balance_tokens: number;
    balance_usd: number;

    // Backend returns these (NOT minimum_required_usd)
    minimum_required_tokens: number;
    can_use_budget: boolean;

    reference_model?: string;
    message?: string;
}

interface AppBudgetBalance {
    balance_usd: number;
    lifetime_added_usd: number;
    lifetime_spent_usd: number;
}

interface AppBudgetSpending {
    hour: number;
    day: number;
    month: number;
}

interface AppBudget {
    balance: AppBudgetBalance;
    current_month_spending: AppBudgetSpending;
    by_bundle: Record<string, AppBudgetSpending>;
}

interface TokenReservationView {
    reservation_id: string;
    bundle_id: string | null;
    tokens_reserved: number;
    tokens_used: number;
    status: string;
    expires_at: string | null;
    created_at: string | null;
    updated_at: string | null;
    notes: string | null;
}

interface LifetimeCreditsBreakdown {
    has_lifetime_credits: boolean;
    tokens_purchased: number;
    tokens_consumed: number;
    tokens_gross_remaining: number;
    tokens_reserved: number;
    tokens_available: number;
    available_usd: number;
    lifetime_usd_purchased: number | null;
    last_purchase: {
        id: string | null;
        amount_usd: number | null;
        notes: string | null;
    };
    reference_model: string;
}

interface QuotaBreakdown {
    user_id: string;
    user_type: string;

    bundle_breakdown: Record<string, any>;

    base_policy: {
        max_concurrent: number | null;
        requests_per_day: number | null;
        requests_per_month: number | null;
        total_requests: number | null;
        tokens_per_hour: number | null;
        tokens_per_day: number | null;
        tokens_per_month: number | null;
        usd_per_hour?: number | null;
        usd_per_day?: number | null;
        usd_per_month?: number | null;
    };

    tier_override: {
        active: boolean;
        expired: boolean;
        expires_at: string | null;
        limits: {
            max_concurrent: number | null;
            requests_per_day: number | null;
            requests_per_month: number | null;
            total_requests: number | null;
            tokens_per_hour: number | null;
            tokens_per_day: number | null;
            tokens_per_month: number | null;
            usd_per_hour?: number | null;
            usd_per_day?: number | null;
            usd_per_month?: number | null;
        };
        grant: {
            id: string | null;
            amount_usd: number | null;
            notes: string | null;
        };
    } | null;

    effective_policy: {
        max_concurrent: number | null;
        requests_per_day: number | null;
        requests_per_month: number | null;
        total_requests: number | null;
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
        requests_total: number;
        tokens_today: number;
        tokens_this_month: number;
        tokens_today_usd?: number | null;
        tokens_this_month_usd?: number | null;
        concurrent: number;
    };

    remaining: {
        requests_today: number | null;
        requests_this_month: number | null;
        tokens_today: number | null;
        tokens_this_month: number | null;
        tokens_today_usd?: number | null;
        tokens_this_month_usd?: number | null;
        percentage_used: number | null;
    };

    lifetime_credits: LifetimeCreditsBreakdown | null;
    active_reservations: TokenReservationView[];
    reference_model?: string;
}

interface Subscription {
    tenant: string;
    project: string;
    user_id: string;
    tier: string;
    status: string;
    monthly_price_cents: number;
    started_at: string;
    next_charge_at: string | null;
    last_charged_at: string | null;
    provider: string;
    stripe_customer_id: string | null;
    stripe_subscription_id: string | null;
    created_at: string;
    updated_at: string;
}

interface SubscriptionBalance {
    period_key?: string | null;
    period_start?: string | null;
    period_end?: string | null;
    status?: string | null;
    balance_usd: number;
    reserved_usd: number;
    available_usd: number;
    balance_tokens?: number | null;
    reserved_tokens?: number | null;
    available_tokens?: number | null;
    topup_usd?: number | null;
    rolled_over_usd?: number | null;
    spent_usd?: number | null;
    lifetime_added_usd?: number | null;
    lifetime_spent_usd?: number | null;
    reference_model?: string;
}

interface SubscriptionPeriod {
    period_key: string;
    period_start: string;
    period_end: string;
    status: string;
    balance_usd: number;
    reserved_usd: number;
    available_usd: number;
    topup_usd: number;
    rolled_over_usd: number;
    spent_usd: number;
    closed_at?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
    notes?: string | null;
}

interface SubscriptionLedgerEntry {
    id: number;
    period_key: string;
    amount_cents: number;
    amount_usd: number;
    kind: string;
    note?: string | null;
    reservation_id?: string | null;
    bundle_id?: string | null;
    provider?: string | null;
    request_id?: string | null;
    created_at?: string | null;
}

interface PendingStripeRequest {
    kind: string;
    external_id: string;
    user_id: string | null;
    amount_cents: number | null;
    amount_usd: number | null;
    tokens: number | null;
    currency: string | null;
    status: string;
    metadata: any;
    created_at: string | null;
    updated_at: string | null;
}

interface EconomicsReference {
    reference_provider: string;
    reference_model: string;
    usd_per_token: number;
}

// =============================================================================
// Settings Manager
// =============================================================================

class SettingsManager {
    private readonly PLACEHOLDER_BASE_URL = '{{' + 'CHAT_BASE_URL' + '}}';
    private readonly PLACEHOLDER_ACCESS_TOKEN = '{{' + 'ACCESS_TOKEN' + '}}';
    private readonly PLACEHOLDER_ID_TOKEN = '{{' + 'ID_TOKEN' + '}}';
    private readonly PLACEHOLDER_ID_TOKEN_HEADER = '{{' + 'ID_TOKEN_HEADER' + '}}';
    private readonly PLACEHOLDER_TENANT = '{{' + 'DEFAULT_TENANT' + '}}';
    private readonly PLACEHOLDER_PROJECT = '{{' + 'DEFAULT_PROJECT' + '}}';
    private readonly PLACEHOLDER_BUNDLE_ID = '{{' + 'DEFAULT_APP_BUNDLE_ID' + '}}';
    private readonly PLACEHOLDER_STRIPE_DASHBOARD = '{{' + 'STRIPE_DASHBOARD_BASE_URL' + '}}';

    private settings: AppSettings = {
        baseUrl: '{{CHAT_BASE_URL}}',
        accessToken: '{{ACCESS_TOKEN}}',
        idToken: '{{ID_TOKEN}}',
        idTokenHeader: '{{ID_TOKEN_HEADER}}',
        defaultTenant: '{{DEFAULT_TENANT}}',
        defaultProject: '{{DEFAULT_PROJECT}}',
        defaultAppBundleId: '{{DEFAULT_APP_BUNDLE_ID}}',
        stripeDashboardBaseUrl: '{{STRIPE_DASHBOARD_BASE_URL}}'
    };

    private configReceivedCallback: (() => void) | null = null;

    getBaseUrl(): string {
        if (this.settings.baseUrl === this.PLACEHOLDER_BASE_URL) {
            return 'http://localhost:8010';
        }
        try {
            const url = new URL(this.settings.baseUrl);
            if (url.port === 'None' || url.hostname.includes('None')) {
                console.warn('[SettingsManager] Invalid baseUrl detected, using fallback');
                return 'http://localhost:8010';
            }
            return this.settings.baseUrl;
        } catch (e) {
            console.warn('[SettingsManager] Invalid baseUrl, using fallback:', this.settings.baseUrl);
            return 'http://localhost:8010';
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
        return this.settings.idTokenHeader === this.PLACEHOLDER_ID_TOKEN_HEADER
            ? 'X-ID-Token'
            : this.settings.idTokenHeader;
    }

    getDefaultTenant(): string {
        return this.settings.defaultTenant === this.PLACEHOLDER_TENANT
            ? 'home'
            : this.settings.defaultTenant;
    }

    getDefaultProject(): string {
        return this.settings.defaultProject === this.PLACEHOLDER_PROJECT
            ? 'demo'
            : this.settings.defaultProject;
    }

    getDefaultAppBundleId(): string {
        return this.settings.defaultAppBundleId === this.PLACEHOLDER_BUNDLE_ID
            ? 'kdcube.codegen.orchestrator'
            : this.settings.defaultAppBundleId;
    }

    getStripeDashboardBaseUrl(): string {
        if (!this.settings.stripeDashboardBaseUrl || this.settings.stripeDashboardBaseUrl === this.PLACEHOLDER_STRIPE_DASHBOARD) {
            return 'https://dashboard.stripe.com';
        }
        return this.settings.stripeDashboardBaseUrl;
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
        console.log('[SettingsManager] Setting up parent listener');
        const identity = "CONTROL_PLANE_ADMIN";

        window.addEventListener('message', (event: MessageEvent) => {
            if (event.data.type === 'CONN_RESPONSE' || event.data.type === 'CONFIG_RESPONSE') {
                const requestedIdentity = event.data.identity;
                if (requestedIdentity !== identity) {
                    console.warn(`[SettingsManager] Ignoring response for identity ${requestedIdentity}`);
                    return;
                }

                console.log('[SettingsManager] Received config from parent', event.data.config);

                if (event.data.config) {
                    const config = event.data.config;
                    const updates: Partial<AppSettings> = {};

                    if (config.baseUrl && typeof config.baseUrl === 'string') {
                        updates.baseUrl = config.baseUrl;
                    }
                    if (config.accessToken !== undefined) {
                        updates.accessToken = config.accessToken;
                    }
                    if (config.idToken !== undefined) {
                        updates.idToken = config.idToken;
                    }
                    if (config.idTokenHeader) {
                        updates.idTokenHeader = config.idTokenHeader;
                    }
                    if (config.defaultTenant) {
                        updates.defaultTenant = config.defaultTenant;
                    }
                    if (config.defaultProject) {
                        updates.defaultProject = config.defaultProject;
                    }
                    if (config.defaultAppBundleId) {
                        updates.defaultAppBundleId = config.defaultAppBundleId;
                    }
                    if (config.stripeDashboardBaseUrl) {
                        updates.stripeDashboardBaseUrl = config.stripeDashboardBaseUrl;
                    }

                    if (Object.keys(updates).length > 0) {
                        this.updateSettings(updates);
                        console.log('[SettingsManager] Settings updated from parent');

                        if (this.configReceivedCallback) {
                            this.configReceivedCallback();
                        }
                    }
                }
            }
        });

        if (this.hasPlaceholderSettings()) {
            console.log('[SettingsManager] Requesting config from parent');

            window.parent.postMessage({
                type: 'CONFIG_REQUEST',
                data: {
                    requestedFields: [
                        'baseUrl', 'accessToken', 'idToken', 'idTokenHeader',
                        'defaultTenant', 'defaultProject', 'defaultAppBundleId', 'stripeDashboardBaseUrl'
                    ],
                    identity: identity
                }
            }, '*');

            return new Promise<boolean>((resolve) => {
                const timeout = setTimeout(() => {
                    console.log('[SettingsManager] Config request timeout');
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
            console.log('[SettingsManager] Using existing settings');
            return Promise.resolve(!this.hasPlaceholderSettings());
        }
    }


}

const settings = new SettingsManager();

// =============================================================================
// Auth Header Helper
// =============================================================================

function appendAuthHeaders(headers: Headers): Headers {
    const accessToken = settings.getAccessToken();
    const idToken = settings.getIdToken();
    const idTokenHeader = settings.getIdTokenHeader();

    if (accessToken) {
        headers.set('Authorization', `Bearer ${accessToken}`);
    }
    if (idToken) {
        headers.set(idTokenHeader, idToken);
    }

    return headers;
}

function makeAuthHeaders(base?: HeadersInit): Headers {
    const headers = new Headers(base);
    return appendAuthHeaders(headers);
}

// =============================================================================
// Control Plane API Client
// =============================================================================

class ControlPlaneAPI {
    constructor(private basePath: string = '/api/admin/control-plane') {}

    private getFullUrl(path: string): string {
        const baseUrl = settings.getBaseUrl();
        return `${baseUrl}${this.basePath}${path}`;
    }

    private async fetchWithAuth(url: string, options: RequestInit = {}): Promise<Response> {
        const headers = makeAuthHeaders(options.headers);

        const response = await fetch(url, {
            ...options,
            headers
        });

        if (!response.ok) {
            const errorText = await response.text().catch(() => response.statusText);
            throw new Error(`API request failed: ${response.status} - ${errorText}`);
        }

        return response;
    }

    async grantTrial(payload: {
        userId: string;
        days: number;
        requestsPerDay: number;
        tokensPerHour?: number;
        tokensPerDay?: number;
        tokensPerMonth?: number;
        usdPerHour?: number;
        usdPerDay?: number;
        usdPerMonth?: number;
        notes?: string;
    }): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/tier-balance/grant-trial'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: payload.userId,
                    days: payload.days,
                    requests_per_day: payload.requestsPerDay,
                    tokens_per_hour: payload.tokensPerHour,
                    tokens_per_day: payload.tokensPerDay,
                    tokens_per_month: payload.tokensPerMonth,
                    usd_per_hour: payload.usdPerHour,
                    usd_per_day: payload.usdPerDay,
                    usd_per_month: payload.usdPerMonth,
                    notes: payload.notes
                })
            }
        );
        return response.json();
    }

    async updateTierBudget(payload: {
        userId: string;
        requestsPerDay?: number;
        requestsPerMonth?: number;
        tokensPerHour?: number;
        tokensPerDay?: number;
        tokensPerMonth?: number;
        usdPerHour?: number;
        usdPerDay?: number;
        usdPerMonth?: number;
        maxConcurrent?: number;
        expiresInDays?: number | null;
        notes?: string;
    }): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/tier-balance/update'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: payload.userId,
                    requests_per_day: payload.requestsPerDay,
                    requests_per_month: payload.requestsPerMonth,
                    tokens_per_hour: payload.tokensPerHour,
                    tokens_per_day: payload.tokensPerDay,
                    tokens_per_month: payload.tokensPerMonth,
                    usd_per_hour: payload.usdPerHour,
                    usd_per_day: payload.usdPerDay,
                    usd_per_month: payload.usdPerMonth,
                    max_concurrent: payload.maxConcurrent,
                    expires_in_days: payload.expiresInDays,
                    notes: payload.notes
                })
            }
        );
        return response.json();
    }

    async getTierBalance(userId: string, includeExpired: boolean = false): Promise<{ status: string; } & TierBalance> {
        const queryParams = new URLSearchParams({
            include_expired: includeExpired.toString()
        });
        const response = await this.fetchWithAuth(
            `${this.getFullUrl(`/tier-balance/user/${userId}`)}?${queryParams}`
        );
        return response.json();
    }

    async deactivateTierBalance(userId: string): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl(`/tier-balance/user/${userId}`),
            { method: 'DELETE' }
        );
        return response.json();
    }

    async addLifetimeCredits(userId: string, usdAmount: number, notes?: string): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/tier-balance/add-lifetime-credits'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: userId,
                    usd_amount: usdAmount,
                    ref_provider: 'anthropic',
                    ref_model: 'claude-sonnet-4-5-20250929',
                    notes
                })
            }
        );
        return response.json();
    }

    async getLifetimeBalance(userId: string): Promise<LifetimeBalance> {
        const response = await this.fetchWithAuth(
            this.getFullUrl(`/tier-balance/lifetime-balance/${userId}`)
        );
        return response.json();
    }

    async listQuotaPolicies(): Promise<{ status: string; count: number; policies: QuotaPolicy[] }> {
        const response = await this.fetchWithAuth(this.getFullUrl('/policies/quota'));
        return response.json();
    }

    async setQuotaPolicy(policy: {
        userType: string;
        maxConcurrent?: number;
        requestsPerDay?: number;
        requestsPerMonth?: number;
        totalRequests?: number;
        tokensPerHour?: number;
        tokensPerDay?: number;
        tokensPerMonth?: number;
        usdPerHour?: number;
        usdPerDay?: number;
        usdPerMonth?: number;
        notes?: string;
    }): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/policies/quota'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_type: policy.userType,
                    max_concurrent: policy.maxConcurrent,
                    requests_per_day: policy.requestsPerDay,
                    requests_per_month: policy.requestsPerMonth,
                    total_requests: policy.totalRequests,
                    tokens_per_hour: policy.tokensPerHour,
                    tokens_per_day: policy.tokensPerDay,
                    tokens_per_month: policy.tokensPerMonth,
                    usd_per_hour: policy.usdPerHour,
                    usd_per_day: policy.usdPerDay,
                    usd_per_month: policy.usdPerMonth,
                    notes: policy.notes
                })
            }
        );
        return response.json();
    }

    async listBudgetPolicies(): Promise<{ status: string; count: number; policies: BudgetPolicy[] }> {
        const response = await this.fetchWithAuth(this.getFullUrl('/policies/budget'));
        return response.json();
    }

    async setBudgetPolicy(policy: {
        provider: string;
        usdPerHour?: number;
        usdPerDay?: number;
        usdPerMonth?: number;
        notes?: string;
    }): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/policies/budget'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider: policy.provider,
                    usd_per_hour: policy.usdPerHour,
                    usd_per_day: policy.usdPerDay,
                    usd_per_month: policy.usdPerMonth,
                    notes: policy.notes
                })
            }
        );
        return response.json();
    }

    // async getUserQuotaBreakdown(userId: string, userType: string): Promise<{ status: string; } & QuotaBreakdown> {
    //     const queryParams = new URLSearchParams({
    //         user_type: userType
    //     });
    //     const response = await this.fetchWithAuth(
    //         `${this.getFullUrl(`/users/${userId}/quota-breakdown`)}?${queryParams}`
    //     );
    //     return response.json();
    // }

    async getUserBudgetBreakdown(userId: string, userType: string): Promise<{ status: string; } & QuotaBreakdown> {
        const queryParams = new URLSearchParams({
            user_type: userType,
            include_expired_override: 'true',
            reservations_limit: '50',
        });

        const response = await this.fetchWithAuth(
            `${this.getFullUrl(`/users/${userId}/budget-breakdown`)}?${queryParams}`
        );
        return response.json();
    }

    async getAppBudgetBalance(): Promise<{ status: string; } & AppBudget> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/app-budget/balance')
        );
        return response.json();
    }

    async getEconomicsReference(): Promise<{ status: string; } & EconomicsReference> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/economics/reference')
        );
        return response.json();
    }

    async topupAppBudget(usdAmount: number, notes?: string): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/app-budget/topup'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    usd_amount: usdAmount,
                    notes
                })
            }
        );
        return response.json();
    }

    async healthCheck(): Promise<any> {
        const response = await this.fetchWithAuth(this.getFullUrl('/health'));
        return response.json();
    }

    async createSubscription(payload: {
        userId: string;
        tier: string;
        provider: 'stripe' | 'internal';
        stripePriceId?: string;
        stripeCustomerId?: string;
        monthlyPriceCentsHint?: number;
    }): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/subscriptions/create'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: payload.userId,
                    tier: payload.tier,
                    provider: payload.provider,
                    stripe_price_id: payload.stripePriceId ?? null,
                    stripe_customer_id: payload.stripeCustomerId ?? null,
                    monthly_price_cents_hint: payload.monthlyPriceCentsHint ?? null,
                })
            }
        );
        return response.json();
    }

    async getSubscription(userId: string): Promise<{ status: string; subscription: Subscription | null; subscription_balance?: SubscriptionBalance | null }> {
        const response = await this.fetchWithAuth(
            this.getFullUrl(`/subscriptions/user/${userId}`)
        );
        return response.json();
    }

    async listSubscriptions(params?: {
        provider?: string;
        userId?: string;
        limit?: number;
        offset?: number;
    }): Promise<{ status: string; count: number; subscriptions: Subscription[] }> {
        const qp = new URLSearchParams();
        if (params?.provider) qp.set('provider', params.provider);
        if (params?.userId) qp.set('user_id', params.userId);
        qp.set('limit', String(params?.limit ?? 50));
        qp.set('offset', String(params?.offset ?? 0));

        const response = await this.fetchWithAuth(
            `${this.getFullUrl('/subscriptions/list')}?${qp.toString()}`
        );
        return response.json();
    }

    async listSubscriptionPeriods(
        userId: string,
        status: 'open' | 'closed' | 'all' = 'closed',
        limit: number = 50,
        offset: number = 0
    ): Promise<{ status: string; count: number; periods: SubscriptionPeriod[] }> {
        const qp = new URLSearchParams();
        qp.set('status', status);
        qp.set('limit', String(limit));
        qp.set('offset', String(offset));
        const response = await this.fetchWithAuth(
            `${this.getFullUrl(`/subscriptions/periods/${userId}`)}?${qp.toString()}`
        );
        return response.json();
    }

    async listSubscriptionLedger(
        userId: string,
        periodKey: string,
        limit: number = 200,
        offset: number = 0
    ): Promise<{ status: string; count: number; ledger: SubscriptionLedgerEntry[] }> {
        const qp = new URLSearchParams();
        qp.set('period_key', periodKey);
        qp.set('limit', String(limit));
        qp.set('offset', String(offset));
        const response = await this.fetchWithAuth(
            `${this.getFullUrl(`/subscriptions/ledger/${userId}`)}?${qp.toString()}`
        );
        return response.json();
    }

    async renewInternalSubscriptionOnce(payload: {
        userId: string;
        chargeAt?: string | null;
        idempotencyKey?: string | null;
    }): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/subscriptions/internal/renew-once'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: payload.userId,
                    charge_at: payload.chargeAt ?? null,
                    idempotency_key: payload.idempotencyKey ?? null,
                }),
            }
        );
        return response.json();
    }

    async topupSubscriptionBudget(userId: string, usdAmount: number, notes?: string, forceTopup: boolean = false): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/subscriptions/budget/topup'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: userId,
                    usd_amount: usdAmount,
                    notes,
                    force_topup: forceTopup
                })
            }
        );
        return response.json();
    }

    async setSubscriptionOverdraft(userId: string, overdraftLimitUsd: number | null, notes?: string): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/subscriptions/budget/overdraft'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: userId,
                    overdraft_limit_usd: overdraftLimitUsd,
                    notes
                })
            }
        );
        return response.json();
    }

    async sweepSubscriptionRollovers(userId?: string): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/subscriptions/rollover/sweep'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: userId || null,
                    limit: 200
                })
            }
        );
        return response.json();
    }

    async refundWallet(payload: {
        userId: string;
        paymentIntentId: string;
        usdAmount?: number | null;
        notes?: string;
    }): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/wallet/refund'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: payload.userId,
                    payment_intent_id: payload.paymentIntentId,
                    usd_amount: payload.usdAmount ?? null,
                    notes: payload.notes
                })
            }
        );
        return response.json();
    }

    async cancelSubscription(payload: {
        userId?: string;
        stripeSubscriptionId?: string;
        notes?: string;
    }): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/subscriptions/cancel'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: payload.userId ?? null,
                    stripe_subscription_id: payload.stripeSubscriptionId ?? null,
                    notes: payload.notes
                })
            }
        );
        return response.json();
    }

    async reconcileStripe(kind: 'all' | 'wallet_refund' | 'subscription_cancel' = 'all'): Promise<any> {
        const response = await this.fetchWithAuth(
            this.getFullUrl('/stripe/reconcile'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ kind, limit: 200 })
            }
        );
        return response.json();
    }

    async listPendingStripeRequests(
        kind: 'all' | 'wallet_refund' | 'subscription_cancel' = 'all',
        limit: number = 200,
        offset: number = 0
    ): Promise<{ status: string; count: number; items: PendingStripeRequest[] }> {
        const qp = new URLSearchParams();
        qp.set('kind', kind);
        qp.set('limit', String(limit));
        qp.set('offset', String(offset));
        const response = await this.fetchWithAuth(
            `${this.getFullUrl('/stripe/pending')}?${qp.toString()}`
        );
        return response.json();
    }

    async listPendingEconomicsEvents(
        kind?: string,
        userId?: string,
        limit: number = 200,
        offset: number = 0
    ): Promise<{ status: string; count: number; items: PendingStripeRequest[] }> {
        const qp = new URLSearchParams();
        if (kind) qp.set('kind', kind);
        if (userId) qp.set('user_id', userId);
        qp.set('limit', String(limit));
        qp.set('offset', String(offset));
        const response = await this.fetchWithAuth(
            `${this.getFullUrl('/economics/pending')}?${qp.toString()}`
        );
        return response.json();
    }
}

// =============================================================================
// UI Components (gentle styling)
// =============================================================================

const Card: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => (
    <div className={`bg-white rounded-2xl shadow-sm border border-gray-200/70 ${className}`}>
        {children}
    </div>
);

const CardHeader: React.FC<{ title: string; subtitle?: string; action?: React.ReactNode }> = ({ title, subtitle, action }) => (
    <div className="px-6 py-5 border-b border-gray-200/70">
        <div className="flex items-start justify-between gap-4">
            <div>
                <h2 className="text-xl font-semibold text-gray-900">{title}</h2>
                {subtitle && <p className="mt-1 text-sm text-gray-600 leading-relaxed">{subtitle}</p>}
            </div>
            {action && <div className="pt-1">{action}</div>}
        </div>
    </div>
);

const CardBody: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => (
    <div className={`px-6 py-5 ${className}`}>
        {children}
    </div>
);

const Callout: React.FC<{
    tone?: 'neutral' | 'info' | 'warning' | 'success';
    title?: string;
    children: React.ReactNode;
}> = ({ tone = 'neutral', title, children }) => {
    const tones = {
        neutral: 'bg-gray-50 border-gray-200 text-gray-700',
        info: 'bg-blue-50 border-blue-200 text-blue-900',
        warning: 'bg-amber-50 border-amber-200 text-amber-900',
        success: 'bg-emerald-50 border-emerald-200 text-emerald-900',
    };
    return (
        <div className={`rounded-xl border p-4 ${tones[tone]}`}>
            {title && <div className="text-sm font-semibold mb-1">{title}</div>}
            <div className="text-sm leading-relaxed">{children}</div>
        </div>
    );
};

const Button: React.FC<{
    children: React.ReactNode;
    onClick?: () => void;
    type?: 'button' | 'submit' | 'reset';
    variant?: 'primary' | 'secondary' | 'danger';
    disabled?: boolean;
    className?: string;
}> = ({ children, onClick, type = 'button', variant = 'primary', disabled = false, className = '' }) => {
    const variants = {
        primary: 'bg-gray-900 hover:bg-gray-800 text-white',
        secondary: 'bg-white hover:bg-gray-50 text-gray-900 border border-gray-200/80',
        danger: 'bg-rose-600 hover:bg-rose-700 text-white',
    };

    return (
        <button
            type={type}
            onClick={onClick}
            disabled={disabled}
            className={`px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${variants[variant]} ${className}`}
        >
            {children}
        </button>
    );
};

const Input: React.FC<{
    label?: string;
    value: string;
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
    type?: string;
    placeholder?: string;
    required?: boolean;
    min?: string | number;
    step?: string;
    className?: string;
}> = ({ label, value, onChange, type = 'text', placeholder, required, min, step, className = '' }) => (
    <div className={className}>
        {label && <label className="block text-sm font-medium text-gray-800 mb-2">{label}</label>}
        <input
            type={type}
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            required={required}
            min={min}
            step={step}
            className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white
                 focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300 transition-colors
                 placeholder:text-gray-400"
        />
    </div>
);

const Select: React.FC<{
    label?: string;
    value: string;
    onChange: (e: React.ChangeEvent<HTMLSelectElement>) => void;
    options: { value: string; label: string }[];
    className?: string;
}> = ({ label, value, onChange, options, className = '' }) => (
    <div className={className}>
        {label && <label className="block text-sm font-medium text-gray-800 mb-2">{label}</label>}
        <select
            value={value}
            onChange={onChange}
            className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white
                 focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300 transition-colors"
        >
            {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
    </div>
);

const TextArea: React.FC<{
    label?: string;
    value: string;
    onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
    placeholder?: string;
    rows?: number;
    className?: string;
}> = ({ label, value, onChange, placeholder, rows = 3, className = '' }) => (
    <div className={className}>
        {label && <label className="block text-sm font-medium text-gray-800 mb-2">{label}</label>}
        <textarea
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            rows={rows}
            className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white
                 focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300 transition-colors
                 placeholder:text-gray-400"
        />
    </div>
);

const StatCard: React.FC<{
    label: string;
    value: string | number;
    hint?: string;
}> = ({ label, value, hint }) => (
    <div className="rounded-2xl border border-gray-200/70 bg-white px-5 py-4 shadow-sm">
        <p className="text-xs font-semibold text-gray-500 tracking-wide uppercase">{label}</p>
        <p className="mt-2 text-2xl font-semibold text-gray-900">{value}</p>
        {hint && <p className="mt-1 text-sm text-gray-600">{hint}</p>}
    </div>
);

const LoadingSpinner: React.FC = () => (
    <div className="flex justify-center items-center py-10">
        <div className="animate-spin rounded-full h-10 w-10 border-2 border-gray-200 border-t-gray-900"></div>
    </div>
);

const EmptyState: React.FC<{ message: string; icon?: string }> = ({ message, icon = '📭' }) => (
    <div className="text-center py-10">
        <div className="text-5xl mb-3">{icon}</div>
        <p className="text-gray-600">{message}</p>
    </div>
);

// =============================================================================
// Subscription display helpers
// =============================================================================

type Tier = 'registered' | 'paid' | 'privileged';

const TIER_OPTIONS: { value: Tier; label: string }[] = [
    { value: 'registered', label: 'registered' },
    { value: 'paid', label: 'paid' },
    { value: 'privileged', label: 'privileged' },
];

const USER_TYPE_OPTIONS = [
    { value: 'registered', label: 'registered (free / pilot default)' },
    { value: 'paid', label: 'paid' },
    { value: 'privileged', label: 'privileged (premium)' },
    { value: 'admin', label: 'admin' },
    { value: 'custom', label: 'custom…' },
];

const PROVIDER_LABEL: Record<string, string> = {
    internal: 'Manual',
    stripe: 'Stripe',
};

function providerLabel(provider: string | null | undefined): string {
    if (!provider) return '—';
    return PROVIDER_LABEL[provider] ?? provider;
}

function formatDateTime(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}

function stripeUrl(path: string): string {
    const base = settings.getStripeDashboardBaseUrl().replace(/\/$/, '');
    const clean = path.replace(/^\//, '');
    return `${base}/${clean}`;
}

function stripeLinkForPending(item: PendingStripeRequest): { id: string; url: string } | null {
    const md = item.metadata || {};
    const refundId = md.stripe_refund_id;
    const subId = md.stripe_subscription_id;
    const piId = md.payment_intent_id;
    if (refundId) return { id: String(refundId), url: stripeUrl(`refunds/${refundId}`) };
    if (subId) return { id: String(subId), url: stripeUrl(`subscriptions/${subId}`) };
    if (piId) return { id: String(piId), url: stripeUrl(`payments/${piId}`) };
    return null;
}

type DueState = 'inactive' | 'overdue' | 'due_soon' | 'scheduled' | 'not_scheduled';

function getDueState(sub: Subscription, now: Date = new Date()): { state: DueState; label: string } {
    if (sub.status !== 'active') return { state: 'inactive', label: 'Inactive' };

    // If there's no next_charge_at, it's simply not scheduled (free/admin, or legacy)
    if (!sub.next_charge_at) return { state: 'not_scheduled', label: 'Not scheduled' };

    const due = new Date(sub.next_charge_at);
    if (Number.isNaN(due.getTime())) return { state: 'not_scheduled', label: 'Not scheduled' };

    const ms = due.getTime() - now.getTime();
    if (ms <= 0) return { state: 'overdue', label: 'Overdue' };

    const days = ms / (1000 * 60 * 60 * 24);
    if (days <= 7) return { state: 'due_soon', label: 'Due soon' };

    return { state: 'scheduled', label: 'Scheduled' };
}

const Pill: React.FC<{ tone?: 'neutral' | 'success' | 'warning' | 'danger'; children: React.ReactNode }> = ({
                                                                                                                tone = 'neutral',
                                                                                                                children,
                                                                                                            }) => {
    const tones = {
        neutral: 'bg-gray-100 text-gray-700 border-gray-200',
        success: 'bg-emerald-50 text-emerald-800 border-emerald-200',
        warning: 'bg-amber-50 text-amber-900 border-amber-200',
        danger: 'bg-rose-50 text-rose-800 border-rose-200',
    };
    return (
        <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border ${tones[tone]}`}>
      {children}
    </span>
    );
};

function DuePill({ sub }: { sub: Subscription }) {
    const due = getDueState(sub);
    const tone =
        due.state === 'overdue' ? 'danger' :
            due.state === 'due_soon' ? 'warning' :
                due.state === 'scheduled' ? 'neutral' :
                    due.state === 'inactive' ? 'neutral' :
                        'neutral';

    return <Pill tone={tone}>{due.label}</Pill>;
}

const Tabs: React.FC<{
    active: string;
    onChange: (id: string) => void;
    items: { id: string; label: string }[];
}> = ({ active, onChange, items }) => (
    <div className="flex flex-wrap gap-2">
        {items.map((t) => {
            const isActive = active === t.id;
            return (
                <button
                    key={t.id}
                    onClick={() => onChange(t.id)}
                    className={[
                        "px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors border",
                        isActive
                            ? "bg-gray-900 text-white border-gray-900"
                            : "bg-white text-gray-700 border-gray-200/80 hover:bg-gray-50",
                    ].join(' ')}
                >
                    {t.label}
                </button>
            );
        })}
    </div>
);

const DividerTitle: React.FC<{ title: string; subtitle?: string }> = ({ title, subtitle }) => (
    <div className="text-center">
        <h1 className="text-4xl md:text-5xl font-semibold text-gray-900 tracking-tight">
            {title}
        </h1>
        <div className="mt-3 flex justify-center">
            <div className="h-1 w-24 bg-gray-900 rounded-full opacity-80"></div>
        </div>
        {subtitle && (
            <p className="mt-4 text-gray-600 text-base md:text-lg leading-relaxed">
                {subtitle}
            </p>
        )}
    </div>
);

// =============================================================================
// Economics Explainers
// =============================================================================

const Details: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
    <details className="rounded-xl border border-gray-200 bg-white p-4">
        <summary className="cursor-pointer text-sm font-semibold text-gray-900">{title}</summary>
        <div className="mt-3 text-sm text-gray-700 leading-relaxed space-y-2">{children}</div>
    </details>
);

const EconomicsOverview: React.FC<{ goTo?: (tabId: string) => void }> = ({ goTo }) => (
    <Callout tone="neutral" title="Economics: how it works (and what you can control)">
        <div className="space-y-4">
            <div className="text-sm text-gray-700 leading-relaxed">
                There are <strong>two funding lanes</strong>. Which lane is used determines <em>who pays</em> and which counters move.
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                    <div className="text-sm font-semibold text-gray-900">Lane A — Tier lane ✅ (company-funded)</div>
                    <div className="mt-2 text-sm text-gray-700 space-y-1 leading-relaxed">
                        <div><strong>Used when:</strong> user is within effective tier limits <em>and</em> project (app) budget has funds.</div>
                        <div><strong>Who pays:</strong> <strong>App Budget</strong> (tenant/project wallet).</div>
                        <div><strong>What moves:</strong> tier counters (requests/tokens) are committed.</div>
                        <div className="text-gray-600">
                            Effective tier = base policy (<code>user_type</code>) possibly replaced by a user’s tier override.
                        </div>
                    </div>
                </div>

                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                    <div className="text-sm font-semibold text-gray-900">Lane B — Paid lane 💳 (user-funded)</div>
                    <div className="mt-2 text-sm text-gray-700 space-y-1 leading-relaxed">
                        <div><strong>Used when:</strong> tier admit is denied (tier quota exceeded) <em>or</em> app budget is empty, but the user has lifetime credits.</div>
                        <div><strong>Who pays:</strong> <strong>User Lifetime Credits</strong> (purchased tokens).</div>
                        <div><strong>What moves:</strong> tier counters are <strong>not</strong> committed (so “quota usage” can look flat).</div>
                    </div>
                </div>
            </div>

            <details className="rounded-xl border border-gray-200 bg-white p-4">
                <summary className="cursor-pointer text-sm font-semibold text-gray-900">
                    Admin levers (what you can change during pilot)
                </summary>
                <div className="mt-3 text-sm text-gray-700 leading-relaxed space-y-2">
                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                        <div className="font-semibold text-gray-900">1) Base tier (by user_type)</div>
                        <div className="text-gray-700">
                            Configure default limits for <code>registered</code>, <code>paid</code>, <code>privileged</code>, <code>admin</code>.
                        </div>
                        {goTo && (
                            <div className="mt-2">
                                <Button variant="secondary" onClick={() => goTo('quotaPolicies')}>Open Tier Quota Policies</Button>
                            </div>
                        )}
                    </div>

                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                        <div className="font-semibold text-gray-900">2) User Tier Override (replaces base while active)</div>
                        <div className="text-gray-700">
                            Temporary or long override for a specific user. <strong>Not additive</strong>.
                        </div>
                        {goTo && (
                            <div className="mt-2 flex flex-wrap gap-2">
                                <Button variant="secondary" onClick={() => goTo('grantTrial')}>Grant Trial</Button>
                                <Button variant="secondary" onClick={() => goTo('updateTier')}>Update Override</Button>
                            </div>
                        )}
                    </div>

                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                        <div className="font-semibold text-gray-900">3) User Lifetime Credits (USD → tokens, do not reset)</div>
                        <div className="text-gray-700">
                            Manual “top-up” for user-funded usage when we don’t have payments connected yet.
                        </div>
                        {goTo && (
                            <div className="mt-2">
                                <Button variant="secondary" onClick={() => goTo('lifetimeCredits')}>Open Lifetime Credits</Button>
                            </div>
                        )}
                    </div>

                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                        <div className="font-semibold text-gray-900">4) App Budget (tenant/project wallet)</div>
                        <div className="text-gray-700">
                            Company funds used for tier lane. If it hits zero, tier-funded usage stops.
                        </div>
                        {goTo && (
                            <div className="mt-2">
                                <Button variant="secondary" onClick={() => goTo('appBudget')}>Open App Budget</Button>
                            </div>
                        )}
                    </div>

                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                        <div className="font-semibold text-gray-900">5) Provider Budget Policies</div>
                        <div className="text-gray-700">
                            Hard caps per provider ($/hour, $/day, $/month) to prevent runaway costs.
                        </div>
                        {goTo && (
                            <div className="mt-2">
                                <Button variant="secondary" onClick={() => goTo('budgetPolicies')}>Open Budget Policies</Button>
                            </div>
                        )}
                    </div>
                </div>
            </details>

            <details className="rounded-xl border border-gray-200 bg-white p-4">
                <summary className="cursor-pointer text-sm font-semibold text-gray-900">
                    Common confusion: “Why do quota counters not increase?”
                </summary>
                <div className="mt-3 text-sm text-gray-700 leading-relaxed space-y-2">
                    <div>
                        In the <strong>paid lane</strong> the system intentionally does not commit tier counters.
                        So you can see lifetime credits decreasing while “Requests today / Tokens today” remain flat.
                    </div>
                    {goTo && (
                        <div className="mt-2">
                            <Button variant="secondary" onClick={() => goTo('quotaBreakdown')}>Open Budget Breakdown</Button>
                        </div>
                    )}
                </div>
            </details>
        </div>
    </Callout>
);


// =============================================================================
// Main Control Plane Admin Component
// =============================================================================

const ControlPlaneAdmin: React.FC = () => {
    const api = useMemo(() => new ControlPlaneAPI(), []);

    const [configStatus, setConfigStatus] = useState<string>('initializing');
    const [viewMode, setViewMode] = useState<string>('grantTrial');

    // separate loading channels: data loading vs actions
    const [loadingData, setLoadingData] = useState<boolean>(false);
    const [loadingAction, setLoadingAction] = useState<boolean>(false);

    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);

    // Data
    const [quotaPolicies, setQuotaPolicies] = useState<QuotaPolicy[]>([]);
    const [budgetPolicies, setBudgetPolicies] = useState<BudgetPolicy[]>([]);
    const [appBudget, setAppBudget] = useState<AppBudget | null>(null);

    // Forms - Grant Trial
    const [trialUserId, setTrialUserId] = useState<string>('');
    const [trialDays, setTrialDays] = useState<number>(7);
    const [trialRequests, setTrialRequests] = useState<number>(100);
    const [trialTokensHour, setTrialTokensHour] = useState<string>('');
    const [trialTokensDay, setTrialTokensDay] = useState<string>('');
    const [trialTokensMonth, setTrialTokensMonth] = useState<string>('300000000');
    const [trialUsdHour, setTrialUsdHour] = useState<string>('');
    const [trialUsdDay, setTrialUsdDay] = useState<string>('');
    const [trialUsdMonth, setTrialUsdMonth] = useState<string>('');
    const [trialNotes, setTrialNotes] = useState<string>('');

    // Forms - Update Tier Budget
    const [updateUserId, setUpdateUserId] = useState<string>('');
    const [updateRequestsDay, setUpdateRequestsDay] = useState<string>('');
    const [updateRequestsMonth, setUpdateRequestsMonth] = useState<string>('');
    const [updateTokensHour, setUpdateTokensHour] = useState<string>('');
    const [updateTokensDay, setUpdateTokensDay] = useState<string>('');
    const [updateTokensMonth, setUpdateTokensMonth] = useState<string>('');
    const [updateUsdHour, setUpdateUsdHour] = useState<string>('');
    const [updateUsdDay, setUpdateUsdDay] = useState<string>('');
    const [updateUsdMonth, setUpdateUsdMonth] = useState<string>('');
    const [updateMaxConcurrent, setUpdateMaxConcurrent] = useState<string>('');
    const [updateExpiresDays, setUpdateExpiresDays] = useState<string>('30');
    const [updateNotes, setUpdateNotes] = useState<string>('');

    // Forms - Tier Balance Lookup
    const [lookupUserId, setLookupUserId] = useState<string>('');
    const [tierBalance, setTierBalance] = useState<TierBalance | null>(null);

    // Forms - Quota Policy
    const [policyUserType, setPolicyUserType] = useState<string>('registered');
    const [policyUserTypeCustom, setPolicyUserTypeCustom] = useState<string>('');
    const [policyMaxConcurrent, setPolicyMaxConcurrent] = useState<string>('');
    const [policyRequestsDay, setPolicyRequestsDay] = useState<string>('');
    const [policyRequestsMonth, setPolicyRequestsMonth] = useState<string>('');
    const [policyTokensHour, setPolicyTokensHour] = useState<string>('');
    const [policyTokensDay, setPolicyTokensDay] = useState<string>('');
    const [policyTokensMonth, setPolicyTokensMonth] = useState<string>('');
    const [policyUsdHour, setPolicyUsdHour] = useState<string>('');
    const [policyUsdDay, setPolicyUsdDay] = useState<string>('');
    const [policyUsdMonth, setPolicyUsdMonth] = useState<string>('');
    const [policyNotes, setPolicyNotes] = useState<string>('');

    // Forms - Budget Policy
    const [budgetProvider, setBudgetProvider] = useState<string>('');
    const [budgetUsdHour, setBudgetUsdHour] = useState<string>('');
    const [budgetUsdDay, setBudgetUsdDay] = useState<string>('');
    const [budgetUsdMonth, setBudgetUsdMonth] = useState<string>('');
    const [budgetNotes, setBudgetNotes] = useState<string>('');

    // Forms - Quota Breakdown
    const [breakdownUserId, setBreakdownUserId] = useState<string>('');
    const [breakdownUserType, setBreakdownUserType] = useState<string>('registered');
    const [quotaBreakdown, setQuotaBreakdown] = useState<QuotaBreakdown | null>(null);

    // Forms - Lifetime Credits
    const [lifetimeUserId, setLifetimeUserId] = useState<string>('');
    const [lifetimeUsdAmount, setLifetimeUsdAmount] = useState<string>('');
    const [lifetimeNotes, setLifetimeNotes] = useState<string>('');
    const [lifetimeBalance, setLifetimeBalance] = useState<LifetimeBalance | null>(null);

    // App Budget
    const [appBudgetTopup, setAppBudgetTopup] = useState<string>('');
    const [appBudgetNotes, setAppBudgetNotes] = useState<string>('');

    // Subscriptions
    const [subProvider, setSubProvider] = useState<'internal' | 'stripe'>('internal');
    const [subUserId, setSubUserId] = useState<string>('');
    const [subTier, setSubTier] = useState<string>('paid');

    const [subStripePriceId, setSubStripePriceId] = useState<string>('');
    const [subStripeCustomerId, setSubStripeCustomerId] = useState<string>('');
    const [subPriceHint, setSubPriceHint] = useState<string>('');

    const [subLookupUserId, setSubLookupUserId] = useState<string>('');
    const [subscription, setSubscription] = useState<Subscription | null>(null);
    const [subBudgetUserId, setSubBudgetUserId] = useState<string>('');
    const [subBudgetUsdAmount, setSubBudgetUsdAmount] = useState<string>('');
    const [subBudgetNotes, setSubBudgetNotes] = useState<string>('');
    const [subBudgetForceTopup, setSubBudgetForceTopup] = useState<boolean>(false);
    const [subOverdraftUsd, setSubOverdraftUsd] = useState<string>('');
    const [subSweepUserId, setSubSweepUserId] = useState<string>('');
    const [subscriptionBalance, setSubscriptionBalance] = useState<SubscriptionBalance | null>(null);

    const [walletRefundUserId, setWalletRefundUserId] = useState<string>('');
    const [walletRefundPaymentIntentId, setWalletRefundPaymentIntentId] = useState<string>('');
    const [walletRefundUsdAmount, setWalletRefundUsdAmount] = useState<string>('');
    const [walletRefundNotes, setWalletRefundNotes] = useState<string>('');

    const [cancelSubUserId, setCancelSubUserId] = useState<string>('');
    const [cancelSubStripeId, setCancelSubStripeId] = useState<string>('');
    const [cancelSubNotes, setCancelSubNotes] = useState<string>('');

    const [stripeReconcileKind, setStripeReconcileKind] = useState<'all' | 'wallet_refund' | 'subscription_cancel'>('all');
    const [pendingStripeKind, setPendingStripeKind] = useState<'all' | 'wallet_refund' | 'subscription_cancel'>('all');
    const [pendingStripeItems, setPendingStripeItems] = useState<PendingStripeRequest[]>([]);
    const [loadingPendingStripe, setLoadingPendingStripe] = useState<boolean>(false);

    const [pendingEconomicsKind, setPendingEconomicsKind] = useState<string>('');
    const [pendingEconomicsUserId, setPendingEconomicsUserId] = useState<string>('');
    const [pendingEconomicsItems, setPendingEconomicsItems] = useState<PendingStripeRequest[]>([]);
    const [loadingPendingEconomics, setLoadingPendingEconomics] = useState<boolean>(false);

    const [subHistoryUserId, setSubHistoryUserId] = useState<string>('');
    const [subHistoryStatus, setSubHistoryStatus] = useState<'closed' | 'open' | 'all'>('closed');
    const [subPeriods, setSubPeriods] = useState<SubscriptionPeriod[]>([]);
    const [subLedger, setSubLedger] = useState<SubscriptionLedgerEntry[]>([]);
    const [subSelectedPeriodKey, setSubSelectedPeriodKey] = useState<string>('');
    const [loadingHistory, setLoadingHistory] = useState<boolean>(false);

    const [subsProviderFilter, setSubsProviderFilter] = useState<string>('');
    const [subsList, setSubsList] = useState<Subscription[]>([]);

    const [breakdownUserTypeCustom, setBreakdownUserTypeCustom] = useState<string>('');

    const [economicsRef, setEconomicsRef] = useState<EconomicsReference | null>(null);

    const safeNumber = (v: any) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
    const usdToTokens = (usdText: string) => {
        if (!economicsRef) return null;
        const usd = parseFloat(usdText);
        if (!Number.isFinite(usd) || usd <= 0) return null;
        return Math.floor(usd / economicsRef.usd_per_token);
    };
    const tokensToUsd = (tokenText: string) => {
        if (!economicsRef) return null;
        const tokens = parseInt(tokenText);
        if (!Number.isFinite(tokens) || tokens <= 0) return null;
        return tokens * economicsRef.usd_per_token;
    };

    useEffect(() => {
        const initializeSettings = async () => {
            console.log('[Admin] Initializing settings');
            try {
                const configReceived = await settings.setupParentListener();
                console.log('[Admin] Config received?', configReceived);

                if (configReceived || !window.parent || window.parent === window) {
                    setConfigStatus('ready');
                }
            } catch (err) {
                console.error('[Admin] Error initializing settings:', err);
                setConfigStatus('error');
            }
        };

        initializeSettings();
    }, []);

    useEffect(() => {
        if (configStatus === 'ready') {
            loadDataForView(viewMode);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [configStatus, viewMode]);

    useEffect(() => {
        const loadEconomicsRef = async () => {
            if (configStatus !== 'ready') return;
            try {
                const ref = await api.getEconomicsReference();
                if (ref.status === 'ok') {
                    setEconomicsRef(ref);
                }
            } catch (err) {
                console.warn('Failed to load economics reference:', err);
            }
        };
        loadEconomicsRef();
    }, [api, configStatus]);

    const loadDataForView = async (mode: string) => {
        const needsData = ['quotaPolicies', 'budgetPolicies', 'appBudget'].includes(mode);
        if (!needsData) return;

        setLoadingData(true);
        setError(null);

        try {
            if (mode === 'quotaPolicies') {
                const result = await api.listQuotaPolicies();
                setQuotaPolicies(result.policies || []);
            } else if (mode === 'budgetPolicies') {
                const result = await api.listBudgetPolicies();
                setBudgetPolicies(result.policies || []);
            } else if (mode === 'appBudget') {
                const balance = await api.getAppBudgetBalance();
                setAppBudget(balance);
            }
        } catch (err) {
            setError((err as Error).message);
            console.error('Failed to load data:', err);
        } finally {
            setLoadingData(false);
        }
    };

    const clearMessages = () => {
        setError(null);
        setSuccess(null);
    };

    const handleGrantTrial = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        try {
            await api.grantTrial({
                userId: trialUserId,
                days: trialDays,
                requestsPerDay: trialRequests,
                tokensPerHour: trialTokensHour ? parseInt(trialTokensHour) : undefined,
                tokensPerDay: trialTokensDay ? parseInt(trialTokensDay) : undefined,
                tokensPerMonth: trialTokensMonth ? parseInt(trialTokensMonth) : undefined,
                usdPerHour: trialUsdHour ? parseFloat(trialUsdHour) : undefined,
                usdPerDay: trialUsdDay ? parseFloat(trialUsdDay) : undefined,
                usdPerMonth: trialUsdMonth ? parseFloat(trialUsdMonth) : undefined,
                notes: trialNotes,
            });
            setSuccess(`Trial granted to ${trialUserId}`);
            setTrialUserId('');
            setTrialNotes('');
            setTrialTokensHour('');
            setTrialTokensDay('');
            setTrialTokensMonth('300000000');
            setTrialUsdHour('');
            setTrialUsdDay('');
            setTrialUsdMonth('');
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleUpdateTierBudget = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        try {
            await api.updateTierBudget({
                userId: updateUserId,
                requestsPerDay: updateRequestsDay ? parseInt(updateRequestsDay) : undefined,
                requestsPerMonth: updateRequestsMonth ? parseInt(updateRequestsMonth) : undefined,
                tokensPerHour: updateTokensHour ? parseInt(updateTokensHour) : undefined,
                tokensPerDay: updateTokensDay ? parseInt(updateTokensDay) : undefined,
                tokensPerMonth: updateTokensMonth ? parseInt(updateTokensMonth) : undefined,
                usdPerHour: updateUsdHour ? parseFloat(updateUsdHour) : undefined,
                usdPerDay: updateUsdDay ? parseFloat(updateUsdDay) : undefined,
                usdPerMonth: updateUsdMonth ? parseFloat(updateUsdMonth) : undefined,
                maxConcurrent: updateMaxConcurrent ? parseInt(updateMaxConcurrent) : undefined,
                expiresInDays: updateExpiresDays === '' ? null : parseInt(updateExpiresDays),
                notes: updateNotes
            });
            setSuccess(`Tier override updated for ${updateUserId}`);
            setUpdateUserId('');
            setUpdateRequestsDay('');
            setUpdateRequestsMonth('');
            setUpdateTokensHour('');
            setUpdateTokensDay('');
            setUpdateTokensMonth('');
            setUpdateUsdHour('');
            setUpdateUsdDay('');
            setUpdateUsdMonth('');
            setUpdateMaxConcurrent('');
            setUpdateExpiresDays('30');
            setUpdateNotes('');
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleLookupTierBalance = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setTierBalance(null);
        setLoadingAction(true);

        try {
            const result = await api.getTierBalance(lookupUserId);
            setTierBalance(result);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleGetQuotaBreakdown = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setQuotaBreakdown(null);
        setLoadingAction(true);

        try {
            const result = await api.getUserBudgetBreakdown(breakdownUserId, breakdownUserType);
            setQuotaBreakdown(result);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleSetQuotaPolicy = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        try {

            await api.setQuotaPolicy({
                userType: policyUserType === 'custom' ? policyUserTypeCustom : policyUserType,
                maxConcurrent: policyMaxConcurrent ? parseInt(policyMaxConcurrent) : undefined,
                requestsPerDay: policyRequestsDay ? parseInt(policyRequestsDay) : undefined,
                requestsPerMonth: policyRequestsMonth ? parseInt(policyRequestsMonth) : undefined,
                tokensPerHour: policyTokensHour ? parseInt(policyTokensHour) : undefined,
                tokensPerDay: policyTokensDay ? parseInt(policyTokensDay) : undefined,
                tokensPerMonth: policyTokensMonth ? parseInt(policyTokensMonth) : undefined,
                usdPerHour: policyUsdHour ? parseFloat(policyUsdHour) : undefined,
                usdPerDay: policyUsdDay ? parseFloat(policyUsdDay) : undefined,
                usdPerMonth: policyUsdMonth ? parseFloat(policyUsdMonth) : undefined,
                notes: policyNotes
            });

            setSuccess(`Quota policy set for ${policyUserType}`);
            // setPolicyUserType(policyUserType);
            setPolicyMaxConcurrent('');
            setPolicyRequestsDay('');
            setPolicyRequestsMonth('');
            setPolicyTokensHour('');
            setPolicyTokensDay('');
            setPolicyTokensMonth('');
            setPolicyUsdHour('');
            setPolicyUsdDay('');
            setPolicyUsdMonth('');
            setPolicyUserTypeCustom('');
            setPolicyNotes('');

            await loadDataForView('quotaPolicies');
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleSetBudgetPolicy = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        try {
            await api.setBudgetPolicy({
                provider: budgetProvider,
                usdPerHour: budgetUsdHour ? parseFloat(budgetUsdHour) : undefined,
                usdPerDay: budgetUsdDay ? parseFloat(budgetUsdDay) : undefined,
                usdPerMonth: budgetUsdMonth ? parseFloat(budgetUsdMonth) : undefined,
                notes: budgetNotes
            });

            setSuccess(`Budget policy set for ${budgetProvider}`);
            setBudgetProvider('');
            setBudgetUsdHour('');
            setBudgetUsdDay('');
            setBudgetUsdMonth('');
            setBudgetNotes('');

            await loadDataForView('budgetPolicies');
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleAddLifetimeCredits = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        const uid = lifetimeUserId.trim();

        try {
            const result = await api.addLifetimeCredits(uid, parseFloat(lifetimeUsdAmount), lifetimeNotes);
            setSuccess(`Added $${lifetimeUsdAmount} (${Number(result.tokens_added).toLocaleString()} tokens) to ${uid}`);

            setLifetimeUsdAmount('');
            setLifetimeNotes('');

            // show fresh balance for same uid
            const balance = await api.getLifetimeBalance(uid);
            setLifetimeBalance(balance);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleCheckLifetimeBalance = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLifetimeBalance(null);
        setLoadingAction(true);

        const uid = lifetimeUserId.trim();

        try {
            const balance = await api.getLifetimeBalance(uid);
            setLifetimeBalance(balance);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleTopupAppBudget = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        try {
            await api.topupAppBudget(parseFloat(appBudgetTopup), appBudgetNotes);
            setSuccess(`App budget topped up: $${appBudgetTopup}`);

            setAppBudgetTopup('');
            setAppBudgetNotes('');

            const balance = await api.getAppBudgetBalance();
            setAppBudget(balance);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleCreateSubscription = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        try {
            const res = await api.createSubscription({
                userId: subUserId.trim(),
                tier: subTier,
                provider: subProvider,
                stripePriceId: subProvider === 'stripe' ? subStripePriceId.trim() : undefined,
                stripeCustomerId: subProvider === 'stripe' ? (subStripeCustomerId.trim() || undefined) : undefined,
                monthlyPriceCentsHint: subProvider === 'stripe' && subPriceHint ? parseInt(subPriceHint) : undefined,
            });

            setSuccess(res.message || `Subscription created for ${subUserId}`);
            setSubUserId('');
            setSubStripePriceId('');
            setSubStripeCustomerId('');
            setSubPriceHint('');
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleLookupSubscription = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);
        setSubscription(null);
        setSubscriptionBalance(null);

        try {
            const res = await api.getSubscription(subLookupUserId.trim());
            setSubscription(res.subscription);
            setSubscriptionBalance(res.subscription_balance || null);
            if (!res.subscription) setSuccess('No subscription found for this user.');
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleLoadSubscriptionsList = async () => {
        clearMessages();
        setLoadingData(true);

        try {
            const res = await api.listSubscriptions({
                provider: subsProviderFilter || undefined,
                limit: 50,
                offset: 0,
            });
            setSubsList(res.subscriptions || []);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingData(false);
        }
    };

    const handleTopupSubscriptionBudget = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        try {
            const res = await api.topupSubscriptionBudget(
                subBudgetUserId.trim(),
                parseFloat(subBudgetUsdAmount),
                subBudgetNotes || undefined,
                subBudgetForceTopup
            );
            setSuccess(`Subscription balance topped up for ${subBudgetUserId}: $${subBudgetUsdAmount}`);
            setSubBudgetUsdAmount('');
            setSubBudgetNotes('');
            setSubBudgetForceTopup(false);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleSetSubscriptionOverdraft = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        const limitVal = subOverdraftUsd.trim() === '' ? null : parseFloat(subOverdraftUsd);
        try {
            await api.setSubscriptionOverdraft(subBudgetUserId.trim(), limitVal, subBudgetNotes || undefined);
            setSuccess(`Subscription overdraft updated for ${subBudgetUserId}`);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleSweepSubscriptionRollovers = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        try {
            const res = await api.sweepSubscriptionRollovers(subSweepUserId.trim() || undefined);
            const moved = res?.moved_usd != null ? `$${Number(res.moved_usd).toFixed(2)}` : 'N/A';
            setSuccess(`Sweep complete. Moved: ${moved}`);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleLoadSubscriptionPeriods = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingHistory(true);

        try {
            const uid = subHistoryUserId.trim();
            if (!uid) {
                setError('User ID is required to load subscription periods.');
                return;
            }
            const res = await api.listSubscriptionPeriods(uid, subHistoryStatus, 50, 0);
            setSubPeriods(res.periods || []);
            setSubLedger([]);
            setSubSelectedPeriodKey('');
            if (!res.periods || res.periods.length === 0) {
                setSuccess('No subscription periods found for this user.');
            }
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingHistory(false);
        }
    };

    const handleLoadSubscriptionLedger = async (periodKey: string) => {
        if (!periodKey) return;
        clearMessages();
        setLoadingHistory(true);
        setSubSelectedPeriodKey(periodKey);
        setSubLedger([]);

        try {
            const uid = subHistoryUserId.trim();
            if (!uid) {
                setError('User ID is required to load ledger entries.');
                return;
            }
            const res = await api.listSubscriptionLedger(uid, periodKey, 200, 0);
            setSubLedger(res.ledger || []);
            if (!res.ledger || res.ledger.length === 0) {
                setSuccess('No ledger entries for this period.');
            }
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingHistory(false);
        }
    };

    const handleWalletRefund = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        try {
            const usdVal = walletRefundUsdAmount.trim() === '' ? null : parseFloat(walletRefundUsdAmount);
            const res = await api.refundWallet({
                userId: walletRefundUserId.trim(),
                paymentIntentId: walletRefundPaymentIntentId.trim(),
                usdAmount: usdVal,
                notes: walletRefundNotes || undefined
            });
            setSuccess(res.message || 'Refund requested; awaiting Stripe confirmation.');
            setWalletRefundUsdAmount('');
            setWalletRefundNotes('');
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleCancelSubscription = async (e: React.FormEvent) => {
        e.preventDefault();
        clearMessages();
        setLoadingAction(true);

        try {
            const res = await api.cancelSubscription({
                userId: cancelSubUserId.trim() || undefined,
                stripeSubscriptionId: cancelSubStripeId.trim() || undefined,
                notes: cancelSubNotes || undefined,
            });
            setSuccess(res.message || 'Cancellation requested; awaiting Stripe confirmation.');
            setCancelSubNotes('');
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleStripeReconcile = async () => {
        clearMessages();
        setLoadingAction(true);
        try {
            const res = await api.reconcileStripe(stripeReconcileKind);
            setSuccess(`Stripe reconcile complete. Applied=${res.applied ?? 0}, Failed=${res.failed ?? 0}`);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingAction(false);
        }
    };

    const handleLoadPendingStripe = async () => {
        clearMessages();
        setLoadingPendingStripe(true);
        try {
            const res = await api.listPendingStripeRequests(pendingStripeKind, 200, 0);
            setPendingStripeItems(res.items || []);
            if (!res.items || res.items.length === 0) {
                setSuccess('No pending Stripe requests.');
            }
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingPendingStripe(false);
        }
    };

    const handleLoadPendingEconomics = async () => {
        clearMessages();
        setLoadingPendingEconomics(true);
        try {
            const kind = pendingEconomicsKind.trim() || undefined;
            const userId = pendingEconomicsUserId.trim() || undefined;
            const res = await api.listPendingEconomicsEvents(kind, userId, 200, 0);
            setPendingEconomicsItems(res.items || []);
            if (!res.items || res.items.length === 0) {
                setSuccess('No pending economics events.');
            }
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoadingPendingEconomics(false);
        }
    };


    if (configStatus === 'initializing') {
        return (
            <div className="min-h-screen bg-white flex items-center justify-center p-8">
                <Card className="max-w-lg w-full">
                    <CardBody className="text-center">
                        <LoadingSpinner />
                        <p className="mt-4 text-gray-600">Initializing Control Plane Admin…</p>
                    </CardBody>
                </Card>
            </div>
        );
    }

    const tabs = [
        { id: 'grantTrial', label: 'Grant Trial' },
        { id: 'updateTier', label: 'Override Tier Limits for User' },
        { id: 'lookup', label: 'Lookup Balance' },
        { id: 'quotaBreakdown', label: 'User Budget Breakdown' },
        { id: 'quotaPolicies', label: 'Tier Limits' },
        { id: 'budgetPolicies', label: 'Project Budget Policies' },
        { id: 'lifetimeCredits', label: 'Lifetime Credits' },
        { id: 'appBudget', label: 'App Budget' },
        { id: 'subscriptions', label: 'Subscriptions' },
    ];

    const usdPerToken =
        lifetimeBalance && lifetimeBalance.balance_tokens > 0
            ? lifetimeBalance.balance_usd / lifetimeBalance.balance_tokens
            : null;

    const minUsd =
        usdPerToken && lifetimeBalance
            ? usdPerToken * Number(lifetimeBalance.minimum_required_tokens || 0)
            : null;

    return (
        <div className="min-h-screen bg-white">
            <div className="max-w-6xl mx-auto px-6 py-10 space-y-8">
                {/* Header */}
                <div className="space-y-6">
                    <DividerTitle
                        title="Control Plane"
                        subtitle="Admin dashboard for user quota policies, tier overrides, purchased credits, and application budget."
                    />

                    <div className="max-w-4xl mx-auto">
                        <EconomicsOverview goTo={(tabId) => { clearMessages(); setViewMode(tabId); }} />
                    </div>
                </div>

                {/* Navigation */}
                <div className="max-w-5xl mx-auto">
                    <Tabs active={viewMode} onChange={(id) => { clearMessages(); setViewMode(id); }} items={tabs} />
                </div>

                {/* Messages */}
                <div className="max-w-5xl mx-auto space-y-3">
                    {success && <Callout tone="success" title="Success">{success}</Callout>}
                    {error && <Callout tone="warning" title="Action failed">{error}</Callout>}
                </div>

                {/* Views */}
                <div className="max-w-5xl mx-auto space-y-6">
                    {/* Grant Trial */}
                    {viewMode === 'grantTrial' && (
                        <Card>
                            <CardHeader
                                title="Grant Trial (temporary tier override)"
                                subtitle="Gives the user a higher tier envelope for a limited time. This OVERRIDES base tier limits — it does not add."
                            />
                            <CardBody className="space-y-6">
                                <Callout tone="info" title="What this does">
                                    Use for onboarding, marketing trials, or time-limited upgrades. Daily/monthly counters keep resetting while the override is active.
                                </Callout>

                                <form onSubmit={handleGrantTrial} className="space-y-5">
                                    <Input
                                        label="User ID *"
                                        value={trialUserId}
                                        onChange={(e) => setTrialUserId(e.target.value)}
                                        placeholder="user123"
                                        required
                                    />

                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <Input
                                            label="Duration (days)"
                                            type="number"
                                            value={trialDays.toString()}
                                            onChange={(e) => setTrialDays(parseInt(e.target.value || '7'))}
                                            min={1}
                                        />
                                        <Input
                                            label="Requests / day (override)"
                                            type="number"
                                            value={trialRequests.toString()}
                                            onChange={(e) => setTrialRequests(parseInt(e.target.value || '0'))}
                                            min={1}
                                        />
                                        <div>
                                            <Input
                                                label="Tokens / hour (override)"
                                                type="number"
                                                value={trialTokensHour}
                                                onChange={(e) => setTrialTokensHour(e.target.value)}
                                                min={1}
                                            />
                                            {trialTokensHour && tokensToUsd(trialTokensHour) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(trialTokensHour)).toFixed(2)}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                    <div className="text-xs text-gray-500">
                                        USD overrides tokens for the same window.
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <div>
                                            <Input
                                                label="Tokens / day (override)"
                                                type="number"
                                                value={trialTokensDay}
                                                onChange={(e) => setTrialTokensDay(e.target.value)}
                                                min={1}
                                            />
                                            {trialTokensDay && tokensToUsd(trialTokensDay) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(trialTokensDay)).toFixed(2)}
                                                </div>
                                            )}
                                        </div>
                                        <div>
                                            <Input
                                                label="Tokens / month (override)"
                                                type="number"
                                                value={trialTokensMonth}
                                                onChange={(e) => setTrialTokensMonth(e.target.value)}
                                                min={1}
                                            />
                                            {trialTokensMonth && tokensToUsd(trialTokensMonth) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(trialTokensMonth)).toFixed(2)}
                                                </div>
                                            )}
                                        </div>
                                        <div>
                                            <Input
                                                label="USD / hour (override)"
                                                type="number"
                                                value={trialUsdHour}
                                                onChange={(e) => setTrialUsdHour(e.target.value)}
                                                min={0}
                                                step="0.01"
                                            />
                                            {trialUsdHour && usdToTokens(trialUsdHour) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(trialUsdHour)).toLocaleString()} tokens
                                                </div>
                                            )}
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <div>
                                            <Input
                                                label="USD / day (override)"
                                                type="number"
                                                value={trialUsdDay}
                                                onChange={(e) => setTrialUsdDay(e.target.value)}
                                                min={0}
                                                step="0.01"
                                            />
                                            {trialUsdDay && usdToTokens(trialUsdDay) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(trialUsdDay)).toLocaleString()} tokens
                                                </div>
                                            )}
                                        </div>
                                        <div>
                                            <Input
                                                label="USD / month (override)"
                                                type="number"
                                                value={trialUsdMonth}
                                                onChange={(e) => setTrialUsdMonth(e.target.value)}
                                                min={0}
                                                step="0.01"
                                            />
                                            {trialUsdMonth && usdToTokens(trialUsdMonth) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(trialUsdMonth)).toLocaleString()} tokens
                                                </div>
                                            )}
                                        </div>
                                        <div className="text-xs text-gray-500 pt-6">
                                            USD overrides tokens for the same window.
                                        </div>
                                    </div>

                                    <TextArea
                                        label="Notes"
                                        value={trialNotes}
                                        onChange={(e) => setTrialNotes(e.target.value)}
                                        placeholder="Welcome trial for new user"
                                    />

                                    <Button type="submit" disabled={loadingAction}>
                                        {loadingAction ? 'Granting…' : 'Grant Trial'}
                                    </Button>
                                </form>
                            </CardBody>
                        </Card>
                    )}

                    {/* Update Tier */}
                    {viewMode === 'updateTier' && (
                        <Card>
                            <CardHeader
                                title="Update Tier Override (partial updates)"
                                subtitle="Only fields you provide are updated. Others remain unchanged. This is ideal for fine-tuning an existing override."
                            />
                            <CardBody className="space-y-6">
                                <Callout tone="warning" title="Override semantics">
                                    This does <strong>not</strong> top-up the base tier. It replaces it for as long as the override is active.
                                </Callout>

                                <form onSubmit={handleUpdateTierBudget} className="space-y-5">
                                    <Input
                                        label="User ID *"
                                        value={updateUserId}
                                        onChange={(e) => setUpdateUserId(e.target.value)}
                                        placeholder="user456"
                                        required
                                    />

                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <Input
                                            label="Requests / day (empty = keep)"
                                            type="number"
                                            value={updateRequestsDay}
                                            onChange={(e) => setUpdateRequestsDay(e.target.value)}
                                            placeholder="100"
                                        />
                                        <Input
                                            label="Requests / month (empty = keep)"
                                            type="number"
                                            value={updateRequestsMonth}
                                            onChange={(e) => setUpdateRequestsMonth(e.target.value)}
                                            placeholder="3000"
                                        />
                                        <div>
                                            <Input
                                                label="Tokens / hour (empty = keep)"
                                                type="number"
                                                value={updateTokensHour}
                                                onChange={(e) => setUpdateTokensHour(e.target.value)}
                                                placeholder="500000"
                                            />
                                            {updateTokensHour && tokensToUsd(updateTokensHour) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(updateTokensHour)).toFixed(2)}
                                                </div>
                                            )}
                                        </div>
                                        <div>
                                            <Input
                                                label="Tokens / day (empty = keep)"
                                                type="number"
                                                value={updateTokensDay}
                                                onChange={(e) => setUpdateTokensDay(e.target.value)}
                                                placeholder="10000000"
                                            />
                                            {updateTokensDay && tokensToUsd(updateTokensDay) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(updateTokensDay)).toFixed(2)}
                                                </div>
                                            )}
                                        </div>
                                        <div>
                                            <Input
                                                label="Tokens / month (empty = keep)"
                                                type="number"
                                                value={updateTokensMonth}
                                                onChange={(e) => setUpdateTokensMonth(e.target.value)}
                                                placeholder="300000000"
                                            />
                                            {updateTokensMonth && tokensToUsd(updateTokensMonth) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(updateTokensMonth)).toFixed(2)}
                                                </div>
                                            )}
                                        </div>
                                        <div>
                                            <Input
                                                label="USD / hour (empty = keep)"
                                                type="number"
                                                value={updateUsdHour}
                                                onChange={(e) => setUpdateUsdHour(e.target.value)}
                                                placeholder="5"
                                                min={0}
                                                step="0.01"
                                            />
                                            {updateUsdHour && usdToTokens(updateUsdHour) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(updateUsdHour)).toLocaleString()} tokens
                                                </div>
                                            )}
                                        </div>
                                        <div>
                                            <Input
                                                label="USD / day (empty = keep)"
                                                type="number"
                                                value={updateUsdDay}
                                                onChange={(e) => setUpdateUsdDay(e.target.value)}
                                                placeholder="50"
                                                min={0}
                                                step="0.01"
                                            />
                                            {updateUsdDay && usdToTokens(updateUsdDay) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(updateUsdDay)).toLocaleString()} tokens
                                                </div>
                                            )}
                                        </div>
                                        <div>
                                            <Input
                                                label="USD / month (empty = keep)"
                                                type="number"
                                                value={updateUsdMonth}
                                                onChange={(e) => setUpdateUsdMonth(e.target.value)}
                                                placeholder="500"
                                                min={0}
                                                step="0.01"
                                            />
                                            {updateUsdMonth && usdToTokens(updateUsdMonth) != null && (
                                                <div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(updateUsdMonth)).toLocaleString()} tokens
                                                </div>
                                            )}
                                        </div>
                                        <Input
                                            label="Max concurrent (empty = keep)"
                                            type="number"
                                            value={updateMaxConcurrent}
                                            onChange={(e) => setUpdateMaxConcurrent(e.target.value)}
                                            placeholder="5"
                                        />
                                        <Input
                                            label="Expires in days (empty = never)"
                                            type="number"
                                            value={updateExpiresDays}
                                            onChange={(e) => setUpdateExpiresDays(e.target.value)}
                                            placeholder="30"
                                        />
                                    </div>

                                    <TextArea
                                        label="Notes"
                                        value={updateNotes}
                                        onChange={(e) => setUpdateNotes(e.target.value)}
                                        placeholder="Promotional campaign / compensation / beta program"
                                    />

                                    <Button type="submit" disabled={loadingAction}>
                                        {loadingAction ? 'Updating…' : 'Update Override'}
                                    </Button>
                                </form>
                            </CardBody>
                        </Card>
                    )}

                    {/* Lookup */}
                    {viewMode === 'lookup' && (
                        <Card>
                            <CardHeader
                                title="Lookup User Balance"
                                subtitle="Shows active tier override (if any) and purchased lifetime credits (if any)."
                            />
                            <CardBody className="space-y-6">
                                <form onSubmit={handleLookupTierBalance} className="space-y-4">
                                    <div className="flex gap-3">
                                        <Input
                                            value={lookupUserId}
                                            onChange={(e) => setLookupUserId(e.target.value)}
                                            placeholder="user123"
                                            required
                                            className="flex-1"
                                        />
                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Loading…' : 'Lookup'}
                                        </Button>
                                    </div>
                                </form>

                                {tierBalance && (
                                    <div className="space-y-5">
                                        <Callout tone="info" title="How requests are funded (lane selection)">
                                            <div className="space-y-2">
                                                <div>
                                                    <strong>If Tier Admit passes:</strong> tier allowance is available and tier counters move (tier lane).
                                                </div>
                                                <div>
                                                    <strong>If Tier Admit is denied:</strong> tier allowance is NOT available. Only lifetime credits can fund the request (paid lane),
                                                    and tier counters are not committed.
                                                </div>
                                                <div className="text-gray-600">
                                                    Note: paid lane can still be blocked by <em>concurrency</em> (max_concurrent).
                                                </div>
                                            </div>
                                        </Callout>
                                        <div className="border-t border-gray-200/70 pt-6">
                                            <div className="flex items-baseline justify-between flex-wrap gap-2">
                                                <h3 className="text-2xl font-semibold text-gray-900">
                                                    {tierBalance.user_id}
                                                </h3>
                                                <div className="text-sm text-gray-500">
                                                    {tierBalance.message || ''}
                                                </div>
                                            </div>

                                            {!tierBalance.has_tier_override && !tierBalance.has_lifetime_budget ? (
                                                <EmptyState message="No tier override and no purchased credits (base tier only)." icon="📋" />
                                            ) : (
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-5">
                                                    {tierBalance.has_tier_override && tierBalance.tier_override && (
                                                        <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                            <div className="flex items-center justify-between">
                                                                <div>
                                                                    <div className="text-sm font-semibold text-gray-900">Tier Override</div>
                                                                    <div className="text-xs text-gray-600 mt-1">
                                                                        Replaces base tier while active
                                                                    </div>
                                                                </div>
                                                                <div className="text-2xl">🎯</div>
                                                            </div>

                                                            <div className="mt-4 space-y-2 text-sm">
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Requests / day</span>
                                                                    <span className="font-semibold text-gray-900">{tierBalance.tier_override.requests_per_day ?? '—'}</span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Tokens / hour</span>
                                                                    <span className="font-semibold text-gray-900">
                                                                        {tierBalance.tier_override.tokens_per_hour?.toLocaleString() ?? '—'}
                                                                        {tierBalance.tier_override.usd_per_hour != null ? ` ($${Number(tierBalance.tier_override.usd_per_hour).toFixed(2)})` : ''}
                                                                    </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Tokens / day</span>
                                                                    <span className="font-semibold text-gray-900">
                                                                        {tierBalance.tier_override.tokens_per_day?.toLocaleString() ?? '—'}
                                                                        {tierBalance.tier_override.usd_per_day != null ? ` ($${Number(tierBalance.tier_override.usd_per_day).toFixed(2)})` : ''}
                                                                    </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Tokens / month</span>
                                                                    <span className="font-semibold text-gray-900">
                                                                        {tierBalance.tier_override.tokens_per_month?.toLocaleString() ?? '—'}
                                                                        {tierBalance.tier_override.usd_per_month != null ? ` ($${Number(tierBalance.tier_override.usd_per_month).toFixed(2)})` : ''}
                                                                    </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Expires</span>
                                                                    <span className="font-semibold text-gray-900">
                                    {tierBalance.tier_override.expires_at
                                        ? new Date(tierBalance.tier_override.expires_at).toLocaleString()
                                        : 'Never'}
                                  </span>
                                                                </div>
                                                                {tierBalance.tier_override.notes && (
                                                                    <div className="pt-3 border-t border-gray-200/70 text-xs text-gray-600 italic">
                                                                        {tierBalance.tier_override.notes}
                                                                    </div>
                                                                )}
                                                                {tierBalance.tier_override.reference_model && (
                                                                    <div className="pt-2 text-xs text-gray-500">
                                                                        Reference: {tierBalance.tier_override.reference_model}
                                                                    </div>
                                                                )}
                                                            </div>
                                                        </div>
                                                    )}

                                                    {tierBalance.has_lifetime_budget && tierBalance.lifetime_budget && (
                                                        <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                            <div className="flex items-center justify-between">
                                                                <div>
                                                                    <div className="text-sm font-semibold text-gray-900">Lifetime Credits</div>
                                                                    <div className="text-xs text-gray-600 mt-1">
                                                                        Purchased tokens (do not reset)
                                                                    </div>
                                                                </div>
                                                                <div className="text-2xl">💳</div>
                                                            </div>

                                                            <div className="mt-4 space-y-2 text-sm">
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Gross remaining</span>
                                                                    <span className="font-semibold text-gray-900">
                                    {tierBalance.lifetime_budget.tokens_gross_remaining.toLocaleString()}
                                  </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Reserved (in-flight)</span>
                                                                    <span className="font-semibold text-gray-900">
                                    {tierBalance.lifetime_budget.tokens_reserved.toLocaleString()}
                                  </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Available now</span>
                                                                    <span className="font-semibold text-gray-900">
                                    {tierBalance.lifetime_budget.tokens_available.toLocaleString()}
                                  </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Available USD (quoted)</span>
                                                                    <span className="font-semibold text-gray-900">
                                    ${Number(tierBalance.lifetime_budget.available_usd || 0).toFixed(2)}
                                  </span>
                                                                </div>

                                                                <div className="pt-3 border-t border-gray-200/70 text-xs text-gray-600">
                                                                    Reference: {tierBalance.lifetime_budget.reference_model || 'anthropic/claude-sonnet-4-5-20250929'}
                                                                </div>
                                                            </div>
                                                        </div>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </CardBody>
                        </Card>
                    )}

                    {/* Quota Breakdown */}
                    {viewMode === 'quotaBreakdown' && (
                        <Card>
                            <CardHeader
                                title="Budget Breakdown"
                                subtitle="Explains base policy vs override vs effective policy, plus current usage and remaining headroom."
                            />
                            <CardBody className="space-y-6">
                                <Callout tone="neutral" title="How to read this view">
                                    <strong>Effective policy</strong> is what the limiter enforces right now (base tier possibly overridden).
                                    “Remaining” is computed from the effective limits minus current counters.
                                </Callout>
                                <Callout tone="warning" title="Paid lane does NOT show up in these counters">
                                    If the user is being served from <strong>lifetime credits</strong> because tier admit is denied, tier counters are not committed.
                                    That means <strong>requests/tokens here can stay flat</strong> while the user’s lifetime balance goes down.
                                    Use <em>Lifetime Balance</em> to confirm paid-lane spend.
                                </Callout>

                                <form onSubmit={handleGetQuotaBreakdown} className="space-y-4">
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <Input
                                            label="User ID *"
                                            value={breakdownUserId}
                                            onChange={(e) => setBreakdownUserId(e.target.value)}
                                            placeholder="user123"
                                            required
                                        />
                                        <Select
                                            label="User Type *"
                                            value={breakdownUserType}
                                            onChange={(e) => setBreakdownUserType(e.target.value)}
                                            options={USER_TYPE_OPTIONS}
                                        />

                                        {breakdownUserType === 'custom' && (
                                            <Input
                                                label="Custom user_type *"
                                                value={breakdownUserTypeCustom}
                                                onChange={(e) => setBreakdownUserTypeCustom(e.target.value)}
                                                placeholder="e.g. enterprise"
                                                required
                                            />
                                        )}
                                    </div>
                                    <Button type="submit" disabled={loadingAction}>
                                        {loadingAction ? 'Analyzing…' : 'Get Breakdown'}
                                    </Button>
                                </form>

                                {quotaBreakdown && (
                                    <div className="space-y-6">
                                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                                            <StatCard label="Requests today" value={quotaBreakdown.current_usage.requests_today} />
                                            <StatCard label="Requests this month" value={quotaBreakdown.current_usage.requests_this_month} />
                                            <StatCard
                                                label="Tokens today"
                                                value={`${(quotaBreakdown.current_usage.tokens_today / 1_000_000).toFixed(2)}M`}
                                                hint={
                                                    quotaBreakdown.current_usage.tokens_today_usd != null
                                                        ? `~$${Number(quotaBreakdown.current_usage.tokens_today_usd).toFixed(2)}`
                                                        : 'raw token counters'
                                                }
                                            />
                                            <StatCard
                                                label="Daily usage %"
                                                value={`${quotaBreakdown.remaining.percentage_used ?? 0}%`}
                                            />
                                        </div>

                                        {/* Credits snapshot */}
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                <div className="flex items-center justify-between">
                                                    <div>
                                                        <div className="text-sm font-semibold text-gray-900">Tier envelope</div>
                                                        <div className="text-xs text-gray-600 mt-1">Base → Override → Effective</div>
                                                    </div>
                                                    <div className="text-2xl">📊</div>
                                                </div>

                                                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-4 text-sm">
                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="font-semibold text-gray-900 mb-1">Base</div>
                                                        <div className="text-gray-600">
                                                            req/day: {quotaBreakdown.base_policy.requests_per_day ?? '—'}<br />
                                                            tok/month: {quotaBreakdown.base_policy.tokens_per_month?.toLocaleString?.() ?? quotaBreakdown.base_policy.tokens_per_month ?? '—'}
                                                            {quotaBreakdown.base_policy.usd_per_month != null
                                                                ? ` ($${Number(quotaBreakdown.base_policy.usd_per_month).toFixed(2)})`
                                                                : ''}
                                                        </div>
                                                    </div>

                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="font-semibold text-gray-900 mb-1">Override</div>
                                                        <div className="text-gray-600">
                                                            {quotaBreakdown.tier_override ? (
                                                                <>
                                                                    {quotaBreakdown.tier_override.active ? (
                                                                        <Pill tone="success">Active</Pill>
                                                                    ) : quotaBreakdown.tier_override.expired ? (
                                                                        <Pill tone="warning">Expired</Pill>
                                                                    ) : (
                                                                        <Pill tone="neutral">Inactive</Pill>
                                                                    )}
                                                                    <div className="mt-2">
                                                                        req/day: {quotaBreakdown.tier_override.limits.requests_per_day ?? '—'}<br />
                                                                        tok/month: {quotaBreakdown.tier_override.limits.tokens_per_month?.toLocaleString?.() ?? quotaBreakdown.tier_override.limits.tokens_per_month ?? '—'}
                                                                        {quotaBreakdown.tier_override.limits.usd_per_month != null
                                                                            ? ` ($${Number(quotaBreakdown.tier_override.limits.usd_per_month).toFixed(2)})`
                                                                            : ''}<br />
                                                                        expires: {quotaBreakdown.tier_override.expires_at ? new Date(quotaBreakdown.tier_override.expires_at).toLocaleString() : '—'}
                                                                    </div>
                                                                </>
                                                            ) : (
                                                                <>No override</>
                                                            )}
                                                        </div>
                                                    </div>

                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="font-semibold text-gray-900 mb-1">Effective</div>
                                                        <div className="text-gray-600">
                                                            req/day: {quotaBreakdown.effective_policy.requests_per_day ?? '—'}<br />
                                                            tok/month: {quotaBreakdown.effective_policy.tokens_per_month?.toLocaleString?.() ?? quotaBreakdown.effective_policy.tokens_per_month ?? '—'}
                                                            {quotaBreakdown.effective_policy.usd_per_month != null
                                                                ? ` ($${Number(quotaBreakdown.effective_policy.usd_per_month).toFixed(2)})`
                                                                : ''}
                                                        </div>
                                                    </div>
                                                </div>
                                                {quotaBreakdown.reference_model && (
                                                    <div className="pt-3 text-xs text-gray-500">
                                                        Reference: {quotaBreakdown.reference_model}
                                                    </div>
                                                )}
                                            </div>

                                            <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                <div className="flex items-center justify-between">
                                                    <div>
                                                        <div className="text-sm font-semibold text-gray-900">Lifetime credits</div>
                                                        <div className="text-xs text-gray-600 mt-1">Gross / reserved / available</div>
                                                    </div>
                                                    <div className="text-2xl">💳</div>
                                                </div>

                                                {!quotaBreakdown.lifetime_credits ? (
                                                    <div className="mt-4 text-sm text-gray-600">
                                                        No lifetime credits record for this user.
                                                    </div>
                                                ) : (
                                                    <div className="mt-4 space-y-2 text-sm">
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Purchased</span>
                                                            <span className="font-semibold text-gray-900">
                                                                {quotaBreakdown.lifetime_credits.tokens_purchased.toLocaleString()}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Consumed</span>
                                                            <span className="font-semibold text-gray-900">
                                                                {quotaBreakdown.lifetime_credits.tokens_consumed.toLocaleString()}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Gross remaining</span>
                                                            <span className="font-semibold text-gray-900">
                                                                {quotaBreakdown.lifetime_credits.tokens_gross_remaining.toLocaleString()}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Reserved</span>
                                                            <span className="font-semibold text-gray-900">
                                                                {quotaBreakdown.lifetime_credits.tokens_reserved.toLocaleString()}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Available now</span>
                                                            <span className="font-semibold text-gray-900">
                                                                {quotaBreakdown.lifetime_credits.tokens_available.toLocaleString()}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Available USD (quoted)</span>
                                                            <span className="font-semibold text-gray-900">
                                                                ${Number(quotaBreakdown.lifetime_credits.available_usd || 0).toFixed(2)}
                                                            </span>
                                                        </div>

                                                        <div className="pt-3 border-t border-gray-200/70 text-xs text-gray-600">
                                                            Reference: {quotaBreakdown.lifetime_credits.reference_model}
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        </div>

                                        {/* Reservations table */}
                                        {quotaBreakdown.active_reservations?.length > 0 && (
                                            <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                <div className="text-sm font-semibold text-gray-900 mb-3">Active credit reservations</div>
                                                <div className="overflow-x-auto">
                                                    <table className="w-full text-sm">
                                                        <thead className="bg-white border-b border-gray-200/70">
                                                        <tr className="text-gray-600">
                                                            <th className="px-4 py-3 text-left font-semibold">Reservation</th>
                                                            <th className="px-4 py-3 text-left font-semibold">Bundle</th>
                                                            <th className="px-4 py-3 text-right font-semibold">Tokens</th>
                                                            <th className="px-4 py-3 text-left font-semibold">Expires</th>
                                                            <th className="px-4 py-3 text-left font-semibold">Notes</th>
                                                        </tr>
                                                        </thead>
                                                        <tbody className="divide-y divide-gray-200/70">
                                                        {quotaBreakdown.active_reservations.map((r) => (
                                                            <tr key={r.reservation_id} className="hover:bg-white/70 transition-colors">
                                                                <td className="px-4 py-3 font-semibold text-gray-900">{r.reservation_id}</td>
                                                                <td className="px-4 py-3 text-gray-700">{r.bundle_id ?? '—'}</td>
                                                                <td className="px-4 py-3 text-right text-gray-700">{Number(r.tokens_reserved || 0).toLocaleString()}</td>
                                                                <td className="px-4 py-3 text-gray-700">{r.expires_at ? new Date(r.expires_at).toLocaleString() : '—'}</td>
                                                                <td className="px-4 py-3 text-gray-600">{r.notes ?? '—'}</td>
                                                            </tr>
                                                        ))}
                                                        </tbody>
                                                    </table>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                )}

                            </CardBody>
                        </Card>
                    )}

                    {/* Quota Policies */}
                    {viewMode === 'quotaPolicies' && (
                        <div className="space-y-6">
                            <Card>
                                <CardHeader
                                    title="Set Tier Policy"
                                    subtitle="Base limits per user_type (global for tenant/project). No bundle_id."
                                />
                                <CardBody className="space-y-6">
                                    <Callout tone="neutral" title="Meaning">
                                        This is the default tier envelope for a user class (free/paid/premium). These counters reset on their window (day/month).
                                    </Callout>
                                    {economicsRef && (
                                        <div className="text-xs text-gray-500">
                                            Reference: {economicsRef.reference_provider}/{economicsRef.reference_model}
                                        </div>
                                    )}

                                    <form onSubmit={handleSetQuotaPolicy} className="space-y-5">
                                        <Select
                                            label="User Type *"
                                            value={policyUserType}
                                            onChange={(e) => setPolicyUserType(e.target.value)}
                                            options={USER_TYPE_OPTIONS}
                                        />
                                        {policyUserType === 'custom' && (
                                            <Input
                                                label="Custom user_type *"
                                                value={policyUserTypeCustom}
                                                onChange={(e) => setPolicyUserTypeCustom(e.target.value)}
                                                placeholder="e.g. enterprise"
                                                required
                                            />
                                        )}

                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <Input
                                                label="Max concurrent"
                                                type="number"
                                                value={policyMaxConcurrent}
                                                onChange={(e) => setPolicyMaxConcurrent(e.target.value)}
                                                placeholder="1"
                                            />
                                            <Input
                                                label="Requests / day"
                                                type="number"
                                                value={policyRequestsDay}
                                                onChange={(e) => setPolicyRequestsDay(e.target.value)}
                                                placeholder="10"
                                            />
                                            <Input
                                                label="Requests / month"
                                                type="number"
                                                value={policyRequestsMonth}
                                                onChange={(e) => setPolicyRequestsMonth(e.target.value)}
                                                placeholder="300"
                                            />
                                            <div>
                                                <Input
                                                    label="Tokens / hour"
                                                    type="number"
                                                    value={policyTokensHour}
                                                    onChange={(e) => setPolicyTokensHour(e.target.value)}
                                                    placeholder="500000"
                                                />
                                                {policyTokensHour && tokensToUsd(policyTokensHour) != null && (
                                                    <div className="text-xs text-gray-500 pt-1">
                                                        ≈ ${Number(tokensToUsd(policyTokensHour)).toFixed(2)}
                                                    </div>
                                                )}
                                            </div>
                                            <div>
                                                <Input
                                                    label="Tokens / day"
                                                    type="number"
                                                    value={policyTokensDay}
                                                    onChange={(e) => setPolicyTokensDay(e.target.value)}
                                                    placeholder="1000000"
                                                />
                                                {policyTokensDay && tokensToUsd(policyTokensDay) != null && (
                                                    <div className="text-xs text-gray-500 pt-1">
                                                        ≈ ${Number(tokensToUsd(policyTokensDay)).toFixed(2)}
                                                    </div>
                                                )}
                                            </div>
                                            <div>
                                                <Input
                                                    label="Tokens / month"
                                                    type="number"
                                                    value={policyTokensMonth}
                                                    onChange={(e) => setPolicyTokensMonth(e.target.value)}
                                                    placeholder="30000000"
                                                />
                                                {policyTokensMonth && tokensToUsd(policyTokensMonth) != null && (
                                                    <div className="text-xs text-gray-500 pt-1">
                                                        ≈ ${Number(tokensToUsd(policyTokensMonth)).toFixed(2)}
                                                    </div>
                                                )}
                                            </div>
                                            <div>
                                                <Input
                                                    label="USD / hour"
                                                    type="number"
                                                    value={policyUsdHour}
                                                    onChange={(e) => setPolicyUsdHour(e.target.value)}
                                                    placeholder="5"
                                                    min={0}
                                                    step="0.01"
                                                />
                                                {policyUsdHour && usdToTokens(policyUsdHour) != null && (
                                                    <div className="text-xs text-gray-500 pt-1">
                                                        ≈ {Number(usdToTokens(policyUsdHour)).toLocaleString()} tokens
                                                    </div>
                                                )}
                                            </div>
                                            <div>
                                                <Input
                                                    label="USD / day"
                                                    type="number"
                                                    value={policyUsdDay}
                                                    onChange={(e) => setPolicyUsdDay(e.target.value)}
                                                    placeholder="50"
                                                    min={0}
                                                    step="0.01"
                                                />
                                                {policyUsdDay && usdToTokens(policyUsdDay) != null && (
                                                    <div className="text-xs text-gray-500 pt-1">
                                                        ≈ {Number(usdToTokens(policyUsdDay)).toLocaleString()} tokens
                                                    </div>
                                                )}
                                            </div>
                                            <div>
                                                <Input
                                                    label="USD / month"
                                                    type="number"
                                                    value={policyUsdMonth}
                                                    onChange={(e) => setPolicyUsdMonth(e.target.value)}
                                                    placeholder="500"
                                                    min={0}
                                                    step="0.01"
                                                />
                                                {policyUsdMonth && usdToTokens(policyUsdMonth) != null && (
                                                    <div className="text-xs text-gray-500 pt-1">
                                                        ≈ {Number(usdToTokens(policyUsdMonth)).toLocaleString()} tokens
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                        <div className="text-xs text-gray-500">
                                            USD overrides tokens for the same window.
                                        </div>

                                        <TextArea
                                            label="Notes"
                                            value={policyNotes}
                                            onChange={(e) => setPolicyNotes(e.target.value)}
                                            placeholder="Free tier limits (global per tenant/project)"
                                        />

                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Saving…' : 'Save Policy'}
                                        </Button>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Current Tier Quota Policies"
                                    subtitle={`${quotaPolicies.length} policy records`}
                                />
                                <CardBody>
                                    {loadingData ? (
                                        <LoadingSpinner />
                                    ) : quotaPolicies.length === 0 ? (
                                        <EmptyState message="No tier policies configured." icon="📋" />
                                    ) : (
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-gray-50 border-b border-gray-200/70">
                                                <tr className="text-gray-600">
                                                    <th className="px-6 py-4 text-left font-semibold">User type</th>
                                                    <th className="px-6 py-4 text-right font-semibold">Max concurrent</th>
                                                    <th className="px-6 py-4 text-right font-semibold">Req/day</th>
                                                    <th className="px-6 py-4 text-right font-semibold">Tok/hour</th>
                                                    <th className="px-6 py-4 text-right font-semibold">Tok/day</th>
                                                    <th className="px-6 py-4 text-right font-semibold">Tok/month</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/hour</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/day</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/month</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Notes</th>
                                                </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-200/70">
                                                {quotaPolicies.map((policy, idx) => (
                                                    <tr key={idx} className="hover:bg-gray-50/70 transition-colors">
                                                        <td className="px-6 py-4 font-semibold text-gray-900">{policy.user_type}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">{policy.max_concurrent ?? '—'}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">{policy.requests_per_day ?? '—'}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">{policy.tokens_per_hour?.toLocaleString() ?? '—'}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">{policy.tokens_per_day?.toLocaleString() ?? '—'}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">{policy.tokens_per_month?.toLocaleString() ?? '—'}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_hour != null ? `$${Number(policy.usd_per_hour).toFixed(2)}` : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_day != null ? `$${Number(policy.usd_per_day).toFixed(2)}` : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_month != null ? `$${Number(policy.usd_per_month).toFixed(2)}` : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-600">{policy.notes || '—'}</td>
                                                    </tr>
                                                ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}
                                    {quotaPolicies.length > 0 && quotaPolicies[0].reference_model && (
                                        <div className="pt-3 text-xs text-gray-500">
                                            Reference: {quotaPolicies[0].reference_model}
                                        </div>
                                    )}
                                </CardBody>
                            </Card>
                        </div>
                    )}

                    {/* Budget Policies */}
                    {viewMode === 'budgetPolicies' && (
                        <div className="space-y-6">
                            <Card>
                                <CardHeader
                                    title="Set Provider Budget Policy"
                                    subtitle="Spending limits per provider for the tenant/project (no bundle_id)."
                                />
                                <CardBody className="space-y-6">
                                    <Callout tone="neutral" title="Meaning">
                                        This is a hard ceiling to prevent runaway costs. Typical usage: cap Anthropic at $/day or $/month.
                                    </Callout>

                                    <form onSubmit={handleSetBudgetPolicy} className="space-y-5">
                                        <Input
                                            label="Provider *"
                                            value={budgetProvider}
                                            onChange={(e) => setBudgetProvider(e.target.value)}
                                            placeholder="anthropic"
                                            required
                                        />

                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <Input
                                                label="USD / hour"
                                                type="number"
                                                step="0.01"
                                                value={budgetUsdHour}
                                                onChange={(e) => setBudgetUsdHour(e.target.value)}
                                                placeholder="10.00"
                                            />
                                            <Input
                                                label="USD / day"
                                                type="number"
                                                step="0.01"
                                                value={budgetUsdDay}
                                                onChange={(e) => setBudgetUsdDay(e.target.value)}
                                                placeholder="200.00"
                                            />
                                            <Input
                                                label="USD / month"
                                                type="number"
                                                step="0.01"
                                                value={budgetUsdMonth}
                                                onChange={(e) => setBudgetUsdMonth(e.target.value)}
                                                placeholder="5000.00"
                                            />
                                        </div>

                                        <TextArea
                                            label="Notes"
                                            value={budgetNotes}
                                            onChange={(e) => setBudgetNotes(e.target.value)}
                                            placeholder="Daily spending limit for provider"
                                        />

                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Saving…' : 'Save Budget Policy'}
                                        </Button>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Current Budget Policies"
                                    subtitle={`${budgetPolicies.length} policy records`}
                                />
                                <CardBody>
                                    {loadingData ? (
                                        <LoadingSpinner />
                                    ) : budgetPolicies.length === 0 ? (
                                        <EmptyState message="No budget policies configured." icon="💵" />
                                    ) : (
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-gray-50 border-b border-gray-200/70">
                                                <tr className="text-gray-600">
                                                    <th className="px-6 py-4 text-left font-semibold">Provider</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/hour</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/day</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/month</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Notes</th>
                                                </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-200/70">
                                                {budgetPolicies.map((policy, idx) => (
                                                    <tr key={idx} className="hover:bg-gray-50/70 transition-colors">
                                                        <td className="px-6 py-4 font-semibold text-gray-900">{policy.provider}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_hour != null ? `$${policy.usd_per_hour.toFixed(2)}` : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_day != null ? `$${policy.usd_per_day.toFixed(2)}` : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_month != null ? `$${policy.usd_per_month.toFixed(2)}` : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-600">{policy.notes || '—'}</td>
                                                    </tr>
                                                ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}
                                </CardBody>
                            </Card>
                        </div>
                    )}

                    {/* Lifetime Credits */}
                    {viewMode === 'lifetimeCredits' && (
                        <div className="space-y-6">
                            <Card>
                                <CardHeader
                                    title="Lifetime Credits (USD → tokens)"
                                    subtitle="One-time purchase adds tokens until depleted. These do not reset. Quoted using the backend reference model."
                                />
                                <CardBody className="space-y-6">
                                    <Callout tone="info" title="Quick interpretation">
                                        “Balance tokens” is what the user can spend. If balance drops below the admission threshold, the system may block paid usage.
                                    </Callout>

                                    <form onSubmit={handleAddLifetimeCredits} className="space-y-5">
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <Input
                                                label="User ID *"
                                                value={lifetimeUserId}
                                                onChange={(e) => setLifetimeUserId(e.target.value)}
                                                placeholder="user123"
                                                required
                                            />
                                            <Input
                                                label="Amount (USD) *"
                                                type="number"
                                                step="0.01"
                                                value={lifetimeUsdAmount}
                                                onChange={(e) => setLifetimeUsdAmount(e.target.value)}
                                                placeholder="10.00"
                                                required
                                            />
                                        </div>

                                        <TextArea
                                            label="Purchase Notes"
                                            value={lifetimeNotes}
                                            onChange={(e) => setLifetimeNotes(e.target.value)}
                                            placeholder="Stripe payment ID / invoice / manual purchase note"
                                        />

                                        <div className="flex flex-wrap gap-3">
                                            <Button type="submit" disabled={loadingAction}>
                                                {loadingAction ? 'Processing…' : 'Add Credits'}
                                            </Button>
                                            <Button
                                                type="button"
                                                variant="secondary"
                                                onClick={() => handleCheckLifetimeBalance(new Event('submit') as any)}
                                                disabled={loadingAction || !lifetimeUserId.trim()}
                                            >
                                                Check Balance
                                            </Button>
                                            <div className="text-sm text-gray-500 flex items-center">
                                                Reference model: <span className="ml-1 font-semibold text-gray-800">anthropic/claude-sonnet-4-5-20250929</span>
                                            </div>
                                        </div>
                                    </form>
                                </CardBody>
                            </Card>

                            {lifetimeBalance && (
                                <Card>
                                    <CardHeader title={`Current Balance: ${lifetimeBalance.user_id}`} />
                                    <CardBody className="space-y-5">
                                        {lifetimeBalance.has_purchased_credits ? (
                                            <>
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                    <StatCard label="Tokens remaining" value={lifetimeBalance.balance_tokens.toLocaleString()} />
                                                    <StatCard label="USD equivalent (quoted)" value={`$${Number(lifetimeBalance.balance_usd || 0).toFixed(2)}`} />
                                                </div>

                                                {!lifetimeBalance.can_use_budget && (
                                                    <Callout tone="warning" title="Below admission threshold">
                                                        Needs at least{' '}
                                                        {Number(lifetimeBalance.minimum_required_tokens || 0).toLocaleString()} tokens
                                                        {minUsd != null ? ` (≈ $${minUsd.toFixed(2)})` : ''}
                                                        {' '}to run in the paid lane.
                                                    </Callout>
                                                )}
                                            </>
                                        ) : (
                                            <EmptyState message="No purchased credits found. This user operates on tier quotas only." icon="💳" />
                                        )}
                                    </CardBody>
                                </Card>
                            )}

                            <Card>
                                <CardHeader
                                    title="What the USD conversion means"
                                    subtitle="We quote purchases using a fixed reference model so USD→tokens is predictable."
                                />
                                <CardBody>
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                            <div className="text-xs font-semibold text-gray-500 uppercase">Example</div>
                                            <div className="mt-2 text-2xl font-semibold text-gray-900">$5.00</div>
                                            <div className="mt-2 text-sm text-gray-600">Converted using reference model rate</div>
                                        </div>
                                        <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                            <div className="text-xs font-semibold text-gray-500 uppercase">Example</div>
                                            <div className="mt-2 text-2xl font-semibold text-gray-900">$10.00</div>
                                            <div className="mt-2 text-sm text-gray-600">Converted using reference model rate</div>
                                        </div>
                                        <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                            <div className="text-xs font-semibold text-gray-500 uppercase">Example</div>
                                            <div className="mt-2 text-2xl font-semibold text-gray-900">$50.00</div>
                                            <div className="mt-2 text-sm text-gray-600">Converted using reference model rate</div>
                                        </div>
                                    </div>
                                </CardBody>
                            </Card>
                        </div>
                    )}

                    {/* App Budget */}
                    {viewMode === 'appBudget' && (
                        <div className="space-y-6">
                            <Card>
                                <CardHeader
                                    title="Application Budget"
                                    subtitle="Tenant/project wallet used for company-funded spending (typical: tier-funded usage)."
                                />
                                <CardBody className="space-y-6">
                                    <Callout tone="neutral" title="Meaning">
                                        This is the master budget for the tenant/project. If your policy charges tier-funded usage to the company,
                                        spending will appear here.
                                    </Callout>

                                    {loadingData ? (
                                        <LoadingSpinner />
                                    ) : !appBudget ? (
                                        <EmptyState message="No budget data loaded." icon="💰" />
                                    ) : (
                                        <>
                                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                                <StatCard label="Current balance" value={`$${Number(appBudget.balance.balance_usd || 0).toFixed(2)}`} />
                                                <StatCard label="Lifetime added" value={`$${Number(appBudget.balance.lifetime_added_usd || 0).toFixed(2)}`} />
                                                <StatCard label="Lifetime spent" value={`$${Number(appBudget.balance.lifetime_spent_usd || 0).toFixed(2)}`} />
                                            </div>

                                            <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                <div className="text-sm font-semibold text-gray-900 mb-3">Current month spending</div>
                                                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="text-xs text-gray-500 font-semibold uppercase">This hour</div>
                                                        <div className="mt-2 text-2xl font-semibold text-gray-900">
                                                            ${Number(appBudget.current_month_spending?.hour || 0).toFixed(2)}
                                                        </div>
                                                    </div>
                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="text-xs text-gray-500 font-semibold uppercase">Today</div>
                                                        <div className="mt-2 text-2xl font-semibold text-gray-900">
                                                            ${Number(appBudget.current_month_spending?.day || 0).toFixed(2)}
                                                        </div>
                                                    </div>
                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="text-xs text-gray-500 font-semibold uppercase">This month</div>
                                                        <div className="mt-2 text-2xl font-semibold text-gray-900">
                                                            ${Number(appBudget.current_month_spending?.month || 0).toFixed(2)}
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>

                                            {appBudget.by_bundle && Object.keys(appBudget.by_bundle).length > 0 && (
                                                <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                    <div className="text-sm font-semibold text-gray-900 mb-3">Spending by bundle</div>
                                                    <div className="space-y-3">
                                                        {Object.entries(appBudget.by_bundle).map(([bundleId, spending]) => (
                                                            <div
                                                                key={bundleId}
                                                                className="flex flex-col md:flex-row md:items-center md:justify-between gap-2
                                           rounded-xl bg-white border border-gray-200/70 p-4"
                                                            >
                                                                <div className="font-semibold text-gray-900">{bundleId}</div>
                                                                <div className="text-sm text-gray-600 flex flex-wrap gap-4">
                                                                    <span>Hour: <strong className="text-gray-900">${Number(spending.hour || 0).toFixed(2)}</strong></span>
                                                                    <span>Day: <strong className="text-gray-900">${Number(spending.day || 0).toFixed(2)}</strong></span>
                                                                    <span>Month: <strong className="text-gray-900">${Number(spending.month || 0).toFixed(2)}</strong></span>
                                                                </div>
                                                            </div>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}
                                        </>
                                    )}
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader title="Top up application budget" subtitle="Adds funds to the tenant/project wallet." />
                                <CardBody className="space-y-6">
                                    <Callout tone="warning" title="When you need this">
                                        If you’re company-funding tier usage (or any fallback path), you want enough budget to prevent service disruption.
                                    </Callout>

                                    <form onSubmit={handleTopupAppBudget} className="space-y-5">
                                        <Input
                                            label="Amount (USD) *"
                                            type="number"
                                            step="0.01"
                                            value={appBudgetTopup}
                                            onChange={(e) => setAppBudgetTopup(e.target.value)}
                                            placeholder="100.00"
                                            required
                                        />
                                        <TextArea
                                            label="Notes"
                                            value={appBudgetNotes}
                                            onChange={(e) => setAppBudgetNotes(e.target.value)}
                                            placeholder="Monthly budget allocation"
                                        />
                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Processing…' : 'Add funds'}
                                        </Button>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader title="Budget flow examples" subtitle="Quick mental model for support & ops." />
                                <CardBody className="space-y-4">
                                    <Callout tone="info" title="Scenario: tier-funded usage">
                                        User operates within effective tier limits → request allowed → company budget is charged (typical policy).
                                    </Callout>
                                    <Callout tone="success" title="Scenario: user-funded fallback">
                                        User exceeds tier → purchased credits present → user credits are charged → app budget not used.
                                    </Callout>
                                    <Callout tone="warning" title="Scenario: mixed / policy-dependent">
                                        Some flows may split charges depending on limiter policy and reservations (in-flight holds).
                                    </Callout>
                                </CardBody>
                            </Card>
                        </div>
                    )}
                    {/* Subscriptions */}
                    {viewMode === 'subscriptions' && (
                        <div className="space-y-6">
                            <Card>
                                <CardHeader
                                    title="Create Subscription"
                                    subtitle="Creates an internal subscription row or a Stripe subscription (Stripe needs stripe_price_id)."
                                />
                                <CardBody className="space-y-6">
                                    <form onSubmit={handleCreateSubscription} className="space-y-5">
                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <Select
                                                label="Provider"
                                                value={subProvider}
                                                onChange={(e) => setSubProvider(e.target.value as any)}
                                                options={[
                                                    { value: 'internal', label: 'Manual' },
                                                    { value: 'stripe', label: 'Stripe' },
                                                ]}
                                            />
                                            <Select
                                                label="Tier"
                                                value={subTier}
                                                onChange={(e) => setSubTier(e.target.value)}
                                                options={[
                                                    { value: 'free', label: 'free' },
                                                    { value: 'paid', label: 'paid' },
                                                    { value: 'premium', label: 'premium' },
                                                    { value: 'admin', label: 'admin' },
                                                ]}
                                            />
                                            <Input
                                                label="User ID *"
                                                value={subUserId}
                                                onChange={(e) => setSubUserId(e.target.value)}
                                                placeholder="user123"
                                                required
                                            />
                                        </div>

                                        {subProvider === 'stripe' && (
                                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                                <Input
                                                    label="stripe_price_id *"
                                                    value={subStripePriceId}
                                                    onChange={(e) => setSubStripePriceId(e.target.value)}
                                                    placeholder="price_..."
                                                    required
                                                />
                                                <Input
                                                    label="stripe_customer_id (optional)"
                                                    value={subStripeCustomerId}
                                                    onChange={(e) => setSubStripeCustomerId(e.target.value)}
                                                    placeholder="cus_..."
                                                />
                                                <Input
                                                    label="monthly_price_cents_hint (optional)"
                                                    type="number"
                                                    value={subPriceHint}
                                                    onChange={(e) => setSubPriceHint(e.target.value)}
                                                    placeholder="2000"
                                                />
                                            </div>
                                        )}

                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Creating…' : 'Create Subscription'}
                                        </Button>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Lookup Subscription (by user)"
                                    subtitle="Shows the current subscription row stored in user_subscriptions."
                                />
                                <CardBody className="space-y-6">
                                    <form onSubmit={handleLookupSubscription} className="space-y-4">
                                        <div className="flex gap-3">
                                            <Input
                                                value={subLookupUserId}
                                                onChange={(e) => setSubLookupUserId(e.target.value)}
                                                placeholder="user123"
                                                required
                                                className="flex-1"
                                            />
                                            <Button type="submit" disabled={loadingAction}>
                                                {loadingAction ? 'Loading…' : 'Lookup'}
                                            </Button>
                                        </div>
                                    </form>

                                    {subscription && (
                                        <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5 text-sm space-y-3">
                                            <div className="flex items-center justify-between">
                                                <div className="font-semibold text-gray-900">Subscription</div>
                                                <DuePill sub={subscription} />
                                            </div>

                                            <div className="space-y-2">
                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">billing</span>
                                                    <strong>{providerLabel(subscription.provider)}</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">tier</span>
                                                    <strong>{subscription.tier}</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">status</span>
                                                    <strong>{subscription.status}</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">monthly price</span>
                                                    <strong>${(Number(subscription.monthly_price_cents || 0) / 100).toFixed(2)} ({subscription.monthly_price_cents}¢)</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">started</span>
                                                    <strong>{formatDateTime(subscription.started_at)}</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">last charge</span>
                                                    <strong>{formatDateTime(subscription.last_charged_at)}</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">next charge</span>
                                                    <strong>{formatDateTime(subscription.next_charge_at)}</strong>
                                                </div>

                                                {subscription.provider === 'stripe' && (
                                                    <>
                                                        <div className="flex justify-between">
                                                            <span className="text-gray-600">stripe_customer_id</span>
                                                            <strong>{subscription.stripe_customer_id || '—'}</strong>
                                                        </div>
                                                        <div className="flex justify-between">
                                                            <span className="text-gray-600">stripe_subscription_id</span>
                                                            <strong>{subscription.stripe_subscription_id || '—'}</strong>
                                                        </div>
                                                    </>
                                                )}
                                            </div>

                                            {subscriptionBalance && (
                                                <div className="pt-4 border-t border-gray-200/70 space-y-2">
                                                    <div className="text-sm font-semibold text-gray-900">Subscription balance</div>
                                                    <div className="text-xs text-gray-600">
                                                        Reference: {subscriptionBalance.reference_model || 'anthropic/claude-sonnet-4-5-20250929'}
                                                    </div>
                                                    {subscriptionBalance.period_start && subscriptionBalance.period_end && (
                                                        <div className="text-xs text-gray-600">
                                                            Period: {formatDateTime(subscriptionBalance.period_start)} → {formatDateTime(subscriptionBalance.period_end)}
                                                        </div>
                                                    )}

                                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
                                                        <div className="rounded-xl bg-white border border-gray-200/70 p-3">
                                                            <div className="text-gray-600">Balance</div>
                                                            <div className="font-semibold text-gray-900">
                                                                ${Number(subscriptionBalance.balance_usd || 0).toFixed(2)}
                                                            </div>
                                                            {subscriptionBalance.balance_tokens != null && (
                                                                <div className="text-xs text-gray-500">
                                                                    {Number(subscriptionBalance.balance_tokens).toLocaleString()} tokens
                                                                </div>
                                                            )}
                                                        </div>
                                                        <div className="rounded-xl bg-white border border-gray-200/70 p-3">
                                                            <div className="text-gray-600">Reserved</div>
                                                            <div className="font-semibold text-gray-900">
                                                                ${Number(subscriptionBalance.reserved_usd || 0).toFixed(2)}
                                                            </div>
                                                            {subscriptionBalance.reserved_tokens != null && (
                                                                <div className="text-xs text-gray-500">
                                                                    {Number(subscriptionBalance.reserved_tokens).toLocaleString()} tokens
                                                                </div>
                                                            )}
                                                        </div>
                                                        <div className="rounded-xl bg-white border border-gray-200/70 p-3">
                                                            <div className="text-gray-600">Available</div>
                                                            <div className="font-semibold text-gray-900">
                                                                ${Number(subscriptionBalance.available_usd || 0).toFixed(2)}
                                                            </div>
                                                            {subscriptionBalance.available_tokens != null && (
                                                                <div className="text-xs text-gray-500">
                                                                    {Number(subscriptionBalance.available_tokens).toLocaleString()} tokens
                                                                </div>
                                                            )}
                                                        </div>
                                                    </div>

                                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
                                                        <div className="rounded-xl bg-white border border-gray-200/70 p-3">
                                                            <div className="text-gray-600">Period top-up</div>
                                                            <div className="font-semibold text-gray-900">
                                                                ${Number(subscriptionBalance.topup_usd ?? subscriptionBalance.lifetime_added_usd ?? 0).toFixed(2)}
                                                            </div>
                                                        </div>
                                                        <div className="rounded-xl bg-white border border-gray-200/70 p-3">
                                                            <div className="text-gray-600">Period spent</div>
                                                            <div className="font-semibold text-gray-900">
                                                                ${Number(subscriptionBalance.spent_usd ?? subscriptionBalance.lifetime_spent_usd ?? 0).toFixed(2)}
                                                            </div>
                                                        </div>
                                                        <div className="rounded-xl bg-white border border-gray-200/70 p-3">
                                                            <div className="text-gray-600">Rolled over</div>
                                                            <div className="font-semibold text-gray-900">
                                                                ${Number(subscriptionBalance.rolled_over_usd || 0).toFixed(2)}
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            )}

                                            {/* Internal ops */}
                                            {subscription.provider === 'internal' &&
                                                subscription.status === 'active' &&
                                                (subscription.tier === 'paid' || subscription.tier === 'premium') && (
                                                    <div className="pt-4 border-t border-gray-200/70 flex flex-wrap items-center justify-between gap-3">
                                                        <div className="text-xs text-gray-600">
                                                            Manual billing: renew will top-up subscription balance and advance next due date.
                                                        </div>

                                                        <Button
                                                            type="button"
                                                            variant="secondary"
                                                            disabled={loadingAction}
                                                            onClick={async () => {
                                                                clearMessages();
                                                                setLoadingAction(true);
                                                                try {
                                                                    const res = await api.renewInternalSubscriptionOnce({ userId: subscription.user_id });
                                                                    setSuccess(res.message || `Renewed ${subscription.user_id}`);
                                                                    // refresh displayed subscription
                                                                    const fresh = await api.getSubscription(subscription.user_id);
                                                                    setSubscription(fresh.subscription);
                                                                    setSubscriptionBalance(fresh.subscription_balance || null);
                                                                } catch (err) {
                                                                    setError((err as Error).message);
                                                                } finally {
                                                                    setLoadingAction(false);
                                                                }
                                                            }}
                                                        >
                                                            {loadingAction ? 'Renewing…' : 'Renew now'}
                                                        </Button>
                                                    </div>
                                                )}
                                        </div>
                                    )}
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Subscription Balance Admin"
                                    subtitle="Manual top-ups and overdraft configuration for a user's subscription balance."
                                />
                                <CardBody className="space-y-6">
                                    <form onSubmit={handleTopupSubscriptionBudget} className="space-y-4">
                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <Input
                                                label="User ID *"
                                                value={subBudgetUserId}
                                                onChange={(e) => setSubBudgetUserId(e.target.value)}
                                                placeholder="user123"
                                                required
                                            />
                                            <Input
                                                label="Top-up USD *"
                                                type="number"
                                                value={subBudgetUsdAmount}
                                                onChange={(e) => setSubBudgetUsdAmount(e.target.value)}
                                                placeholder="50"
                                                required
                                            />
                                            <Input
                                                label="Notes"
                                                value={subBudgetNotes}
                                                onChange={(e) => setSubBudgetNotes(e.target.value)}
                                                placeholder="Optional notes"
                                            />
                                        </div>
                                        <label className="flex items-center gap-2 text-sm text-gray-700">
                                            <input
                                                type="checkbox"
                                                checked={subBudgetForceTopup}
                                                onChange={(e) => setSubBudgetForceTopup(e.target.checked)}
                                                className="h-4 w-4 rounded border-gray-300 text-gray-900 focus:ring-gray-900/20"
                                            />
                                            Force topup (allow multiple in the same billing period)
                                        </label>
                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Processing…' : 'Top-up Subscription Balance'}
                                        </Button>
                                    </form>

                                    <form onSubmit={handleSetSubscriptionOverdraft} className="space-y-4">
                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <Input
                                                label="User ID *"
                                                value={subBudgetUserId}
                                                onChange={(e) => setSubBudgetUserId(e.target.value)}
                                                placeholder="user123"
                                                required
                                            />
                                            <Input
                                                label="Overdraft Limit USD (blank = unlimited)"
                                                type="number"
                                                value={subOverdraftUsd}
                                                onChange={(e) => setSubOverdraftUsd(e.target.value)}
                                                placeholder="0"
                                            />
                                            <Input
                                                label="Notes"
                                                value={subBudgetNotes}
                                                onChange={(e) => setSubBudgetNotes(e.target.value)}
                                                placeholder="Optional notes"
                                            />
                                        </div>
                                        <Button type="submit" variant="secondary" disabled={loadingAction}>
                                            {loadingAction ? 'Updating…' : 'Set Overdraft'}
                                        </Button>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Wallet Refund (Stripe)"
                                    subtitle="Refund a Stripe payment_intent. Credits are removed immediately; finalization happens via Stripe webhook."
                                />
                                <CardBody className="space-y-4">
                                    <form onSubmit={handleWalletRefund} className="space-y-4">
                                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                                            <Input
                                                label="User ID *"
                                                value={walletRefundUserId}
                                                onChange={(e) => setWalletRefundUserId(e.target.value)}
                                                placeholder="user123"
                                                required
                                            />
                                            <Input
                                                label="Payment Intent ID *"
                                                value={walletRefundPaymentIntentId}
                                                onChange={(e) => setWalletRefundPaymentIntentId(e.target.value)}
                                                placeholder="pi_..."
                                                required
                                            />
                                            <Input
                                                label="Refund USD (blank = full)"
                                                type="number"
                                                value={walletRefundUsdAmount}
                                                onChange={(e) => setWalletRefundUsdAmount(e.target.value)}
                                                placeholder="25.00"
                                            />
                                            <Input
                                                label="Notes"
                                                value={walletRefundNotes}
                                                onChange={(e) => setWalletRefundNotes(e.target.value)}
                                                placeholder="Optional notes"
                                            />
                                        </div>
                                        <Button type="submit" variant="danger" disabled={loadingAction}>
                                            {loadingAction ? 'Processing…' : 'Request Refund'}
                                        </Button>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Cancel Stripe Subscription"
                                    subtitle="Request cancellation at period end (current balance remains usable)."
                                />
                                <CardBody className="space-y-4">
                                    <form onSubmit={handleCancelSubscription} className="space-y-4">
                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <Input
                                                label="User ID"
                                                value={cancelSubUserId}
                                                onChange={(e) => setCancelSubUserId(e.target.value)}
                                                placeholder="user123"
                                            />
                                            <Input
                                                label="Stripe Subscription ID"
                                                value={cancelSubStripeId}
                                                onChange={(e) => setCancelSubStripeId(e.target.value)}
                                                placeholder="sub_..."
                                            />
                                            <Input
                                                label="Notes"
                                                value={cancelSubNotes}
                                                onChange={(e) => setCancelSubNotes(e.target.value)}
                                                placeholder="Optional notes"
                                            />
                                        </div>
                                        <Button type="submit" variant="secondary" disabled={loadingAction}>
                                            {loadingAction ? 'Submitting…' : 'Request Cancellation'}
                                        </Button>
                                    </form>
                                    <div className="text-xs text-gray-500">
                                        Provide either User ID or Stripe Subscription ID.
                                    </div>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Stripe Reconcile"
                                    subtitle="Check pending Stripe refund/cancel requests if a webhook was missed."
                                />
                                <CardBody className="space-y-4">
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
                                        <Select
                                            label="Kind"
                                            value={stripeReconcileKind}
                                            onChange={(e) => setStripeReconcileKind(e.target.value as 'all' | 'wallet_refund' | 'subscription_cancel')}
                                            options={[
                                                { value: 'all', label: 'all' },
                                                { value: 'wallet_refund', label: 'wallet_refund' },
                                                { value: 'subscription_cancel', label: 'subscription_cancel' },
                                            ]}
                                        />
                                        <div className="md:col-span-2">
                                            <Button type="button" variant="secondary" disabled={loadingAction} onClick={handleStripeReconcile}>
                                                {loadingAction ? 'Reconciling…' : 'Run Reconcile'}
                                            </Button>
                                        </div>
                                    </div>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Pending Stripe Requests"
                                    subtitle="Audit view for pending refunds/cancellations."
                                    action={
                                        <Button variant="secondary" onClick={handleLoadPendingStripe} disabled={loadingPendingStripe}>
                                            {loadingPendingStripe ? 'Loading…' : 'Refresh'}
                                        </Button>
                                    }
                                />
                                <CardBody className="space-y-4">
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <Select
                                            label="Kind filter"
                                            value={pendingStripeKind}
                                            onChange={(e) => setPendingStripeKind(e.target.value as 'all' | 'wallet_refund' | 'subscription_cancel')}
                                            options={[
                                                { value: 'all', label: 'all' },
                                                { value: 'wallet_refund', label: 'wallet_refund' },
                                                { value: 'subscription_cancel', label: 'subscription_cancel' },
                                            ]}
                                        />
                                    </div>

                                    {pendingStripeItems.length === 0 ? (
                                        <EmptyState message="No pending Stripe requests loaded." icon="🧾" />
                                    ) : (
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-gray-50 border-b border-gray-200/70">
                                                <tr className="text-gray-600">
                                                    <th className="px-6 py-4 text-left font-semibold">Kind</th>
                                                    <th className="px-6 py-4 text-left font-semibold">User</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Amount</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Tokens</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Stripe ID</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Open</th>
                                                    <th className="px-6 py-4 text-left font-semibold">External ID</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Created</th>
                                                </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-200/70">
                                                {pendingStripeItems.map((p) => {
                                                    const stripeLink = stripeLinkForPending(p);
                                                    return (
                                                    <tr key={`${p.kind}:${p.external_id}`} className="hover:bg-gray-50/70 transition-colors">
                                                        <td className="px-6 py-4 text-gray-700">{p.kind}</td>
                                                        <td className="px-6 py-4 text-gray-700">{p.user_id || '—'}</td>
                                                        <td className="px-6 py-4 text-gray-700">
                                                            {p.amount_usd != null ? `$${Number(p.amount_usd).toFixed(2)}` : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-700">
                                                            {p.tokens != null ? Number(p.tokens).toLocaleString() : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-700">{stripeLink?.id || '—'}</td>
                                                        <td className="px-6 py-4">
                                                            {stripeLink ? (
                                                                <a
                                                                    href={stripeLink.url}
                                                                    target="_blank"
                                                                    rel="noreferrer"
                                                                    className="text-gray-900 underline"
                                                                >
                                                                    Open
                                                                </a>
                                                            ) : (
                                                                <span className="text-gray-400">—</span>
                                                            )}
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-500">{p.external_id}</td>
                                                        <td className="px-6 py-4 text-gray-600">{formatDateTime(p.created_at)}</td>
                                                    </tr>
                                                );
                                                })}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Pending Economics Events"
                                    subtitle="All pending internal economics events (not just Stripe)."
                                    action={
                                        <Button variant="secondary" onClick={handleLoadPendingEconomics} disabled={loadingPendingEconomics}>
                                            {loadingPendingEconomics ? 'Loading…' : 'Refresh'}
                                        </Button>
                                    }
                                />
                                <CardBody className="space-y-4">
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <Input
                                            label="Kind filter (optional)"
                                            value={pendingEconomicsKind}
                                            onChange={(e) => setPendingEconomicsKind(e.target.value)}
                                            placeholder="subscription_rollover"
                                        />
                                        <Input
                                            label="User ID filter (optional)"
                                            value={pendingEconomicsUserId}
                                            onChange={(e) => setPendingEconomicsUserId(e.target.value)}
                                            placeholder="user123"
                                        />
                                        <div className="flex items-end">
                                            <Button type="button" variant="secondary" disabled={loadingPendingEconomics} onClick={handleLoadPendingEconomics}>
                                                {loadingPendingEconomics ? 'Loading…' : 'Load'}
                                            </Button>
                                        </div>
                                    </div>

                                    {pendingEconomicsItems.length === 0 ? (
                                        <EmptyState message="No pending economics events loaded." icon="🧾" />
                                    ) : (
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-gray-50 border-b border-gray-200/70">
                                                <tr className="text-gray-600">
                                                    <th className="px-6 py-4 text-left font-semibold">Kind</th>
                                                    <th className="px-6 py-4 text-left font-semibold">User</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Amount</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Tokens</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Stripe ID</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Open</th>
                                                    <th className="px-6 py-4 text-left font-semibold">External ID</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Created</th>
                                                </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-200/70">
                                                {pendingEconomicsItems.map((p) => {
                                                    const stripeLink = stripeLinkForPending(p);
                                                    return (
                                                    <tr key={`${p.kind}:${p.external_id}`} className="hover:bg-gray-50/70 transition-colors">
                                                        <td className="px-6 py-4 text-gray-700">{p.kind}</td>
                                                        <td className="px-6 py-4 text-gray-700">{p.user_id || '—'}</td>
                                                        <td className="px-6 py-4 text-gray-700">
                                                            {p.amount_usd != null ? `$${Number(p.amount_usd).toFixed(2)}` : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-700">
                                                            {p.tokens != null ? Number(p.tokens).toLocaleString() : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-700">{stripeLink?.id || '—'}</td>
                                                        <td className="px-6 py-4">
                                                            {stripeLink ? (
                                                                <a
                                                                    href={stripeLink.url}
                                                                    target="_blank"
                                                                    rel="noreferrer"
                                                                    className="text-gray-900 underline"
                                                                >
                                                                    Open
                                                                </a>
                                                            ) : (
                                                                <span className="text-gray-400">—</span>
                                                            )}
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-500">{p.external_id}</td>
                                                        <td className="px-6 py-4 text-gray-600">{formatDateTime(p.created_at)}</td>
                                                    </tr>
                                                );
                                                })}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Sweep Unused Subscription Balances"
                                    subtitle="Moves unused subscription balance to project budget for due subscriptions."
                                />
                                <CardBody className="space-y-4">
                                    <form onSubmit={handleSweepSubscriptionRollovers} className="space-y-4">
                                        <div className="flex gap-3">
                                            <Input
                                                label="User ID (optional)"
                                                value={subSweepUserId}
                                                onChange={(e) => setSubSweepUserId(e.target.value)}
                                                placeholder="user123"
                                                className="flex-1"
                                            />
                                            <Button type="submit" variant="secondary" disabled={loadingAction}>
                                                {loadingAction ? 'Sweeping…' : 'Sweep Now'}
                                            </Button>
                                        </div>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Subscription Period History"
                                    subtitle="Closed periods and ledger entries for a user's subscription."
                                />
                                <CardBody className="space-y-4">
                                    <form onSubmit={handleLoadSubscriptionPeriods} className="space-y-4">
                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <Input
                                                label="User ID *"
                                                value={subHistoryUserId}
                                                onChange={(e) => setSubHistoryUserId(e.target.value)}
                                                placeholder="user123"
                                                required
                                            />
                                            <Select
                                                label="Period status"
                                                value={subHistoryStatus}
                                                onChange={(e) => setSubHistoryStatus(e.target.value as 'closed' | 'open' | 'all')}
                                                options={[
                                                    { value: 'closed', label: 'closed' },
                                                    { value: 'open', label: 'open' },
                                                    { value: 'all', label: 'all' },
                                                ]}
                                            />
                                            <div className="flex items-end">
                                                <Button type="submit" variant="secondary" disabled={loadingHistory}>
                                                    {loadingHistory ? 'Loading…' : 'Load Periods'}
                                                </Button>
                                            </div>
                                        </div>
                                    </form>

                                    {subPeriods.length === 0 ? (
                                        <EmptyState message="No subscription periods loaded." icon="📚" />
                                    ) : (
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-gray-50 border-b border-gray-200/70">
                                                <tr className="text-gray-600">
                                                    <th className="px-6 py-4 text-left font-semibold">Period</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Status</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Topup</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Spent</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Rolled</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Balance</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Closed</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Actions</th>
                                                </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-200/70">
                                                {subPeriods.map((p) => (
                                                    <tr
                                                        key={p.period_key}
                                                        className={p.period_key === subSelectedPeriodKey ? 'bg-gray-50/80' : 'hover:bg-gray-50/70 transition-colors'}
                                                    >
                                                        <td className="px-6 py-4 text-gray-700">
                                                            <div className="font-medium text-gray-900">{formatDateTime(p.period_start)} → {formatDateTime(p.period_end)}</div>
                                                            <div className="text-xs text-gray-500">{p.period_key}</div>
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-700">{p.status}</td>
                                                        <td className="px-6 py-4 text-gray-700">${Number(p.topup_usd || 0).toFixed(2)}</td>
                                                        <td className="px-6 py-4 text-gray-700">${Number(p.spent_usd || 0).toFixed(2)}</td>
                                                        <td className="px-6 py-4 text-gray-700">${Number(p.rolled_over_usd || 0).toFixed(2)}</td>
                                                        <td className="px-6 py-4 text-gray-700">${Number(p.balance_usd || 0).toFixed(2)}</td>
                                                        <td className="px-6 py-4 text-gray-600">{formatDateTime(p.closed_at)}</td>
                                                        <td className="px-6 py-4">
                                                            <Button
                                                                type="button"
                                                                variant="secondary"
                                                                disabled={loadingHistory}
                                                                onClick={() => handleLoadSubscriptionLedger(p.period_key)}
                                                            >
                                                                {loadingHistory && p.period_key === subSelectedPeriodKey ? 'Loading…' : 'View Ledger'}
                                                            </Button>
                                                        </td>
                                                    </tr>
                                                ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}

                                    {subSelectedPeriodKey && (
                                        <div className="pt-4 border-t border-gray-200/70 space-y-3">
                                            <div className="flex items-center justify-between">
                                                <div className="text-sm text-gray-600">
                                                    Ledger for period: <span className="font-medium text-gray-900">{subSelectedPeriodKey}</span>
                                                </div>
                                                <Button
                                                    type="button"
                                                    variant="secondary"
                                                    disabled={loadingHistory}
                                                    onClick={() => handleLoadSubscriptionLedger(subSelectedPeriodKey)}
                                                >
                                                    {loadingHistory ? 'Refreshing…' : 'Refresh Ledger'}
                                                </Button>
                                            </div>

                                            {subLedger.length === 0 ? (
                                                <EmptyState message="No ledger entries for this period." icon="🧾" />
                                            ) : (
                                                <div className="overflow-x-auto">
                                                    <table className="w-full text-sm">
                                                        <thead className="bg-gray-50 border-b border-gray-200/70">
                                                        <tr className="text-gray-600">
                                                            <th className="px-6 py-4 text-left font-semibold">Time</th>
                                                            <th className="px-6 py-4 text-left font-semibold">Kind</th>
                                                            <th className="px-6 py-4 text-left font-semibold">Amount</th>
                                                            <th className="px-6 py-4 text-left font-semibold">Provider</th>
                                                            <th className="px-6 py-4 text-left font-semibold">Note</th>
                                                            <th className="px-6 py-4 text-left font-semibold">Request</th>
                                                        </tr>
                                                        </thead>
                                                        <tbody className="divide-y divide-gray-200/70">
                                                        {subLedger.map((l) => {
                                                            const amt = Number(l.amount_usd || 0);
                                                            const sign = amt >= 0 ? '+' : '-';
                                                            return (
                                                                <tr key={l.id} className="hover:bg-gray-50/70 transition-colors">
                                                                    <td className="px-6 py-4 text-gray-600">{formatDateTime(l.created_at)}</td>
                                                                    <td className="px-6 py-4 text-gray-700">{l.kind}</td>
                                                                    <td className="px-6 py-4 font-semibold text-gray-900">
                                                                        {sign}${Math.abs(amt).toFixed(2)}
                                                                    </td>
                                                                    <td className="px-6 py-4 text-gray-700">{l.provider || '—'}</td>
                                                                    <td className="px-6 py-4 text-gray-700">{l.note || '—'}</td>
                                                                    <td className="px-6 py-4 text-gray-500">{l.request_id || '—'}</td>
                                                                </tr>
                                                            );
                                                        })}
                                                        </tbody>
                                                    </table>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader
                                    title="Recent Subscriptions"
                                    subtitle="Lists last updated subscriptions for this tenant/project."
                                    action={
                                        <Button
                                            variant="secondary"
                                            onClick={handleLoadSubscriptionsList}
                                            disabled={loadingData}
                                        >
                                            {loadingData ? 'Loading…' : 'Refresh'}
                                        </Button>
                                    }
                                />
                                <CardBody className="space-y-4">
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <Select
                                            label="Provider filter"
                                            value={subsProviderFilter}
                                            onChange={(e) => setSubsProviderFilter(e.target.value)}
                                            options={[
                                                { value: '', label: 'all' },
                                                { value: 'internal', label: 'internal' },
                                                { value: 'stripe', label: 'stripe' },
                                            ]}
                                        />
                                    </div>

                                    {subsList.length === 0 ? (
                                        <EmptyState message="No subscriptions loaded (click Refresh)." icon="🧾" />
                                    ) : (
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-gray-50 border-b border-gray-200/70">
                                                <tr className="text-gray-600">
                                                    <th className="px-6 py-4 text-left font-semibold">User</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Billing</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Tier</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Due</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Next</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Last</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Updated</th>
                                                </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-200/70">
                                                {subsList.map((s) => (
                                                    <tr key={`${s.tenant}:${s.project}:${s.user_id}`} className="hover:bg-gray-50/70 transition-colors">
                                                        <td className="px-6 py-4 font-semibold text-gray-900">{s.user_id}</td>
                                                        <td className="px-6 py-4 text-gray-700">{providerLabel(s.provider)}</td>
                                                        <td className="px-6 py-4 text-gray-700">{s.tier}</td>
                                                        <td className="px-6 py-4"><DuePill sub={s} /></td>
                                                        <td className="px-6 py-4 text-gray-700">{formatDateTime(s.next_charge_at)}</td>
                                                        <td className="px-6 py-4 text-gray-700">{formatDateTime(s.last_charged_at)}</td>
                                                        <td className="px-6 py-4 text-gray-600">{formatDateTime(s.updated_at)}</td>
                                                    </tr>
                                                ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}
                                </CardBody>
                            </Card>
                        </div>
                    )}
                    {/* Data lists loading indicator (global hint) */}
                    {(viewMode === 'quotaPolicies' || viewMode === 'budgetPolicies' || viewMode === 'appBudget') && loadingData && (
                        <div className="text-center text-sm text-gray-500">Loading…</div>
                    )}


                </div>
            </div>
        </div>
    );
};

// Render
const rootElement = document.getElementById('root');
if (rootElement) {
    const root = ReactDOM.createRoot(rootElement);
    root.render(<ControlPlaneAdmin />);
}
