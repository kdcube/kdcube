// Control Plane Monitoring Dashboard (TypeScript)

import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import ReactDOM from 'react-dom/client';

// =============================================================================
// Types
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

interface GatewayRoleLimits {
    hourly: number;
    burst: number;
    burst_window: number;
}

interface GatewayConfigurationView {
    current_profile: string;
    instance_id: string;
    tenant_id: string;
    display_name: string;
    guarded_rest_patterns?: string[];
    rate_limits: Record<string, GatewayRoleLimits>;
    service_capacity: {
        concurrent_requests_per_instance: number;
        avg_processing_time_seconds: number;
        requests_per_hour: number;
    };
    backpressure_settings: {
        capacity_buffer: number;
        queue_depth_multiplier: number;
        anonymous_pressure_threshold: number;
        registered_pressure_threshold: number;
        hard_limit_threshold: number;
    };
    circuit_breaker_settings: Record<string, any>;
    monitoring_settings: Record<string, any>;
}

interface SystemMonitoringResponse {
    instances?: Record<string, any>;
    global_stats?: Record<string, any>;
    queue_stats?: {
        anonymous: number;
        registered: number;
        privileged: number;
        total: number;
        capacity_context?: Record<string, any>;
        analytics?: Record<string, any>;
    };
    throttling_stats?: Record<string, any>;
    throttling_by_period?: Record<string, any>;
    recent_throttling_events?: Array<any>;
    gateway_configuration?: GatewayConfigurationView;
    capacity_transparency?: Record<string, any>;
    timestamp?: number;
}

interface CircuitBreakerStats {
    name: string;
    state: 'closed' | 'open' | 'half_open';
    failure_count: number;
    success_count: number;
    total_requests: number;
    total_failures: number;
    consecutive_failures: number;
    current_window_failures: number;
    last_failure_time?: number;
    last_success_time?: number;
    opened_at?: number;
}

interface CircuitBreakerSummary {
    total_circuits: number;
    open_circuits: number;
    half_open_circuits: number;
    closed_circuits: number;
}

// =============================================================================
// Settings Manager (same pattern as other widgets)
// =============================================================================

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
        defaultAppBundleId: '{{DEFAULT_APP_BUNDLE_ID}}'
    };

    private configReceivedCallback: (() => void) | null = null;

    getBaseUrl(): string {
        if (this.settings.baseUrl === this.PLACEHOLDER_BASE_URL) {
            return 'http://localhost:8010';
        }
        try {
            const url = new URL(this.settings.baseUrl);
            if (url.port === 'None' || url.hostname.includes('None')) {
                return 'http://localhost:8010';
            }
            return this.settings.baseUrl;
        } catch (e) {
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
        const identity = 'CONTROL_PLANE_MONITORING';

        window.addEventListener('message', (event: MessageEvent) => {
            if (event.data.type === 'CONN_RESPONSE' || event.data.type === 'CONFIG_RESPONSE') {
                const requestedIdentity = event.data.identity;
                if (requestedIdentity !== identity) {
                    return;
                }

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
                        if (this.configReceivedCallback) {
                            this.configReceivedCallback();
                        }
                    }
                }
            }
        });

        if (this.hasPlaceholderSettings()) {
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
                    resolve(false);
                }, 3000);

                const originalCallback = this.configReceivedCallback;
                this.onConfigReceived(() => {
                    clearTimeout(timeout);
                    if (originalCallback) originalCallback();
                    resolve(true);
                });
            });
        }

        return Promise.resolve(!this.hasPlaceholderSettings());
    }
}

const settings = new SettingsManager();

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
// API Client
// =============================================================================

class MonitoringAPI {
    constructor(private basePath: string = '') {}

    private url(path: string): string {
        return `${settings.getBaseUrl()}${this.basePath}${path}`;
    }
    //
    // private url(path: string): string {
    //     return `${this.baseUrl}${path}`;
    // }

    async getSystemStatus(): Promise<SystemMonitoringResponse> {
        const res = await fetch(this.url('/monitoring/system'), {
            method: 'GET',
            headers: makeAuthHeaders(),
        });
        if (!res.ok) throw new Error(`Failed to load system status (${res.status})`);
        return res.json();
    }

