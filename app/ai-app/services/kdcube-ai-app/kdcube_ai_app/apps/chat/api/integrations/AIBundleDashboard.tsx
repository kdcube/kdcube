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
    agenticBundlesRoot: string;
}

interface BundleEntry {
    id: string;
    name?: string | null;
    path: string;
    module?: string | null;
    singleton?: boolean | null;
    description?: string | null;
    version?: string | null;
    git_url?: string | null;
    git_ref?: string | null;
    git_subdir?: string | null;
    git_commit?: string | null;
}

interface BundlesResponse {
    available_bundles: Record<string, BundleEntry>;
    default_bundle_id?: string | null;
}

interface BundlesUpdatePayload {
    op: 'merge' | 'replace';
    bundles: Record<string, BundleEntry>;
    default_bundle_id?: string | null;
}

interface BundlePropsPayload {
    tenant: string;
    project: string;
    op: 'replace' | 'merge';
    props: Record<string, unknown>;
}

interface BundleCleanupPayload {
    drop_sys_modules: boolean;
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
    private readonly PLACEHOLDER_AGENTIC_BUNDLES_ROOT = '{{' + 'AGENTIC_BUNDLES_ROOT' + '}}';

    private settings: AppSettings = {
        baseUrl: '{{CHAT_BASE_URL}}',
        accessToken: '{{ACCESS_TOKEN}}',
        idToken: '{{ID_TOKEN}}',
        idTokenHeader: '{{ID_TOKEN_HEADER}}',
        defaultTenant: '{{DEFAULT_TENANT}}',
        defaultProject: '{{DEFAULT_PROJECT}}',
        defaultAppBundleId: '{{DEFAULT_APP_BUNDLE_ID}}',
        hostBundlesPath: '{{HOST_BUNDLES_PATH}}',
        agenticBundlesRoot: '{{AGENTIC_BUNDLES_ROOT}}'
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
            const trimmed = this.settings.baseUrl.replace(/\/+$/, '');
            return trimmed.endsWith('/api') ? trimmed.slice(0, -4) : trimmed;
        } catch {
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

    getHostBundlesPath(): string {
        return this.settings.hostBundlesPath === this.PLACEHOLDER_HOST_BUNDLES_PATH
            ? ''
            : this.settings.hostBundlesPath;
    }

    getAgenticBundlesRoot(): string {
        return this.settings.agenticBundlesRoot === this.PLACEHOLDER_AGENTIC_BUNDLES_ROOT
            ? ''
            : this.settings.agenticBundlesRoot;
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

    setupParentListener(): Promise<boolean> {
        const identity = "INTEGRATIONS_BUNDLES_ADMIN";

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
                    if (config.hostBundlesPath) {
                        updates.hostBundlesPath = config.hostBundlesPath;
                    }
                    if (config.agenticBundlesRoot) {
                        updates.agenticBundlesRoot = config.agenticBundlesRoot;
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
                        'defaultTenant', 'defaultProject', 'defaultAppBundleId',
                        'hostBundlesPath', 'agenticBundlesRoot'
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

    async listBundles(): Promise<BundlesResponse> {
        const response = await this.fetchWithAuth(this.buildUrl('/bundles'));
        return response.json();
    }

    async updateBundles(payload: BundlesUpdatePayload): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl('/bundles'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }
        );
        return response.json();
    }

    async resetFromEnv(): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl('/bundles/reset-env'),
            { method: 'POST' }
        );
        return response.json();
    }

    async cleanupBundles(payload: BundleCleanupPayload): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl('/bundles/cleanup'),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }
        );
        return response.json();
    }

    async getBundleProps(bundleId: string, tenant: string, project: string): Promise<any> {
        const params = new URLSearchParams({ tenant, project });
        const response = await this.fetchWithAuth(
            this.buildUrl(`/bundles/${encodeURIComponent(bundleId)}/props?${params.toString()}`)
        );
        return response.json();
    }

    async setBundleProps(bundleId: string, payload: BundlePropsPayload): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl(`/bundles/${encodeURIComponent(bundleId)}/props`),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }
        );
        return response.json();
    }

    async resetBundlePropsFromCode(bundleId: string, payload: { tenant: string; project: string }): Promise<any> {
        const response = await this.fetchWithAuth(
            this.buildUrl(`/bundles/${encodeURIComponent(bundleId)}/props/reset-code`),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }
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
}> = ({ label, value, onChange, placeholder }) => (
    <div>
        <label className="block text-sm font-medium text-gray-800 mb-2">{label}</label>
        <input
            className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10"
            value={value}
            onChange={e => onChange(e.target.value)}
            placeholder={placeholder}
        />
    </div>
);

