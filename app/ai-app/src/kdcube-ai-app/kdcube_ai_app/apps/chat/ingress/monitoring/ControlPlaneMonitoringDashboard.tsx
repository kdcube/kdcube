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
    bypass_throttling_patterns?: string[];
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
        paid_pressure_threshold?: number;
        hard_limit_threshold: number;
    };
    circuit_breaker_settings: Record<string, any>;
    monitoring_settings: Record<string, any>;
}

interface SystemMonitoringResponse {
    gateway_config_source?: string;
    gateway_config_raw?: any;
    gateway_config_components?: Record<string, any> | null;
    components?: Record<string, any>;
    autoscaler?: Record<string, any>;
    instances?: Record<string, any>;
    global_stats?: Record<string, any>;
    queue_stats?: {
        anonymous: number;
        registered: number;
        paid?: number;
        privileged: number;
        total: number;
        capacity_context?: Record<string, any>;
        analytics?: Record<string, any>;
    };
    queue_analytics?: {
        wait_times?: Record<string, number>;
        throughput?: Record<string, number>;
        individual_queues?: Record<string, {
            size?: number;
            avg_wait?: number;
            throughput?: number;
            blocked?: boolean;
        }>;
    };
    queue_utilization?: number;
    throttling_stats?: Record<string, any>;
    throttling_by_period?: Record<string, any>;
    throttling_windows?: Record<string, any>;
    recent_throttling_events?: Array<any>;
    gateway_configuration?: GatewayConfigurationView;
    capacity_transparency?: Record<string, any>;
    db_connections?: {
        max_connections?: number;
        source?: string;
        pool_max_per_worker?: number;
        processes_per_instance?: number;
        estimated_per_instance?: number;
        instance_count?: number;
        estimated_total?: number;
        percent_of_max?: number;
        warning?: boolean;
        warning_reason?: string | null;
        warning_level?: string | null;
    };
    sse_connections?: {
        total_connections?: number;
        sessions?: number;
        max_connections?: number;
    };
    connection_pools?: {
        components?: Record<string, any>;
    };
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

const isRawGatewayConfigPayload = (payload: any): boolean => {
    if (!payload || typeof payload !== 'object') return false;
    if (payload.raw_config || payload.gateway_config || payload.config) return true;
    if (payload.profile) return true;
    const sections = ['service_capacity', 'backpressure', 'rate_limits', 'pools', 'limits'];
    for (const key of sections) {
        const value = payload[key];
        if (value && typeof value === 'object') {
            if ('ingress' in value || 'proc' in value || 'processor' in value || 'worker' in value) {
                return true;
            }
        }
    }
    return false;
};

interface CircuitBreakerSummary {
    total_circuits: number;
    open_circuits: number;
    half_open_circuits: number;
    closed_circuits: number;
}

interface BurstUser {
    token: string;
    user_id?: string;
    username?: string;
    roles?: string[];
}

interface BurstUsersResponse {
    enabled: boolean;
    counts: Record<string, number>;
    users: {
        admin: BurstUser[];
        registered: BurstUser[];
        paid: BurstUser[];
    };
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

