import React, { useEffect, useMemo, useRef, useState } from 'react';
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
    hostBundlesPath: string;
    bundlesRoot: string;
}

interface Scope {
    tenant?: string;
    project?: string;
}

interface TenantProjectItem {
    tenant: string;
    project: string;
    schema?: string;
    source?: string;
}

interface BundleAPIEndpoint {
    alias: string;
    http_method: string;
    route: string;
    user_types: string[];
    user_types_default?: string[];
    user_types_path?: string | null;
    user_types_overridden?: boolean;
    roles: string[];
    roles_default?: string[];
    roles_path?: string | null;
    roles_overridden?: boolean;
    auth?: Record<string, any> | null;
    auth_path?: string | null;
    authority_id?: string | null;
    grants?: string[] | null;
    enabled_path?: string | null;
}

interface BundleMCPEndpoint {
    alias: string;
    route: string;
    transport: string;
    transport_default?: string;
    transport_config?: string | null;
    transport_overridden?: boolean;
    auth?: Record<string, any> | null;
    auth_path?: string | null;
    authority_id?: string | null;
    grants?: string[] | null;
    enabled_path?: string | null;
}

interface BundleWidget {
    alias: string;
    icon?: Record<string, string> | null;
    user_types: string[];
    user_types_default?: string[];
    user_types_path?: string | null;
    user_types_overridden?: boolean;
    roles: string[];
    roles_default?: string[];
    roles_path?: string | null;
    roles_overridden?: boolean;
    auth?: Record<string, any> | null;
    auth_path?: string | null;
    authority_id?: string | null;
    grants?: string[] | null;
    enabled_path?: string | null;
}

interface BundleScheduledJob {
    method_name: string;
    alias?: string | null;
    cron_expression?: string | null;
    cron_expression_default?: string | null;
    expr_config?: string | null;
    cron_expression_overridden?: boolean;
    timezone?: string | null;
    timezone_default?: string | null;
    tz_config?: string | null;
    timezone_overridden?: boolean;
    span?: string | null;
    enabled_path?: string | null;
}

interface BundleEntry {
    id: string;
    name?: string | null;
    path: string;
    module?: string | null;
    singleton?: boolean | null;
    description?: string | null;
    version?: string | null;
    repo?: string | null;
    ref?: string | null;
    subdir?: string | null;
    git_commit?: string | null;
    apis?: BundleAPIEndpoint[] | null;
    mcp_endpoints?: BundleMCPEndpoint[] | null;
    widgets?: BundleWidget[] | null;
    scheduled_jobs?: BundleScheduledJob[] | null;
    on_message?: string | null;
    on_job?: string | null;
    enabled_path?: string | null;
    allowed_roles?: string[] | null;
    allowed_roles_default?: string[] | null;
    allowed_roles_path?: string | null;
    allowed_roles_overridden?: boolean;
}

interface BundlesResponse {
    available_bundles: Record<string, BundleEntry>;
    default_bundle_id?: string | null;
    tenant?: string;
    project?: string;
    authority?: AuthorityDescriptor | null;
}

interface AuthorityDescriptor {
    kind: string;
    label: string;
    description?: string | null;
    detail?: string | null;
}

interface BundlesUpdatePayload {
    op: 'merge' | 'replace';
    bundles: Record<string, BundleEntry>;
    default_bundle_id?: string | null;
    tenant?: string;
    project?: string;
}

interface BundlePropsPayload {
    tenant?: string;
    project?: string;
    op: 'replace' | 'merge';
    props: Record<string, unknown>;
}

interface BundleDefaultsError {
    code?: string;
    message?: string;
    where?: string;
    bundle_id?: string;
    managed?: boolean;
}

interface BundleCleanupPayload {
    drop_sys_modules: boolean;
    tenant?: string;
    project?: string;
}

interface BundleReloadAuthorityPayload {
    tenant?: string;
    project?: string;
    bundle_id?: string;
}

interface BundleSecretsPayload {
    tenant?: string;
    project?: string;
    mode?: 'set' | 'clear';
    secrets: Record<string, unknown>;
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
    private readonly PLACEHOLDER_HOST_BUNDLES_PATH = '{{' + 'HOST_BUNDLES_PATH' + '}}';
    private readonly PLACEHOLDER_BUNDLES_ROOT = '{{' + 'BUNDLES_ROOT' + '}}';

    private settings: AppSettings = {
        baseUrl: '{{CHAT_BASE_URL}}',
        accessToken: '{{ACCESS_TOKEN}}',
        idToken: '{{ID_TOKEN}}',
        idTokenHeader: '{{ID_TOKEN_HEADER}}',
        defaultTenant: '{{DEFAULT_TENANT}}',
        defaultProject: '{{DEFAULT_PROJECT}}',
        defaultAppBundleId: '{{DEFAULT_APP_BUNDLE_ID}}',
        hostBundlesPath: '{{HOST_BUNDLES_PATH}}',
        bundlesRoot: '{{BUNDLES_ROOT}}'
    };

    private configReceivedCallback: (() => void) | null = null;

    getBaseUrl(): string {
        if (this.settings.baseUrl === this.PLACEHOLDER_BASE_URL) {
            return window.location.origin;
        }
        try {
            const url = new URL(this.settings.baseUrl);
            if (url.port === 'None' || url.hostname.includes('None')) {
                return window.location.origin;
            }
            const trimmed = this.settings.baseUrl.replace(/\/+$/, '');
            return trimmed.endsWith('/api') ? trimmed.slice(0, -4) : trimmed;
        } catch {
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

    getHostBundlesPath(): string {
        return this.settings.hostBundlesPath === this.PLACEHOLDER_HOST_BUNDLES_PATH
            ? ''
            : this.settings.hostBundlesPath;
    }

    getBundlesRoot(): string {
        return this.settings.bundlesRoot === this.PLACEHOLDER_BUNDLES_ROOT
            ? ''
            : this.settings.bundlesRoot;
    }

    updateSettings(partial: Partial<AppSettings>): void {
        this.settings = { ...this.settings, ...partial };
    }

    hasPlaceholderSettings(): boolean {
        return this.settings.baseUrl === this.PLACEHOLDER_BASE_URL;
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
        if (config.hostBundlesPath) updates.hostBundlesPath = config.hostBundlesPath;
        if (config.bundlesRoot) updates.bundlesRoot = config.bundlesRoot;
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
        const identity = "INTEGRATIONS_BUNDLES_ADMIN";

        window.addEventListener('message', (event: MessageEvent) => {
            if (event.data.type === 'CONN_RESPONSE' || event.data.type === 'CONFIG_RESPONSE') {
                const requestedIdentity = event.data.identity;
                if (requestedIdentity !== identity) {
                    return;
                }

                if (event.data.config) this.applyRuntimeConfig(event.data.config);
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
                                'defaultTenant', 'defaultProject', 'defaultAppBundleId',
                                'hostBundlesPath', 'bundlesRoot'
                            ],
                            identity: identity
                        }
                    }, '*');
                    const timeout = window.setTimeout(() => finish(false), 3000);
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

// =============================================================================
// Auth Helpers
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

function normalizeScope(tenant: string, project: string): Scope {
    const t = (tenant || '').trim();
    const p = (project || '').trim();
    return {
        tenant: t || undefined,
        project: p || undefined
    };
}

function formatScopeLabel(tenant?: string, project?: string): string {
    const t = (tenant || '').trim();
    const p = (project || '').trim();
    if (t && p) return `${t} / ${p}`;
    if (t) return t;
    if (p) return p;
    return '';
}

function parseScopeValue(value: string): Scope {
    const raw = (value || '').trim();
    if (!raw) return {};
    let tenant = raw;
    let project = '';
    if (raw.includes('::')) {
        [tenant, project] = raw.split('::', 2);
    } else if (raw.includes('/')) {
        [tenant, project] = raw.split('/', 2);
    }
    return normalizeScope((tenant || '').trim(), (project || '').trim());
}

function buildScopeParams(scope?: Scope): string {
    if (!scope) return '';
    const params = new URLSearchParams();
    if (scope.tenant) params.set('tenant', scope.tenant);
    if (scope.project) params.set('project', scope.project);
    const query = params.toString();
    return query ? `?${query}` : '';
}

function withScope<T extends Record<string, unknown>>(payload: T, scope?: Scope): T & Scope {
    const out: Record<string, unknown> = { ...payload };
    if (scope?.tenant && out.tenant === undefined) {
        out.tenant = scope.tenant;
    }
    if (scope?.project && out.project === undefined) {
        out.project = scope.project;
    }
    return out as T & Scope;
}

// =============================================================================
// Integrations API Client
// =============================================================================

class IntegrationsAPI {
    constructor(private basePath: string = '/admin/integrations') {}

    private buildUrl(path: string): string {
        return `${settings.getBaseUrl()}${this.basePath}${path}`;
    }

    private async fetchWithAuth(url: string, options: RequestInit = {}): Promise<Response> {
        const headers = makeAuthHeaders(options.headers);
        const response = await fetch(url, { ...options, headers });
        if (!response.ok) {
            const errorText = await response.text().catch(() => response.statusText);
            throw new Error(`API request failed: ${response.status} - ${errorText}`);
        }
        return response;
    }

    async listTenantProjects(): Promise<TenantProjectItem[]> {
        const response = await this.fetchWithAuth(
            `${settings.getBaseUrl()}/api/admin/control-plane/conversations/tenant-projects`
        );
        const data = await response.json();
        return data.items || [];
    }

    async listBundles(scope?: Scope): Promise<BundlesResponse> {
        const response = await this.fetchWithAuth(this.buildUrl(`/bundles${buildScopeParams(scope)}`));
        return response.json();
    }

    async updateBundles(payload: BundlesUpdatePayload, scope?: Scope): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl('/bundles'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(withScope(payload, scope))
            }
        );
        return response.json();
    }

    async reloadFromAuthority(scope?: Scope, bundleId?: string): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl('/bundles/reload-authority'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(withScope({
                    ...(bundleId ? { bundle_id: bundleId } : {})
                } as BundleReloadAuthorityPayload, scope))
            }
        );
        return response.json();
    }

    async cleanupBundles(payload: BundleCleanupPayload, scope?: Scope): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl('/bundles/cleanup'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(withScope(payload, scope))
            }
        );
        return response.json();
    }

    async getBundleProps(bundleId: string, scope?: Scope): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl(`/bundles/${encodeURIComponent(bundleId)}/props${buildScopeParams(scope)}`)
        );
        return response.json();
    }

    async setBundleProps(bundleId: string, payload: BundlePropsPayload, scope?: Scope): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl(`/bundles/${encodeURIComponent(bundleId)}/props`),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(withScope(payload, scope))
            }
        );
        return response.json();
    }

    async resetBundlePropsFromCode(bundleId: string, scope?: Scope): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl(`/bundles/${encodeURIComponent(bundleId)}/props/reset-code`),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(withScope({}, scope))
            }
        );
        return response.json();
    }

    async setBundleSecrets(bundleId: string, payload: BundleSecretsPayload, scope?: Scope): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl(`/bundles/${encodeURIComponent(bundleId)}/secrets`),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(withScope(payload, scope))
            }
        );
        return response.json();
    }

    async getBundleSecrets(bundleId: string, scope?: Scope): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl(`/bundles/${encodeURIComponent(bundleId)}/secrets${buildScopeParams(scope)}`)
        );
        return response.json();
    }
}

const api = new IntegrationsAPI();

// =============================================================================
// UI Components
// =============================================================================

const Card: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => (
    <div className={`bg-white rounded-2xl shadow-sm border border-gray-200/70 ${className}`}>{children}</div>
);

const OverridableValue: React.FC<{
    value: string | string[] | null | undefined;
    defaultValue?: string | string[] | null;
    overridden?: boolean;
}> = ({ value, defaultValue, overridden }) => {
    const display = Array.isArray(value)
        ? (value.length ? value.join(', ') : '—')
        : (value || '—');
    const defaultDisplay = Array.isArray(defaultValue)
        ? (defaultValue.length ? defaultValue.join(', ') : '—')
        : (defaultValue || '—');
    const title = overridden ? `default: ${defaultDisplay}` : undefined;
    return (
        <span className="inline-flex items-center gap-1.5" title={title}>
            <span>{display}</span>
            {overridden && (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-50 border border-amber-200 text-amber-800 text-[10px] font-semibold uppercase tracking-wide">
                    overridden
                </span>
            )}
        </span>
    );
};

// =============================================================================
// Override editing helpers
// =============================================================================

// Build a nested patch object from a regular dot-path: "a.b.c" -> {a:{b:{c:value}}}
function nestedDotPathPatch(path: string, value: unknown): Record<string, any> {
    const parts = path.split('.').filter(Boolean);
    if (parts.length === 0) return {};
    const root: Record<string, any> = {};
    let cursor: Record<string, any> = root;
    for (let i = 0; i < parts.length - 1; i++) {
        cursor[parts[i]] = {};
        cursor = cursor[parts[i]];
    }
    cursor[parts[parts.length - 1]] = value;
    return root;
}

// Read a value at a regular dot-path; returns undefined when the path is absent.
function readDotPath(obj: any, path: string | null | undefined): any {
    if (!path) return undefined;
    const parts = path.split('.').filter(Boolean);
    let cursor: any = obj;
    for (const p of parts) {
        if (cursor == null || typeof cursor !== 'object') return undefined;
        cursor = cursor[p];
    }
    return cursor;
}

// enabled.api uses a flat route-aware key for "<route>.<alias>.<METHOD>".
function apiEnabledKey(spec: { route?: string | null; alias: string; http_method: string }): string {
    return `${spec.route || 'operations'}.${spec.alias}.${spec.http_method}`;
}