    async getCircuitBreakers(): Promise<{ summary: CircuitBreakerSummary; circuits: Record<string, CircuitBreakerStats> }> {
        const res = await fetch(this.url('/admin/circuit-breakers'), {
            method: 'GET',
            headers: makeAuthHeaders(),
        });
        if (!res.ok) throw new Error(`Failed to load circuit breakers (${res.status})`);
        return res.json();
    }

    async resetCircuitBreaker(name: string): Promise<void> {
        const res = await fetch(this.url(`/admin/circuit-breakers/${name}/reset`), {
            method: 'POST',
            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
        });
        if (!res.ok) throw new Error(`Failed to reset circuit breaker (${res.status})`);
    }

    async validateGatewayConfig(payload: any): Promise<any> {
        const res = await fetch(this.url('/admin/gateway/validate-config'), {
            method: 'POST',
            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`Validation failed (${res.status})`);
        return res.json();
    }

    async updateGatewayConfig(payload: any): Promise<any> {
        const res = await fetch(this.url('/admin/gateway/update-config'), {
            method: 'POST',
            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`Update failed (${res.status})`);
        return res.json();
    }

    async resetGatewayConfig(payload: any): Promise<any> {
        const res = await fetch(this.url('/admin/gateway/reset-config'), {
            method: 'POST',
            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`Reset failed (${res.status})`);
        return res.json();
    }
}

// =============================================================================
// UI Components (simple, neutral palette)
// =============================================================================

const Card: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => (
    <div className={`bg-white rounded-2xl shadow-sm border border-gray-200/70 ${className}`}>
        {children}
    </div>
);

const CapacityPanel: React.FC<{ capacity?: Record<string, any> }> = ({ capacity }) => {
    if (!capacity) return null;
    const metrics = capacity.capacity_metrics || {};
    const scaling = capacity.instance_scaling || {};
    const thresholds = capacity.threshold_breakdown || {};
    const warnings: string[] = capacity.capacity_warnings || [];
    const hasActual = metrics.actual_runtime && metrics.health_metrics;
    const health = metrics.health_metrics || {};

    return (
        <Card>
            <CardHeader title="Capacity Transparency" subtitle="Actual runtime vs configured capacity." />
            <CardBody className="space-y-4">
                {warnings.length > 0 && (
                    <div className="p-3 rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-sm">
                        {warnings.map((w, i) => (
                            <div key={i}>• {w}</div>
                        ))}
                    </div>
                )}

                {hasActual && (
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Configured</div>
                            <div className="text-sm font-semibold">{health.processes_vs_configured?.configured ?? '—'}</div>
                            <div className="text-xs text-gray-500">processes</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Actual</div>
                            <div className="text-sm font-semibold">{health.processes_vs_configured?.actual ?? '—'}</div>
                            <div className="text-xs text-gray-500">running</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Healthy</div>
                            <div className="text-sm font-semibold">{health.processes_vs_configured?.healthy ?? '—'}</div>
                            <div className="text-xs text-gray-500">{Math.round((health.process_health_ratio ?? 0) * 100)}% health</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Process Deficit</div>
                            <div className="text-sm font-semibold">{health.processes_vs_configured?.process_deficit ?? 0}</div>
                            <div className="text-xs text-gray-500">missing</div>
                        </div>
                    </div>
                )}

                {metrics.actual_runtime && metrics.configuration && (
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Per Process</div>
                            <div className="text-sm font-semibold">{metrics.configuration.configured_concurrent_per_process ?? '—'}</div>
                            <div className="text-xs text-gray-500">{metrics.configuration.configured_avg_processing_time_seconds ?? '—'}s avg</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Actual Concurrent</div>
                            <div className="text-sm font-semibold">{metrics.actual_runtime.actual_concurrent_per_instance ?? '—'}</div>
                            <div className="text-xs text-gray-500">per instance</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Effective</div>
                            <div className="text-sm font-semibold">{metrics.actual_runtime.actual_effective_concurrent_per_instance ?? '—'}</div>
                            <div className="text-xs text-gray-500">after buffer</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Total Capacity</div>
                            <div className="text-sm font-semibold">{metrics.actual_runtime.actual_total_capacity_per_instance ?? '—'}</div>
                            <div className="text-xs text-gray-500">per instance</div>
                        </div>
                    </div>
                )}

                {scaling && (
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Instances</div>
                            <div className="text-sm font-semibold">{scaling.detected_instances ?? '—'}</div>
                            <div className="text-xs text-gray-500">detected</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">System Concurrent</div>
                            <div className="text-sm font-semibold">{scaling.total_concurrent_capacity ?? '—'}</div>
                            <div className="text-xs text-gray-500">total</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">System Total</div>
                            <div className="text-sm font-semibold">{scaling.total_system_capacity ?? '—'}</div>
                            <div className="text-xs text-gray-500">capacity</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Health Ratio</div>
                            <div className="text-sm font-semibold">{Math.round((scaling.process_health_ratio ?? 0) * 100)}%</div>
                            <div className="text-xs text-gray-500">system</div>
                        </div>
                    </div>
                )}

                {thresholds && (
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Anonymous Blocks At</div>
                            <div className="text-sm font-semibold">{thresholds.anonymous_blocks_at ?? '—'}</div>
                            <div className="text-xs text-gray-500">{thresholds.anonymous_percentage ?? '—'}%</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Registered Blocks At</div>
                            <div className="text-sm font-semibold">{thresholds.registered_blocks_at ?? '—'}</div>
                            <div className="text-xs text-gray-500">{thresholds.registered_percentage ?? '—'}%</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Hard Limit At</div>
                            <div className="text-sm font-semibold">{thresholds.hard_limit_at ?? '—'}</div>
                            <div className="text-xs text-gray-500">{thresholds.hard_limit_percentage ?? '—'}%</div>
                        </div>
                    </div>
                )}
            </CardBody>
        </Card>
    );
};

const CardHeader: React.FC<{ title: string; subtitle?: string; action?: React.ReactNode }> = ({ title, subtitle, action }) => (
    <div className="px-4 py-3 border-b border-gray-200/70">
        <div className="flex items-start justify-between gap-4">
            <div>
                <h2 className="text-base font-semibold text-gray-900">{title}</h2>
                {subtitle && <p className="mt-1 text-xs text-gray-600 leading-relaxed">{subtitle}</p>}
            </div>
            {action && <div className="pt-1">{action}</div>}
        </div>
    </div>
);

const CardBody: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => (
    <div className={`px-4 py-3 ${className}`}>
        {children}
    </div>
);

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
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${variants[variant]} ${className}`}
        >
            {children}
        </button>
    );
};

const Input: React.FC<{
    label?: string;
    value: string;
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
    placeholder?: string;
    className?: string;
}> = ({ label, value, onChange, placeholder, className = '' }) => (
    <div className={className}>
        {label && <label className="block text-xs font-medium text-gray-800 mb-1.5">{label}</label>}
        <input
            type="text"
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            className="w-full px-3 py-1.5 border border-gray-200/80 rounded-lg bg-white text-xs focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300 transition-colors placeholder:text-gray-400"
        />
    </div>
);

const TextArea: React.FC<{
    label?: string;
    value: string;
    onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
    className?: string;
}> = ({ label, value, onChange, className = '' }) => (
    <div className={className}>
        {label && <label className="block text-xs font-medium text-gray-800 mb-1.5">{label}</label>}
        <textarea
            value={value}
            onChange={onChange}
            rows={10}
            className="w-full px-3 py-2 border border-gray-200/80 rounded-lg bg-white font-mono text-xs leading-relaxed focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300"
        />
    </div>
);

const Pill: React.FC<{ tone?: 'neutral' | 'success' | 'warning' | 'danger'; children: React.ReactNode }> = ({ tone = 'neutral', children }) => {
    const tones = {
        neutral: 'bg-gray-100 text-gray-700',
        success: 'bg-emerald-100 text-emerald-700',
        warning: 'bg-amber-100 text-amber-700',
        danger: 'bg-rose-100 text-rose-700',
    };
    return <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${tones[tone]}`}>{children}</span>;
};

// =============================================================================
// App
// =============================================================================

const MonitoringDashboard: React.FC = () => {
    const api = useMemo(() => new MonitoringAPI(), []);
    const [system, setSystem] = useState<SystemMonitoringResponse | null>(null);
    const [circuitBreakers, setCircuitBreakers] = useState<Record<string, CircuitBreakerStats>>({});
    const [circuitSummary, setCircuitSummary] = useState<CircuitBreakerSummary | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const [lastUpdate, setLastUpdate] = useState<string | null>(null);

    const [tenant, setTenant] = useState(settings.getDefaultTenant());
    const [project, setProject] = useState(settings.getDefaultProject());
    const [dryRun, setDryRun] = useState(false);
    const [configJson, setConfigJson] = useState<string>('');
    const [validationResult, setValidationResult] = useState<any>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);

