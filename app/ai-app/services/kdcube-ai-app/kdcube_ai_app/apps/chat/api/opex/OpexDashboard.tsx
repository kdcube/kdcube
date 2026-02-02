const { useState, useEffect, useRef, useMemo } = React;

// =============================================================================
// Settings & Configuration
// =============================================================================

interface AppSettings {
    baseUrl: string;
    accessToken: string | null;
    idToken: string | null;
    idTokenHeader: string;
    defaultTenant: string;
    defaultProject: string;
    defaultAppBundleId: string;
}

class SettingsManager {
    // Placeholder constants (won't be replaced by Python script)
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
        defaultAppBundleId: '{{DEFAULT_APP_BUNDLE_ID}}'
    };

    private configReceivedCallback: (() => void) | null = null;

    getBaseUrl(): string {
        if (this.settings.baseUrl === this.PLACEHOLDER_BASE_URL) {
            return 'http://localhost:8010';
        }

        // Validate URL
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
        if (this.settings.accessToken === this.PLACEHOLDER_ACCESS_TOKEN ||
            !this.settings.accessToken) {
            return null;
        }
        return this.settings.accessToken;
    }

    getIdToken(): string | null {
        if (this.settings.idToken === this.PLACEHOLDER_ID_TOKEN ||
            !this.settings.idToken) {
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

    // Check if settings are still placeholders (not configured)
    hasPlaceholderSettings(): boolean {
        return this.settings.baseUrl === this.PLACEHOLDER_BASE_URL;
    }

    updateSettings(partial: Partial<AppSettings>) {
        this.settings = { ...this.settings, ...partial };
    }

    // Register callback to be called when config is received
    onConfigReceived(callback: () => void) {
        this.configReceivedCallback = callback;
    }

    setupParentListener() {
        console.log('[SettingsManager] Setting up parent listener');
        const identity = "OPEX_DASHBOARD";
        const isInIframe = window.parent !== window;

        let configReceived = false;

        window.addEventListener('message', (event) => {
            if (event.data.type === 'CONN_RESPONSE' || event.data.type === 'CONFIG_RESPONSE') {
                const requestedIdentity = event.data.identity;
                if (requestedIdentity !== identity) {
                    console.warn(`[SettingsManager] Ignoring response for identity ${requestedIdentity}`);
                    return;
                }

                configReceived = true;
                console.log('[SettingsManager] Received config from parent', event.data.config);

                // Validate and update config
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

                    if (Object.keys(updates).length > 0) {
                        this.updateSettings(updates);
                        console.log('[SettingsManager] Settings updated from parent');

                        // Trigger callback
                        if (this.configReceivedCallback) {
                            this.configReceivedCallback();
                        }
                    }
                }
            }
        });

        // Only request config if in iframe AND settings are still placeholders
        // if (isInIframe && this.hasPlaceholderSettings()) {
        if (this.hasPlaceholderSettings()) {
            console.log('[SettingsManager] In iframe with placeholder settings, requesting config from parent');

            window.parent.postMessage({
                type: 'CONFIG_REQUEST',
                data: {
                    requestedFields: [
                        'baseUrl',
                        'accessToken',
                        'idToken',
                        'idTokenHeader',
                        'defaultTenant',
                        'defaultProject',
                        'defaultAppBundleId'
                    ],
                    identity: identity
                }
            }, '*');

            // Return a promise that resolves when config is received or timeout occurs
            return new Promise<boolean>((resolve) => {
                const timeout = setTimeout(() => {
                    if (!configReceived) {
                        console.log('[SettingsManager] Config request timeout - using local settings');
                        resolve(false);
                    }
                }, 3000); // 3 second timeout

                // Override callback to also resolve promise
                const originalCallback = this.configReceivedCallback;
                this.onConfigReceived(() => {
                    clearTimeout(timeout);
                    if (originalCallback) originalCallback();
                    resolve(true);
                });
            });
        } else {
            console.log('[SettingsManager] Not in iframe or settings already configured, using existing settings');
            return Promise.resolve(!this.hasPlaceholderSettings());
        }
    }

    debugSettings() {
        console.log('[SettingsManager] Current settings:', {
            baseUrl: this.settings.baseUrl,
            hasAccessToken: !!this.settings.accessToken,
            hasIdToken: !!this.settings.idToken,
            tenant: this.settings.defaultTenant,
            project: this.settings.defaultProject,
            bundleId: this.settings.defaultAppBundleId,
            hasPlaceholders: this.hasPlaceholderSettings()
        });
        console.log('[SettingsManager] Effective values:', {
            baseUrl: this.getBaseUrl(),
            accessToken: this.getAccessToken() ? '<set>' : '<not set>',
            tenant: this.getDefaultTenant(),
            project: this.getDefaultProject(),
            bundleId: this.getDefaultAppBundleId()
        });
    }
}

// Global settings instance
const settings = new SettingsManager();

// =============================================================================
// Auth Header Helper
// =============================================================================

