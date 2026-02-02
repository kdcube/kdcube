// Redis Browser Admin App (TypeScript)

import React, {useEffect, useMemo, useState} from 'react';
import ReactDOM from 'react-dom/client';

interface AppSettings {
    baseUrl: string;
    accessToken: string | null;
    idToken: string | null;
    idTokenHeader: string;
    defaultTenant: string;
    defaultProject: string;
    defaultAppBundleId: string;
}

interface RedisKeyItem {
    key: string;
    type: string;
    ttl: number | null;
}

interface RedisKeyDetails {
    key: string;
    type: string;
    ttl: number | null;
    length?: number | null;
    value: unknown;
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
        const identity = 'REDIS_BROWSER_ADMIN';

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

class RedisBrowserAPI {
    constructor(private basePath: string = '/api/admin/control-plane/redis') {}

    private buildUrl(path: string): string {
        return `${settings.getBaseUrl()}${this.basePath}${path}`;
    }

    async listKeys(prefix: string, cursor: number, limit: number): Promise<{items: RedisKeyItem[]; next_cursor: number}> {
        const params = new URLSearchParams();
        if (prefix) params.set('prefix', prefix);
        params.set('cursor', String(cursor));
        params.set('limit', String(limit));
        const res = await fetch(this.buildUrl(`/keys?${params.toString()}`), {headers: makeAuthHeaders()});
        if (!res.ok) throw new Error('Failed to load keys');
        return await res.json();
    }

    async getKey(key: string, maxItems: number): Promise<RedisKeyDetails> {
        const params = new URLSearchParams();
        params.set('key', key);
        params.set('max_items', String(maxItems));
        const res = await fetch(this.buildUrl(`/key?${params.toString()}`), {headers: makeAuthHeaders()});
        if (!res.ok) throw new Error('Failed to load key');
        return await res.json();
    }
}

const api = new RedisBrowserAPI();

const RedisBrowserAdmin: React.FC = () => {
    const [configReady, setConfigReady] = useState(false);
    const [prefix, setPrefix] = useState('');
    const [cursor, setCursor] = useState(0);
    const [keys, setKeys] = useState<RedisKeyItem[]>([]);
    const [selectedKey, setSelectedKey] = useState('');
    const [manualKey, setManualKey] = useState('');
    const [keyDetails, setKeyDetails] = useState<RedisKeyDetails | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [limit] = useState(200);

    useEffect(() => {
        settings.setupParentListener().then(() => {
            setConfigReady(true);
        });
    }, []);

    const loadKeys = async (reset: boolean) => {
        if (!configReady) return;
        setLoading(true);
        setError(null);
        try {
            const nextCursor = reset ? 0 : cursor;
            const data = await api.listKeys(prefix, nextCursor, limit);
            setCursor(data.next_cursor || 0);
            setKeys((prev) => reset ? data.items : [...prev, ...data.items]);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoading(false);
        }
    };

    const loadKeyDetails = async (key: string) => {
        if (!key) return;
        setLoading(true);
        setError(null);
        setSelectedKey(key);
        try {
            const data = await api.getKey(key, 200);
            setKeyDetails(data);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoading(false);
        }
    };

    const summary = useMemo(() => {
        if (!keyDetails) return 'Select a key to inspect.';
        const ttl = keyDetails.ttl === null ? 'n/a' : keyDetails.ttl;
        const len = keyDetails.length ?? 'n/a';
        return `Type: ${keyDetails.type} • TTL: ${ttl} • Size: ${len}`;
    }, [keyDetails]);

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50">
            <div className="max-w-7xl mx-auto px-6 py-10">
                <div className="flex items-center justify-between mb-8">
                    <div>
                        <h1 className="text-4xl font-semibold text-gray-900 tracking-tight">Redis Browser</h1>
                        <p className="text-gray-600 mt-2">Explore Redis keys and inspect stored values.</p>
                    </div>
                    <div className="text-sm text-gray-500">{loading ? 'Loading…' : 'Ready'}</div>
                </div>

                {error && (
                    <div className="mb-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-red-700 text-sm">
                        {error}
                    </div>
                )}

                <div className="grid grid-cols-1 lg:grid-cols-[340px_1fr] gap-6">
                    <div className="space-y-6">
                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="text-sm font-semibold text-gray-900 mb-3">Key filter</div>
                            <input
                                className="w-full rounded-xl border border-gray-200 px-3 py-2 text-xs"
                                placeholder="Prefix (e.g. kdcube:cp:)"
                                value={prefix}
                                onChange={(e) => setPrefix(e.target.value)}
                            />
                            <div className="flex gap-2 mt-3">
                                <button
                                    className="flex-1 px-3 py-2 rounded-xl text-xs font-semibold bg-gray-900 text-white"
                                    onClick={() => {
                                        setCursor(0);
                                        setKeys([]);
                                        loadKeys(true);
                                    }}
                                >
                                    Search
                                </button>
                                <button
                                    className="flex-1 px-3 py-2 rounded-xl text-xs font-semibold border border-gray-200 text-gray-700"
                                    onClick={() => loadKeys(false)}
                                    disabled={cursor === 0 && keys.length > 0}
                                >
                                    Load more
                                </button>
                            </div>
                        </div>

                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="text-sm font-semibold text-gray-900 mb-3">Keys</div>
                            <div className="max-h-72 overflow-auto divide-y divide-gray-100">
                                {keys.map((item) => (
                                    <button
                                        key={item.key}
                                        className={`w-full text-left px-3 py-2 text-xs transition ${selectedKey === item.key ? 'bg-indigo-50' : 'hover:bg-gray-50'}`}
                                        onClick={() => loadKeyDetails(item.key)}
                                    >
                                        <div className="font-semibold text-gray-900 truncate">{item.key}</div>
                                        <div className="text-gray-500">{item.type} • TTL {item.ttl ?? 'n/a'}</div>
                                    </button>
                                ))}
                                {!keys.length && (
                                    <div className="px-3 py-4 text-xs text-gray-500">No keys loaded.</div>
                                )}
                            </div>
                        </div>
                    </div>

                    <div className="space-y-6">
                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="text-sm font-semibold text-gray-900 mb-3">Inspect key</div>
                            <div className="flex gap-2">
                                <input
                                    className="flex-1 rounded-xl border border-gray-200 px-3 py-2 text-xs"
                                    placeholder="Paste key and press Enter"
                                    value={manualKey}
                                    onChange={(e) => setManualKey(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter') {
                                            e.preventDefault();
                                            loadKeyDetails(manualKey.trim());
                                        }
                                    }}
                                />
                                <button
                                    className="px-3 py-2 rounded-xl text-xs font-semibold bg-indigo-600 text-white"
                                    onClick={() => loadKeyDetails(manualKey.trim())}
                                >
                                    Load
                                </button>
                            </div>
                        </div>

                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="flex items-center justify-between mb-3">
                                <div className="text-sm font-semibold text-gray-900">Key data</div>
                                <div className="text-xs text-gray-500">{selectedKey || '—'}</div>
                            </div>
                            <div className="text-xs text-gray-500 mb-3">{summary}</div>
                            <pre className="text-xs bg-gray-900 text-gray-100 rounded-xl p-4 max-h-[420px] overflow-auto">
                                {keyDetails ? JSON.stringify(keyDetails.value, null, 2) : 'Select a key to load details.'}
                            </pre>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

const rootElement = document.getElementById('root');
if (rootElement) {
    const root = ReactDOM.createRoot(rootElement);
    root.render(<RedisBrowserAdmin />);
}