// Legacy API enable keys did not include route. Keep read fallback only; writes
// always use route-aware keys so public and operations aliases can coexist.
function legacyApiEnabledKey(spec: { alias: string; http_method: string }): string {
    return `${spec.alias}.${spec.http_method}`;
}

function buildEnabledApiPatch(spec: { route?: string | null; alias: string; http_method: string }, value: unknown): Record<string, any> {
    return { enabled: { api: { [apiEnabledKey(spec)]: value } } };
}

// enabled.<kind>.<alias> for non-api kinds — alias is a single map key.
function buildEnabledKindPatch(kind: 'mcp' | 'widget' | 'cron' | 'bundle', alias: string | null, value: unknown): Record<string, any> {
    if (kind === 'bundle') return { enabled: { bundle: value } };
    return { enabled: { [kind]: { [alias as string]: value } } };
}

function buildProviderAuthPatch(
    kind: 'api' | 'widget' | 'mcp',
    spec: { route?: string | null; alias: string; http_method?: string | null },
    value: unknown,
): Record<string, any> {
    return buildProviderSurfaceSectionPatch(kind, spec, 'auth', value);
}

function buildProviderVisibilityFieldPatch(
    kind: 'api' | 'widget',
    spec: { route?: string | null; alias: string; http_method?: string | null },
    field: 'user_types' | 'roles',
    value: unknown,
): Record<string, any> {
    return buildProviderSurfaceSectionPatch(kind, spec, 'visibility', { [field]: value });
}

function buildBundleAllowedRolesPatch(value: unknown): Record<string, any> {
    return {
        surfaces: {
            as_provider: {
                bundle: {
                    visibility: {
                        allowed_roles: value,
                    },
                },
            },
        },
    };
}

function buildProviderSurfaceSectionPatch(
    kind: 'api' | 'widget' | 'mcp',
    spec: { route?: string | null; alias: string; http_method?: string | null },
    section: 'auth' | 'visibility',
    value: unknown,
): Record<string, any> {
    if (kind === 'api') {
        return {
            surfaces: {
                as_provider: {
                    api: {
                        [spec.route || 'operations']: {
                            [spec.alias]: {
                                [spec.http_method || 'POST']: {
                                    [section]: value,
                                },
                            },
                        },
                    },
                },
            },
        };
    }
    return {
        surfaces: {
            as_provider: {
                [kind]: {
                    [spec.alias]: {
                        [section]: value,
                    },
                },
            },
        },
    };
}

// Deep-merge two record trees (used to combine multiple per-field patches into one Save call).
function deepMergeMaps(a: Record<string, any>, b: Record<string, any>): Record<string, any> {
    const out: Record<string, any> = { ...a };
    for (const [k, v] of Object.entries(b)) {
        if (v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) {
            out[k] = deepMergeMaps(out[k], v);
        } else {
            out[k] = v;
        }
    }
    return out;
}

// Parse comma-separated string into a tuple of trimmed non-empty strings.
function parseChips(s: string): string[] {
    return s.split(',').map(x => x.trim()).filter(Boolean);
}

const KNOWN_USER_TYPES: ReadonlyArray<string> = ['anonymous', 'registered', 'paid', 'privileged'];
const KNOWN_TRANSPORTS: ReadonlyArray<string> = ['streamable-http'];

// Pill that renders next to a field name to flag whether the field is currently
// overridden, descriptor-configurable but not overridden, or hard-coded.
const FieldStatePill: React.FC<{ overridden?: boolean; configurable: boolean }> = ({ overridden, configurable }) => {
    if (overridden) {
        return <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-50 border border-amber-200 text-amber-800 text-[10px] font-semibold uppercase tracking-wide">overridden</span>;
    }
    if (configurable) {
        return <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-800 text-[10px] font-semibold uppercase tracking-wide">configurable</span>;
    }
    return <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-gray-100 border border-gray-200 text-gray-500 text-[10px] font-semibold uppercase tracking-wide">hard-coded</span>;
};

const FieldHint: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <p className="mt-1 text-[11px] text-gray-500">{children}</p>
);

const FieldRow: React.FC<{
    label: string;
    overridden?: boolean;
    configurable?: boolean;
    onResetToDefault?: () => void;
    children: React.ReactNode;
    hint?: React.ReactNode;
}> = ({ label, overridden, configurable, onResetToDefault, children, hint }) => (
    <div>
        <div className="flex items-center justify-between mb-1.5">
            <label className="block text-sm font-medium text-gray-800">
                <span className="mr-2">{label}</span>
                <FieldStatePill overridden={overridden} configurable={configurable === true} />
            </label>
            {onResetToDefault && (
                <button
                    type="button"
                    onClick={onResetToDefault}
                    className="text-[11px] text-gray-500 hover:text-gray-700 cursor-pointer underline-offset-2 hover:underline"
                >
                    Reset to default
                </button>
            )}
        </div>
        {children}
        {hint && <FieldHint>{hint}</FieldHint>}
    </div>
);

const UserTypesEditor: React.FC<{
    value: string[];
    disabled?: boolean;
    onChange: (value: string[]) => void;
}> = ({ value, disabled, onChange }) => {
    const selected = new Set(value);
    const setChecked = (userType: string, checked: boolean) => {
        if (disabled) return;
        const next = new Set(selected);
        if (checked) {
            next.add(userType);
        } else {
            next.delete(userType);
        }
        onChange(KNOWN_USER_TYPES.filter(ut => next.has(ut)));
    };

    return (
        <div className={`rounded-xl border border-gray-200/80 bg-white p-3 ${disabled ? 'bg-gray-50 text-gray-400' : ''}`}>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {KNOWN_USER_TYPES.map(ut => (
                    <label
                        key={ut}
                        className={`inline-flex items-center gap-2 text-sm rounded-lg px-2.5 py-2 border ${
                            selected.has(ut)
                                ? 'border-blue-200 bg-blue-50 text-blue-800'
                                : 'border-gray-200 bg-white text-gray-700'
                        } ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:border-blue-200 hover:bg-blue-50/70'}`}
                    >
                        <input
                            type="checkbox"
                            checked={selected.has(ut)}
                            disabled={disabled}
                            onChange={e => setChecked(ut, e.target.checked)}
                        />
                        <span>{ut}</span>
                    </label>
                ))}
            </div>
            <div className="mt-2 flex items-center gap-3 text-[11px]">
                <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onChange([...KNOWN_USER_TYPES])}
                    className="text-blue-700 hover:text-blue-900 disabled:text-gray-400 disabled:cursor-not-allowed"
                >
                    Select all
                </button>
                <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onChange([])}
                    className="text-gray-600 hover:text-gray-900 disabled:text-gray-400 disabled:cursor-not-allowed"
                >
                    Clear
                </button>
            </div>
        </div>
    );
};

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
    <div className={`px-6 py-5 ${className}`}>{children}</div>
);

const Button: React.FC<{
    children: React.ReactNode;
    onClick?: () => void;
    type?: 'button' | 'submit';
    variant?: 'primary' | 'secondary' | 'danger';
    disabled?: boolean;
}> = ({ children, onClick, type = 'button', variant = 'primary', disabled = false }) => {
    const variants: Record<string, string> = {
        primary: 'bg-gray-900 text-white hover:bg-gray-800',
        secondary: 'bg-gray-100 text-gray-800 hover:bg-gray-200',
        danger: 'bg-red-600 text-white hover:bg-red-500'
    };
    return (
        <button
            type={type}
            onClick={onClick}
            disabled={disabled}
            className={`px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${variants[variant]}`}
        >
            {children}
        </button>
    );
};

const InputField: React.FC<{
    label: string;
    value: string;
    onChange: (v: string) => void;
    placeholder?: string;
    listId?: string;
}> = ({ label, value, onChange, placeholder, listId }) => (
    <div>
        <label className="block text-sm font-medium text-gray-800 mb-2">{label}</label>
        <input
            className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10"
            value={value}
            onChange={e => onChange(e.target.value)}
            placeholder={placeholder}
            list={listId}
        />
    </div>
);

const isRecord = (value: unknown): value is Record<string, unknown> => (
    typeof value === 'object' && value !== null && !Array.isArray(value)
);

const normalizeDotPath = (raw: string): string[] => (
    raw
        .split('.')
        .map(part => part.trim())
        .filter(Boolean)
);

const setNestedValue = (target: Record<string, unknown>, path: string[], value: unknown): Record<string, unknown> => {
    const next: Record<string, unknown> = { ...(isRecord(target) ? target : {}) };
    let cursor: Record<string, unknown> = next;
    path.forEach((part, idx) => {
        if (idx === path.length - 1) {
            cursor[part] = value;
            return;
        }
        const existing = cursor[part];
        if (!isRecord(existing)) {
            cursor[part] = {};
        } else {
            cursor[part] = { ...existing };
        }
        cursor = cursor[part] as Record<string, unknown>;
    });
    return next;
};

const deleteNestedValue = (target: Record<string, unknown>, path: string[]): Record<string, unknown> => {
    const next: Record<string, unknown> = { ...(isRecord(target) ? target : {}) };
    let cursor: Record<string, unknown> = next;
    path.forEach((part, idx) => {
        if (idx === path.length - 1) {
            delete cursor[part];
            return;
        }
        const existing = cursor[part];
        if (!isRecord(existing)) {
            cursor[part] = {};
        } else {
            cursor[part] = { ...existing };
        }
        cursor = cursor[part] as Record<string, unknown>;
    });
    return next;
};

const buildNestedObject = (path: string[], value: unknown): Record<string, unknown> => {
    return path.reduceRight<Record<string, unknown>>((acc, key) => ({ [key]: acc }), value as Record<string, unknown>);
};

const parseJsonValue = (raw: string): { ok: true; value: unknown } | { ok: false; error: string } => {
    const trimmed = raw.trim();
    if (!trimmed) {
        return { ok: false, error: 'Value is required.' };
    }
    try {
        return { ok: true, value: JSON.parse(trimmed) };
    } catch {
        return { ok: true, value: trimmed };
    }
};

const extractDotKeys = (node: unknown, out: string[], prefix = ''): void => {
    if (node === null || node === undefined) {
        return;
    }
    if (Array.isArray(node)) {
        node.forEach((value, idx) => {
            const nextPrefix = prefix ? `${prefix}.${idx}` : `${idx}`;
            extractDotKeys(value, out, nextPrefix);
        });
        return;
    }
    if (isRecord(node)) {
        Object.entries(node).forEach(([key, value]) => {
            const nextPrefix = prefix ? `${prefix}.${key}` : key;
            extractDotKeys(value, out, nextPrefix);
        });
        return;
    }
    if (prefix) {
        out.push(prefix);
    }
};

const deepMergeObjects = (base: Record<string, unknown>, patch: Record<string, unknown>): Record<string, unknown> => {
    const merged: Record<string, unknown> = { ...(base || {}) };
    Object.entries(patch || {}).forEach(([key, value]) => {
        const baseValue = merged[key];
        if (isRecord(baseValue) && isRecord(value)) {
            merged[key] = deepMergeObjects(baseValue, value);
        } else {
            merged[key] = value;
        }
    });
    return merged;
};

// =============================================================================
// ResourceEditorCard — per-kind override editor with combobox + form + Save
// =============================================================================

type EditableKind = 'api' | 'widget' | 'mcp' | 'cron';

interface ResourceEditorCardProps {
    kind: EditableKind;
    bundle: BundleEntry;
    editorProps: Record<string, any>;
    editorPropsLoading: boolean;
    onSave: (patch: Record<string, any>) => Promise<void>;
}