// =============================================================================
// Main Component
// =============================================================================

const AIBundleDashboard: React.FC = () => {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [bundles, setBundles] = useState<Record<string, BundleEntry>>({});
    const [defaultBundleId, setDefaultBundleId] = useState<string>('');
    const [editingId, setEditingId] = useState<string | null>(null);
    const [propsTenant, setPropsTenant] = useState(settings.getDefaultTenant());
    const [propsProject, setPropsProject] = useState(settings.getDefaultProject());
    const [propsBundleId, setPropsBundleId] = useState<string>('');
    const [propsJson, setPropsJson] = useState<string>('{}');
    const [propsDefaultsJson, setPropsDefaultsJson] = useState<string>('{}');
    const [propsLoading, setPropsLoading] = useState<boolean>(false);
    const bundleVersion = useMemo(() => {
        try {
            const parsed = JSON.parse(propsDefaultsJson || '{}');
            return typeof parsed?.bundle_version === 'string' ? parsed.bundle_version : '';
        } catch {
            return '';
        }
    }, [propsDefaultsJson]);
    const bundleSnapshotPath = useMemo(() => {
        if (!bundleVersion || !propsBundleId || !propsTenant || !propsProject) return '';
        return `cb/tenants/${propsTenant}/projects/${propsProject}/ai-bundle-snapshots/${propsBundleId}.${bundleVersion}.zip`;
    }, [bundleVersion, propsBundleId, propsTenant, propsProject]);

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
        git_url: '',
        git_ref: '',
        git_subdir: ''
    });
    const formRef = useRef<HTMLDivElement | null>(null);

    const bundleList = useMemo(() => Object.values(bundles).sort((a, b) => a.id.localeCompare(b.id)), [bundles]);
    const derivedGitPath = useMemo(() => {
        if (!form.git_url) return '';
        const id = form.id || '<bundle_id>';
        const ref = (form.git_ref || '').trim();
        const subdir = (form.git_subdir || '').trim();
        const base = `<bundles_root>/${id}${ref ? `__${ref}` : ''}`;
        return subdir ? `${base}/${subdir}` : base;
    }, [form.git_url, form.git_ref, form.git_subdir, form.id]);
    const derivedHostPath = useMemo(() => {
        if (!form.git_url) return '';
        const root = settings.getHostBundlesPath() || '<HOST_BUNDLES_PATH>';
        const id = form.id || '<bundle_id>';
        const ref = (form.git_ref || '').trim();
        const subdir = (form.git_subdir || '').trim();
        const base = `${root.replace(/\/+$/, '')}/${id}${ref ? `__${ref}` : ''}`;
        return subdir ? `${base}/${subdir}` : base;
    }, [form.git_url, form.git_ref, form.git_subdir, form.id]);
    const derivedAgenticPath = useMemo(() => {
        if (!form.git_url) return '';
        const root = settings.getAgenticBundlesRoot() || '<AGENTIC_BUNDLES_ROOT>';
        const id = form.id || '<bundle_id>';
        const ref = (form.git_ref || '').trim();
        const subdir = (form.git_subdir || '').trim();
        const base = `${root.replace(/\/+$/, '')}/${id}${ref ? `__${ref}` : ''}`;
        return subdir ? `${base}/${subdir}` : base;
    }, [form.git_url, form.git_ref, form.git_subdir, form.id]);

    const loadBundles = async () => {
        try {
            setLoading(true);
            const data = await api.listBundles();
            setBundles(data.available_bundles || {});
            setDefaultBundleId(data.default_bundle_id || '');
            if (!propsBundleId) {
                setPropsBundleId(data.default_bundle_id || '');
            }
            setError(null);
        } catch (e: any) {
            setError(e.message || 'Failed to load bundles');
        } finally {
            setLoading(false);
        }
    };

    const loadProps = async () => {
        if (!propsBundleId) return;
        try {
            setPropsLoading(true);
            const data = await api.getBundleProps(propsBundleId, propsTenant, propsProject);
            const props = data.props || {};
            const defaults = data.defaults || {};
            setPropsJson(JSON.stringify(props, null, 2));
            setPropsDefaultsJson(JSON.stringify(defaults, null, 2));
        } catch (e: any) {
            setError(e.message || 'Failed to load bundle props');
        } finally {
            setPropsLoading(false);
        }
    };

    useEffect(() => {
        settings.onConfigReceived(() => loadBundles());
        settings.setupParentListener().then(loadBundles).catch(loadBundles);
    }, []);

    useEffect(() => {
        if (!propsBundleId) return;
        loadProps();
    }, [propsBundleId, propsTenant, propsProject]);

    const resetForm = () => {
        setEditingId(null);
        setForm({ id: '', name: '', path: '', module: '', singleton: false, description: '', git_url: '', git_ref: '', git_subdir: '' });
    };

    const saveBundle = async () => {
        if (!form.id || (!form.path && !form.git_url)) {
            setError('Bundle id is required. Provide either a path or a git URL.');
            return;
        }
        try {
            const payload = { ...form, singleton: !!form.singleton };
            await api.updateBundles({
                op: 'merge',
                bundles: { [payload.id]: payload },
                default_bundle_id: defaultBundleId || undefined
            });
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
            });
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
            git_url: entry.git_url || '',
            git_ref: entry.git_ref || '',
            git_subdir: entry.git_subdir || ''
        });
        setTimeout(() => formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0);
    };

    const updateDefault = async () => {
        try {
            await api.updateBundles({
                op: 'merge',
                bundles: {},
                default_bundle_id: defaultBundleId || undefined
            });
            await loadBundles();
        } catch (e: any) {
            setError(e.message || 'Failed to update default bundle');
        }
    };

    const resetFromEnv = async () => {
        try {
            await api.resetFromEnv();
            await loadBundles();
        } catch (e: any) {
            setError(e.message || 'Failed to reset from env');
        }
    };

    const cleanupBundles = async () => {
        try {
            await api.cleanupBundles({ drop_sys_modules: true });
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
            const parsed = propsJson.trim() ? JSON.parse(propsJson) : {};
            await api.setBundleProps(propsBundleId, {
                tenant: propsTenant,
                project: propsProject,
                op,
                props: parsed
            });
            await loadProps();
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
            await api.resetBundlePropsFromCode(propsBundleId, {
                tenant: propsTenant,
                project: propsProject
            });
            await loadProps();
        } catch (e: any) {
            setError(e.message || 'Failed to reset props from code');
        }
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

                {error && (
                    <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {error}
                    </div>
                )}

                <Card>
                    <CardHeader
                        title="Registry"
                        subtitle="Current bundles stored in the registry."
                        action={
                            <div className="flex gap-2">
                                <Button variant="secondary" onClick={loadBundles}>Refresh</Button>
                                <Button variant="secondary" onClick={resetFromEnv}>Reset from env</Button>
                                <Button variant="secondary" onClick={cleanupBundles}>Cleanup old versions</Button>
                            </div>
                        }
                    />
                    <CardBody className="space-y-4">
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
                                    {bundleList.map(b => (
                                        <tr key={b.id} className="hover:bg-gray-50/70 transition-colors">
                                            <td className="px-4 py-3 font-semibold text-gray-900">{b.id}</td>
                                            <td className="px-4 py-3 text-gray-700">{b.name || '—'}</td>
                                            <td className="px-4 py-3 text-gray-700">{b.path}</td>
                                            <td className="px-4 py-3 text-gray-700">{b.module || '—'}</td>
                                            <td className="px-4 py-3 text-gray-700">{b.singleton ? 'true' : 'false'}</td>
                                            <td className="px-4 py-3 text-gray-600">{b.description || '—'}</td>
                                            <td className="px-4 py-3 text-gray-600">{b.version || '—'}</td>
                                            <td className="px-4 py-3 text-gray-600">
                                                {b.git_url ? (
                                                    <div className="space-y-1">
                                                        <div className="truncate max-w-[220px]" title={b.git_url || ''}>{b.git_url}</div>
                                                        {b.git_ref && <div>ref: {b.git_ref}</div>}
                                                        {b.git_commit && <div className="text-xs text-gray-500">commit: {b.git_commit.slice(0, 12)}</div>}
                                                    </div>
                                                ) : '—'}
                                            </td>
                                            <td className="px-4 py-3 text-right">
                                                <div className="flex justify-end gap-2">
                                                    <Button variant="secondary" onClick={() => editBundle(b)}>Edit</Button>
                                                    <Button variant="danger" onClick={() => deleteBundle(b.id)}>Delete</Button>
                                                </div>
                                            </td>
                                        </tr>
                                    ))}
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
                        subtitle="Override bundle props per tenant/project. Reset to code defaults when needed."
                        action={
                            <div className="flex gap-2">
                                <Button variant="secondary" onClick={loadProps} disabled={!propsBundleId || propsLoading}>
                                    {propsLoading ? 'Loading…' : 'Refresh'}
                                </Button>
                                <Button variant="secondary" onClick={resetPropsFromCode} disabled={!propsBundleId}>
                                    Reset from code
                                </Button>
                            </div>
                        }
                    />
                    <CardBody className="space-y-5">
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <InputField label="Tenant" value={propsTenant} onChange={v => setPropsTenant(v)} placeholder="home" />
                            <InputField label="Project" value={propsProject} onChange={v => setPropsProject(v)} placeholder="demo" />
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
                            <Button variant="secondary" onClick={() => saveProps('merge')}>Merge props</Button>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-800 mb-2">Code defaults (read-only)</label>
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
                        title={editingId ? `Edit bundle: ${editingId}` : 'Add bundle'}
                        subtitle="Provide id and either path or git URL; module is optional unless using zip/whl."
                        action={editingId ? <Button variant="secondary" onClick={resetForm}>Cancel edit</Button> : undefined}
                    />
                    <CardBody className="space-y-5">
                        <div ref={formRef} />
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <InputField label="Bundle ID" value={form.id} onChange={v => setForm({ ...form, id: v })} placeholder="kdcube.demo.1" />
                            <InputField label="Name" value={form.name || ''} onChange={v => setForm({ ...form, name: v })} placeholder="Demo bundle" />
                            <InputField label="Path" value={form.path} onChange={v => setForm({ ...form, path: v })} placeholder="/bundles/demo" />
                            <InputField label="Module" value={form.module || ''} onChange={v => setForm({ ...form, module: v })} placeholder="demo.entrypoint" />
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <InputField label="Git URL" value={form.git_url || ''} onChange={v => setForm({ ...form, git_url: v })} placeholder="https://github.com/org/repo.git" />
                            <InputField label="Git Ref" value={form.git_ref || ''} onChange={v => setForm({ ...form, git_ref: v })} placeholder="main | v1.2.3 | <commit>" />
                            <InputField label="Git Subdir" value={form.git_subdir || ''} onChange={v => setForm({ ...form, git_subdir: v })} placeholder="optional/subdir/inside/repo" />
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
                                    <span className="font-medium">AGENTIC_BUNDLES_ROOT:</span>{' '}
                                    <code className="px-1 py-0.5 rounded bg-white border border-slate-200">
                                        {settings.getAgenticBundlesRoot() || '—'}
                                    </code>
                                </div>
                                <div>
                                    <span className="font-medium">Current path:</span>{' '}
                                    <code className="px-1 py-0.5 rounded bg-white border border-slate-200">{form.path || '—'}</code>
                                </div>
                                {derivedGitPath ? (
                                    <div>
                                        <span className="font-medium">Derived path (git template):</span>{' '}
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
                                        <span className="font-medium">Derived path (AGENTIC_BUNDLES_ROOT):</span>{' '}
                                        <code className="px-1 py-0.5 rounded bg-white border border-slate-200">{derivedAgenticPath}</code>
                                    </div>
                                ) : null}
                            </div>
                            <div className="mt-2 text-[11px] text-slate-600">
                                Updates take effect when the bundle path changes. For git bundles, use a new <code>git_ref</code>.
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