    async clearGatewayConfigCache(payload: any): Promise<any> {
        const res = await fetch(this.url('/admin/gateway/clear-cache'), {
            method: 'POST',
            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`Clear cache failed (${res.status})`);
        return res.json();
    }

    async resetThrottling(payload: any): Promise<any> {
        const res = await fetch(this.url('/admin/throttling/reset'), {
            method: 'POST',
            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`Reset throttling failed (${res.status})`);
        return res.json();
    }

    async getBurstUsers(): Promise<BurstUsersResponse | null> {
        const res = await fetch(this.url('/admin/burst/users'), {
            method: 'GET',
            headers: makeAuthHeaders(),
        });
        let data: any = null;
        try {
            data = await res.json();
        } catch (_) {
            data = null;
        }
        if (!res.ok) {
            const detail = data?.detail || data?.message;
            throw new Error(detail ? `Burst users: ${detail}` : `Failed to load burst users (${res.status})`);
        }
        return data;
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

const CapacityPanel: React.FC<{
    capacity?: Record<string, any>;
    dbConnections?: SystemMonitoringResponse["db_connections"];
    capacitySource?: string;
    capacitySourceActual?: number;
    capacitySourceHealthy?: number;
}> = ({ capacity, dbConnections, capacitySource, capacitySourceActual, capacitySourceHealthy }) => {
    if (!capacity) return null;
    const metrics = capacity.capacity_metrics || {};
    const scaling = capacity.instance_scaling || {};
    const thresholds = capacity.threshold_breakdown || {};
    const warnings: string[] = capacity.capacity_warnings || [];
    const hasActual = metrics.actual_runtime && metrics.health_metrics;
    const health = metrics.health_metrics || {};
    const actualProcesses = capacitySourceActual ?? health.processes_vs_configured?.actual ?? 0;
    const configuredProcesses = health.processes_vs_configured?.configured ?? 0;
    const healthyProcesses = capacitySourceHealthy ?? health.processes_vs_configured?.healthy ?? 0;

    return (
        <Card>
            <CardHeader
                title="Capacity Transparency"
                subtitle={`Capacity source: ${capacitySource || 'unknown'}. Actual runtime vs configured capacity.`}
            />
            <CardBody className="space-y-4">
                <Legend>
                    Compares configured worker counts to live heartbeats from the capacity source component.
                </Legend>
                {dbConnections?.warning ? (
                    <div className="p-3 rounded-xl border border-rose-200 bg-rose-50 text-rose-800 text-sm">
                        <div className="font-semibold">DB connection capacity warning</div>
                        <div>
                            {dbConnections.warning_reason || 'Estimated DB connections are close to max_connections.'}
                            {dbConnections.percent_of_max != null ? ` (${dbConnections.percent_of_max}% of max)` : ''}
                        </div>
                        <div className="text-[11px] text-rose-700 mt-1">
                            estimated_total={dbConnections.estimated_total ?? '—'} · max_connections={dbConnections.max_connections ?? '—'} ·
                            pool_per_worker={dbConnections.pool_max_per_worker ?? '—'} · processes_per_instance={dbConnections.processes_per_instance ?? '—'}
                        </div>
                        <div className="text-[11px] text-rose-700">
                            source={dbConnections.source || 'unknown'}
                        </div>
                    </div>
                ) : null}
                {actualProcesses === 0 ? (
                    <div className="p-3 rounded-xl bg-amber-50 border border-amber-200 text-amber-800 text-sm">
                        No capacity-source processes detected. Start the capacity source service (usually `proc`) or
                        align configured worker counts with the running service.
                    </div>
                ) : warnings.length > 0 && (
                    <div className="p-3 rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-sm">
                        {warnings.map((w, i) => (
                            <div key={i}>• {w}</div>
                        ))}
                    </div>
                )}

                {hasActual && actualProcesses > 0 && (
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Configured</div>
                            <div className="text-sm font-semibold">{configuredProcesses ?? '—'}</div>
                            <div className="text-xs text-gray-500">processes</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Actual</div>
                            <div className="text-sm font-semibold">{actualProcesses ?? '—'}</div>
                            <div className="text-xs text-gray-500">running</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Healthy</div>
                            <div className="text-sm font-semibold">{healthyProcesses ?? '—'}</div>
                            <div className="text-xs text-gray-500">{Math.round((health.process_health_ratio ?? 0) * 100)}% health</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Process Deficit</div>
                            <div className="text-sm font-semibold">{health.processes_vs_configured?.process_deficit ?? 0}</div>
                            <div className="text-xs text-gray-500">missing</div>
                        </div>
                    </div>
                )}

                {metrics.actual_runtime && metrics.configuration && actualProcesses > 0 && (
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

                {scaling && actualProcesses > 0 && (
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
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
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
                            <div className="text-xs text-gray-600">Paid Blocks At</div>
                            <div className="text-sm font-semibold">{thresholds.paid_blocks_at ?? '—'}</div>
                            <div className="text-xs text-gray-500">{thresholds.paid_percentage ?? '—'}%</div>
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

const LatencyTable: React.FC<{ title: string; data?: Record<string, any>; compact?: boolean; showMax?: boolean; className?: string }> = ({
    title,
    data,
    compact = false,
    showMax = true,
    className = '',
}) => {
    const windows = ["1m", "15m", "1h"] as const;
    const padding = compact ? 'p-3' : 'p-4';
    const titleClass = compact ? 'text-xs font-semibold mb-2' : 'text-sm font-semibold mb-2';
    if (!data) {
        return (
            <div className={`${padding} rounded-xl bg-gray-100 ${className}`}>
                <div className={titleClass}>{title}</div>
                <div className="text-xs text-gray-500">No samples yet.</div>
            </div>
        );
    }
    return (
        <div className={`${padding} rounded-xl bg-gray-100 ${className}`}>
            <div className={titleClass}>{title}</div>
            <div className="grid grid-cols-4 gap-2 text-[11px] text-gray-600">
                <div className="font-semibold">Window</div>
                <div className="font-semibold">p50</div>
                <div className="font-semibold">p95</div>
                <div className="font-semibold">p99</div>
                {windows.map((w) => (
                    <React.Fragment key={w}>
                        <div>{w}</div>
                        <div>{data?.[w]?.p50 ?? '—'}</div>
                        <div>{data?.[w]?.p95 ?? '—'}</div>
                        <div>{data?.[w]?.p99 ?? '—'}</div>
                    </React.Fragment>
                ))}
            </div>
            {showMax && (
                <div className="text-[11px] text-gray-500 mt-2">max (1h): {data?.["1h"]?.max ?? '—'} ms</div>
            )}
        </div>
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

const Legend: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <div className="text-[11px] text-gray-500 mb-3">Legend: {children}</div>
);

// =============================================================================
// App
// =============================================================================

type BurstSession = {
    token: string;
    streamId: string;
    role: 'admin' | 'registered' | 'paid';
    es: EventSource;
};

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
    const [selectedComponent, setSelectedComponent] = useState<'ingress' | 'proc'>('ingress');
    const [configJson, setConfigJson] = useState<string>('');
    const [validationResult, setValidationResult] = useState<any>(null);
    const [actionMessage, setActionMessage] = useState<string | null>(null);

    const [resetSessionId, setResetSessionId] = useState('');
    const [resetAllSessions, setResetAllSessions] = useState(false);
    const [resetRateLimits, setResetRateLimits] = useState(true);
    const [resetBackpressure, setResetBackpressure] = useState(true);
    const [resetThrottlingStats, setResetThrottlingStats] = useState(false);
    const [purgeChatQueues, setPurgeChatQueues] = useState(false);
    const [resettingThrottling, setResettingThrottling] = useState(false);
    const [resetThrottlingMessage, setResetThrottlingMessage] = useState<string | null>(null);
    const [clearCacheMessage, setClearCacheMessage] = useState<string | null>(null);

    const [burstUsers, setBurstUsers] = useState<BurstUsersResponse | null>(null);
    const [burstError, setBurstError] = useState<string | null>(null);
    const [burstStatus, setBurstStatus] = useState<string | null>(null);
    const [burstAdminCount, setBurstAdminCount] = useState('10');
    const [burstRegisteredCount, setBurstRegisteredCount] = useState('10');
    const [burstMessagesPerUser, setBurstMessagesPerUser] = useState('1');
    const [burstConcurrency, setBurstConcurrency] = useState('10');
    const [burstMessage, setBurstMessage] = useState('ping');
    const [burstBundleId, setBurstBundleId] = useState('');
    const [burstOpenCount, setBurstOpenCount] = useState(0);
    const [burstRunning, setBurstRunning] = useState(false);
    const burstSessionsRef = useRef<BurstSession[]>([]);

    const [plannerAdmins, setPlannerAdmins] = useState('10');
    const [plannerRegistered, setPlannerRegistered] = useState('15');
    const [plannerPaid, setPlannerPaid] = useState('15');
    const [plannerPageLoad, setPlannerPageLoad] = useState('12');
    const [plannerTabs, setPlannerTabs] = useState('10');
    const [plannerPageWindow, setPlannerPageWindow] = useState('10');
    const [plannerSafety, setPlannerSafety] = useState('1.5');
    const [plannerConcurrentPerProcess, setPlannerConcurrentPerProcess] = useState('5');
    const [plannerProcessesPerInstance, setPlannerProcessesPerInstance] = useState('1');
    const [plannerAvgProcessing, setPlannerAvgProcessing] = useState('25');
    const [plannerInstances, setPlannerInstances] = useState('1');
    const plannerInitializedRef = useRef(false);
    const gatewayCacheKeyPattern = `${tenant || '<tenant>'}:${project || '<project>'}:kdcube:config:gateway:current`;

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

    const loadBurstUsers = useCallback(async () => {
        try {
            const res = await api.getBurstUsers();
            setBurstUsers(res);
            setBurstError(null);
        } catch (e: any) {
            setBurstUsers(null);
            setBurstError(e?.message || 'Failed to load burst users');
        }
    }, [api]);

    useEffect(() => {
        let mounted = true;
        settings.setupParentListener().then(() => {
            if (mounted) {
                refreshAll();
                loadBurstUsers();
            }
        });
        return () => { mounted = false; };
    }, [refreshAll, loadBurstUsers]);

    useEffect(() => {
        if (!autoRefresh) return;
        const t = setInterval(() => refreshAll(), 5000);
        return () => clearInterval(t);
    }, [autoRefresh, refreshAll]);

    const queue = system?.queue_stats;
    const capacityCtx = system?.queue_stats?.capacity_context || {};
    const queueAnalytics = system?.queue_analytics;
    const queueUtilization = system?.queue_utilization;
    const throttling = system?.throttling_stats;
    const events = system?.recent_throttling_events || [];
    const lastThrottle = events.length ? events[0] : null;
    const gateway = system?.gateway_configuration;
    const throttlingByPeriod = system?.throttling_by_period || {};
    const throttlingWindows = system?.throttling_windows || {};
    const sseStats = system?.sse_connections;
    const components = system?.components || {};
    const autoscaler = system?.autoscaler || {};
    const configSource = system?.gateway_config_source || 'unknown';
    const configRaw = system?.gateway_config_raw;
    const configComponents = system?.gateway_config_components || {};
    const capacitySource =
        configRaw?.backpressure?.capacity_source_component
        || configComponents?.ingress?.backpressure?.capacity_source_component
        || configComponents?.proc?.backpressure?.capacity_source_component
        || configRaw?.capacity_source_component;
    const capacitySourceKey = useMemo(() => {
        const raw = (capacitySource || '').toLowerCase();
        if (raw.includes('proc')) return 'proc';
        if (raw.includes('rest') || raw.includes('ingress')) return 'ingress';
        if (raw.startsWith('chat:proc')) return 'proc';
        if (raw.startsWith('chat:rest')) return 'ingress';
        return raw || 'proc';
    }, [capacitySource]);
    const capacitySourceActual = components?.[capacitySourceKey]?.actual_processes;
    const capacitySourceHealthy = components?.[capacitySourceKey]?.healthy_processes;
    const plannerComponentKey = capacitySourceKey || 'proc';
    const poolAggregateEntries = useMemo(() => {
        return Object.entries(components)
            .map(([name, data]: [string, any]) => {
                const poolsAgg = data?.pools_aggregate;
                const pgUtil = poolsAgg?.postgres?.utilization_percent ?? 0;
                const redisUtil = poolsAgg?.redis?.async?.utilization_percent ?? 0;
                const sortKey = Math.max(pgUtil, redisUtil);
                return { name, data, poolsAgg, sortKey };
            })
            .sort((a, b) => (b.sortKey ?? -1) - (a.sortKey ?? -1));
    }, [components]);

    useEffect(() => {
        if (!system?.gateway_configuration) return;
        if (configRaw) {
            setConfigJson(JSON.stringify(configRaw, null, 2));
            return;
        }
        const compCfg = (configComponents && configComponents[selectedComponent]) || system.gateway_configuration;
        const sc = compCfg.service_capacity || {};
        const bp = compCfg.backpressure || compCfg.backpressure_settings || {};
        const payload = {
            tenant,
            project,
            component: selectedComponent,
            guarded_rest_patterns: compCfg.guarded_rest_patterns || [],
            bypass_throttling_patterns: compCfg.bypass_throttling_patterns || [],
            service_capacity: {
                concurrent_requests_per_process: sc.concurrent_requests_per_process ?? sc.concurrent_requests_per_instance ?? 5,
                processes_per_instance: sc.processes_per_instance ?? 1,
                avg_processing_time_seconds: sc.avg_processing_time_seconds ?? 25,
            },
            backpressure: {
                capacity_buffer: bp.capacity_buffer ?? 0.2,
                queue_depth_multiplier: bp.queue_depth_multiplier ?? 2.0,
                anonymous_pressure_threshold: bp.anonymous_pressure_threshold ?? 0.6,
                registered_pressure_threshold: bp.registered_pressure_threshold ?? 0.8,
                paid_pressure_threshold: bp.paid_pressure_threshold ?? 0.8,
                hard_limit_threshold: bp.hard_limit_threshold ?? 0.95,
            },
            rate_limits: compCfg.rate_limits || {},
        };
        setConfigJson(JSON.stringify(payload, null, 2));
    }, [system, tenant, project, selectedComponent, configComponents, configRaw]);

    useEffect(() => {
        plannerInitializedRef.current = false;
    }, [selectedComponent, system?.gateway_configuration]);

    useEffect(() => {
        if (plannerInitializedRef.current) return;
        if (!system) return;
        const compCfg = (configComponents && configComponents[plannerComponentKey]) || system.gateway_configuration;
        const sc = compCfg?.service_capacity || {};
        const instanceCount = components?.[plannerComponentKey]?.instance_count ?? system.queue_stats?.capacity_context?.instance_count ?? 1;
        setPlannerConcurrentPerProcess(String(sc.concurrent_requests_per_process ?? 5));
        setPlannerProcessesPerInstance(String(sc.processes_per_instance ?? 1));
        setPlannerAvgProcessing(String(sc.avg_processing_time_seconds ?? 25));
        setPlannerInstances(String(instanceCount));
        plannerInitializedRef.current = true;
    }, [system, selectedComponent, configComponents, components, plannerComponentKey]);

    const planner = useMemo(() => {
        const toNum = (value: string, fallback: number) => {
            const n = Number(value);
            return Number.isFinite(n) ? n : fallback;
        };
        const admins = toNum(plannerAdmins, 0);
        const registered = toNum(plannerRegistered, 0);
        const paid = toNum(plannerPaid, 0);
        const totalUsers = admins + registered + paid;
        const pageLoad = toNum(plannerPageLoad, 0);
        const maxTabs = Math.max(1, toNum(plannerTabs, 1));
        const windowSeconds = Math.max(1, toNum(plannerPageWindow, 10));
        const safety = Math.max(1.0, toNum(plannerSafety, 1.2));
        const concurrentPerProcess = Math.max(1, toNum(plannerConcurrentPerProcess, 1));
        const processesPerInstance = Math.max(1, toNum(plannerProcessesPerInstance, 1));
        const instances = Math.max(1, toNum(plannerInstances, 1));
        const avgSeconds = Math.max(1, toNum(plannerAvgProcessing, 25));

        const burstPerSession = pageLoad * maxTabs;
        const suggestedBurst = Math.ceil(burstPerSession * safety);

        const peakRps = windowSeconds > 0 ? (pageLoad * totalUsers) / windowSeconds : 0;
        const totalConcurrent = concurrentPerProcess * processesPerInstance * instances;
        const maxRps = avgSeconds > 0 ? totalConcurrent / avgSeconds : 0;
        const peakUtilization = maxRps > 0 ? peakRps / maxRps : 0;

        return {
            totalUsers,
            burstPerSession,
            suggestedBurst,
            peakRps,
            maxRps,
            peakUtilization,
            totalConcurrent,
            windowSeconds,
            concurrentPerProcess,
            processesPerInstance,
            avgSeconds,
            safety,
        };
    }, [
        plannerAdmins,
        plannerRegistered,
        plannerPaid,
        plannerPageLoad,
        plannerTabs,
        plannerPageWindow,
        plannerSafety,
        plannerConcurrentPerProcess,
        plannerProcessesPerInstance,
        plannerAvgProcessing,
        plannerInstances,
    ]);

    const recommendedConfigJson = useMemo(() => {
        const compCfg = (configComponents && configComponents[selectedComponent]) || gateway;
        const roleLimits = compCfg?.rate_limits || {};
        const recommendedBurst = Math.max(1, planner.suggestedBurst || 1);
        const windowSeconds = Math.max(1, Math.round(planner.windowSeconds || 60));
        const baseBackpressure = compCfg?.backpressure || compCfg?.backpressure_settings || {};
        const poolsCfg = compCfg?.pools || {};
        const limitsCfg = compCfg?.limits || {};
        const currentServiceCapacity = compCfg?.service_capacity || {};
        const usePlannerCapacity = selectedComponent === plannerComponentKey;
        const serviceCapacityPayload = usePlannerCapacity
            ? {
                concurrent_requests_per_process: Math.max(1, Math.round(planner.concurrentPerProcess || 1)),
                processes_per_instance: Math.max(1, Math.round(planner.processesPerInstance || 1)),
                avg_processing_time_seconds: Math.max(1, Math.round(planner.avgSeconds || 25)),
            }
            : {
                concurrent_requests_per_process: currentServiceCapacity.concurrent_requests_per_process ?? currentServiceCapacity.concurrent_requests_per_instance ?? 5,
                processes_per_instance: currentServiceCapacity.processes_per_instance ?? 1,
                avg_processing_time_seconds: currentServiceCapacity.avg_processing_time_seconds ?? 25,
            };
        const suggestedPgPoolMax = selectedComponent === 'proc'
            ? Math.max(1, Math.round(planner.concurrentPerProcess || 1))
            : (poolsCfg?.pg_pool_max_size ?? 4);
        const suggestedRedisMax = selectedComponent === 'proc'
            ? Math.max(20, Math.round((planner.concurrentPerProcess || 1) * 4))
            : (poolsCfg?.redis_max_connections ?? 20);
        const suggested = {
            tenant,
            project,
            component: selectedComponent,
            service_capacity: {
                ...serviceCapacityPayload,
            },
            backpressure: {
                capacity_buffer: baseBackpressure.capacity_buffer ?? 0.2,
                queue_depth_multiplier: baseBackpressure.queue_depth_multiplier ?? 2.0,
                anonymous_pressure_threshold: baseBackpressure.anonymous_pressure_threshold ?? 0.6,
                registered_pressure_threshold: baseBackpressure.registered_pressure_threshold ?? 0.8,
                paid_pressure_threshold: baseBackpressure.paid_pressure_threshold ?? 0.8,
                hard_limit_threshold: baseBackpressure.hard_limit_threshold ?? 0.95,
            },
            rate_limits: {
                roles: {
                    anonymous: {
                        hourly: roleLimits?.anonymous?.hourly ?? 120,
                        burst: roleLimits?.anonymous?.burst ?? 10,
                        burst_window: roleLimits?.anonymous?.burst_window ?? windowSeconds,
                    },
                    registered: {
                        hourly: roleLimits?.registered?.hourly ?? 600,
                        burst: recommendedBurst,
                        burst_window: windowSeconds,
                    },
                    paid: {
                        hourly: roleLimits?.paid?.hourly ?? 2000,
                        burst: recommendedBurst,
                        burst_window: windowSeconds,
                    },
                    privileged: {
                        hourly: roleLimits?.privileged?.hourly ?? -1,
                        burst: Math.max(recommendedBurst, roleLimits?.privileged?.burst ?? 200),
                        burst_window: windowSeconds,
                    },
                }
            },
            pools: {
                pg_pool_min_size: poolsCfg?.pg_pool_min_size ?? 0,
                pg_pool_max_size: suggestedPgPoolMax,
                redis_max_connections: suggestedRedisMax,
            },
            limits: selectedComponent === 'ingress'
                ? { max_sse_connections_per_instance: limitsCfg?.max_sse_connections_per_instance ?? 200 }
                : {
                    max_integrations_ops_concurrency: limitsCfg?.max_integrations_ops_concurrency ?? 200,
                    max_queue_size: limitsCfg?.max_queue_size ?? 0,
                },
        };
        return JSON.stringify(suggested, null, 2);
    }, [gateway, planner, tenant, project, selectedComponent, configComponents, plannerComponentKey]);

    const handleValidate = async () => {
        try {
            const payload = JSON.parse(configJson);
            if (!isRawGatewayConfigPayload(payload) && !payload.component) {
                payload.component = selectedComponent;
            }
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
            if (!isRawGatewayConfigPayload(payload) && !payload.component) {
                payload.component = selectedComponent;
            }
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

    const handleClearCache = async () => {
        try {
            const payload = { tenant, project };
            const res = await api.clearGatewayConfigCache(payload);
            const key = res?.result?.key;
            const deleted = res?.result?.deleted ?? 0;
            setClearCacheMessage(`Cleared cache key ${key || '(unknown)'} (deleted=${deleted}). Restart to re-apply env/GATEWAY_CONFIG_JSON.`);
        } catch (e: any) {
            setClearCacheMessage(e?.message || 'Clear cache failed');
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

    const handleResetThrottling = async () => {
        if (!resetRateLimits && !resetBackpressure && !resetThrottlingStats && !purgeChatQueues) {
            setResetThrottlingMessage('Select at least one reset option');
            return;
        }
        setResettingThrottling(true);
        setResetThrottlingMessage(null);
        try {
            const payload: any = {
                tenant,
                project,
                reset_rate_limits: resetRateLimits,
                reset_backpressure: resetBackpressure,
                reset_throttling_stats: resetThrottlingStats,
                purge_chat_queues: purgeChatQueues,
                all_sessions: resetAllSessions,
            };
            if (resetSessionId.trim()) {
                payload.session_id = resetSessionId.trim();
            }
            const res = await api.resetThrottling(payload);
            setResetThrottlingMessage(res?.message || 'Throttling reset');
            await refreshAll();
        } catch (e: any) {
            setResetThrottlingMessage(e?.message || 'Failed to reset throttling');
        } finally {
            setResettingThrottling(false);
        }
    };

    const closeBurstStreams = useCallback(() => {
        const sessions = burstSessionsRef.current || [];
        sessions.forEach((s) => {
            try { s.es.close(); } catch (_) { /* noop */ }
        });
        burstSessionsRef.current = [];
        setBurstOpenCount(0);
    }, []);

    const openBurstStreams = useCallback(async () => {
        if (!burstUsers?.users) {
            setBurstStatus('Burst users not loaded');
            return;
        }
        closeBurstStreams();

        const adminCount = Math.max(0, parseInt(burstAdminCount, 10) || 0);
        const regCount = Math.max(0, parseInt(burstRegisteredCount, 10) || 0);
        const admins = burstUsers.users.admin.slice(0, adminCount);
        const regs = burstUsers.users.registered.slice(0, regCount);

        const selected: Array<{ user: BurstUser; role: BurstSession['role'] }> = [
            ...admins.map((u) => ({ user: u, role: 'admin' as const })),
            ...regs.map((u) => ({ user: u, role: 'registered' as const })),
        ];

        if (!selected.length) {
            setBurstStatus('No users selected for SSE streams');
            return;
        }

        const baseUrl = settings.getBaseUrl().replace(/\/$/, '');
        const sessions: BurstSession[] = [];
        selected.forEach((entry, idx) => {
            const streamId = `burst-${entry.role}-${idx}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
            const url = new URL(`${baseUrl}/sse/stream`);
            url.searchParams.set('stream_id', streamId);
            url.searchParams.set('bearer_token', entry.user.token);
            if (tenant) url.searchParams.set('tenant', tenant);
            if (project) url.searchParams.set('project', project);
            const es = new EventSource(url.toString());
            es.addEventListener('error', () => {
                // keep simple: errors are visible in devtools
            });
            sessions.push({ token: entry.user.token, streamId, role: entry.role, es });
        });

        burstSessionsRef.current = sessions;
        setBurstOpenCount(sessions.length);
        setBurstStatus(`Opened ${sessions.length} SSE streams`);
    }, [burstUsers, burstAdminCount, burstRegisteredCount, closeBurstStreams, tenant, project]);

    const runWithConcurrency = async (tasks: Array<() => Promise<void>>, limit: number) => {
        let idx = 0;
        const safeLimit = Math.max(1, Math.min(limit, tasks.length || 1));
        const workers = new Array(safeLimit).fill(null).map(async () => {
            while (idx < tasks.length) {
                const current = idx++;
                await tasks[current]();
            }
        });
        await Promise.all(workers);
    };

    const sendBurstMessages = useCallback(async () => {
        const sessions = burstSessionsRef.current || [];
        if (!sessions.length) {
            setBurstStatus('No active SSE streams. Open streams first.');
            return;
        }
        const perUser = Math.max(1, parseInt(burstMessagesPerUser, 10) || 1);
        const concurrency = Math.max(1, parseInt(burstConcurrency, 10) || 10);
        const baseUrl = settings.getBaseUrl().replace(/\/$/, '');
        const payloadBase: any = { message: { text: burstMessage || 'ping' } };
        if (burstBundleId) payloadBase.message.bundle_id = burstBundleId;

        const tasks: Array<() => Promise<void>> = [];
        sessions.forEach((s) => {
            for (let i = 0; i < perUser; i++) {
                tasks.push(async () => {
                    const convId = `burst-${s.streamId}-${i}`;
                    const turnId = `turn_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
                    const payload = {
                        ...payloadBase,
                        message: {
                            ...(payloadBase.message || {}),
                            conversation_id: convId,
                            turn_id: turnId,
                        },
                    };
                    const res = await fetch(`${baseUrl}/sse/chat?stream_id=${encodeURIComponent(s.streamId)}`, {
                        method: 'POST',
                        headers: new Headers({
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${s.token}`,
                        }),
                        body: JSON.stringify(payload),
                    });
                    if (!res.ok) {
                        throw new Error(`chat ${res.status}`);
                    }
                });
            }
        });

        const startedAt = performance.now();
        setBurstRunning(true);
        setBurstStatus(`Sending ${tasks.length} messages…`);
        try {
            await runWithConcurrency(tasks, concurrency);
            const elapsed = Math.round(performance.now() - startedAt);
            setBurstStatus(`Burst complete: ${tasks.length} messages in ${elapsed}ms`);
        } catch (e: any) {
            setBurstStatus(`Burst error: ${e?.message || 'unknown error'}`);
        } finally {
            setBurstRunning(false);
        }
    }, [burstMessagesPerUser, burstConcurrency, burstMessage, burstBundleId]);

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
                        title="Tenant Summary"
                        subtitle={`Last update: ${lastUpdate || '—'}`}
                        action={gateway ? <Pill tone="success">{gateway.current_profile}</Pill> : null}
                    />
                    <CardBody>
                        <Legend>
                            Proc queue = backpressure queue depth; SSE = active ingress streams; Instances = heartbeat counts; throttled (1h) = 429/503 totals.
                        </Legend>
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Tenant / Project</div>
                                <div className="text-sm font-semibold">{configRaw?.tenant || gateway?.tenant_id || '—'}</div>
                                <div className="text-xs text-gray-500">{configRaw?.project || gateway?.display_name || '—'}</div>
                                <div className="text-[11px] text-gray-500">Config source: {configSource}</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Proc Queue</div>
                                <div className="text-sm font-semibold">{queue?.total ?? 0}</div>
                                <div className="text-xs text-gray-500">{Math.round((capacityCtx.pressure_ratio || 0) * 100)}% pressure</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Ingress SSE</div>
                                <div className="text-sm font-semibold">
                                    {sseStats?.global_total_connections ?? sseStats?.total_connections ?? 0}
                                    {typeof (sseStats?.global_max_connections ?? sseStats?.max_connections) === 'number'
                                        && (sseStats?.global_max_connections ?? sseStats?.max_connections) > 0
                                        ? ` / ${(sseStats?.global_max_connections ?? sseStats?.max_connections)}`
                                        : ''}
                                </div>
                                <div className="text-xs text-gray-500">sessions {sseStats?.global_sessions ?? sseStats?.sessions ?? 0}</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Instances</div>
                                <div className="text-sm font-semibold">
                                    ingress {components?.ingress?.instance_count ?? 0} · proc {components?.proc?.instance_count ?? 0}
                                </div>
                                <div className="text-xs text-gray-500">
                                    throttled (1h) {throttling?.total_throttled ?? 0} · {(throttling?.throttle_rate ?? 0).toFixed(1)}%
                                </div>
                            </div>
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Components & Autoscaler" subtitle="Ingress/proc health, capacity, and scaling signals." />
                    <CardBody>
                        <Legend>
                            Utilization = current / max; decision is autoscaler suggestion; windows are rolling 1m/15m/1h.
                        </Legend>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {(["ingress", "proc"] as const).map((comp) => {
                                const data = components?.[comp];
                                const auto = autoscaler?.[comp];
                                const decision = auto?.decision || 'hold';
                                const tone = decision === 'scale_up' ? 'danger' : decision === 'scale_down' ? 'warning' : 'success';
                                return (
                                    <div key={comp} className="p-4 rounded-xl bg-gray-100">
                                        <div className="flex items-center justify-between mb-2">
                                            <div className="text-sm font-semibold">{comp}</div>
                                            <Pill tone={tone}>{decision}</Pill>
                                        </div>
                                        {data ? (
                                            <div className="space-y-1 text-xs text-gray-600">
                                                <div>Instances: {data.instance_count ?? 0}</div>
                                                <div>
                                                    Processes: {data.healthy_processes ?? 0}/{data.actual_processes ?? 0}
                                                    {typeof data.expected_processes === 'number' ? ` (expected ${data.expected_processes})` : ''}
                                                </div>
                                                <div>Utilization: {data.utilization_percent ?? 0}%</div>
                                                {comp === 'ingress' && data.sse && (
                                                    <div>
                                                        SSE: {data.sse.total_connections ?? 0}
                                                        {data.sse.max_connections ? ` / ${data.sse.max_connections}` : ''}
                                                        {data.sse.utilization_percent ? ` (${data.sse.utilization_percent}%)` : ''}
                                                        {data.sse.windows && (
                                                            <div className="text-[11px] text-gray-500">
                                                                windows: 1m {data.sse.windows["1m"] ?? '—'} · 15m {data.sse.windows["15m"] ?? '—'} · 1h {data.sse.windows["1h"] ?? '—'} · max {data.sse.windows["max"] ?? '—'}
                                                            </div>
                                                        )}
                                                    </div>
                                                )}
                                                {comp === 'ingress' && (
                                                    <LatencyTable
                                                        title="Ingress REST latency (ms)"
                                                        data={data.latency?.rest_ms}
                                                        compact
                                                        className="mt-2"
                                                    />
                                                )}
                                                {comp === 'proc' && data.queue && (
                                                    <div>
                                                        Queue: {data.queue.total ?? 0} · pressure {(data.queue.pressure_ratio ?? 0).toFixed(2)}
                                                        {data.queue.windows && (
                                                            <div className="text-[11px] text-gray-500">
                                                                depth windows: 1m {data.queue.windows.depth?.["1m"] ?? '—'} · 15m {data.queue.windows.depth?.["15m"] ?? '—'} · 1h {data.queue.windows.depth?.["1h"] ?? '—'} · max {data.queue.windows.depth?.["max"] ?? '—'}
                                                                <br />
                                                                pressure windows: 1m {data.queue.windows.pressure_ratio?.["1m"] ?? '—'} · 15m {data.queue.windows.pressure_ratio?.["15m"] ?? '—'} · 1h {data.queue.windows.pressure_ratio?.["1h"] ?? '—'} · max {data.queue.windows.pressure_ratio?.["max"] ?? '—'}
                                                            </div>
                                                        )}
                                                        {data.latency && (
                                                            <div className="text-[11px] text-gray-500 mt-1">
                                                                Latency: see Latency card.
                                                            </div>
                                                        )}
                                                    </div>
                                                )}
                                                {data.pools && (
                                                    <div className="text-[11px] text-gray-500">
                                                        Pools: pg_max={data.pools.pg_pool_max_size ?? '—'} · redis_max={data.pools.redis_max_connections ?? '—'}
                                                        {data.pools.estimated_pg_total ? ` · est_pg_total=${data.pools.estimated_pg_total}` : ''}
                                                    </div>
                                                )}
                                                {auto?.reasons?.length ? (
                                                    <div className="text-[11px] text-gray-500">Reasons: {auto.reasons.join('; ')}</div>
                                                ) : (
                                                    <div className="text-[11px] text-gray-500">Reasons: none</div>
                                                )}
                                                {Array.isArray(data.instances) && data.instances.length > 0 && (
                                                    <div className="text-[11px] text-gray-500 mt-1">
                                                        <div className="mb-1">Instances:</div>
                                                        <div className="flex flex-wrap gap-2">
                                                            {data.instances.map((i: any) => {
                                                                const unhealthy = (i.healthy_processes ?? 0) < (i.processes ?? 0);
                                                                return (
                                                                    <span key={i.instance_id} className="flex items-center gap-1">
                                                                        <span>{i.instance_id}</span>
                                                                        {i.draining && <Pill tone="warning">draining</Pill>}
                                                                        {!i.draining && unhealthy && <Pill tone="danger">unhealthy</Pill>}
                                                                    </span>
                                                                );
                                                            })}
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        ) : (
                                            <div className="text-xs text-gray-500">No heartbeat data.</div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Latency (Rolling Windows)" subtitle="P50/P95/P99 in ms over 1m, 15m, 1h windows." />
                    <CardBody>
                        <Legend>
                            Windows are rolling; max = 1h high-water mark.
                        </Legend>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <LatencyTable title="Ingress REST" data={components?.ingress?.latency?.rest_ms} />
                            <LatencyTable title="Proc Queue Wait" data={components?.proc?.latency?.queue_wait_ms} />
                            <LatencyTable title="Proc Execution" data={components?.proc?.latency?.exec_ms} />
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Pools (Aggregated)" subtitle="Totals across all workers, sorted by utilization." />
                    <CardBody>
                        <Legend>
                            Reported = number of workers reporting; max in-use = 1h high-water mark; totals are aggregated across the component.
                        </Legend>
                        {poolAggregateEntries.length ? (
                            <div className="space-y-3">
                                {poolAggregateEntries.map(({ name, poolsAgg }) => {
                                    const pg = poolsAgg?.postgres || {};
                                    const rAsync = poolsAgg?.redis?.async || {};
                                    const rAsyncDecode = poolsAgg?.redis?.async_decode || {};
                                    const rSync = poolsAgg?.redis?.sync || {};
                                    const fmt = (val: any) => (val === null || val === undefined ? '—' : val);
                                    const fmtMaybeZero = (val: any, fallbackZero: boolean) => {
                                        if (val === null || val === undefined) {
                                            return fallbackZero ? 0 : '—';
                                        }
                                        return val;
                                    };
                                    const pgReported = pg.reported_processes ?? 0;
                                    const raReported = rAsync.reported_processes ?? 0;
                                    const radReported = rAsyncDecode.reported_processes ?? 0;
                                    const rsReported = rSync.reported_processes ?? 0;
                                    const windows = poolsAgg?.utilization_windows || {};
                                    const inUseWindows = poolsAgg?.in_use_windows || {};
                                    const fmtWindow = (w: any) => {
                                        if (!w) return '—';
                                        const w1m = w["1m"] ?? '—';
                                        const w15 = w["15m"] ?? '—';
                                        const w1h = w["1h"] ?? '—';
                                        const wMax = w["max"] ?? '—';
                                        return `1m ${w1m}% · 15m ${w15}% · 1h ${w1h}% · max ${wMax}%`;
                                    };
                                    const fmtInUseMax = (w: any) => {
                                        if (!w) return '—';
                                        return w["max"] ?? '—';
                                    };
                                    return (
                                        <div key={name} className="p-4 rounded-xl bg-gray-100">
                                            <div className="text-sm font-semibold mb-2">{name}</div>
                                            <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs text-gray-600">
                                                <div>
                                                    <div className="text-[11px] text-gray-500">PG</div>
                                                    <div className="text-sm font-semibold">
                                                        {pgReported ? `${fmtMaybeZero(pg.in_use_total, true)}/${fmt(pg.max_total ?? pg.size_total)}` : '—'}
                                                        {pgReported && pg.utilization_percent != null ? ` (${pg.utilization_percent}%)` : ''}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        reported {pgReported}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        {fmtWindow(windows.postgres)}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        max in-use (1h): {fmtInUseMax(inUseWindows.postgres)}
                                                    </div>
                                                </div>
                                                <div>
                                                    <div className="text-[11px] text-gray-500">Redis (async)</div>
                                                    <div className="text-sm font-semibold">
                                                        {raReported ? `${fmt(rAsync.in_use_total)}/${fmt(rAsync.max_total ?? rAsync.total_total)}` : '—'}
                                                        {raReported && rAsync.utilization_percent != null ? ` (${rAsync.utilization_percent}%)` : ''}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        reported {raReported}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        {fmtWindow(windows.redis_async)}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        max in-use (1h): {fmtInUseMax(inUseWindows.redis_async)}
                                                    </div>
                                                </div>
                                                <div>
                                                    <div className="text-[11px] text-gray-500">Redis (async decode)</div>
                                                    <div className="text-sm font-semibold">
                                                        {radReported ? `${fmt(rAsyncDecode.in_use_total)}/${fmt(rAsyncDecode.max_total ?? rAsyncDecode.total_total)}` : '—'}
                                                        {radReported && rAsyncDecode.utilization_percent != null ? ` (${rAsyncDecode.utilization_percent}%)` : ''}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        reported {radReported}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        {fmtWindow(windows.redis_async_decode)}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        max in-use (1h): {fmtInUseMax(inUseWindows.redis_async_decode)}
                                                    </div>
                                                </div>
                                                <div>
                                                    <div className="text-[11px] text-gray-500">Redis (sync)</div>
                                                    <div className="text-sm font-semibold">
                                                        {rsReported ? `${fmt(rSync.in_use_total)}/${fmt(rSync.max_total ?? rSync.total_total)}` : '—'}
                                                        {rsReported && rSync.utilization_percent != null ? ` (${rSync.utilization_percent}%)` : ''}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        reported {rsReported}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        {fmtWindow(windows.redis_sync)}
                                                    </div>
                                                    <div className="text-[11px] text-gray-500">
                                                        max in-use (1h): {fmtInUseMax(inUseWindows.redis_sync)}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        ) : (
                            <div className="text-xs text-gray-500">No pool data reported yet.</div>
                        )}
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Traffic (Requests)" subtitle="Totals and average per minute by period." />
                    <CardBody>
                        <Legend>
                            Periods are rolling windows; values show totals and averages per minute.
                        </Legend>
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
                        {Object.keys(throttlingWindows).length > 0 && (
                            <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4">
                                {["1m", "15m", "1h"].map((key) => {
                                    const win = throttlingWindows[key] || {};
                                    return (
                                        <div key={key} className="p-4 rounded-xl bg-gray-100">
                                            <div className="text-xs text-gray-600">{key} throttling</div>
                                            <div className="text-sm font-semibold">{win.total_throttled ?? 0}</div>
                                            <div className="text-xs text-gray-500">
                                                429 {win.rate_limit_429 ?? 0} · 503 {win.backpressure_503 ?? 0}
                                            </div>
                                            <div className="text-xs text-gray-500">
                                                {win.events_per_min != null ? `${win.events_per_min} / min` : '—'}
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Queues" subtitle="Current queue sizes and admission state." />
                    <CardBody>
                        <Legend>
                            Queue sizes are current backpressure queues; “accepting/blocked” is per-role admission status.
                        </Legend>
                        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
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
                                <div className="text-xs text-gray-600">Paid</div>
                                <div className="text-sm font-semibold">{queue?.paid ?? 0}</div>
                                <div className="text-xs text-gray-500">
                                    {(capacityCtx.accepting_paid ?? true) ? 'accepting' : 'blocked'}
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

                <Card>
                    <CardHeader title="Queue Analytics" subtitle="Average wait time and throughput (last hour)." />
                    <CardBody>
                        <Legend>
                            Analytics are rolling (last hour) across proc workers.
                        </Legend>
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                            {["anonymous", "registered", "paid", "privileged"].map((key) => {
                                const q = queueAnalytics?.individual_queues?.[key] || {};
                                const wait = q.avg_wait ?? 0;
                                const throughput = q.throughput ?? 0;
                                return (
                                    <div key={key} className="p-4 rounded-xl bg-gray-100">
                                        <div className="text-xs text-gray-600">{key}</div>
                                        <div className="text-sm font-semibold">{q.size ?? 0} queued</div>
                                        <div className="text-xs text-gray-500">avg wait {wait.toFixed(2)}s</div>
                                        <div className="text-xs text-gray-500">throughput {throughput}/hr</div>
                                        <div className="text-xs text-gray-500">{q.blocked ? 'blocked' : 'accepting'}</div>
                                    </div>
                                );
                            })}
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Utilization</div>
                                <div className="text-sm font-semibold">
                                    {typeof queueUtilization === 'number' ? `${queueUtilization.toFixed(1)}%` : '—'}
                                </div>
                                <div className="text-xs text-gray-500">queue / weighted capacity</div>
                            </div>
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Burst Simulator" subtitle="Dev-only load generator using SimpleIDP tokens." />
                    <CardBody className="space-y-4">
                        <Legend>
                            Uses SimpleIDP tokens to open SSE streams and send synthetic chat bursts.
                        </Legend>
                        {burstError && (
                            <div className="text-xs text-rose-700">{burstError}</div>
                        )}
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                            <Input label="Admin streams" value={burstAdminCount} onChange={(e) => setBurstAdminCount(e.target.value)} />
                            <Input label="Registered streams" value={burstRegisteredCount} onChange={(e) => setBurstRegisteredCount(e.target.value)} />
                            <Input label="Messages / user" value={burstMessagesPerUser} onChange={(e) => setBurstMessagesPerUser(e.target.value)} />
                            <Input label="Concurrency" value={burstConcurrency} onChange={(e) => setBurstConcurrency(e.target.value)} />
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <Input label="Message text" value={burstMessage} onChange={(e) => setBurstMessage(e.target.value)} />
                            <Input label="Bundle ID (optional)" value={burstBundleId} onChange={(e) => setBurstBundleId(e.target.value)} />
                        </div>
                        <div className="flex flex-wrap items-center gap-3">
                            <Button variant="secondary" onClick={loadBurstUsers}>Load tokens</Button>
                            <Button variant="secondary" onClick={openBurstStreams}>Open SSE</Button>
                            <Button variant="secondary" onClick={closeBurstStreams}>Close SSE</Button>
                            <Button onClick={sendBurstMessages} disabled={burstRunning}>Send chat burst</Button>
                            <span className="text-xs text-gray-600">
                                Open streams: {burstOpenCount}
                            </span>
                        </div>
                        {burstUsers ? (
                            <div className="text-xs text-gray-500">
                                Available tokens: admin {burstUsers.counts?.admin ?? 0}, registered {burstUsers.counts?.registered ?? 0}, paid {burstUsers.counts?.paid ?? 0}
                            </div>
                        ) : (
                            <div className="text-xs text-gray-500">
                                Enable with `MONITORING_BURST_ENABLE=1` and `AUTH_PROVIDER=simple`.
                            </div>
                        )}
                        {burstStatus && (
                            <div className="text-xs text-gray-600">{burstStatus}</div>
                        )}
                    </CardBody>
                </Card>

                <CapacityPanel
                    capacity={system?.capacity_transparency}
                    dbConnections={system?.db_connections}
                    capacitySource={capacitySource}
                    capacitySourceActual={capacitySourceActual}
                    capacitySourceHealthy={capacitySourceHealthy}
                />

                <Card>
                    <CardHeader
                        title="Capacity Planner (Rough)"
                        subtitle={`Estimate burst limits and compare expected peak traffic to capacity. Uses service_capacity for capacity source: ${plannerComponentKey}.`}
                    />
                    <CardBody className="space-y-4">
                        <Legend>
                            Rough sizing only; validate with real traffic and latency.
                        </Legend>
                        <div className="flex flex-wrap items-center gap-3 text-xs text-gray-600">
                            <label className="flex items-center gap-2">
                                Draft target
                                <select
                                    className="border border-gray-200 rounded px-2 py-1 text-xs"
                                    value={selectedComponent}
                                    onChange={(e) => setSelectedComponent(e.target.value as 'ingress' | 'proc')}
                                >
                                    <option value="ingress">ingress</option>
                                    <option value="proc">proc</option>
                                </select>
                            </label>
                            <span>Config source: {configSource}</span>
                        </div>
                        <div className="text-xs text-gray-500">
                            {`Source: GATEWAY_CONFIG_JSON.service_capacity.${plannerComponentKey} (or admin update). Assumes all instances in the selected tenant/project share the same config.`}
                        </div>
                        {selectedComponent !== plannerComponentKey && (
                            <div className="text-xs text-amber-700">
                                Planner is anchored to capacity source <span className="font-semibold">{plannerComponentKey}</span>. Updating
                                <span className="font-semibold"> {selectedComponent}</span> will keep its current service_capacity and only
                                apply rate limits/limits for that component.
                            </div>
                        )}
                        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
                            <Input label="Admins" value={plannerAdmins} onChange={(e) => setPlannerAdmins(e.target.value)} />
                            <Input label="Registered" value={plannerRegistered} onChange={(e) => setPlannerRegistered(e.target.value)} />
                            <Input label="Paid" value={plannerPaid} onChange={(e) => setPlannerPaid(e.target.value)} />
                            <Input label="Page-load requests" value={plannerPageLoad} onChange={(e) => setPlannerPageLoad(e.target.value)} />
                            <Input label="Max tabs / session" value={plannerTabs} onChange={(e) => setPlannerTabs(e.target.value)} />
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
                            <Input label="Page-load window (s)" value={plannerPageWindow} onChange={(e) => setPlannerPageWindow(e.target.value)} />
                            <Input label="Safety factor" value={plannerSafety} onChange={(e) => setPlannerSafety(e.target.value)} />
                            <Input label={`Concurrent / processor (${plannerComponentKey}.service_capacity.concurrent_requests_per_process)`} value={plannerConcurrentPerProcess} onChange={(e) => setPlannerConcurrentPerProcess(e.target.value)} />
                            <Input label={`Workers / instance (${plannerComponentKey}.service_capacity.processes_per_instance)`} value={plannerProcessesPerInstance} onChange={(e) => setPlannerProcessesPerInstance(e.target.value)} />
                            <Input label="Instances" value={plannerInstances} onChange={(e) => setPlannerInstances(e.target.value)} />
                            <Input label="Avg processing (s)" value={plannerAvgProcessing} onChange={(e) => setPlannerAvgProcessing(e.target.value)} />
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Total users</div>
                                <div className="text-sm font-semibold">{planner.totalUsers}</div>
                                <div className="text-xs text-gray-500">admins + registered + paid</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Burst / session (min)</div>
                                <div className="text-sm font-semibold">{planner.burstPerSession}</div>
                                <div className="text-xs text-gray-500">page-load × tabs</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Suggested burst</div>
                                <div className="text-sm font-semibold">{planner.suggestedBurst}</div>
                                <div className="text-xs text-gray-500">with safety factor</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Peak RPS</div>
                                <div className="text-sm font-semibold">{planner.peakRps.toFixed(1)}</div>
                                <div className="text-xs text-gray-500">page-load surge</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Max RPS</div>
                                <div className="text-sm font-semibold">{planner.maxRps.toFixed(1)}</div>
                                <div className="text-xs text-gray-500">capacity estimate</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Peak utilization</div>
                                <div className="text-sm font-semibold">
                                    {(planner.peakUtilization * 100).toFixed(1)}%
                                </div>
                                <div className="text-xs text-gray-500">
                                    {planner.peakUtilization > 1 ? 'over capacity' : 'ok'}
                                </div>
                            </div>
                        </div>
                        <div className="text-[11px] text-gray-500">
                            Suggested burst is a per-session value. Set it per role in the config JSON under `rate_limits`.
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader
                        title={`Recommended Config Draft (${selectedComponent})`}
                        subtitle="Computed from the planner inputs. Copy into Gateway Configuration if desired."
                    />
                    <CardBody className="space-y-3">
                        <Legend>
                            Draft is component-scoped and preserves current hourly limits.
                        </Legend>
                        {selectedComponent !== plannerComponentKey && (
                            <div className="text-xs text-amber-700">
                                Service capacity stays anchored to <span className="font-semibold">{plannerComponentKey}</span>. This draft
                                only changes rate limits/limits for <span className="font-semibold">{selectedComponent}</span>.
                            </div>
                        )}
                        <TextArea value={recommendedConfigJson} onChange={() => { /* read-only */ }} />
                        <div className="text-[11px] text-gray-500">
                            This draft keeps current hourly limits, updates burst/burst_window, and mirrors the planner’s service capacity values.
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Circuit Breakers" subtitle="Live circuit states and resets." />
                    <CardBody>
                        <Legend>
                            States and counters are aggregated per circuit; reset clears the circuit’s rolling failure window.
                        </Legend>
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
                    <CardHeader title="Reset Throttling / Backpressure" subtitle="Clear rate-limit counters and backpressure slots." />
                    <CardBody className="space-y-3">
                        <Legend>
                            Actions apply to the selected tenant/project. “All sessions” clears all rate-limit keys.
                        </Legend>
                        <div className="text-xs text-gray-600">
                            Active scope: <span className="font-semibold">{tenant || '—'}</span> / <span className="font-semibold">{project || '—'}</span>
                        </div>
                        <div className="text-[11px] text-gray-500">
                            Affected keys:
                            <div className="font-mono break-all">
                                {tenant && project ? `${tenant}:${project}:kdcube:system:ratelimit:<session_id>` : '<tenant>:<project>:kdcube:system:ratelimit:<session_id>'}
                            </div>
                            <div className="font-mono break-all">
                                {tenant && project ? `${tenant}:${project}:kdcube:system:capacity:counter` : '<tenant>:<project>:kdcube:system:capacity:counter'}
                            </div>
                            <div className="font-mono break-all">
                                {tenant && project ? `${tenant}:${project}:kdcube:throttling:*` : '<tenant>:<project>:kdcube:throttling:*'}
                            </div>
                            <div className="font-mono break-all">
                                {tenant && project ? `${tenant}:${project}:kdcube:chat:prompt:queue:*` : '<tenant>:<project>:kdcube:chat:prompt:queue:*'}
                            </div>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <Input
                                label="Session ID (optional)"
                                value={resetSessionId}
                                onChange={(e) => setResetSessionId(e.target.value)}
                                placeholder="defaults to current session"
                            />
                            <div className="flex items-end">
                                <label className="text-xs text-gray-600 flex items-center gap-2">
                                    <input
                                        type="checkbox"
                                        checked={resetAllSessions}
                                        onChange={(e) => setResetAllSessions(e.target.checked)}
                                    />
                                    All sessions (danger)
                                </label>
                            </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-4">
                            <label className="text-xs text-gray-600 flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={resetRateLimits}
                                    onChange={(e) => setResetRateLimits(e.target.checked)}
                                />
                                Reset rate limits
                            </label>
                            <label className="text-xs text-gray-600 flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={resetBackpressure}
                                    onChange={(e) => setResetBackpressure(e.target.checked)}
                                />
                                Reset backpressure counters
                            </label>
                            <label className="text-xs text-gray-600 flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={resetThrottlingStats}
                                    onChange={(e) => setResetThrottlingStats(e.target.checked)}
                                />
                                Clear throttling stats
                            </label>
                            <label className="text-xs text-gray-600 flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={purgeChatQueues}
                                    onChange={(e) => setPurgeChatQueues(e.target.checked)}
                                />
                                Purge chat queues (drops pending tasks)
                            </label>
                        </div>
                        {(resetAllSessions || purgeChatQueues) && (
                            <div className="text-xs text-rose-700">
                                {resetAllSessions ? 'Warning: clears rate limits for all sessions in this tenant/project.' : ''}
                                {resetAllSessions && purgeChatQueues ? ' ' : ''}
                                {purgeChatQueues ? 'Warning: purging queues drops pending chat tasks.' : ''}
                            </div>
                        )}
                        <div className="flex flex-wrap items-center gap-3">
                            <Button
                                variant="danger"
                                onClick={handleResetThrottling}
                                disabled={resettingThrottling}
                            >
                                Reset
                            </Button>
                            {resetThrottlingMessage && (
                                <span className="text-xs text-gray-600">{resetThrottlingMessage}</span>
                            )}
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Gateway Configuration" subtitle="View, validate, update, or reset config." />
                    <CardBody className="space-y-4">
                        <Legend>
                            Updates are stored in Redis cache and broadcast to live replicas for this tenant/project.
                        </Legend>
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

                        <div className="flex flex-wrap items-center gap-3 text-xs text-gray-600">
                            <span>Config source: {configSource}</span>
                        </div>

                        {configRaw && (
                            <TextArea
                                label="Current Gateway Config (read-only)"
                                value={JSON.stringify(configRaw, null, 2)}
                                onChange={() => {}}
                            />
                        )}

                        <TextArea label="Gateway Config JSON (editable)" value={configJson} onChange={(e) => setConfigJson(e.target.value)} />

                        <div className="flex flex-wrap gap-3">
                            <Button variant="secondary" onClick={handleValidate}>Validate</Button>
                            <Button onClick={handleUpdate}>Update</Button>
                            <Button variant="danger" onClick={handleReset}>Reset to Env</Button>
                            {actionMessage && <span className="text-sm text-gray-600">{actionMessage}</span>}
                        </div>

                        <div className="text-xs text-amber-700">
                            Note: changing `service_capacity.processes_per_instance` requires a service restart to affect worker count.
                        </div>
                        <div className="text-xs text-gray-600">
                            Updates are persisted in the tenant/project cache and broadcast to running replicas.
                            Paste the full `GATEWAY_CONFIG_JSON` to replace the cached config.
                        </div>

                        <div className="flex flex-wrap items-center gap-3">
                            <Button variant="secondary" onClick={handleClearCache}>Clear Cached Config</Button>
                            <span className="text-xs text-gray-500">Cache key: {gatewayCacheKeyPattern}</span>
                            {clearCacheMessage && <span className="text-xs text-gray-600">{clearCacheMessage}</span>}
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
                        <Legend>
                            Counts are for the last hour. Events list the most recent throttles (429/503).
                        </Legend>
                        {lastThrottle && (
                            <div className="mb-4 p-3 rounded-xl bg-amber-50 border border-amber-200 text-amber-900 text-xs">
                                <div className="font-semibold">Latest throttle</div>
                                <div>reason: {lastThrottle.reason}</div>
                                <div>endpoint: {lastThrottle.endpoint || '—'}</div>
                                <div>user_type: {lastThrottle.user_type || '—'} · status: {lastThrottle.http_status || '—'}</div>
                                {lastThrottle.retry_after ? (
                                    <div>retry_after: {lastThrottle.retry_after}s</div>
                                ) : null}
                            </div>
                        )}
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
                                    <div className="text-gray-500">{e.endpoint || '—'}</div>
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