const ResourceEditorCard: React.FC<ResourceEditorCardProps> = ({
    kind, bundle, editorProps, editorPropsLoading, onSave,
}) => {
    // Build the resource list for the combobox.
    const resources: Array<{ key: string; label: string }> = useMemo(() => {
        if (kind === 'api') {
            return (bundle.apis || []).map(ep => ({
                key: `${ep.route}|${ep.alias}|${ep.http_method}`,
                label: `${ep.alias} [${ep.http_method}] ${ep.route}`,
            }));
        }
        if (kind === 'widget') {
            return (bundle.widgets || []).map(w => ({ key: w.alias, label: w.alias }));
        }
        if (kind === 'mcp') {
            return (bundle.mcp_endpoints || []).map(m => ({
                key: m.alias,
                label: `${m.alias} (${m.route})`,
            }));
        }
        return (bundle.scheduled_jobs || []).map(c => ({
            key: c.alias || c.method_name,
            label: c.alias || c.method_name,
        }));
    }, [kind, bundle]);

    const [selectedKey, setSelectedKey] = useState<string>(resources[0]?.key || '');
    const [saving, setSaving] = useState(false);
    const [flash, setFlash] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    // Reset selection when bundle changes and current key is no longer valid.
    useEffect(() => {
        if (!resources.find(r => r.key === selectedKey)) {
            setSelectedKey(resources[0]?.key || '');
        }
    }, [resources, selectedKey]);

    // Resolve the currently selected spec (typed loosely — kind-specific access below).
    const selectedSpec: any = useMemo(() => {
        if (!selectedKey) return null;
        if (kind === 'api') {
            const [route, alias, method] = selectedKey.split('|');
            return (bundle.apis || []).find(ep => ep.route === route && ep.alias === alias && ep.http_method === method) || null;
        }
        if (kind === 'widget') {
            return (bundle.widgets || []).find(w => w.alias === selectedKey) || null;
        }
        if (kind === 'mcp') {
            return (bundle.mcp_endpoints || []).find(m => m.alias === selectedKey) || null;
        }
        return (bundle.scheduled_jobs || []).find(c => (c.alias || c.method_name) === selectedKey) || null;
    }, [kind, bundle, selectedKey]);

    // Read current enabled override. Missing/null means the platform default:
    // enabled. Keep the persisted config sparse by writing only false.
    const enabledRaw = useMemo(() => {
        if (!selectedSpec) return undefined;
        const enabledRoot = (editorProps as any)?.enabled || {};
        if (kind === 'api') {
            const apiEnabled = enabledRoot?.api || {};
            const routeAware = apiEnabled[apiEnabledKey(selectedSpec)];
            return routeAware !== undefined ? routeAware : apiEnabled[legacyApiEnabledKey(selectedSpec)];
        }
        return enabledRoot?.[kind]?.[selectedSpec.alias];
    }, [kind, selectedSpec, editorProps]);

    const enabledEffective = useMemo(() => {
        const raw = enabledRaw;
        if (raw === undefined) return true;
        if (raw === null) return true;
        if (raw === false || raw === 0) return false;
        if (typeof raw === 'string' && ['false', 'disable', 'disabled', 'off', '0'].includes(raw.trim().toLowerCase())) return false;
        return Boolean(raw);
    }, [enabledRaw]);

    // Form state (reset to current effective values when selection changes).
    const [formEnabled, setFormEnabled] = useState<boolean>(true);
    const [formUserTypes, setFormUserTypes] = useState<string[]>([]);
    const [formRoles, setFormRoles] = useState<string>('');
    const [formAuthorityId, setFormAuthorityId] = useState<string>('');
    const [formGrants, setFormGrants] = useState<string>('');
    const [formTransport, setFormTransport] = useState<string>('streamable-http');
    const [formCron, setFormCron] = useState<string>('');
    const [formTimezone, setFormTimezone] = useState<string>('');

    useEffect(() => {
        setFlash(null);
        setError(null);
        if (!selectedSpec) return;
        setFormEnabled(enabledEffective);
        if (kind === 'api' || kind === 'widget') {
            setFormUserTypes(Array.isArray(selectedSpec.user_types) ? [...selectedSpec.user_types] : []);
            setFormRoles(Array.isArray(selectedSpec.roles) ? selectedSpec.roles.join(', ') : '');
        }
        if (kind === 'api' || kind === 'widget' || kind === 'mcp') {
            const auth = (selectedSpec as any).auth || {};
            setFormAuthorityId(String(auth.authority_id || auth.authority || ''));
            const grants = Array.isArray(auth.grants)
                ? auth.grants
                : Array.isArray(auth.scopes)
                    ? auth.scopes
                    : Array.isArray(auth.required_grants)
                        ? auth.required_grants
                        : [];
            setFormGrants(grants.join(', '));
        }
        if (kind === 'mcp') {
            setFormTransport(selectedSpec.transport || 'streamable-http');
        }
        if (kind === 'cron') {
            // Cron uses the platform contract: when expr_config is set and the
            // path is missing/null, the job is "not scheduled" (effective is
            // None). Mirror what's actually written at the override path so
            // explicit values like "disable" or empty strings are visible to
            // the operator; fall back to the decorator default when the key
            // is absent so the form is not blank by default.
            const rawCron = readDotPath(editorProps, selectedSpec.expr_config);
            const cronInitial = typeof rawCron === 'string'
                ? rawCron
                : (selectedSpec.cron_expression_default || '');
            setFormCron(cronInitial);
            const rawTz = readDotPath(editorProps, selectedSpec.tz_config);
            const tzInitial = typeof rawTz === 'string'
                ? rawTz
                : (selectedSpec.timezone_default || '');
            setFormTimezone(tzInitial);
        }
    }, [kind, selectedSpec, enabledEffective, editorProps]);

    const title = ({
        api: 'Edit API endpoint',
        widget: 'Edit widget',
        mcp: 'Edit MCP endpoint',
        cron: 'Edit cron job',
    } as Record<EditableKind, string>)[kind];

    const handleSave = async () => {
        if (!selectedSpec) return;
        setError(null);
        setFlash(null);
        try {
            setSaving(true);
            // Build composite patch from changed fields.
            let patch: Record<string, any> = {};
            // enabled toggle: default is enabled, so only persist false.
            const enabledIsExplicitTruthy = enabledRaw !== undefined && enabledRaw !== null && enabledEffective;
            if (formEnabled !== enabledEffective || enabledIsExplicitTruthy) {
                const enabledValue = formEnabled ? null : false;
                if (kind === 'api') {
                    patch = deepMergeMaps(patch, buildEnabledApiPatch(selectedSpec, enabledValue));
                } else {
                    patch = deepMergeMaps(patch, buildEnabledKindPatch(kind, selectedSpec.alias, enabledValue));
                }
            }
            // Provider-surface overrides are descriptor paths when the surface is configurable.
            if (kind === 'api' || kind === 'widget') {
                if (selectedSpec.user_types_path) {
                    patch = deepMergeMaps(
                        patch,
                        buildProviderVisibilityFieldPatch(kind, selectedSpec, 'user_types', formUserTypes),
                    );
                }
                if (selectedSpec.roles_path) {
                    patch = deepMergeMaps(
                        patch,
                        buildProviderVisibilityFieldPatch(kind, selectedSpec, 'roles', parseChips(formRoles)),
                    );
                }
            }
            if (kind === 'api' || kind === 'widget' || kind === 'mcp') {
                const currentAuth = { ...((selectedSpec as any).auth || {}) };
                const currentAuthority = String(currentAuth.authority_id || currentAuth.authority || '');
                const currentGrants = Array.isArray(currentAuth.grants)
                    ? currentAuth.grants
                    : Array.isArray(currentAuth.scopes)
                        ? currentAuth.scopes
                        : Array.isArray(currentAuth.required_grants)
                            ? currentAuth.required_grants
                            : [];
                const nextAuthority = formAuthorityId.trim();
                const nextGrants = parseChips(formGrants);
                const grantsChanged = nextGrants.join('\n') !== currentGrants.map(String).join('\n');
                if (nextAuthority !== currentAuthority || grantsChanged) {
                    const nextAuth: Record<string, any> = { ...currentAuth };
                    delete nextAuth.authority;
                    delete nextAuth.scopes;
                    delete nextAuth.required_grants;
                    if (nextAuthority) {
                        nextAuth.authority_id = nextAuthority;
                    } else {
                        delete nextAuth.authority_id;
                    }
                    if (nextGrants.length) {
                        nextAuth.grants = nextGrants;
                    } else {
                        delete nextAuth.grants;
                    }
                    const authValue = Object.keys(nextAuth).length ? nextAuth : null;
                    patch = deepMergeMaps(patch, buildProviderAuthPatch(kind, selectedSpec, authValue));
                }
            }
            if (kind === 'mcp' && selectedSpec.transport_config) {
                patch = deepMergeMaps(patch, nestedDotPathPatch(selectedSpec.transport_config, formTransport));
            }
            if (kind === 'cron') {
                if (selectedSpec.expr_config) {
                    patch = deepMergeMaps(patch, nestedDotPathPatch(selectedSpec.expr_config, formCron));
                }
                if (selectedSpec.tz_config) {
                    patch = deepMergeMaps(patch, nestedDotPathPatch(selectedSpec.tz_config, formTimezone));
                }
            }
            await onSave(patch);
            setFlash('Saved ✓');
            window.setTimeout(() => setFlash(null), 1800);
        } catch (e: any) {
            setError(e?.message || 'Save failed');
        } finally {
            setSaving(false);
        }
    };

    const resetField = async (configPath: string | null | undefined, value: unknown = null) => {
        if (!configPath) return;
        setError(null);
        try {
            setSaving(true);
            await onSave(nestedDotPathPatch(configPath, value));
            setFlash('Reset ✓');
            window.setTimeout(() => setFlash(null), 1800);
        } catch (e: any) {
            setError(e?.message || 'Reset failed');
        } finally {
            setSaving(false);
        }
    };

    const resetProviderVisibilityField = async (field: 'user_types' | 'roles') => {
        if (!(kind === 'api' || kind === 'widget') || !selectedSpec) return;
        setError(null);
        try {
            setSaving(true);
            await onSave(buildProviderVisibilityFieldPatch(kind, selectedSpec, field, null));
            setFlash('Reset ✓');
            window.setTimeout(() => setFlash(null), 1800);
        } catch (e: any) {
            setError(e?.message || 'Reset failed');
        } finally {
            setSaving(false);
        }
    };

    const resetEnabled = async () => {
        if (!selectedSpec) return;
        setError(null);
        try {
            setSaving(true);
            const patch = kind === 'api'
                ? buildEnabledApiPatch(selectedSpec, null)
                : buildEnabledKindPatch(kind, selectedSpec.alias, null);
            await onSave(patch);
            setFlash('Reset ✓');
            window.setTimeout(() => setFlash(null), 1800);
        } catch (e: any) {
            setError(e?.message || 'Reset failed');
        } finally {
            setSaving(false);
        }
    };

    if (resources.length === 0) {
        return null;
    }

    return (
        <Card>
            <CardHeader
                title={title}
                subtitle="Resource overrides are written to bundle props (op: merge). Changes apply immediately at request time."
                action={
                    <div className="flex items-center gap-3">
                        {flash && <span className="text-xs text-emerald-700 font-semibold">{flash}</span>}
                        {error && <span className="text-xs text-red-700">{error}</span>}
                        <Button variant="primary" disabled={saving || !selectedSpec || editorPropsLoading} onClick={handleSave}>
                            {saving ? 'Saving…' : 'Save'}
                        </Button>
                    </div>
                }
            />
            <CardBody className="space-y-5">
                <div>
                    <label className="block text-sm font-medium text-gray-800 mb-2">Resource</label>
                    <select
                        className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white text-sm"
                        value={selectedKey}
                        onChange={e => setSelectedKey(e.target.value)}
                    >
                        {resources.map(r => (
                            <option key={r.key} value={r.key}>{r.label}</option>
                        ))}
                    </select>
                </div>

                {selectedSpec && (
                    <div className="space-y-5">
                        <FieldRow
                            label="Enabled"
                            overridden={enabledRaw !== undefined && enabledRaw !== null}
                            configurable
                            onResetToDefault={
                                enabledRaw !== undefined && enabledRaw !== null
                                    ? resetEnabled
                                    : undefined
                            }
                        >
                            <label className="inline-flex items-center gap-2 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={formEnabled}
                                    onChange={e => setFormEnabled(e.target.checked)}
                                />
                                <span className={formEnabled ? 'text-emerald-700 font-semibold text-sm' : 'text-gray-500 font-semibold text-sm'}>
                                    {formEnabled ? 'enabled' : 'disabled'}
                                </span>
                            </label>
                            <FieldHint>
                                Maps to <code>{selectedSpec.enabled_path}</code>. Missing value means enabled; disabling writes <code>false</code>.
                            </FieldHint>
                        </FieldRow>

                        {(kind === 'api' || kind === 'widget') && (
                            <>
                                <FieldRow
                                    label="User types"
                                    overridden={selectedSpec.user_types_overridden}
                                    configurable={Boolean(selectedSpec.user_types_path)}
                                    onResetToDefault={selectedSpec.user_types_path && selectedSpec.user_types_overridden ? () => resetProviderVisibilityField('user_types') : undefined}
                                >
                                    <UserTypesEditor
                                        value={formUserTypes}
                                        onChange={setFormUserTypes}
                                        disabled={!selectedSpec.user_types_path}
                                    />
                                    <FieldHint>
                                        {selectedSpec.user_types_path
                                            ? <>Descriptor path: <code>{selectedSpec.user_types_path}</code>. Check selected user types to restrict access. No selection means all user types are allowed.</>
                                            : formUserTypes.length === 0
                                                ? <>No descriptor path is available — hard-coded empty list, so all user types are allowed.</>
                                                : <>No descriptor path is available — value is hard-coded.</>}
                                    </FieldHint>
                                </FieldRow>

                                <FieldRow
                                    label="Roles"
                                    overridden={selectedSpec.roles_overridden}
                                    configurable={Boolean(selectedSpec.roles_path)}
                                    onResetToDefault={selectedSpec.roles_path && selectedSpec.roles_overridden ? () => resetProviderVisibilityField('roles') : undefined}
                                >
                                    <input
                                        value={formRoles}
                                        onChange={e => setFormRoles(e.target.value)}
                                        disabled={!selectedSpec.roles_path}
                                        placeholder={!selectedSpec.roles_path && !formRoles ? 'all roles allowed' : 'kdcube:role:editor, kdcube:role:viewer'}
                                        className="w-full px-3 py-2 border border-gray-200/80 rounded-xl bg-white text-sm font-mono disabled:bg-gray-50 disabled:text-gray-400"
                                    />
                                    <FieldHint>
                                        {selectedSpec.roles_path
                                            ? <>Descriptor path: <code>{selectedSpec.roles_path}</code>. Comma-separated. Empty saves an explicit empty list (all roles allowed).</>
                                            : parseChips(formRoles).length === 0
                                                ? <>No descriptor path is available — hard-coded empty list, so all roles are allowed.</>
                                                : <>No descriptor path is available — value is hard-coded.</>}
                                    </FieldHint>
                                </FieldRow>
                            </>
                        )}

                        {(kind === 'api' || kind === 'widget' || kind === 'mcp') && (
                            <>
                                <FieldRow
                                    label="Authority ID"
                                    overridden={Boolean(formAuthorityId)}
                                    configurable
                                >
                                    <input
                                        value={formAuthorityId}
                                        onChange={e => setFormAuthorityId(e.target.value)}
                                        placeholder="kdcube.platform, delegated_client, custom.identity"
                                        className="w-full px-3 py-2 border border-gray-200/80 rounded-xl bg-white text-sm font-mono"
                                    />
                                    <FieldHint>
                                        Descriptor path: <code>{(selectedSpec as any).auth_path || 'surfaces.as_provider'}</code>. Empty means no authority-id restriction.
                                    </FieldHint>
                                </FieldRow>

                                <FieldRow
                                    label="Grants"
                                    overridden={parseChips(formGrants).length > 0}
                                    configurable
                                >
                                    <input
                                        value={formGrants}
                                        onChange={e => setFormGrants(e.target.value)}
                                        placeholder="conversations:read, feedback:write"
                                        className="w-full px-3 py-2 border border-gray-200/80 rounded-xl bg-white text-sm font-mono"
                                    />
                                    <FieldHint>
                                        Comma-separated delegated authority grants required by this provider surface. Empty means no grant restriction.
                                    </FieldHint>
                                </FieldRow>
                            </>
                        )}

                        {kind === 'mcp' && (
                            <FieldRow
                                label="Transport"
                                overridden={selectedSpec.transport_overridden}
                                configurable={Boolean(selectedSpec.transport_config)}
                                onResetToDefault={selectedSpec.transport_config && selectedSpec.transport_overridden ? () => resetField(selectedSpec.transport_config, null) : undefined}
                            >
                                <select
                                    value={formTransport}
                                    onChange={e => setFormTransport(e.target.value)}
                                    disabled={!selectedSpec.transport_config}
                                    className="w-full px-3 py-2 border border-gray-200/80 rounded-xl bg-white text-sm disabled:bg-gray-50 disabled:text-gray-400"
                                >
                                    {KNOWN_TRANSPORTS.map(t => <option key={t} value={t}>{t}</option>)}
                                </select>
                                <FieldHint>
                                    {selectedSpec.transport_config
                                        ? <>Override path: <code>{selectedSpec.transport_config}</code>.</>
                                        : <>No <code>transport_config</code> declared in the decorator — value is hard-coded.</>}
                                </FieldHint>
                            </FieldRow>
                        )}

                        {kind === 'cron' && (
                            <>
                                <FieldRow
                                    label="Cron expression"
                                    overridden={selectedSpec.cron_expression_overridden}
                                    configurable={Boolean(selectedSpec.expr_config)}
                                    onResetToDefault={
                                        selectedSpec.expr_config && selectedSpec.cron_expression_overridden
                                            ? () => resetField(selectedSpec.expr_config, selectedSpec.cron_expression_default ?? null)
                                            : undefined
                                    }
                                >
                                    <input
                                        value={formCron}
                                        onChange={e => setFormCron(e.target.value)}
                                        disabled={!selectedSpec.expr_config}
                                        placeholder={selectedSpec.cron_expression_default || '*/15 * * * *'}
                                        className="w-full px-3 py-2 border border-gray-200/80 rounded-xl bg-white text-sm font-mono disabled:bg-gray-50 disabled:text-gray-400"
                                    />
                                    <FieldHint>
                                        {selectedSpec.expr_config ? (
                                            <>
                                                Override path: <code>{selectedSpec.expr_config}</code>.
                                                {' '}Decorator default: <code>{selectedSpec.cron_expression_default || '—'}</code>.
                                                <br />
                                                <span className="text-gray-400">
                                                    Setting the value to <code>disable</code> (or an empty string) suppresses scheduling
                                                    without flipping <code>enabled.cron.{selectedSpec.alias}</code>.
                                                    Removing the value entirely also stops the job. "Reset to default" therefore writes
                                                    the decorator default back to the override path instead of clearing it.
                                                </span>
                                            </>
                                        ) : (
                                            <>No <code>expr_config</code> declared in the decorator — schedule is hard-coded.</>
                                        )}
                                    </FieldHint>
                                </FieldRow>

                                <FieldRow
                                    label="Timezone"
                                    overridden={selectedSpec.timezone_overridden}
                                    configurable={Boolean(selectedSpec.tz_config)}
                                    onResetToDefault={
                                        selectedSpec.tz_config && selectedSpec.timezone_overridden
                                            ? () => resetField(selectedSpec.tz_config, selectedSpec.timezone_default ?? null)
                                            : undefined
                                    }
                                >
                                    <input
                                        value={formTimezone}
                                        onChange={e => setFormTimezone(e.target.value)}
                                        disabled={!selectedSpec.tz_config}
                                        placeholder={selectedSpec.timezone_default || 'Europe/Berlin'}
                                        className="w-full px-3 py-2 border border-gray-200/80 rounded-xl bg-white text-sm font-mono disabled:bg-gray-50 disabled:text-gray-400"
                                    />
                                    <FieldHint>
                                        {selectedSpec.tz_config ? (
                                            <>
                                                Override path: <code>{selectedSpec.tz_config}</code>.
                                                {' '}Decorator default: <code>{selectedSpec.timezone_default || 'UTC'}</code>.
                                                <br />
                                                <span className="text-gray-400">
                                                    Empty / missing falls back to the decorator default. "Reset to default" writes the
                                                    decorator default back to the override path.
                                                </span>
                                            </>
                                        ) : (
                                            <>No <code>tz_config</code> declared in the decorator — timezone is hard-coded.</>
                                        )}
                                    </FieldHint>
                                </FieldRow>
                            </>
                        )}
                    </div>
                )}
            </CardBody>
        </Card>
    );
};