    const refreshAll = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [sys, cb] = await Promise.all([
                api.getSystemStatus(),
                api.getCircuitBreakers(),
            ]);
            setSystem(sys);
            setCircuitBreakers(cb.circuits || {});
            setCircuitSummary(cb.summary || null);
            setLastUpdate(new Date().toLocaleTimeString());
        } catch (e: any) {
            setError(e?.message || 'Failed to load monitoring data');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        let mounted = true;
        settings.setupParentListener().then(() => {
            if (mounted) refreshAll();
        });
        return () => { mounted = false; };
    }, [refreshAll]);

    useEffect(() => {
        if (!autoRefresh) return;
        const t = setInterval(() => refreshAll(), 5000);
        return () => clearInterval(t);
    }, [autoRefresh, refreshAll]);

    useEffect(() => {
        if (!system?.gateway_configuration) return;
        const cfg = system.gateway_configuration;
        const capacityCfg = system.capacity_transparency?.capacity_metrics?.configuration || {};
        const payload = {
            tenant,
            project,
            guarded_rest_patterns: cfg.guarded_rest_patterns || [],
            service_capacity: {
                concurrent_per_process: capacityCfg.configured_concurrent_per_process ?? 5,
                processes_per_instance: capacityCfg.configured_processes_per_instance ?? 1,
                avg_processing_time_seconds: capacityCfg.configured_avg_processing_time_seconds ?? (cfg.service_capacity?.avg_processing_time_seconds ?? 25),
            },
            backpressure: {
                capacity_buffer: cfg.backpressure_settings?.capacity_buffer ?? 0.2,
                queue_depth_multiplier: cfg.backpressure_settings?.queue_depth_multiplier ?? 2.0,
                anonymous_pressure_threshold: cfg.backpressure_settings?.anonymous_pressure_threshold ?? 0.6,
                registered_pressure_threshold: cfg.backpressure_settings?.registered_pressure_threshold ?? 0.8,
                hard_limit_threshold: cfg.backpressure_settings?.hard_limit_threshold ?? 0.95,
            },
            rate_limits: cfg.rate_limits || {},
        };
        setConfigJson(JSON.stringify(payload, null, 2));
    }, [system, tenant, project]);

    const queue = system?.queue_stats;
    const capacityCtx = system?.queue_stats?.capacity_context || {};
    const throttling = system?.throttling_stats;
    const events = system?.recent_throttling_events || [];
    const gateway = system?.gateway_configuration;
    const throttlingByPeriod = system?.throttling_by_period || {};

    const handleValidate = async () => {
        try {
            const payload = JSON.parse(configJson);
            const res = await api.validateGatewayConfig(payload);
            setValidationResult(res);
            setActionMessage('Validation completed');
        } catch (e: any) {
            setActionMessage(e?.message || 'Validation failed');
        }
    };

    const handleUpdate = async () => {
        try {
            const payload = JSON.parse(configJson);
            await api.updateGatewayConfig(payload);
            setActionMessage('Config updated');
            await refreshAll();
        } catch (e: any) {
            setActionMessage(e?.message || 'Update failed');
        }
    };

    const handleReset = async () => {
        try {
            const payload = { tenant, project, dry_run: dryRun };
            await api.resetGatewayConfig(payload);
            setActionMessage(dryRun ? 'Dry run completed' : 'Config reset to env');
            await refreshAll();
        } catch (e: any) {
            setActionMessage(e?.message || 'Reset failed');
        }
    };

    const resetCircuit = async (name: string) => {
        try {
            await api.resetCircuitBreaker(name);
            await refreshAll();
        } catch (e: any) {
            setActionMessage(e?.message || 'Failed to reset circuit breaker');
        }
    };

    return (
        <div className="min-h-screen bg-gray-50 text-gray-900">
            <div className="max-w-6xl mx-auto px-4 py-4 space-y-4">
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <h1 className="text-lg font-semibold">Gateway Monitoring</h1>
                        <p className="text-xs text-gray-600">System health, queues, throttling, and config management.</p>
                    </div>
                    <div className="flex items-center gap-3">
                        <label className="text-[11px] text-gray-600 flex items-center gap-2">
                            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
                            Auto refresh
                        </label>
                        <Button variant="secondary" onClick={refreshAll} disabled={loading}>
                            {loading ? 'Refreshing…' : 'Refresh'}
                        </Button>
                    </div>
                </div>

                {error && (
                    <Card>
                        <CardBody>
                            <div className="text-xs text-rose-700">{error}</div>
                        </CardBody>
                    </Card>
                )}

                <Card>
                    <CardHeader
                        title="System Summary"
                        subtitle={`Last update: ${lastUpdate || '—'}`}
                        action={gateway ? <Pill tone="success">{gateway.current_profile}</Pill> : null}
                    />
                    <CardBody>
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Instance</div>
                                <div className="text-sm font-semibold">{gateway?.instance_id || '—'}</div>
                                <div className="text-xs text-gray-500">{gateway?.tenant_id || '—'}</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Total Queue</div>
                                <div className="text-sm font-semibold">{queue?.total ?? 0}</div>
                                <div className="text-xs text-gray-500">{Math.round((capacityCtx.pressure_ratio || 0) * 100)}% pressure</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Instances</div>
                                <div className="text-sm font-semibold">{capacityCtx.instance_count ?? 0}</div>
                                <div className="text-xs text-gray-500">Weighted cap {capacityCtx.weighted_max_capacity ?? 0}</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Throttled (1h)</div>
                                <div className="text-sm font-semibold">{throttling?.total_throttled ?? 0}</div>
                                <div className="text-xs text-gray-500">{(throttling?.throttle_rate ?? 0).toFixed(1)}%</div>
                            </div>
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Traffic (Requests)" subtitle="Totals and average per minute by period." />
                    <CardBody>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            {["1h", "3h", "24h"].map((key) => {
                                const period = throttlingByPeriod[key] || {};
                                const total = period.total_requests ?? 0;
                                const hours = parseInt(key.replace("h", ""), 10) || 1;
                                const perHour = hours ? total / hours : 0;
                                const perMin = perHour / 60;
                                return (
                                    <div key={key} className="p-4 rounded-xl bg-gray-100">
                                        <div className="text-xs text-gray-600">{key} total</div>
                                        <div className="text-sm font-semibold">{Math.round(total)}</div>
                                        <div className="text-xs text-gray-500">
                                            ~{Math.round(perMin)} / min · ~{Math.round(perHour)} / hour
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Queues" subtitle="Current queue sizes and admission state." />
                    <CardBody>
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Anonymous</div>
                                <div className="text-sm font-semibold">{queue?.anonymous ?? 0}</div>
                                <div className="text-xs text-gray-500">
                                    {capacityCtx.accepting_anonymous ? 'accepting' : 'blocked'}
                                </div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Registered</div>
                                <div className="text-sm font-semibold">{queue?.registered ?? 0}</div>
                                <div className="text-xs text-gray-500">
                                    {capacityCtx.accepting_registered ? 'accepting' : 'blocked'}
                                </div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Privileged</div>
                                <div className="text-sm font-semibold">{queue?.privileged ?? 0}</div>
                                <div className="text-xs text-gray-500">
                                    {capacityCtx.accepting_privileged ? 'accepting' : 'blocked'}
                                </div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Hard Limit</div>
                                <div className="text-sm font-semibold">{capacityCtx.thresholds?.hard_limit_threshold ?? 0}</div>
                                <div className="text-xs text-gray-500">items</div>
                            </div>
                        </div>
                    </CardBody>
                </Card>

                <CapacityPanel capacity={system?.capacity_transparency} />

                <Card>
                    <CardHeader title="Circuit Breakers" subtitle="Live circuit states and resets." />
                    <CardBody>
                        <div className="flex items-center gap-3 mb-4">
                            <Pill tone={circuitSummary?.open_circuits ? 'danger' : 'success'}>
                                Open: {circuitSummary?.open_circuits ?? 0}
                            </Pill>
                            <Pill tone="neutral">Half-open: {circuitSummary?.half_open_circuits ?? 0}</Pill>
                            <Pill tone="neutral">Closed: {circuitSummary?.closed_circuits ?? 0}</Pill>
                        </div>
                        <div className="space-y-3">
                            {Object.entries(circuitBreakers).map(([name, cb]) => (
                                <div key={name} className="flex items-center justify-between p-3 rounded-xl bg-gray-100">
                                    <div className="text-sm">
                                        <div className="font-semibold">{name}</div>
                                        <div className="text-xs text-gray-600">
                                            state: {cb.state} • failures: {cb.current_window_failures}/{cb.failure_count}
                                        </div>
                                    </div>
                                    <Button variant="secondary" onClick={() => resetCircuit(name)}>
                                        Reset
                                    </Button>
                                </div>
                            ))}
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Gateway Configuration" subtitle="View, validate, update, or reset config." />
                    <CardBody className="space-y-4">
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <Input label="Tenant" value={tenant} onChange={(e) => setTenant(e.target.value)} />
                            <Input label="Project" value={project} onChange={(e) => setProject(e.target.value)} />
                            <div className="flex items-end gap-3">
                                <label className="text-xs text-gray-600 flex items-center gap-2">
                                    <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
                                    Dry run reset
                                </label>
                            </div>
                        </div>

                        <TextArea label="Update Payload (JSON)" value={configJson} onChange={(e) => setConfigJson(e.target.value)} />

                        <div className="flex flex-wrap gap-3">
                            <Button variant="secondary" onClick={handleValidate}>Validate</Button>
                            <Button onClick={handleUpdate}>Update</Button>
                            <Button variant="danger" onClick={handleReset}>Reset to Env</Button>
                            {actionMessage && <span className="text-sm text-gray-600">{actionMessage}</span>}
                        </div>

                        {validationResult && (
                            <div className="mt-4 p-3 rounded-xl bg-gray-100 text-xs font-mono whitespace-pre-wrap">
                                {JSON.stringify(validationResult, null, 2)}
                            </div>
                        )}
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Throttling (Recent)" subtitle="Last hour summary and recent events." />
                    <CardBody>
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
                            <div className="p-3 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Total</div>
                                <div className="text-sm font-semibold">{throttling?.total_requests ?? 0}</div>
                            </div>
                            <div className="p-3 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Throttled</div>
                                <div className="text-sm font-semibold">{throttling?.total_throttled ?? 0}</div>
                            </div>
                            <div className="p-3 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">429</div>
                                <div className="text-sm font-semibold">{throttling?.rate_limit_429 ?? 0}</div>
                            </div>
                            <div className="p-3 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">503</div>
                                <div className="text-sm font-semibold">{throttling?.backpressure_503 ?? 0}</div>
                            </div>
                        </div>

                        <div className="space-y-2">
                            {events.slice(0, 10).map((e, idx) => (
                                <div key={e.event_id || idx} className="text-xs flex items-center justify-between bg-white border border-gray-200/70 rounded-xl px-3 py-2">
                                    <div className="text-gray-700">{e.reason}</div>
                                    <div className="text-gray-500">{e.user_type}</div>
                                    <div className="text-gray-500">{e.http_status}</div>
                                </div>
                            ))}
                            {events.length === 0 && <div className="text-sm text-gray-500">No recent events.</div>}
                        </div>
                    </CardBody>
                </Card>
            </div>
        </div>
    );
};

// Render
const rootElement = document.getElementById('root');
if (rootElement) {
    const root = ReactDOM.createRoot(rootElement);
    root.render(<MonitoringDashboard />);
}