function appendAuthHeaders(headers: Headers): Headers {
    const accessToken = settings.getAccessToken();
    const idToken = settings.getIdToken();
    const idTokenHeader = settings.getIdTokenHeader();

    // Always: Bearer = access_token
    if (accessToken) {
        headers.set('Authorization', `Bearer ${accessToken}`);
    }

    // Always send ID token in separate header when available
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
// REST API Client (with Auth)
// =============================================================================

class AccountingAPI {
    constructor(private basePath: string = '/api/opex') {
        // basePath is relative, will be combined with settings.getBaseUrl()
    }

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

    async fetchUsageByUser(params) {
        const { tenant, project, dateFrom, dateTo, appBundleId, serviceTypes } = params;
        const queryParams = new URLSearchParams({
            tenant,
            project,
            date_from: dateFrom,
            date_to: dateTo,
            ...(appBundleId && { app_bundle_id: appBundleId }),
            ...(serviceTypes && { service_types: serviceTypes.join(',') })
        });

        const response = await this.fetchWithAuth(`${this.getFullUrl('/users')}?${queryParams}`);
        return response.json();
    }

    async fetchTotalUsage(params) {
        const { tenant, project, dateFrom, dateTo, appBundleId, serviceTypes } = params;
        const queryParams = new URLSearchParams({
            tenant,
            project,
            date_from: dateFrom,
            date_to: dateTo,
            ...(appBundleId && { app_bundle_id: appBundleId }),
            ...(serviceTypes && { service_types: serviceTypes.join(',') })
        });

        const response = await this.fetchWithAuth(`${this.getFullUrl('/total')}?${queryParams}`);
        return response.json();
    }

    async fetchConversationUsage(params) {
        const { tenant, project, userId, conversationId, dateFrom, dateTo, appBundleId } = params;
        const queryParams = new URLSearchParams({
            tenant,
            project,
            user_id: userId,
            conversation_id: conversationId,
            ...(dateFrom && { date_from: dateFrom }),
            ...(dateTo && { date_to: dateTo }),
            ...(appBundleId && { app_bundle_id: appBundleId })
        });

        const response = await this.fetchWithAuth(`${this.getFullUrl('/conversation')}?${queryParams}`);
        return response.json();
    }

    async fetchTurnUsage(params) {
        const { tenant, project, userId, conversationId, turnId, dateFrom, dateTo, appBundleId } = params;
        const queryParams = new URLSearchParams({
            tenant,
            project,
            user_id: userId,
            conversation_id: conversationId,
            turn_id: turnId,
            ...(dateFrom && { date_from: dateFrom }),
            ...(dateTo && { date_to: dateTo }),
            ...(appBundleId && { app_bundle_id: appBundleId })
        });

        const response = await this.fetchWithAuth(`${this.getFullUrl('/turn')}?${queryParams}`);
        return response.json();
    }

    async fetchAgentUsage(params) {
        const { tenant, project, dateFrom, dateTo, userId, conversationId, turnId, appBundleId } = params;
        const queryParams = new URLSearchParams({
            tenant,
            project,
            date_from: dateFrom,
            date_to: dateTo,
            ...(userId && { user_id: userId }),
            ...(conversationId && { conversation_id: conversationId }),
            ...(turnId && { turn_id: turnId }),
            ...(appBundleId && { app_bundle_id: appBundleId })
        });

        const response = await this.fetchWithAuth(`${this.getFullUrl('/agents')}?${queryParams}`);
        return response.json();
    }

    async healthCheck() {
        const response = await this.fetchWithAuth(this.getFullUrl('/health'));
        return response.json();
    }
}

interface WebSearchPriceInfo {
    provider: string;
    tier: string;
    cost_per_1k_requests: number;
    limits: {
        requests_per_second: number | null;
        requests_per_month: number | null;
    };
}

interface PriceTable {
    llm: Array<{
        model: string;
        provider: string;
        input_tokens_1M: number;
        output_tokens_1M: number;
        cache_pricing?: {
            '5m': { write_tokens_1M: number; read_tokens_1M: number };
            '1h': { write_tokens_1M: number; read_tokens_1M: number };
        };
        cache_write_tokens_1M?: number;
        cache_read_tokens_1M?: number;
    }>;
    embedding: Array<{
        model: string;
        provider: string;
        tokens_1M: number;
    }>;
    web_search: WebSearchPriceInfo[];
}

interface RollupItem {
    service: string;
    provider: string;
    model: string;
    tier?: string;  // NEW: for web_search
    spent: {
        input?: number;
        output?: number;
        cache_creation?: number;
        cache_5m_write?: number;
        cache_1h_write?: number;
        cache_read?: number;
        tokens?: number;
        search_queries?: number;  // NEW
        search_results?: number;  // NEW
    };
}

interface BreakdownItem {
    service: string;
    provider: string;
    model: string;
    tier?: string | null;  // NEW
    spent: RollupItem['spent'];
    cost: number;
}

interface Metrics {
    totalCost: number;
    eventCount?: number;
    userCount?: number;
    agentCount?: number;
    totalTokens?: number;
    inputTokens?: number;
    outputTokens?: number;
    totalSearchQueries?: number;  // NEW
    breakdown: BreakdownItem[];
    userBreakdown?: Array<{
        userId: string;
        cost: number;
        rollup: RollupItem[];
    }>;
    agentBreakdown?: Array<{
        agentName: string;
        cost: number;
        breakdown: BreakdownItem[];
    }>;
}

// =============================================================================
// Price Table Configuration
// =============================================================================

const PRICE_TABLE: PriceTable = {
    llm: [
        {
            model: 'claude-sonnet-4-5-20250929',
            provider: 'anthropic',
            input_tokens_1M: 3.00,
            output_tokens_1M: 15.00,
            cache_pricing: {
                '5m': { write_tokens_1M: 3.00, read_tokens_1M: 0.30 },
                '1h': { write_tokens_1M: 3.75, read_tokens_1M: 0.30 }
            }
        },
        {
            model: 'claude-haiku-4-5-20251001',
            provider: 'anthropic',
            input_tokens_1M: 1.00,
            output_tokens_1M: 5.00,
            cache_pricing: {
                '5m': { write_tokens_1M: 1.00, read_tokens_1M: 0.10 },
                '1h': { write_tokens_1M: 2.00, read_tokens_1M: 0.10 }
            }
        },
        {
            model: 'gpt-4o-mini',
            provider: 'openai',
            input_tokens_1M: 0.15,
            output_tokens_1M: 0.60,
            cache_write_tokens_1M: 0.00,
            cache_read_tokens_1M: 0.075
        }
    ],
    embedding: [
        {
            model: 'text-embedding-3-small',
            provider: 'openai',
            tokens_1M: 0.02
        }
    ],
    web_search: [
        {
            provider: 'brave',
            tier: 'free',
            cost_per_1k_requests: 0.00,
            limits: { requests_per_second: 1, requests_per_month: 2000 }
        },
        {
            provider: 'brave',
            tier: 'base',
            cost_per_1k_requests: 3.00,
            limits: { requests_per_second: 20, requests_per_month: 20000000 }
        },
        {
            provider: 'brave',
            tier: 'pro',
            cost_per_1k_requests: 5.00,
            limits: { requests_per_second: 50, requests_per_month: null }
        },
        {
            provider: 'duckduckgo',
            tier: 'free',
            cost_per_1k_requests: 0.00,
            limits: { requests_per_second: null, requests_per_month: null }
        }
    ]
};

// =============================================================================
// DATE HELPERS
// =============================================================================

/**
 * Get yesterday's date in YYYY-MM-DD format
 */
function getYesterday(): string {
    const date = new Date();
    date.setDate(date.getDate() - 1);
    return date.toISOString().split('T')[0];
}

/**
 * Get date N days ago from yesterday in YYYY-MM-DD format
 */
function getDaysAgoFromYesterday(daysAgo: number): string {
    const date = new Date();
    date.setDate(date.getDate() - 1 - daysAgo);
    return date.toISOString().split('T')[0];
}

/**
 * Get default date range: [one week ago, yesterday]
 */
function getDefaultDateRange(): { dateFrom: string; dateTo: string } {
    return {
        dateFrom: getDaysAgoFromYesterday(7),  // 7 days before yesterday
        dateTo: getYesterday()                  // Yesterday
    };
}

/**
 * Check if date is today
 */
function isToday(dateStr: string): boolean {
    return dateStr === new Date().toISOString().split('T')[0];
}

// =============================================================================
// Cost Calculator Helper
// =============================================================================

function calculateCosts(rollup) {
    // Guard against null/undefined rollup
    if (!rollup || !Array.isArray(rollup)) {
        return { totalCost: 0, breakdown: [] };
    }

    let totalCost = 0;
    const breakdown = [];

    rollup.forEach(item => {
        // Guard against malformed items
        if (!item) return;

        const { service, provider, model, spent } = item;

        // Guard against missing spent data
        if (!spent) {
            breakdown.push({
                service,
                provider,
                model,
                tier: null,
                spent: {},
                cost: 0
            });
            return;
        }

        let cost = 0;

        if (service === 'llm') {
            const priceInfo = PRICE_TABLE.llm.find(
                p => p.provider === provider && p.model === model
            );

            if (priceInfo) {
                const inputCost = ((spent.input || 0) / 1_000_000) * priceInfo.input_tokens_1M;
                const outputCost = ((spent.output || 0) / 1_000_000) * priceInfo.output_tokens_1M;

                let cacheWriteCost = 0;
                if (priceInfo.cache_pricing) {
                    const cache5m = ((spent.cache_5m_write || 0) / 1_000_000);
                    const cache1h = ((spent.cache_1h_write || 0) / 1_000_000);
                    cacheWriteCost =
                        cache5m * priceInfo.cache_pricing['5m'].write_tokens_1M +
                        cache1h * priceInfo.cache_pricing['1h'].write_tokens_1M;
                } else {
                    cacheWriteCost = ((spent.cache_creation || 0) / 1_000_000) *
                        (priceInfo.cache_write_tokens_1M || 0);
                }

                const cacheReadCost = ((spent.cache_read || 0) / 1_000_000) *
                    (priceInfo.cache_read_tokens_1M || 0);

                cost = inputCost + outputCost + cacheWriteCost + cacheReadCost;
            }
        } else if (service === 'embedding') {
            const priceInfo = PRICE_TABLE.embedding.find(
                p => p.provider === provider && p.model === model
            );

            if (priceInfo) {
                cost = ((spent.tokens || 0) / 1_000_000) * priceInfo.tokens_1M;
            }
        } else if (service === 'web_search') {
            // NEW: web_search cost calculation
            // Get tier from item or default to 'free'
            const tier = item.tier || 'free';

            const priceInfo = PRICE_TABLE.web_search.find(
                p => p.provider === provider && p.tier === tier
            );

            if (priceInfo) {
                const searchQueries = spent.search_queries || 0;
                cost = (searchQueries / 1000.0) * priceInfo.cost_per_1k_requests;
            }
        }

        totalCost += cost;
        breakdown.push({
            service,
            provider,
            model,
            tier: item.tier || null,  // Include tier for web_search
            spent,
            cost
        });
    });

    return { totalCost, breakdown };
}

// =============================================================================
// OPEX Dashboard Component
// =============================================================================

const OPEXDashboard = () => {
    const api = useMemo(() => new AccountingAPI(), []);

    // Configuration status: 'initializing' | 'ready' | 'error'
    const [configStatus, setConfigStatus] = useState('initializing');

    // State - use settings for defaults
    const [viewMode, setViewMode] = useState('overview');
    const [tenant, setTenant] = useState(settings.getDefaultTenant());
    const [project, setProject] = useState(settings.getDefaultProject());

    // Calculate and set default date range
    const defaultDates = useMemo(() => getDefaultDateRange(), []);
    const [dateFrom, setDateFrom] = useState(defaultDates.dateFrom);
    const [dateTo, setDateTo] = useState(defaultDates.dateTo);

    const [appBundleId, setAppBundleId] = useState(settings.getDefaultAppBundleId());

    // Specific filters
    const [selectedUserId, setSelectedUserId] = useState('');
    const [conversationId, setConversationId] = useState('');
    const [turnId, setTurnId] = useState('');

    // Data state
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [authError, setAuthError] = useState(null);
    const [data, setData] = useState(null);

    // Chart refs
    const pieChartRef = useRef(null);
    const barChartRef = useRef(null);
    const timeChartRef = useRef(null);
    const pieChartInstance = useRef(null);
    const barChartInstance = useRef(null);
    const timeChartInstance = useRef(null);

    // Initialize settings and wait for config
    useEffect(() => {
        const initializeSettings = async () => {
            console.log('[Dashboard] ===== INITIALIZATION START =====');
            const isInIframe = window.parent !== window;
            console.log('[Dashboard] Running in iframe:', isInIframe);
            console.log('[Dashboard] Has placeholder settings:', settings.hasPlaceholderSettings());

            try {
                // Setup listener and wait for config (or timeout)
                const configReceived = await settings.setupParentListener();
                console.log('[Dashboard] Config received?', configReceived);

                if (configReceived) {
                    console.log('[Dashboard] ✓✓✓ Config received from parent!');
                    setTenant(settings.getDefaultTenant());
                    setProject(settings.getDefaultProject());
                    setAppBundleId(settings.getDefaultAppBundleId());
                    settings.debugSettings();
                    setConfigStatus('ready');  // ← Only set ready if config received
                } else if (isInIframe) {
                    console.error('[Dashboard] ✗✗✗ In iframe but NO config received!');
                    console.error('[Dashboard] Will NOT set ready - staying in initializing');
                    // DO NOT call setConfigStatus('ready') here!
                    // Dashboard will stay in "Waiting for Configuration" screen
                } else {
                    console.log('[Dashboard] Not in iframe, using local settings');
                    settings.debugSettings();
                    setConfigStatus('ready');  // ← Only set ready if NOT in iframe
                }

            } catch (err) {
                console.error('[Dashboard] Error initializing settings:', err);
                setConfigStatus('error');
            }

            console.log('[Dashboard] ===== INITIALIZATION END =====');
        };

        initializeSettings();
    }, []);

    // Fetch data based on view mode
    const fetchData = async () => {
        if (configStatus !== 'ready') {
            console.log('[Dashboard] Skipping fetch - config not ready');
            return;
        }

        setLoading(true);
        setError(null);
        setAuthError(null);

        try {
            let result;
            const baseParams = { tenant, project, dateFrom, dateTo, appBundleId };

            switch (viewMode) {
                case 'overview':
                    result = await api.fetchTotalUsage(baseParams);
                    break;
                case 'users':
                    result = await api.fetchUsageByUser(baseParams);
                    break;
                case 'conversation':
                    if (!selectedUserId || !conversationId) {
                        throw new Error('User ID and Conversation ID required');
                    }
                    result = await api.fetchConversationUsage({
                        ...baseParams,
                        userId: selectedUserId,
                        conversationId
                    });
                    break;
                case 'turn':
                    if (!selectedUserId || !conversationId || !turnId) {
                        throw new Error('User ID, Conversation ID, and Turn ID required');
                    }
                    result = await api.fetchTurnUsage({
                        ...baseParams,
                        userId: selectedUserId,
                        conversationId,
                        turnId
                    });
                    break;
                case 'agents':
                    result = await api.fetchAgentUsage({
                        ...baseParams,
                        userId: selectedUserId || undefined,
                        conversationId: conversationId || undefined,
                        turnId: turnId || undefined
                    });
                    break;
                default:
                    throw new Error('Invalid view mode');
            }

            // Debug: log the response structure
            console.log(`[${viewMode}] API Response:`, result);

            setData(result);
        } catch (err) {
            const errorMsg = err.message || 'Unknown error';

            // Check for auth errors
            if (errorMsg.includes('401') || errorMsg.includes('403')) {
                setAuthError('Authentication failed. Please check your credentials.');
            } else {
                setError(errorMsg);
            }

            console.error('Failed to fetch data:', err);
        } finally {
            setLoading(false);
        }
    };

    // Auto-fetch when config is ready and params are set
    useEffect(() => {
        if (configStatus === 'ready' && tenant && project && dateFrom && dateTo) {
            fetchData();
        }
    }, [configStatus, viewMode, tenant, project, dateFrom, dateTo, appBundleId, selectedUserId, conversationId, turnId]);

    // Compute metrics
    const metrics = useMemo<Metrics | null>(() => {
        if (!data) return null;

        if (viewMode === 'overview') {
            const { rollup, total, user_count, event_count } = data;
            const { totalCost, breakdown } = calculateCosts(rollup || []);

            // Calculate total search queries
            const totalSearchQueries = (rollup || [])
                .filter((item: RollupItem) => item.service === 'web_search')
                .reduce((sum: number, item: RollupItem) => sum + (item.spent?.search_queries || 0), 0);

            return {
                totalCost,
                eventCount: event_count || 0,
                userCount: user_count || 0,
                totalTokens: total?.total_tokens || 0,
                inputTokens: total?.input_tokens || 0,
                outputTokens: total?.output_tokens || 0,
                totalSearchQueries,  // NEW
                breakdown
            };
        } else if (viewMode === 'users') {
            // FIX: API returns { users: {...}, total_users: N, cost_estimate: {...} }
            const usersData = data.users || {};
            let totalCost = 0;
            const userBreakdown: Metrics['userBreakdown'] = [];

            Object.entries(usersData).forEach(([userId, userData]: [string, any]) => {
                // Guard against null/undefined userData
                if (!userData || !userData.rollup) {
                    console.warn(`Missing rollup data for user: ${userId}`);
                    return;
                }

                const { rollup } = userData;
                const { totalCost: userCost } = calculateCosts(rollup || []);
                totalCost += userCost;
                userBreakdown.push({ userId, cost: userCost, rollup });
            });

            return {
                totalCost,
                userCount: data.total_users || Object.keys(usersData).length,
                breakdown: [],
                userBreakdown
            };
        } else if (viewMode === 'conversation' || viewMode === 'turn') {
            const { rollup, total, event_count } = data;
            const { totalCost, breakdown } = calculateCosts(rollup || []);

            return {
                totalCost,
                eventCount: event_count || 0,
                totalTokens: total?.total_tokens || 0,
                inputTokens: total?.input_tokens || 0,
                outputTokens: total?.output_tokens || 0,
                breakdown
            };
        } else if (viewMode === 'agents') {
            // FIX: API returns { agents: {...}, total_agents: N, cost_estimate: {...} }
            const agentsData = data.agents || {};
            let totalCost = 0;
            const agentBreakdown: Metrics['agentBreakdown'] = [];

            Object.entries(agentsData).forEach(([agentName, agentData]: [string, any]) => {
                // Guard against null/undefined agentData
                if (!agentData || !agentData.rollup) {
                    console.warn(`Missing rollup data for agent: ${agentName}`);
                    return;
                }

                const { rollup } = agentData;
                const { totalCost: agentCost, breakdown } = calculateCosts(rollup || []);
                totalCost += agentCost;
                agentBreakdown.push({
                    agentName,
                    cost: agentCost,
                    breakdown
                });
            });

            return {
                totalCost,
                agentCount: data.total_agents || Object.keys(agentsData).length,
                breakdown: [],
                agentBreakdown
            };
        }

        return null;
    }, [data, viewMode]);

    // Pie Chart: Cost Distribution by Service/Model
    useEffect(() => {
        const ctx = pieChartRef.current;
        if (!ctx || !metrics || !metrics.breakdown) return;

        if (pieChartInstance.current) {
            pieChartInstance.current.destroy();
        }

        const labels = metrics.breakdown.map(b => `${b.service}/${b.model}`);
        const costs = metrics.breakdown.map(b => b.cost);

        pieChartInstance.current = new window.Chart(ctx, {
            type: 'pie',
            data: {
                labels,
                datasets: [{
                    data: costs,
                    backgroundColor: [
                        '#3b82f6', '#10b981', '#f59e0b', '#ef4444',
                        '#8b5cf6', '#ec4899', '#14b8a6', '#f97316'
                    ]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom' },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const label = context.label || '';
                                const value = context.parsed;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((value / total) * 100).toFixed(1);
                                return `${label}: $${value.toFixed(3)} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });

        return () => {
            if (pieChartInstance.current) {
                pieChartInstance.current.destroy();
            }
        };
    }, [metrics]);

    // Bar Chart: Token Usage Breakdown
    useEffect(() => {
        const ctx = barChartRef.current;
        if (!ctx || !metrics || !metrics.breakdown) return;

        if (barChartInstance.current) {
            barChartInstance.current.destroy();
        }

        const labels = metrics.breakdown.map(b => `${b.service}/${b.model}`);
        const inputTokens = metrics.breakdown.map(b => (b.spent.input || 0) / 1000);
        const outputTokens = metrics.breakdown.map(b => (b.spent.output || 0) / 1000);
        const cacheTokens = metrics.breakdown.map(b => (b.spent.cache_creation || 0) / 1000);

        barChartInstance.current = new window.Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Input (K tokens)',
                        data: inputTokens,
                        backgroundColor: '#3b82f6'
                    },
                    {
                        label: 'Output (K tokens)',
                        data: outputTokens,
                        backgroundColor: '#10b981'
                    },
                    {
                        label: 'Cache Write (K tokens)',
                        data: cacheTokens,
                        backgroundColor: '#f59e0b'
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { stacked: false },
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Tokens (thousands)' }
                    }
                },
                plugins: {
                    legend: { position: 'bottom' }
                }
            }
        });

        return () => {
            if (barChartInstance.current) {
                barChartInstance.current.destroy();
            }
        };
    }, [metrics]);

    // Show initialization loading state
    if (configStatus === 'initializing') {
        return (
            <div className="max-w-7xl mx-auto p-6 space-y-6 bg-gray-50 min-h-screen">
                <div className="bg-white rounded-lg shadow-sm border p-12 text-center">
                    <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
                    <p className="mt-4 text-gray-600">Initializing dashboard...</p>
                    <p className="mt-2 text-sm text-gray-500">Waiting for configuration</p>
                </div>
            </div>
        );
    }
    return (
        <div className="max-w-7xl mx-auto p-6 space-y-6 bg-gray-50 min-h-screen">
            {/* Header */}
            <div className="bg-white rounded-lg shadow-sm border p-6">
                <h1 className="text-3xl font-bold mb-2">OPEX Accounting Dashboard</h1>
                <p className="text-gray-600">Real-time operational cost tracking and analysis</p>
            </div>

            {/* Controls */}
            <div className="bg-white rounded-lg shadow-sm border p-6">
                <h2 className="text-lg font-semibold mb-4">Query Parameters</h2>

                {/* View Mode Selector */}
                <div className="mb-4">
                    <label className="block text-sm font-medium mb-2">View Mode</label>
                    <div className="flex flex-wrap gap-2">
                        {['overview', 'users', 'conversation', 'turn', 'agents'].map(mode => (
                            <button
                                key={mode}
                                onClick={() => setViewMode(mode)}
                                className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                                    viewMode === mode
                                        ? 'bg-blue-600 text-white'
                                        : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                }`}
                            >
                                {mode.charAt(0).toUpperCase() + mode.slice(1)}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Base Parameters */}
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
                    <div>
                        <label className="block text-sm font-medium mb-2">Tenant</label>
                        <input
                            type="text"
                            value={tenant}
                            onChange={(e) => setTenant(e.target.value)}
                            className="w-full px-3 py-2 border rounded-md text-sm"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium mb-2">Project</label>
                        <input
                            type="text"
                            value={project}
                            onChange={(e) => setProject(e.target.value)}
                            className="w-full px-3 py-2 border rounded-md text-sm"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium mb-2">Date From</label>
                        <input
                            type="date"
                            value={dateFrom}
                            onChange={(e) => setDateFrom(e.target.value)}
                            className="w-full px-3 py-2 border rounded-md text-sm"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium mb-2">Date To</label>
                        <input
                            type="date"
                            value={dateTo}
                            onChange={(e) => setDateTo(e.target.value)}
                            className="w-full px-3 py-2 border rounded-md text-sm"
                        />
                    </div>
                </div>

                {/* Conditional Filters */}
                {(viewMode === 'conversation' || viewMode === 'turn' || viewMode === 'agents') && (
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                        <div>
                            <label className="block text-sm font-medium mb-2">
                                User ID {(viewMode === 'conversation' || viewMode === 'turn') && <span className="text-red-500">*</span>}
                            </label>
                            <input
                                type="text"
                                value={selectedUserId}
                                onChange={(e) => setSelectedUserId(e.target.value)}
                                className="w-full px-3 py-2 border rounded-md text-sm"
                                placeholder="user-123"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-2">
                                Conversation ID {(viewMode === 'conversation' || viewMode === 'turn') && <span className="text-red-500">*</span>}
                            </label>
                            <input
                                type="text"
                                value={conversationId}
                                onChange={(e) => setConversationId(e.target.value)}
                                className="w-full px-3 py-2 border rounded-md text-sm"
                                placeholder="conv-abc-123"
                            />
                        </div>
                        {viewMode === 'turn' && (
                            <div>
                                <label className="block text-sm font-medium mb-2">
                                    Turn ID <span className="text-red-500">*</span>
                                </label>
                                <input
                                    type="text"
                                    value={turnId}
                                    onChange={(e) => setTurnId(e.target.value)}
                                    className="w-full px-3 py-2 border rounded-md text-sm"
                                    placeholder="turn_123456"
                                />
                            </div>
                        )}
                    </div>
                )}

                <div className="mb-4">
                    <label className="block text-sm font-medium mb-2">App Bundle ID</label>
                    <input
                        type="text"
                        value={appBundleId}
                        onChange={(e) => setAppBundleId(e.target.value)}
                        className="w-full px-3 py-2 border rounded-md text-sm"
                        placeholder={appBundleId}
                    />
                </div>

                <button
                    onClick={fetchData}
                    disabled={loading}
                    className="w-full md:w-auto px-6 py-2 bg-blue-600 text-white rounded-md font-medium hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
                >
                    {loading ? 'Loading...' : 'Refresh Data'}
                </button>
            </div>

            {/* Error Display */}
            {error && (
                <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded">
                    <div className="flex">
                        <div className="flex-shrink-0">
                            <svg className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                            </svg>
                        </div>
                        <div className="ml-3">
                            <p className="text-sm text-red-700">{error}</p>
                        </div>
                    </div>
                </div>
            )}
            {/* Auth Error Display */}
            {authError && (
                <div className="bg-yellow-50 border-l-4 border-yellow-500 p-4 rounded">
                    <div className="flex">
                        <div className="flex-shrink-0">
                            <svg className="h-5 w-5 text-yellow-400" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                            </svg>
                        </div>
                        <div className="ml-3">
                            <p className="text-sm text-yellow-700">{authError}</p>
                            <p className="text-xs text-yellow-600 mt-1">
                                Base URL: {settings.getBaseUrl()}
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {/* Metrics Cards */}
            {metrics && (
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div className="bg-white rounded-lg shadow-sm border p-6">
                        <div className="text-sm text-gray-600 mb-1">Total Cost</div>
                        <div className="text-3xl font-bold text-blue-600">
                            ${metrics.totalCost.toFixed(4)}
                        </div>
                    </div>

                    {metrics.eventCount !== undefined && (
                        <div className="bg-white rounded-lg shadow-sm border p-6">
                            <div className="text-sm text-gray-600 mb-1">Events</div>
                            <div className="text-3xl font-bold text-green-600">
                                {metrics.eventCount.toLocaleString()}
                            </div>
                        </div>
                    )}

                    {metrics.userCount !== undefined && (
                        <div className="bg-white rounded-lg shadow-sm border p-6">
                            <div className="text-sm text-gray-600 mb-1">Users</div>
                            <div className="text-3xl font-bold text-purple-600">
                                {metrics.userCount}
                            </div>
                        </div>
                    )}

                    {metrics.agentCount !== undefined && (
                        <div className="bg-white rounded-lg shadow-sm border p-6">
                            <div className="text-sm text-gray-600 mb-1">Agents</div>
                            <div className="text-3xl font-bold text-orange-600">
                                {metrics.agentCount}
                            </div>
                        </div>
                    )}

                    {metrics.totalTokens !== undefined && (
                        <div className="bg-white rounded-lg shadow-sm border p-6">
                            <div className="text-sm text-gray-600 mb-1">Total Tokens</div>
                            <div className="text-3xl font-bold text-indigo-600">
                                {(metrics.totalTokens / 1000).toFixed(1)}K
                            </div>
                        </div>
                    )}
                    {/* Search Queries Card */}
                    {metrics.totalSearchQueries !== undefined && metrics.totalSearchQueries > 0 && (
                        <div className="bg-white rounded-lg shadow-sm border p-6">
                            <div className="text-sm text-gray-600 mb-1">Search Queries</div>
                            <div className="text-3xl font-bold text-teal-600">
                                {metrics.totalSearchQueries.toLocaleString()}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Charts */}
            {metrics && metrics.breakdown && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div className="bg-white rounded-lg shadow-sm border p-6">
                        <h3 className="text-lg font-semibold mb-4">Cost Distribution</h3>
                        <div style={{ height: '300px' }}>
                            <canvas ref={pieChartRef}></canvas>
                        </div>
                    </div>

                    <div className="bg-white rounded-lg shadow-sm border p-6">
                        <h3 className="text-lg font-semibold mb-4">Token Usage</h3>
                        <div style={{ height: '300px' }}>
                            <canvas ref={barChartRef}></canvas>
                        </div>
                    </div>
                </div>
            )}

            {/* Detailed Tables */}
            {metrics && (
                <div className="bg-white rounded-lg shadow-sm border p-6">
                    <h3 className="text-lg font-semibold mb-4">Detailed Breakdown</h3>

                    {/* Users View */}
                    {viewMode === 'users' && metrics.userBreakdown && (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead className="bg-gray-50">
                                <tr>
                                    <th className="px-4 py-3 text-left font-semibold">User ID</th>
                                    <th className="px-4 py-3 text-right font-semibold">Cost</th>
                                    <th className="px-4 py-3 text-right font-semibold">Services</th>
                                </tr>
                                </thead>
                                <tbody className="divide-y">
                                {metrics.userBreakdown.map((user, idx) => (
                                    <tr key={idx} className="hover:bg-gray-50">
                                        <td className="px-4 py-3 font-medium">{user.userId}</td>
                                        <td className="px-4 py-3 text-right">${user.cost.toFixed(4)}</td>
                                        <td className="px-4 py-3 text-right">{user.rollup.length}</td>
                                    </tr>
                                ))}
                                </tbody>
                            </table>
                        </div>
                    )}

                    {/* Agents View */}
                    {viewMode === 'agents' && metrics.agentBreakdown && (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead className="bg-gray-50">
                                <tr>
                                    <th className="px-4 py-3 text-left font-semibold">Agent</th>
                                    <th className="px-4 py-3 text-right font-semibold">Cost</th>
                                    <th className="px-4 py-3 text-left font-semibold">Models Used</th>
                                </tr>
                                </thead>
                                <tbody className="divide-y">
                                {metrics.agentBreakdown.map((agent, idx) => (
                                    <tr key={idx} className="hover:bg-gray-50">
                                        <td className="px-4 py-3 font-medium">{agent.agentName}</td>
                                        <td className="px-4 py-3 text-right">${agent.cost.toFixed(4)}</td>
                                        <td className="px-4 py-3">
                                            <div className="space-y-1">
                                                {agent.breakdown.map((b, i) => (
                                                    <div key={i} className="text-xs">
                                                        {b.service}/{b.model}: ${b.cost.toFixed(4)}
                                                    </div>
                                                ))}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                                </tbody>
                            </table>
                        </div>
                    )}

                    {/* Generic Breakdown */}
                    {(viewMode === 'overview' || viewMode === 'conversation' || viewMode === 'turn') && metrics.breakdown && (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead className="bg-gray-50">
                                <tr>
                                    <th className="px-4 py-3 text-left font-semibold">Service</th>
                                    <th className="px-4 py-3 text-left font-semibold">Provider</th>
                                    <th className="px-4 py-3 text-left font-semibold">Model</th>
                                    <th className="px-4 py-3 text-right font-semibold">Input Tokens</th>
                                    <th className="px-4 py-3 text-right font-semibold">Output Tokens</th>
                                    <th className="px-4 py-3 text-right font-semibold">Cache Write</th>
                                    <th className="px-4 py-3 text-right font-semibold">Cache Read</th>
                                    <th className="px-4 py-3 text-right font-semibold">Cost</th>
                                </tr>
                                </thead>
                                <tbody className="divide-y">
                                {metrics.breakdown.map((item, idx) => {
                                    const isSearch = item.service === 'web_search';
                                    const modelOrTier = isSearch
                                        ? (item.tier || 'free')
                                        : (item.model || '').substring(0, 30);

                                    return (
                                        <tr key={idx} className="hover:bg-gray-50">
                                            <td className="px-4 py-3">
                                                {item.service === 'web_search' && '🔍 '}
                                                {item.service}
                                            </td>
                                            <td className="px-4 py-3">{item.provider}</td>
                                            <td className="px-4 py-3 text-xs">{modelOrTier}</td>
                                            <td className="px-4 py-3 text-right">
                                                {isSearch
                                                    ? (item.spent.search_queries || 0).toLocaleString()
                                                    : (item.spent.input || 0).toLocaleString()
                                                }
                                            </td>
                                            <td className="px-4 py-3 text-right">
                                                {isSearch
                                                    ? (item.spent.search_results || 0).toLocaleString()
                                                    : (item.spent.output || 0).toLocaleString()
                                                }
                                            </td>
                                            <td className="px-4 py-3 text-right">
                                                {isSearch ? '-' : (item.spent.cache_creation || 0).toLocaleString()}
                                            </td>
                                            <td className="px-4 py-3 text-right">
                                                {isSearch ? '-' : (item.spent.cache_read || 0).toLocaleString()}
                                            </td>
                                            <td className="px-4 py-3 text-right font-semibold">
                                                ${item.cost.toFixed(4)}
                                            </td>
                                        </tr>
                                    );
                                })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* Loading State */}
            {loading && !metrics && (
                <div className="bg-white rounded-lg shadow-sm border p-12 text-center">
                    <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
                    <p className="mt-4 text-gray-600">Loading data...</p>
                </div>
            )}

            {/* Empty State */}
            {!loading && !metrics && !error && (
                <div className="bg-white rounded-lg shadow-sm border p-12 text-center">
                    <svg className="mx-auto h-12 w-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                    </svg>
                    <h3 className="mt-2 text-sm font-medium text-gray-900">No data</h3>
                    <p className="mt-1 text-sm text-gray-500">Set parameters and click Refresh Data to begin.</p>
                </div>
            )}
        </div>
    );
};

// Render
const rootElement = document.getElementById('root');
if (rootElement) {
    const root = ReactDOM.createRoot(rootElement);
    root.render(<OPEXDashboard />);
}