// =============================================================================
// Main Component
// =============================================================================

const AIBundleDashboard: React.FC = () => {
    const [loading, setLoading] = useState(true);
    const [configReady, setConfigReady] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [bundles, setBundles] = useState<Record<string, BundleEntry>>({});
    const [defaultBundleId, setDefaultBundleId] = useState<string>('');
    const [bundleAuthority, setBundleAuthority] = useState<AuthorityDescriptor | null>(null);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [reloadingBundleId, setReloadingBundleId] = useState<string | null>(null);
    const [scopeTenant, setScopeTenant] = useState(settings.getDefaultTenant());
    const [scopeProject, setScopeProject] = useState(settings.getDefaultProject());
    const [scopeInput, setScopeInput] = useState(
        formatScopeLabel(settings.getDefaultTenant(), settings.getDefaultProject())
    );
    const [tenantProjects, setTenantProjects] = useState<TenantProjectItem[]>([]);
    const [tenantProjectsLoading, setTenantProjectsLoading] = useState(false);
    const [tenantProjectsError, setTenantProjectsError] = useState<string | null>(null);
    const [propsBundleId, setPropsBundleId] = useState<string>('');
    const [propsJson, setPropsJson] = useState<string>('{}');
    const [propsDefaultsJson, setPropsDefaultsJson] = useState<string>('{}');
    const [propsDefaultsError, setPropsDefaultsError] = useState<BundleDefaultsError | null>(null);
    const [propsLoading, setPropsLoading] = useState<boolean>(false);
    const [propsKeyPath, setPropsKeyPath] = useState<string>('');
    const [propsValue, setPropsValue] = useState<string>('');
    const [secretsBundleId, setSecretsBundleId] = useState<string>('');
    const [secretsJson, setSecretsJson] = useState<string>('{}');
    const [secretsSaving, setSecretsSaving] = useState<boolean>(false);
    const [secretsStatus, setSecretsStatus] = useState<{ mode: 'set' | 'clear'; keys: string[] } | null>(null);
    const [secretsKeys, setSecretsKeys] = useState<string[]>([]);
    const [secretsLoading, setSecretsLoading] = useState<boolean>(false);
    const [secretsKeyPath, setSecretsKeyPath] = useState<string>('');
    const [secretsValue, setSecretsValue] = useState<string>('');
    const [interfaceBundleId, setInterfaceBundleId] = useState<string>('');
    const [editorProps, setEditorProps] = useState<Record<string, any>>({});
    const [editorPropsLoading, setEditorPropsLoading] = useState<boolean>(false);
    const [formBundleRoles, setFormBundleRoles] = useState<string>('');
    const [bundleRolesSaving, setBundleRolesSaving] = useState<boolean>(false);
    const [bundleRolesFlash, setBundleRolesFlash] = useState<string | null>(null);
    const [bundleRolesError, setBundleRolesError] = useState<string | null>(null);
    const [formPersistSelector, setFormPersistSelector] = useState<string>('');
    const [persistSelectorSaving, setPersistSelectorSaving] = useState<boolean>(false);
    const [persistSelectorFlash, setPersistSelectorFlash] = useState<string | null>(null);
    const [persistSelectorError, setPersistSelectorError] = useState<string | null>(null);
    const [formTelemetrySelector, setFormTelemetrySelector] = useState<string>('');
    const [telemetrySelectorSaving, setTelemetrySelectorSaving] = useState<boolean>(false);
    const [telemetrySelectorFlash, setTelemetrySelectorFlash] = useState<string | null>(null);
    const [telemetrySelectorError, setTelemetrySelectorError] = useState<string | null>(null);
    const registryScope = useMemo(() => normalizeScope(scopeTenant, scopeProject), [scopeTenant, scopeProject]);
    const propsScope = useMemo(() => normalizeScope(scopeTenant, scopeProject), [scopeTenant, scopeProject]);
    const draftScope = useMemo(() => parseScopeValue(scopeInput), [scopeInput]);
    const scopeDirty = useMemo(() => {
        const applied = normalizeScope(scopeTenant, scopeProject);
        return applied.tenant !== draftScope.tenant || applied.project !== draftScope.project;
    }, [scopeTenant, scopeProject, draftScope]);
    const bundleVersion = useMemo(() => {
        try {
            const parsed = JSON.parse(propsDefaultsJson || '{}');
            return typeof parsed?.bundle_version === 'string' ? parsed.bundle_version : '';
        } catch {
            return '';
        }
    }, [propsDefaultsJson]);
    const authorityLabel = useMemo(() => {
        const label = (bundleAuthority?.label || '').trim();
        return label || 'configured bundle authority';
    }, [bundleAuthority]);
    const authorityDescription = useMemo(() => {
        const description = (bundleAuthority?.description || '').trim();
        return description || `Reload from ${authorityLabel}.`;
    }, [bundleAuthority, authorityLabel]);
    const authorityDetail = useMemo(() => {
        const detail = (bundleAuthority?.detail || '').trim();
        return detail;
    }, [bundleAuthority]);
    const reloadAuthorityLabel = useMemo(() => `Reload from ${authorityLabel}`, [authorityLabel]);
    const propsResolutionLabel = useMemo(() => authorityLabel, [authorityLabel]);
    const bundleSnapshotPath = useMemo(() => {
        if (!bundleVersion || !propsBundleId || !scopeTenant || !scopeProject) return '';
        return `cb/tenants/${scopeTenant}/projects/${scopeProject}/ai-bundle-snapshots/${propsBundleId}.${bundleVersion}.zip`;
    }, [bundleVersion, propsBundleId, scopeTenant, scopeProject]);

    const copyText = async (value: string) => {
        if (!value) return;
        try {
            await navigator.clipboard.writeText(value);
        } catch {
            try {
                const el = document.createElement('textarea');
                el.value = value;
                el.style.position = 'fixed';
                el.style.opacity = '0';
                document.body.appendChild(el);
                el.select();
                document.execCommand('copy');
                document.body.removeChild(el);
            } catch {
                // no-op
            }
        }
    };

    const [form, setForm] = useState<BundleEntry>({
        id: '',
        name: '',
        path: '',
        module: '',
        singleton: false,
        description: '',
        repo: '',
        ref: '',
        subdir: ''
    });
    const formRef = useRef<HTMLDivElement | null>(null);

    const bundleList = useMemo(() => Object.values(bundles).sort((a, b) => a.id.localeCompare(b.id)), [bundles]);
    const deriveRepoName = (repoUrl: string): string => {
        const trimmed = (repoUrl || '').trim().replace(/\/+$/, '');
        if (!trimmed) return '';
        const last = trimmed.split('/').pop() || '';
        return last.endsWith('.git') ? last.slice(0, -4) : last;
    };
    const derivedGitPath = useMemo(() => {
        if (!form.repo) return '';
        const id = form.id || '<bundle_id>';
        const ref = (form.ref || '').trim();
        const subdir = (form.subdir || '').trim();
        const repo = deriveRepoName(form.repo) || '<repo>';
        const base = `<bundles_root>/${repo}__${id}${ref ? `__${ref}` : ''}`;
        return subdir ? `${base}/${subdir}` : base;
    }, [form.repo, form.ref, form.subdir, form.id]);
    const derivedHostPath = useMemo(() => {
        if (!form.repo) return '';
        const root = settings.getHostBundlesPath() || '<HOST_BUNDLES_PATH>';
        const id = form.id || '<bundle_id>';
        const ref = (form.ref || '').trim();
        const subdir = (form.subdir || '').trim();
        const repo = deriveRepoName(form.repo) || '<repo>';
        const base = `${root.replace(/\/+$/, '')}/${repo}__${id}${ref ? `__${ref}` : ''}`;
        return subdir ? `${base}/${subdir}` : base;
    }, [form.repo, form.ref, form.subdir, form.id]);
    const derivedAgenticPath = useMemo(() => {
        if (!form.repo) return '';
        const root = settings.getBundlesRoot() || '<BUNDLES_ROOT>';
        const id = form.id || '<bundle_id>';
        const ref = (form.ref || '').trim();
        const subdir = (form.subdir || '').trim();
        const repo = deriveRepoName(form.repo) || '<repo>';
        const base = `${root.replace(/\/+$/, '')}/${repo}__${id}${ref ? `__${ref}` : ''}`;
        return subdir ? `${base}/${subdir}` : base;
    }, [form.repo, form.ref, form.subdir, form.id]);

    const loadBundles = async (scopeOverride?: Scope, opts?: { quiet?: boolean }) => {
        const quiet = opts?.quiet === true;
        try {
            if (!quiet) setLoading(true);
            const data = await api.listBundles(scopeOverride ?? registryScope);
            setBundles(data.available_bundles || {});
            setDefaultBundleId(data.default_bundle_id || '');
            setBundleAuthority(data.authority || null);
            if (!propsBundleId || !(propsBundleId in (data.available_bundles || {}))) {
                setPropsBundleId(data.default_bundle_id || '');
            }
            if (!secretsBundleId || !(secretsBundleId in (data.available_bundles || {}))) {
                setSecretsBundleId(data.default_bundle_id || '');
            }
            if (!interfaceBundleId || !(interfaceBundleId in (data.available_bundles || {}))) {
                setInterfaceBundleId(data.default_bundle_id || '');
            }
            if (!quiet) setError(null);
        } catch (e: any) {
            if (!quiet) {
                setError(e.message || 'Failed to load bundles');
                setBundleAuthority(null);
            }
            throw e;
        } finally {
            if (!quiet) setLoading(false);
        }
    };

    const loadProps = async () => {
        if (!propsBundleId) return;
        try {
            setPropsLoading(true);
            const data = await api.getBundleProps(propsBundleId, propsScope);
            const props = data.props || {};
            const defaults = data.defaults || {};
            const merged = deepMergeObjects(defaults, props);
            setPropsJson(JSON.stringify(merged, null, 2));
            setPropsDefaultsJson(JSON.stringify(defaults, null, 2));
            setPropsDefaultsError(data.defaults_error || null);
        } catch (e: any) {
            setPropsDefaultsError(null);
            setError(e.message || 'Failed to load bundle props');
        } finally {
            setPropsLoading(false);
        }
    };

    const parseJsonObject = (raw: string, label: string): Record<string, unknown> => {
        const trimmed = raw.trim();
        if (!trimmed) {
            return {};
        }
        try {
            const parsed = JSON.parse(trimmed);
            if (!isRecord(parsed)) {
                throw new Error(`${label} must be a JSON object.`);
            }
            return parsed;
        } catch (err: any) {
            const message = err?.message ? String(err.message) : '';
            throw new Error(message || `Invalid ${label} JSON.`);
        }
    };


    const collectSecretKeys = (payload: Record<string, unknown>): string[] => {
        const keys: string[] = [];
        extractDotKeys(payload, keys);
        return keys.sort();
    };

    const applyPropsDotPath = (mode: 'set' | 'delete') => {
        const path = normalizeDotPath(propsKeyPath);
        if (!path.length) {
            setError('Enter a dot-path for props.');
            return;
        }
        try {
            const parsed = parseJsonObject(propsJson, 'Props');
            let updated = parsed;
            if (mode === 'set') {
                const parsedValue = parseJsonValue(propsValue);
                if (!parsedValue.ok) {
                    setError(parsedValue.error);
                    return;
                }
                updated = setNestedValue(parsed, path, parsedValue.value);
            } else {
                updated = deleteNestedValue(parsed, path);
            }
            setPropsJson(JSON.stringify(updated, null, 2));
            setError(null);
        } catch (e: any) {
            setError(e.message || 'Failed to update props.');
        }
    };

    const submitSecretDotPath = async (mode: 'set' | 'clear') => {
        if (!secretsBundleId) {
            setError('Select a bundle to update secrets.');
            return;
        }
        const path = normalizeDotPath(secretsKeyPath);
        if (!path.length) {
            setError('Enter a dot-path for secrets.');
            return;
        }
        let value: unknown = true;
        if (mode === 'set') {
            const parsedValue = parseJsonValue(secretsValue);
            if (!parsedValue.ok) {
                setError(parsedValue.error);
                return;
            }
            value = parsedValue.value;
        }
        try {
            setSecretsSaving(true);
            const payload = buildNestedObject(path, value);
            const response = await api.setBundleSecrets(secretsBundleId, { secrets: payload, mode }, propsScope);
            setSecretsStatus({ mode, keys: response.keys || [] });
            if (response.stored_keys) {
                setSecretsKeys(response.stored_keys);
            } else if (response.keys) {
                setSecretsKeys(response.keys);
            }
            setError(null);
        } catch (e: any) {
            setError(e.message || 'Failed to update secrets');
        } finally {
            setSecretsSaving(false);
        }
    };

    useEffect(() => {
        const applyDefaults = () => {
            const nextTenant = settings.getDefaultTenant();
            const nextProject = settings.getDefaultProject();
            setScopeTenant(nextTenant);
            setScopeProject(nextProject);
            setScopeInput(formatScopeLabel(nextTenant, nextProject));
        };

        settings.setupParentListener()
            .then(() => {
                applyDefaults();
                setConfigReady(true);
            })
            .catch(() => {
                applyDefaults();
                setConfigReady(true);
            });
    }, []);

    useEffect(() => {
        if (!configReady) return;
        loadBundles();
    }, [configReady]);

    useEffect(() => {
        if (!configReady) return;
        setTenantProjectsLoading(true);
        setTenantProjectsError(null);
        api.listTenantProjects()
            .then(setTenantProjects)
            .catch((err) => setTenantProjectsError(err.message || 'Failed to load tenant/projects'))
            .finally(() => setTenantProjectsLoading(false));
    }, [configReady]);

    useEffect(() => {
        if (!propsBundleId) return;
        loadProps();
    }, [propsBundleId, scopeTenant, scopeProject]);

    const loadSecrets = async () => {
        if (!secretsBundleId) return;
        try {
            setSecretsLoading(true);
            const data = await api.getBundleSecrets(secretsBundleId, propsScope);
            setSecretsKeys(data.keys || []);
        } catch (e: any) {
            setError(e.message || 'Failed to load bundle secrets');
        } finally {
            setSecretsLoading(false);
        }
    };

    const reloadBundleFromAuthority = async (bundleId: string) => {
        if (!bundleId) return;
        try {
            setReloadingBundleId(bundleId);
            await api.reloadFromAuthority(registryScope, bundleId);
            await loadBundles();
            if (propsBundleId === bundleId) {
                await loadProps();
            }
            if (secretsBundleId === bundleId) {
                await loadSecrets();
            }
            setError(null);
        } catch (e: any) {
            setError(e.message || `Failed to reload bundle ${bundleId} from ${authorityLabel}`);
        } finally {
            setReloadingBundleId(null);
        }
    };

    useEffect(() => {
        if (!secretsBundleId) return;
        loadSecrets();
    }, [secretsBundleId, scopeTenant, scopeProject]);

    const loadEditorProps = async (bundleId: string) => {
        if (!bundleId) {
            setEditorProps({});
            return;
        }
        try {
            setEditorPropsLoading(true);
            const data = await api.getBundleProps(bundleId, registryScope);
            setEditorProps((data?.props as Record<string, any>) || {});
        } catch {
            setEditorProps({});
        } finally {
            setEditorPropsLoading(false);
        }
    };

    useEffect(() => {
        loadEditorProps(interfaceBundleId);
    }, [interfaceBundleId, scopeTenant, scopeProject]);

    useEffect(() => {
        const persistSel = readDotPath(editorProps, 'events.record.persist.selector');
        setFormPersistSelector(Array.isArray(persistSel) ? persistSel.join(', ') : '');
        const telemetrySel = readDotPath(editorProps, 'events.record.telemetry.selector');
        setFormTelemetrySelector(Array.isArray(telemetrySel) ? telemetrySel.join(', ') : '');
    }, [editorProps]);

    useEffect(() => {
        const b = interfaceBundleId ? bundles[interfaceBundleId] : null;
        setBundleRolesFlash(null);
        setBundleRolesError(null);
        setFormBundleRoles(b && Array.isArray(b.allowed_roles) ? b.allowed_roles.join(', ') : '');
        setPersistSelectorFlash(null);
        setPersistSelectorError(null);
        setTelemetrySelectorFlash(null);
        setTelemetrySelectorError(null);
    }, [interfaceBundleId, bundles]);

    // Apply a merge-patch to the current bundle's props, then quietly refresh
    // both the bundles snapshot (for descriptors / pills) and editorProps
    // (for the editor cards' form values). Avoids the global loading spinner.
    const saveOverrideAndRefresh = async (patch: Record<string, any>) => {
        if (!interfaceBundleId) return;
        await api.setBundleProps(interfaceBundleId, { op: 'merge', props: patch }, registryScope);
        await Promise.all([
            loadBundles(registryScope, { quiet: true }).catch(() => undefined),
            loadEditorProps(interfaceBundleId),
        ]);
    };

    const resetForm = () => {
        setEditingId(null);
        setForm({ id: '', name: '', path: '', module: '', singleton: false, description: '', repo: '', ref: '', subdir: '' });
    };

    const saveBundle = async () => {
        if (!form.id || (!form.path && !form.repo)) {
            setError('Bundle id is required. Provide either a path or a repo.');
            return;
        }
        try {
            const payload = {
                ...form,
                path: form.repo ? '' : form.path,
                git_commit: undefined,
                singleton: !!form.singleton
            };
            await api.updateBundles({
                op: 'merge',
                bundles: { [payload.id]: payload },
                default_bundle_id: defaultBundleId || undefined
            }, registryScope);
            resetForm();
            await loadBundles();
        } catch (e: any) {
            setError(e.message || 'Failed to save bundle');
        }
    };

    const deleteBundle = async (id: string) => {
        const next = { ...bundles };
        delete next[id];
        const nextDefault = defaultBundleId === id ? (Object.keys(next)[0] || '') : defaultBundleId;
        try {
            await api.updateBundles({
                op: 'replace',
                bundles: next,
                default_bundle_id: nextDefault || undefined
            }, registryScope);
            await loadBundles();
        } catch (e: any) {
            setError(e.message || 'Failed to delete bundle');
        }
    };

    const editBundle = (entry: BundleEntry) => {
        setEditingId(entry.id);
        setForm({
            id: entry.id,
            name: entry.name || '',
            path: entry.path || '',
            module: entry.module || '',
            singleton: !!entry.singleton,
            description: entry.description || '',
            repo: entry.repo || '',
            ref: entry.ref || '',
            subdir: entry.subdir || ''
        });
        setTimeout(() => formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0);
    };

    const updateDefault = async () => {
        try {
            await api.updateBundles({
                op: 'merge',
                bundles: {},
                default_bundle_id: defaultBundleId || undefined
            }, registryScope);
            await loadBundles();
        } catch (e: any) {
            setError(e.message || 'Failed to update default bundle');
        }
    };

    const reloadFromAuthority = async () => {
        try {
            await api.reloadFromAuthority(registryScope);
            await loadBundles();
        } catch (e: any) {
            setError(e.message || `Failed to reload from ${authorityLabel}`);
        }
    };

    const cleanupBundles = async () => {
        try {
            await api.cleanupBundles({ drop_sys_modules: true }, registryScope);
        } catch (e: any) {
            setError(e.message || 'Failed to cleanup bundles');
        }
    };

    const saveProps = async (op: 'replace' | 'merge') => {
        if (!propsBundleId) {
            setError('Select a bundle to update props.');
            return;
        }
        try {
            const parsed = parseJsonObject(propsJson, 'Props');
            await api.setBundleProps(propsBundleId, {
                op,
                props: parsed
            }, propsScope);
            await loadProps();
            setError(null);
        } catch (e: any) {
            setError(e.message || 'Failed to update props');
        }
    };

    const resetPropsFromCode = async () => {
        if (!propsBundleId) {
            setError('Select a bundle to reset props.');
            return;
        }
        try {
            await api.resetBundlePropsFromCode(propsBundleId, propsScope);
            await loadProps();
        } catch (e: any) {
            setError(e.message || 'Failed to reset props from code');
        }
    };

    const saveSecrets = async () => {
        if (!secretsBundleId) {
            setError('Select a bundle to update secrets.');
            return;
        }
        try {
            setSecretsSaving(true);
            const parsed = parseJsonObject(secretsJson, 'Secrets');
            const keys = collectSecretKeys(parsed);
            if (!keys.length) {
                setError('Provide at least one secret key to save.');
                return;
            }
            const response = await api.setBundleSecrets(secretsBundleId, { secrets: parsed, mode: 'set' }, propsScope);
            setSecretsStatus({ mode: 'set', keys: response.keys || [] });
            if (response.stored_keys) {
                setSecretsKeys(response.stored_keys);
            } else if (response.keys) {
                setSecretsKeys(response.keys);
            }
            setError(null);
        } catch (e: any) {
            setError(e.message || 'Failed to update secrets');
        } finally {
            setSecretsSaving(false);
        }
    };

    const clearSecrets = async () => {
        if (!secretsBundleId) {
            setError('Select a bundle to clear secrets.');
            return;
        }
        let parsed: Record<string, unknown> = {};
        try {
            parsed = parseJsonObject(secretsJson, 'Secrets');
        } catch (e: any) {
            setError(e.message || 'Invalid secrets JSON.');
            return;
        }
        const keys = collectSecretKeys(parsed);
        if (!keys.length) {
            setError('Provide at least one secret key to clear.');
            return;
        }
        const confirmed = window.confirm(
            `Clear these secrets for this bundle?\\n- ${keys.join('\\n- ')}\\nThis cannot be undone.`
        );
        if (!confirmed) return;
        try {
            setSecretsSaving(true);
            const response = await api.setBundleSecrets(secretsBundleId, { secrets: parsed, mode: 'clear' }, propsScope);
            setSecretsStatus({ mode: 'clear', keys: response.keys || [] });
            if (response.stored_keys) {
                setSecretsKeys(response.stored_keys);
            } else if (response.keys) {
                setSecretsKeys(response.keys);
            }
            setError(null);
        } catch (e: any) {
            setError(e.message || 'Failed to clear secrets');
        } finally {
            setSecretsSaving(false);
        }
    };

    const applyScope = async () => {
        const parsed = parseScopeValue(scopeInput);
        const nextTenant = parsed.tenant || '';
        const nextProject = parsed.project || '';
        setScopeTenant(nextTenant);
        setScopeProject(nextProject);
        setScopeInput(formatScopeLabel(nextTenant, nextProject));
        await loadBundles(parsed);
    };

    if (loading) {
        return (
            <div className="min-h-screen bg-white flex items-center justify-center p-8">
                <div className="text-center">
                    <div className="animate-spin rounded-full h-10 w-10 border-2 border-gray-200 border-t-gray-900"></div>
                    <p className="mt-4 text-gray-600">Loading AI bundle registry…</p>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-white">
            <div className="max-w-6xl mx-auto px-6 py-10 space-y-8">
                <div className="text-center">
                    <h1 className="text-4xl md:text-5xl font-semibold text-gray-900 tracking-tight">AI Bundles</h1>
                    <div className="mt-3 flex justify-center">
                        <div className="h-1 w-24 bg-gray-900 rounded-full opacity-80"></div>
                    </div>
                    <p className="mt-4 text-gray-600 text-base md:text-lg leading-relaxed">
                        Manage dynamic bundles (plugins) and set the default bundle for the tenant/project.
                    </p>
                </div>

                <Card>
                    <CardHeader title="Tenant / Project" subtitle="All registry and bundle props operations use this scope." />
                    <CardBody>
                        <InputField
                            label="Tenant / Project"
                            value={scopeInput}
                            onChange={v => setScopeInput(v)}
                            placeholder={formatScopeLabel(settings.getDefaultTenant(), settings.getDefaultProject())}
                            listId="tenant-project-options"
                        />
                        <datalist id="tenant-project-options">
                            {tenantProjects.map((tp) => {
                                const value = formatScopeLabel(tp.tenant, tp.project);
                                return (
                                    <option key={`${tp.tenant}::${tp.project}`} value={value} label={value} />
                                );
                            })}
                        </datalist>
                        <div className="mt-4 flex items-center gap-3">
                            <Button variant="primary" onClick={applyScope} disabled={!scopeDirty}>
                                Apply scope
                            </Button>
                            {!scopeDirty ? (
                                <span className="text-xs text-gray-500">Scope is up to date.</span>
                            ) : null}
                            {tenantProjectsLoading ? (
                                <span className="text-xs text-gray-500">Loading tenant/projects…</span>
                            ) : null}
                            {!tenantProjectsLoading && tenantProjectsError ? (
                                <span className="text-xs text-red-600">{tenantProjectsError}</span>
                            ) : null}
                        </div>
                    </CardBody>
                </Card>

                {error && (
                    <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {error}
                    </div>
                )}

                <Card>
                    <CardHeader
                        title="Registry"
                        subtitle={`Current bundles stored in the registry. ${authorityDescription} This replaces the runtime registry and descriptor-backed bundle props from that source.`}
                        action={
                            <div className="flex gap-2">
                                <Button variant="secondary" onClick={loadBundles}>Refresh</Button>
                                <Button variant="secondary" onClick={reloadFromAuthority}>{reloadAuthorityLabel}</Button>
                                <Button variant="secondary" onClick={cleanupBundles}>Cleanup old versions</Button>
                            </div>
                        }
                    />
                    <CardBody className="space-y-4">
                        <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600">
                            <div>
                                <strong className="text-gray-800">Current reload source:</strong> {authorityLabel}
                            </div>
                            {authorityDetail ? (
                                <div className="mt-1 break-all">
                                    <strong className="text-gray-800">Location:</strong> {authorityDetail}
                                </div>
                            ) : null}
                        </div>
                        <div className="flex items-center gap-3">
                            <label className="text-sm font-medium text-gray-800">Default bundle</label>
                            <select
                                className="px-3 py-2 border border-gray-200 rounded-lg text-sm"
                                value={defaultBundleId}
                                onChange={e => setDefaultBundleId(e.target.value)}
                            >
                                <option value="">—</option>
                                {bundleList.map(b => (
                                    <option key={b.id} value={b.id}>{b.id}</option>
                                ))}
                            </select>
                            <Button variant="primary" onClick={updateDefault}>Save default</Button>
                        </div>

                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead className="bg-gray-50 border-b border-gray-200/70">
                                    <tr className="text-gray-600">
                                        <th className="px-4 py-3 text-left font-semibold">ID</th>
                                        <th className="px-4 py-3 text-left font-semibold">Name</th>
                                        <th className="px-4 py-3 text-left font-semibold">Path</th>
                                        <th className="px-4 py-3 text-left font-semibold">Module</th>
                                        <th className="px-4 py-3 text-left font-semibold">Singleton</th>
                                        <th className="px-4 py-3 text-left font-semibold">Description</th>
                                        <th className="px-4 py-3 text-left font-semibold">Version</th>
                                        <th className="px-4 py-3 text-left font-semibold">Git</th>
                                        <th className="px-4 py-3 text-right font-semibold">Actions</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-200/70">
                                    {bundleList.map(b => {
                                        const isAdminBundle = b.id === 'kdcube.admin';
                                        return (
                                        <tr key={b.id} className="hover:bg-gray-50/70 transition-colors">
                                            <td className="px-4 py-3 font-semibold text-gray-900">{b.id}</td>
                                            <td className="px-4 py-3 text-gray-700">{b.name || '—'}</td>
                                            <td className="px-4 py-3 text-gray-700">{b.path}</td>
                                            <td className="px-4 py-3 text-gray-700">{b.module || '—'}</td>
                                            <td className="px-4 py-3 text-gray-700">{b.singleton ? 'true' : 'false'}</td>
                                            <td className="px-4 py-3 text-gray-600">{b.description || '—'}</td>
                                            <td className="px-4 py-3 text-gray-600">{b.version || '—'}</td>
                                            <td className="px-4 py-3 text-gray-600">
                                                {b.repo ? (
                                                    <div className="space-y-1">
                                                        <div className="truncate max-w-[220px]" title={b.repo || ''}>{b.repo}</div>
                                                        {b.ref && <div>ref: {b.ref}</div>}
                                                        {b.git_commit && <div className="text-xs text-gray-500">commit: {b.git_commit.slice(0, 12)}</div>}
                                                    </div>
                                                ) : '—'}
                                            </td>
                                            <td className="px-4 py-3 text-right">
                                                <div className="flex justify-end gap-2">
                                                    <Button
                                                        variant="secondary"
                                                        onClick={() => reloadBundleFromAuthority(b.id)}
                                                        disabled={reloadingBundleId === b.id}
                                                        title={`Reload ${b.id} from ${authorityLabel}`}
                                                    >
                                                        {reloadingBundleId === b.id ? 'Reloading…' : 'Reload'}
                                                    </Button>
                                                    <Button
                                                        variant="secondary"
                                                        onClick={() => editBundle(b)}
                                                        disabled={isAdminBundle}
                                                        title={isAdminBundle ? 'Admin bundle is protected' : undefined}
                                                    >
                                                        Edit
                                                    </Button>
                                                    <Button
                                                        variant="danger"
                                                        onClick={() => deleteBundle(b.id)}
                                                        disabled={isAdminBundle}
                                                        title={isAdminBundle ? 'Admin bundle is protected' : undefined}
                                                    >
                                                        Delete
                                                    </Button>
                                                </div>
                                            </td>
                                        </tr>
                                        );
                                    })}
                                    {bundleList.length === 0 && (
                                        <tr>
                                            <td colSpan={9} className="px-4 py-6 text-center text-gray-500">
                                                No bundles configured.
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader
                        title={
                            <div className="flex items-center gap-3">
                                <span>Bundle props</span>
                                {bundleVersion ? (
                                    <div className="flex items-center gap-2">
                                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-gray-900 text-white">
                                            v{bundleVersion}
                                        </span>
                                        <Button variant="secondary" onClick={() => copyText(bundleVersion)}>Copy</Button>
                                    </div>
                                ) : null}
                            </div>
                        }
                        subtitle={`Override bundle props per tenant/project. ${reloadAuthorityLabel} re-applies props from that source; reset from code restores bundle code defaults only.`}
                        action={
                            <div className="flex gap-2">
                                <Button variant="secondary" onClick={loadProps} disabled={!propsBundleId || propsLoading}>
                                    {propsLoading ? 'Loading…' : 'Refresh'}
                                </Button>
                                <Button variant="secondary" onClick={resetPropsFromCode} disabled={!propsBundleId || !!propsDefaultsError}>
                                    Reset from code
                                </Button>
                            </div>
                        }
                    />
                    <CardBody className="space-y-5">
                        <div>
                            <label className="block text-sm font-medium text-gray-800 mb-2">Bundle ID</label>
                            <select
                                className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white text-sm"
                                value={propsBundleId}
                                onChange={e => setPropsBundleId(e.target.value)}
                            >
                                <option value="">—</option>
                                {bundleList.map(b => (
                                    <option key={b.id} value={b.id}>{b.id}</option>
                                ))}
                            </select>
                        </div>

                        <div className="text-xs text-gray-600">
                            Props resolution order: <strong>code defaults → {propsResolutionLabel} → runtime overrides</strong>.
                            The editor shows the full effective props; <strong>Save props</strong> stores exactly what you see.
                            Use dot-path updates for precise changes. <strong>{reloadAuthorityLabel}</strong> rebuilds this Redis props layer from the
                            current source, removes keys no longer present there, and discards runtime overrides.
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <InputField
                                label="Dot-path (props)"
                                value={propsKeyPath}
                                onChange={v => setPropsKeyPath(v)}
                                placeholder="role_models.solver.react.v2.decision.v2.strong.model"
                            />
                            <InputField
                                label="Value (JSON or string)"
                                value={propsValue}
                                onChange={v => setPropsValue(v)}
                                placeholder={'"claude-sonnet-4-6"'}
                            />
                        </div>

                        <div className="flex flex-wrap gap-3">
                            <Button variant="secondary" onClick={() => applyPropsDotPath('set')}>
                                Apply dot-path to editor
                            </Button>
                            <Button variant="secondary" onClick={() => applyPropsDotPath('delete')}>
                                Remove key from editor
                            </Button>
                        </div>

                        {bundleSnapshotPath ? (
                            <div className="flex flex-wrap items-center gap-2 text-xs text-gray-600">
                                <span className="font-semibold">Snapshot path:</span>
                                <code className="px-2 py-1 rounded bg-gray-100 border border-gray-200">{bundleSnapshotPath}</code>
                                <Button variant="secondary" onClick={() => copyText(bundleSnapshotPath)}>Copy path</Button>
                            </div>
                        ) : null}

                        <div>
                            <label className="block text-sm font-medium text-gray-800 mb-2">Props JSON</label>
                            <textarea
                                className="w-full min-h-[220px] px-4 py-3 border border-gray-200/80 rounded-xl bg-white text-sm font-mono focus:outline-none focus:ring-2 focus:ring-gray-900/10"
                                value={propsJson}
                                onChange={e => setPropsJson(e.target.value)}
                                placeholder={`{\n  "key": "value"\n}`}
                            />
                        </div>

                        <div className="flex flex-wrap gap-3">
                            <Button variant="primary" onClick={() => saveProps('replace')}>Save props</Button>
                            <Button variant="secondary" onClick={loadProps} disabled={!propsBundleId || propsLoading}>
                                {propsLoading ? 'Loading…' : 'Reset editor'}
                            </Button>
                        </div>
                        <div className="text-xs text-gray-500">
                            The JSON editor shows the <strong>full effective props</strong> (defaults + overrides).<br />
                            <strong>Save props</strong> stores exactly what you see in the editor.
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-800 mb-2">Code defaults (read-only)</label>
                            {propsDefaultsError ? (
                                <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                                    <div className="font-semibold">Code defaults could not be loaded for this bundle.</div>
                                    <div className="mt-1">
                                        Persisted props are still editable. Reset from code is disabled until the bundle loads.
                                    </div>
                                    <div className="mt-2 font-mono text-xs break-all">
                                        {propsDefaultsError.code || 'BundleLoadError'}: {propsDefaultsError.message || 'Unknown error'}
                                    </div>
                                </div>
                            ) : null}
                            <textarea
                                className="w-full min-h-[180px] px-4 py-3 border border-gray-200/70 rounded-xl bg-gray-50 text-sm font-mono text-gray-600"
                                value={propsDefaultsJson}
                                readOnly
                            />
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader
                        title="Bundle secrets"
                        subtitle="Write-only secrets for bundles. Use dot-path for single keys or JSON for bulk updates."
                    />
                    <CardBody className="space-y-5">
                        <div>
                            <label className="block text-sm font-medium text-gray-800 mb-2">Bundle ID</label>
                            <select
                                className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white text-sm"
                                value={secretsBundleId}
                                onChange={e => setSecretsBundleId(e.target.value)}
                            >
                                <option value="">—</option>
                                {bundleList.map(b => (
                                    <option key={b.id} value={b.id}>{b.id}</option>
                                ))}
                            </select>
                        </div>

                        <div className="text-xs text-gray-600">
                            {secretsLoading ? 'Loading keys…' : (
                                <>
                                    Known keys:{' '}
                                    <code>{(secretsKeys || []).join(', ') || 'none'}</code>
                                </>
                            )}
                        </div>

                        <div>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <InputField
                                    label="Dot-path (secrets)"
                                    value={secretsKeyPath}
                                    onChange={v => setSecretsKeyPath(v)}
                                    placeholder="openai.api_key"
                                />
                            <InputField
                                label="Value (JSON or string)"
                                value={secretsValue}
                                onChange={v => setSecretsValue(v)}
                                placeholder={'"sk-..."'}
                            />
                            </div>
                            <div className="mt-3 flex flex-wrap gap-3">
                                <Button variant="primary" onClick={() => submitSecretDotPath('set')} disabled={secretsSaving}>
                                    {secretsSaving ? 'Saving…' : 'Set key'}
                                </Button>
                                <Button variant="secondary" onClick={() => submitSecretDotPath('clear')} disabled={secretsSaving}>
                                    Clear key
                                </Button>
                            </div>
                            <div className="mt-2 text-xs text-gray-500">
                                Dot-path writes a single key. Values accept JSON (objects/arrays) or raw strings.
                            </div>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-800 mb-2">Bulk secrets JSON (optional)</label>
                            <textarea
                                className="w-full min-h-[180px] px-4 py-3 border border-gray-200/80 rounded-xl bg-white text-sm font-mono focus:outline-none focus:ring-2 focus:ring-gray-900/10"
                                value={secretsJson}
                                onChange={e => setSecretsJson(e.target.value)}
                                placeholder={`{\n  \"openai\": { \"api_key\": \"...\" },\n  \"stripe\": { \"secret_key\": \"...\" }\n}`}
                            />
                        </div>

                        <div className="flex flex-wrap gap-3">
                            <Button variant="primary" onClick={saveSecrets} disabled={secretsSaving}>
                                {secretsSaving ? 'Saving…' : 'Set secrets (JSON)'}
                            </Button>
                            <Button variant="secondary" onClick={clearSecrets} disabled={secretsSaving}>
                                Clear keys (JSON)
                            </Button>
                        </div>
                        {secretsStatus ? (
                            <div className="text-xs text-gray-600">
                                {secretsStatus.mode === 'set' ? 'Saved' : 'Cleared'} keys:{' '}
                                <code>{(secretsStatus.keys || []).join(', ') || 'none'}</code>
                            </div>
                        ) : null}
                        <div className="text-xs text-gray-500">
                            Secrets are stored under <code>bundles.&lt;bundle_id&gt;.secrets.*</code> and are write-only.
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader
                        title="Bundle interface"
                        subtitle="Declared APIs, MCP endpoints, widgets, and scheduled jobs (cron) for the selected bundle."
                        action={<Button variant="secondary" onClick={() => loadBundles()}>Refresh</Button>}
                    />
                    <CardBody className="space-y-5">
                        <div>
                            <label className="block text-sm font-medium text-gray-800 mb-2">Bundle ID</label>
                            <select
                                className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white text-sm"
                                value={interfaceBundleId}
                                onChange={e => setInterfaceBundleId(e.target.value)}
                            >
                                <option value="">—</option>
                                {bundleList.map(b => (
                                    <option key={b.id} value={b.id}>{b.id}</option>
                                ))}
                            </select>
                        </div>

                        {(() => {
                            const b = interfaceBundleId ? bundles[interfaceBundleId] : null;
                            if (!b) return (
                                <div className="text-sm text-gray-500">Select a bundle to view its interface.</div>
                            );

                            const apis = b.apis || [];
                            const mcp = b.mcp_endpoints || [];
                            const widgets = b.widgets || [];
                            const jobs = b.scheduled_jobs || [];
                            const allowedRoles = b.allowed_roles || [];
                            const hasAny = apis.length > 0 || mcp.length > 0 || widgets.length > 0 || jobs.length > 0 || b.on_message || b.on_job;

                            if (!hasAny && allowedRoles.length === 0) return (
                                <div className="text-sm text-gray-500">No interface declared for this bundle.</div>
                            );

                            const bundleEnabledRaw = (editorProps as any)?.enabled?.bundle;
                            const bundleEnabledEffective = bundleEnabledRaw === undefined
                                ? true
                                : !(bundleEnabledRaw === false || bundleEnabledRaw === 0 || (typeof bundleEnabledRaw === 'string' && ['false','disable','disabled','off','0'].includes(bundleEnabledRaw.trim().toLowerCase())));
                            return (
                                <div className="space-y-5">
                                    <div className="flex flex-wrap items-center gap-3 text-xs">
                                        <span className="ml-auto inline-flex items-center gap-2">
                                            <span className="text-gray-700 font-semibold">enabled.bundle:</span>
                                            <label className="inline-flex items-center gap-1.5 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={bundleEnabledEffective}
                                                    onChange={async e => {
                                                        try {
                                                            await saveOverrideAndRefresh(buildEnabledKindPatch('bundle', null, e.target.checked));
                                                        } catch (err: any) {
                                                            setError(err?.message || 'Failed to update enabled.bundle');
                                                        }
                                                    }}
                                                />
                                                <span className={bundleEnabledEffective ? 'text-emerald-700 font-semibold' : 'text-gray-500 font-semibold'}>
                                                    {bundleEnabledEffective ? 'enabled' : 'disabled'}
                                                </span>
                                            </label>
                                        </span>
                                    </div>

                                    <FieldRow
                                        label="Allowed roles"
                                        overridden={b.allowed_roles_overridden}
                                        configurable={Boolean(b.allowed_roles_path)}
                                        onResetToDefault={b.allowed_roles_path && b.allowed_roles_overridden ? async () => {
                                            setBundleRolesError(null);
                                            try {
                                                setBundleRolesSaving(true);
                                                await saveOverrideAndRefresh(buildBundleAllowedRolesPatch(null));
                                                setBundleRolesFlash('Reset ✓');
                                                window.setTimeout(() => setBundleRolesFlash(null), 1800);
                                            } catch (e: any) {
                                                setBundleRolesError(e?.message || 'Reset failed');
                                            } finally {
                                                setBundleRolesSaving(false);
                                            }
                                        } : undefined}
                                        hint={b.allowed_roles_path
                                            ? <>Descriptor path: <code>{b.allowed_roles_path}</code>. Comma-separated. Empty saves an explicit empty list (visible to all).</>
                                            : <>No descriptor path is available — value is hard-coded.</>}
                                    >
                                        <div className="flex items-center gap-2">
                                            <input
                                                value={formBundleRoles}
                                                onChange={e => setFormBundleRoles(e.target.value)}
                                                disabled={!b.allowed_roles_path || bundleRolesSaving || editorPropsLoading}
                                                placeholder="kdcube:role:editor, kdcube:role:viewer"
                                                className="flex-1 px-3 py-2 border border-gray-200/80 rounded-xl bg-white text-sm font-mono disabled:bg-gray-50 disabled:text-gray-400"
                                            />
                                            {b.allowed_roles_path && (
                                                <Button
                                                    variant="primary"
                                                    disabled={bundleRolesSaving || editorPropsLoading}
                                                    onClick={async () => {
                                                        setBundleRolesError(null);
                                                        try {
                                                            setBundleRolesSaving(true);
                                                            await saveOverrideAndRefresh(buildBundleAllowedRolesPatch(parseChips(formBundleRoles)));
                                                            setBundleRolesFlash('Saved ✓');
                                                            window.setTimeout(() => setBundleRolesFlash(null), 1800);
                                                        } catch (e: any) {
                                                            setBundleRolesError(e?.message || 'Save failed');
                                                        } finally {
                                                            setBundleRolesSaving(false);
                                                        }
                                                    }}
                                                >
                                                    {bundleRolesSaving ? 'Saving…' : 'Save'}
                                                </Button>
                                            )}
                                            {bundleRolesFlash && <span className="text-xs text-emerald-700 font-semibold">{bundleRolesFlash}</span>}
                                            {bundleRolesError && <span className="text-xs text-red-700">{bundleRolesError}</span>}
                                        </div>
                                    </FieldRow>

                                    {/* ── events.record.persist ── */}
                                    {(['persist', 'telemetry'] as const).map(section => {
                                        const isPersist = section === 'persist';
                                        const override = readDotPath(editorProps, `events.record.${section}`);
                                        const isOverridden = override != null && typeof override === 'object';
                                        const enabledRaw = readDotPath(editorProps, `events.record.${section}.enabled`);
                                        const enabledEffective = enabledRaw === false ? false : true;
                                        const [formSelector, setFormSelector] = isPersist
                                            ? [formPersistSelector, setFormPersistSelector]
                                            : [formTelemetrySelector, setFormTelemetrySelector];
                                        const [saving, setSaving] = isPersist
                                            ? [persistSelectorSaving, setPersistSelectorSaving]
                                            : [telemetrySelectorSaving, setTelemetrySelectorSaving];
                                        const [flash, setFlash] = isPersist
                                            ? [persistSelectorFlash, setPersistSelectorFlash]
                                            : [telemetrySelectorFlash, setTelemetrySelectorFlash];
                                        const [err, setErr] = isPersist
                                            ? [persistSelectorError, setPersistSelectorError]
                                            : [telemetrySelectorError, setTelemetrySelectorError];
                                        const placeholder = isPersist
                                            ? 'accounting.usage, chat.turn.summary'
                                            : 'accounting.usage, chat.complete, react.tool.call, …';
                                        return (
                                            <FieldRow
                                                key={section}
                                                label={`events.record.${section}`}
                                                overridden={isOverridden}
                                                configurable
                                                onResetToDefault={isOverridden ? async () => {
                                                    setErr(null);
                                                    try {
                                                        setSaving(true);
                                                        await saveOverrideAndRefresh(nestedDotPathPatch(`events.record.${section}`, null));
                                                        setFlash('Reset ✓');
                                                        window.setTimeout(() => setFlash(null), 1800);
                                                    } catch (e: any) {
                                                        setErr(e?.message || 'Reset failed');
                                                    } finally {
                                                        setSaving(false);
                                                    }
                                                } : undefined}
                                                hint={<>
                                                    Bundle-level override for <code>events.record.{section}</code>.
                                                    Overrides assembly.yaml defaults for this bundle. Reset clears the override.
                                                    {isPersist && <> Types emitted through processor comm (e.g. <code>chat.complete</code>) will not appear in artifacts even if listed.</>}
                                                </>}
                                            >
                                                <div className="space-y-2">
                                                    <label className="inline-flex items-center gap-2 cursor-pointer">
                                                        <input
                                                            type="checkbox"
                                                            checked={enabledEffective}
                                                            disabled={saving || editorPropsLoading}
                                                            onChange={async e => {
                                                                setErr(null);
                                                                try {
                                                                    setSaving(true);
                                                                    await saveOverrideAndRefresh(nestedDotPathPatch(`events.record.${section}.enabled`, e.target.checked));
                                                                    setFlash('Saved ✓');
                                                                    window.setTimeout(() => setFlash(null), 1800);
                                                                } catch (ex: any) {
                                                                    setErr(ex?.message || 'Save failed');
                                                                } finally {
                                                                    setSaving(false);
                                                                }
                                                            }}
                                                        />
                                                        <span className={`text-sm font-semibold ${enabledEffective ? 'text-emerald-700' : 'text-gray-500'}`}>
                                                            {enabledEffective ? 'enabled' : 'disabled'}
                                                        </span>
                                                        <span className="text-xs text-gray-400">(events.record.{section}.enabled)</span>
                                                    </label>
                                                    <div className="flex items-center gap-2">
                                                        <input
                                                            value={formSelector}
                                                            onChange={e => setFormSelector(e.target.value)}
                                                            disabled={saving || editorPropsLoading}
                                                            placeholder={placeholder}
                                                            className="flex-1 px-3 py-2 border border-gray-200/80 rounded-xl bg-white text-sm font-mono disabled:bg-gray-50 disabled:text-gray-400"
                                                        />
                                                        <Button
                                                            variant="primary"
                                                            disabled={saving || editorPropsLoading}
                                                            onClick={async () => {
                                                                setErr(null);
                                                                try {
                                                                    setSaving(true);
                                                                    await saveOverrideAndRefresh(nestedDotPathPatch(`events.record.${section}.selector`, parseChips(formSelector)));
                                                                    setFlash('Saved ✓');
                                                                    window.setTimeout(() => setFlash(null), 1800);
                                                                } catch (e: any) {
                                                                    setErr(e?.message || 'Save failed');
                                                                } finally {
                                                                    setSaving(false);
                                                                }
                                                            }}
                                                        >
                                                            {saving ? 'Saving…' : 'Save selector'}
                                                        </Button>
                                                        {flash && <span className="text-xs text-emerald-700 font-semibold">{flash}</span>}
                                                        {err && <span className="text-xs text-red-700">{err}</span>}
                                                    </div>
                                                </div>
                                            </FieldRow>
                                        );
                                    })}

                                    {(b.on_message || b.on_job) && (
                                        <div className="flex flex-wrap gap-3 text-xs text-gray-600">
                                            {b.on_message && (
                                                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-gray-100 border border-gray-200">
                                                    <span className="font-semibold text-gray-800">on_message</span>
                                                    <code>{b.on_message}</code>
                                                </span>
                                            )}
                                            {b.on_job && (
                                                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-gray-100 border border-gray-200">
                                                    <span className="font-semibold text-gray-800">on_job</span>
                                                    <code>{b.on_job}</code>
                                                </span>
                                            )}
                                        </div>
                                    )}

                                    {apis.length > 0 && (
                                        <div>
                                            <div className="text-sm font-semibold text-gray-800 mb-2">
                                                API Endpoints
                                                <span className="ml-2 text-xs font-normal text-gray-500">({apis.length})</span>
                                            </div>
                                            <div className="overflow-x-auto rounded-xl border border-gray-200">
                                                <table className="w-full text-xs">
                                                    <thead className="bg-gray-50 border-b border-gray-200">
                                                        <tr className="text-gray-600">
                                                            <th className="px-3 py-2 text-left font-semibold">Alias</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Method</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Route</th>
                                                            <th className="px-3 py-2 text-left font-semibold">User types</th>
                                                            <th className="px-3 py-2 text-left font-semibold">User types config</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Roles</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Roles config</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Enabled path</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="divide-y divide-gray-100">
                                                        {apis.map((ep, i) => (
                                                            <tr key={i} className="hover:bg-gray-50/70">
                                                                <td className="px-3 py-2 font-mono font-semibold text-gray-900">{ep.alias}</td>
                                                                <td className="px-3 py-2 font-mono uppercase text-blue-700">{ep.http_method}</td>
                                                                <td className="px-3 py-2 font-mono text-gray-700">{ep.route}</td>
                                                                <td className="px-3 py-2 text-gray-600">
                                                                    <OverridableValue
                                                                        value={ep.user_types}
                                                                        defaultValue={ep.user_types_default}
                                                                        overridden={ep.user_types_overridden}
                                                                    />
                                                                </td>
                                                                <td className="px-3 py-2 font-mono text-gray-500">{ep.user_types_path || '—'}</td>
                                                                <td className="px-3 py-2 text-gray-600">
                                                                    <OverridableValue
                                                                        value={ep.roles}
                                                                        defaultValue={ep.roles_default}
                                                                        overridden={ep.roles_overridden}
                                                                    />
                                                                </td>
                                                                <td className="px-3 py-2 font-mono text-gray-500">{ep.roles_path || '—'}</td>
                                                                <td className="px-3 py-2 font-mono text-gray-500">{ep.enabled_path || '—'}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    )}

                                    {widgets.length > 0 && (
                                        <div>
                                            <div className="text-sm font-semibold text-gray-800 mb-2">
                                                Widgets
                                                <span className="ml-2 text-xs font-normal text-gray-500">({widgets.length})</span>
                                            </div>
                                            <div className="overflow-x-auto rounded-xl border border-gray-200">
                                                <table className="w-full text-xs">
                                                    <thead className="bg-gray-50 border-b border-gray-200">
                                                        <tr className="text-gray-600">
                                                            <th className="px-3 py-2 text-left font-semibold">Alias</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Icon</th>
                                                            <th className="px-3 py-2 text-left font-semibold">User types</th>
                                                            <th className="px-3 py-2 text-left font-semibold">User types config</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Roles</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Roles config</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Enabled path</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="divide-y divide-gray-100">
                                                        {widgets.map((w, i) => {
                                                            const iconLabel = w.icon
                                                                ? Object.entries(w.icon).map(([k, v]) => `${k}:${v}`).join(', ')
                                                                : '';
                                                            return (
                                                                <tr key={i} className="hover:bg-gray-50/70">
                                                                    <td className="px-3 py-2 font-mono font-semibold text-gray-900">{w.alias}</td>
                                                                    <td className="px-3 py-2 font-mono text-gray-500">{iconLabel || '—'}</td>
                                                                    <td className="px-3 py-2 text-gray-600">
                                                                        <OverridableValue
                                                                            value={w.user_types}
                                                                            defaultValue={w.user_types_default}
                                                                            overridden={w.user_types_overridden}
                                                                        />
                                                                    </td>
                                                                    <td className="px-3 py-2 font-mono text-gray-500">{w.user_types_path || '—'}</td>
                                                                    <td className="px-3 py-2 text-gray-600">
                                                                        <OverridableValue
                                                                            value={w.roles}
                                                                            defaultValue={w.roles_default}
                                                                            overridden={w.roles_overridden}
                                                                        />
                                                                    </td>
                                                                    <td className="px-3 py-2 font-mono text-gray-500">{w.roles_path || '—'}</td>
                                                                    <td className="px-3 py-2 font-mono text-gray-500">{w.enabled_path || '—'}</td>
                                                                </tr>
                                                            );
                                                        })}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    )}

                                    {mcp.length > 0 && (
                                        <div>
                                            <div className="text-sm font-semibold text-gray-800 mb-2">
                                                MCP Endpoints
                                                <span className="ml-2 text-xs font-normal text-gray-500">({mcp.length})</span>
                                            </div>
                                            <div className="overflow-x-auto rounded-xl border border-gray-200">
                                                <table className="w-full text-xs">
                                                    <thead className="bg-gray-50 border-b border-gray-200">
                                                        <tr className="text-gray-600">
                                                            <th className="px-3 py-2 text-left font-semibold">Alias</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Route</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Transport</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Transport config</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Enabled path</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="divide-y divide-gray-100">
                                                        {mcp.map((ep, i) => (
                                                            <tr key={i} className="hover:bg-gray-50/70">
                                                                <td className="px-3 py-2 font-mono font-semibold text-gray-900">{ep.alias}</td>
                                                                <td className="px-3 py-2 font-mono text-gray-700">{ep.route}</td>
                                                                <td className="px-3 py-2 text-gray-600">
                                                                    <OverridableValue
                                                                        value={ep.transport}
                                                                        defaultValue={ep.transport_default}
                                                                        overridden={ep.transport_overridden}
                                                                    />
                                                                </td>
                                                                <td className="px-3 py-2 font-mono text-gray-500">{ep.transport_config || '—'}</td>
                                                                <td className="px-3 py-2 font-mono text-gray-500">{ep.enabled_path || '—'}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    )}

                                    {jobs.length > 0 && (
                                        <div>
                                            <div className="text-sm font-semibold text-gray-800 mb-2">
                                                Scheduled Jobs (Cron)
                                                <span className="ml-2 text-xs font-normal text-gray-500">({jobs.length})</span>
                                            </div>
                                            <div className="overflow-x-auto rounded-xl border border-gray-200">
                                                <table className="w-full text-xs">
                                                    <thead className="bg-gray-50 border-b border-gray-200">
                                                        <tr className="text-gray-600">
                                                            <th className="px-3 py-2 text-left font-semibold">Alias</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Method</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Cron</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Expr config</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Timezone</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Tz config</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Span</th>
                                                            <th className="px-3 py-2 text-left font-semibold">Enabled path</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="divide-y divide-gray-100">
                                                        {jobs.map((job, i) => (
                                                            <tr key={i} className="hover:bg-gray-50/70">
                                                                <td className="px-3 py-2 font-mono font-semibold text-gray-900">{job.alias || '—'}</td>
                                                                <td className="px-3 py-2 font-mono text-gray-700">{job.method_name}</td>
                                                                <td className="px-3 py-2 font-mono text-gray-700 whitespace-nowrap">
                                                                    <OverridableValue
                                                                        value={job.cron_expression}
                                                                        defaultValue={job.cron_expression_default}
                                                                        overridden={job.cron_expression_overridden}
                                                                    />
                                                                </td>
                                                                <td className="px-3 py-2 font-mono text-gray-500">{job.expr_config || '—'}</td>
                                                                <td className="px-3 py-2 text-gray-600">
                                                                    <OverridableValue
                                                                        value={job.timezone}
                                                                        defaultValue={job.timezone_default}
                                                                        overridden={job.timezone_overridden}
                                                                    />
                                                                </td>
                                                                <td className="px-3 py-2 font-mono text-gray-500">{job.tz_config || '—'}</td>
                                                                <td className="px-3 py-2 text-gray-600">{job.span || '—'}</td>
                                                                <td className="px-3 py-2 font-mono text-gray-500">{job.enabled_path || '—'}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            );
                        })()}
                    </CardBody>
                </Card>

                {interfaceBundleId && bundles[interfaceBundleId] && (
                    <ResourceEditorCard
                        kind="api"
                        bundle={bundles[interfaceBundleId]}
                        editorProps={editorProps}
                        editorPropsLoading={editorPropsLoading}
                        onSave={saveOverrideAndRefresh}
                    />
                )}
                {interfaceBundleId && bundles[interfaceBundleId] && (
                    <ResourceEditorCard
                        kind="widget"
                        bundle={bundles[interfaceBundleId]}
                        editorProps={editorProps}
                        editorPropsLoading={editorPropsLoading}
                        onSave={saveOverrideAndRefresh}
                    />
                )}
                {interfaceBundleId && bundles[interfaceBundleId] && (
                    <ResourceEditorCard
                        kind="mcp"
                        bundle={bundles[interfaceBundleId]}
                        editorProps={editorProps}
                        editorPropsLoading={editorPropsLoading}
                        onSave={saveOverrideAndRefresh}
                    />
                )}
                {interfaceBundleId && bundles[interfaceBundleId] && (
                    <ResourceEditorCard
                        kind="cron"
                        bundle={bundles[interfaceBundleId]}
                        editorProps={editorProps}
                        editorPropsLoading={editorPropsLoading}
                        onSave={saveOverrideAndRefresh}
                    />
                )}

                <Card>
                    <CardHeader
                        title={editingId ? `Edit bundle: ${editingId}` : 'Add bundle'}
                        subtitle="Provide id and either path or repo; module is optional unless using zip/whl."
                        action={editingId ? <Button variant="secondary" onClick={resetForm}>Cancel edit</Button> : undefined}
                    />
                    <CardBody className="space-y-5">
                        <div ref={formRef} />
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <InputField label="Bundle ID" value={form.id} onChange={v => setForm({ ...form, id: v })} placeholder="demo.react@1.0.0" />
                            <InputField label="Name" value={form.name || ''} onChange={v => setForm({ ...form, name: v })} placeholder="Demo bundle" />
                            <InputField label="Path" value={form.path} onChange={v => setForm({ ...form, path: v })} placeholder="/bundles" />
                            <InputField label="Module" value={form.module || ''} onChange={v => setForm({ ...form, module: v })} placeholder="demo.react@1.0.0.entrypoint" />
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <InputField label="Repo" value={form.repo || ''} onChange={v => setForm({ ...form, repo: v })} placeholder="git@github.com:org/repo.git" />
                            <InputField label="Ref" value={form.ref || ''} onChange={v => setForm({ ...form, ref: v })} placeholder="main | v1.2.3 | <commit>" />
                            <InputField label="Subdir" value={form.subdir || ''} onChange={v => setForm({ ...form, subdir: v })} placeholder="path/to/bundles" />
                        </div>
                        <div className="rounded-xl border border-slate-200/70 bg-slate-50 px-4 py-3 text-xs text-slate-700">
                            <div className="font-semibold mb-1">Resolved path preview</div>
                            <div className="space-y-1">
                                <div>
                                    <span className="font-medium">HOST_BUNDLES_PATH:</span>{' '}
                                    <code className="px-1 py-0.5 rounded bg-white border border-slate-200">
                                        {settings.getHostBundlesPath() || '—'}
                                    </code>
                                </div>
                                <div>
                                    <span className="font-medium">BUNDLES_ROOT:</span>{' '}
                                    <code className="px-1 py-0.5 rounded bg-white border border-slate-200">
                                        {settings.getBundlesRoot() || '—'}
                                    </code>
                                </div>
                                <div>
                                    <span className="font-medium">Current path:</span>{' '}
                                    <code className="px-1 py-0.5 rounded bg-white border border-slate-200">{form.path || '—'}</code>
                                </div>
                                {derivedGitPath ? (
                                    <div>
                                        <span className="font-medium">Derived path (repo/ref template):</span>{' '}
                                        <code className="px-1 py-0.5 rounded bg-white border border-slate-200">{derivedGitPath}</code>
                                    </div>
                                ) : null}
                                {derivedHostPath ? (
                                    <div>
                                        <span className="font-medium">Derived path (HOST_BUNDLES_PATH):</span>{' '}
                                        <code className="px-1 py-0.5 rounded bg-white border border-slate-200">{derivedHostPath}</code>
                                    </div>
                                ) : null}
                                {derivedAgenticPath ? (
                                    <div>
                                        <span className="font-medium">Derived path (BUNDLES_ROOT):</span>{' '}
                                        <code className="px-1 py-0.5 rounded bg-white border border-slate-200">{derivedAgenticPath}</code>
                                    </div>
                                ) : null}
                            </div>
                            <div className="mt-2 text-[11px] text-slate-600">
                                Updates take effect when the bundle path changes. For repo bundles, use a new <code>ref</code>.
                                For local bundles, deploy to a new path and update <code>path</code>.
                            </div>
                        </div>
                        <div className="rounded-xl border border-amber-200/60 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                            <div className="font-semibold mb-1">Private Git repos</div>
                            <div>Set one of:</div>
                            <ul className="list-disc pl-5 space-y-1">
                                <li><code>GIT_SSH_KEY_PATH</code> (+ optional <code>GIT_SSH_KNOWN_HOSTS</code>, <code>GIT_SSH_STRICT_HOST_KEY_CHECKING</code>)</li>
                                <li>or embed a token in the URL: <code>https://&lt;token&gt;@github.com/org/repo.git</code></li>
                            </ul>
                        </div>
                        <InputField label="Description" value={form.description || ''} onChange={v => setForm({ ...form, description: v })} placeholder="Optional description" />

                        <div className="flex items-center gap-2">
                            <input
                                type="checkbox"
                                checked={!!form.singleton}
                                onChange={e => setForm({ ...form, singleton: e.target.checked })}
                                className="h-4 w-4"
                            />
                            <span className="text-sm text-gray-700">Singleton (reuse workflow instance)</span>
                        </div>

                        <div className="flex gap-3">
                            <Button variant="primary" onClick={saveBundle}>
                                {editingId ? 'Save changes' : 'Add bundle'}
                            </Button>
                            <Button variant="secondary" onClick={resetForm}>Clear</Button>
                        </div>
                    </CardBody>
                </Card>
            </div>
        </div>
    );
};

const rootEl = document.getElementById('root');
if (rootEl) {
    const root = ReactDOM.createRoot(rootEl);
    root.render(<AIBundleDashboard />);
}
