// Conversation Browser Admin App (TypeScript)

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

interface TenantProjectItem {
    tenant: string;
    project: string;
    schema: string;
    source: string;
}

interface ConversationListItem {
    conversation_id: string;
    last_activity_at?: string | null;
    started_at?: string | null;
    title?: string | null;
}

interface ConversationDetails {
    user_id: string;
    conversation_id: string;
    conversation_title?: string | null;
    started_at?: string | null;
    last_activity_at?: string | null;
    turns?: Array<{ turn_id: string; ts_first?: string | null; ts_last?: string | null; artifacts?: unknown[] }>;
}

interface ConversationFetch {
    user_id: string;
    conversation_id: string;
    turns: Array<{ turn_id: string; artifacts: unknown[] }>;
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
        const identity = 'CONVERSATION_BROWSER_ADMIN';

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

class ConversationBrowserAPI {
    constructor(private basePath: string = '/api/admin/control-plane/conversations') {}

    private buildUrl(path: string): string {
        return `${settings.getBaseUrl()}${this.basePath}${path}`;
    }

    async listTenantProjects(): Promise<TenantProjectItem[]> {
        const res = await fetch(this.buildUrl('/tenant-projects'), {headers: makeAuthHeaders()});
        if (!res.ok) throw new Error('Failed to load tenant/projects');
        const data = await res.json();
        return data.items || [];
    }

    async listUsers(tenant: string, project: string, search?: string): Promise<string[]> {
        const params = new URLSearchParams();
        if (search) params.set('search', search);
        const res = await fetch(this.buildUrl(`/${tenant}/${project}/users?${params.toString()}`), {
            headers: makeAuthHeaders()
        });
        if (!res.ok) throw new Error('Failed to load users');
        const data = await res.json();
        return data.items || [];
    }

    async listConversations(tenant: string, project: string, userId: string): Promise<ConversationListItem[]> {
        const res = await fetch(this.buildUrl(`/${tenant}/${project}/${userId}/conversations`), {
            headers: makeAuthHeaders()
        });
        if (!res.ok) throw new Error('Failed to load conversations');
        const data = await res.json();
        return data.items || [];
    }

    async getConversationDetails(tenant: string, project: string, userId: string, conversationId: string): Promise<ConversationDetails> {
        const res = await fetch(
            this.buildUrl(`/${tenant}/${project}/${userId}/conversations/${conversationId}/details`),
            {headers: makeAuthHeaders()}
        );
        if (!res.ok) throw new Error('Failed to load conversation details');
        return await res.json();
    }

    async fetchConversation(tenant: string, project: string, userId: string, conversationId: string): Promise<ConversationFetch> {
        const res = await fetch(
            this.buildUrl(`/${tenant}/${project}/${userId}/conversations/${conversationId}/fetch`),
            {
                method: 'POST',
                headers: makeAuthHeaders({'Content-Type': 'application/json'}),
                body: JSON.stringify({materialize: true})
            }
        );
        if (!res.ok) throw new Error('Failed to fetch conversation');
        return await res.json();
    }

    async exportUserExcel(tenant: string, project: string, userId: string, conversationIds?: string[]): Promise<Blob> {
        const params = new URLSearchParams();
        if (conversationIds && conversationIds.length) {
            params.set('conversation_ids', conversationIds.join(','));
        }
        const suffix = params.toString() ? `?${params.toString()}` : '';
        const res = await fetch(this.buildUrl(`/${tenant}/${project}/${userId}/export.xlsx${suffix}`), {
            headers: makeAuthHeaders()
        });
        if (!res.ok) throw new Error('Failed to export Excel');
        return await res.blob();
    }
}

const api = new ConversationBrowserAPI();

const ConversationBrowserAdmin: React.FC = () => {
    const [configReady, setConfigReady] = useState(false);
    const [tenantProjects, setTenantProjects] = useState<TenantProjectItem[]>([]);
    const [tenant, setTenant] = useState(settings.getDefaultTenant());
    const [project, setProject] = useState(settings.getDefaultProject());
    const [userSearch, setUserSearch] = useState('');
    const [users, setUsers] = useState<string[]>([]);
    const [selectedUser, setSelectedUser] = useState<string>('');
    const [conversations, setConversations] = useState<ConversationListItem[]>([]);
    const [selectedConversationId, setSelectedConversationId] = useState<string>('');
    const [selectedConversationIds, setSelectedConversationIds] = useState<string[]>([]);
    const [manualConversationId, setManualConversationId] = useState('');
    const [conversationDetails, setConversationDetails] = useState<ConversationDetails | null>(null);
    const [conversationFetch, setConversationFetch] = useState<ConversationFetch | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const tenantProjectOptions = useMemo(() => tenantProjects.map(tp => ({
        value: `${tp.tenant}::${tp.project}`,
        label: `${tp.tenant} / ${tp.project}`,
        item: tp
    })), [tenantProjects]);

    useEffect(() => {
        settings.setupParentListener().then(() => {
            setTenant(settings.getDefaultTenant());
            setProject(settings.getDefaultProject());
            setConfigReady(true);
        });
    }, []);

    useEffect(() => {
        if (!configReady) return;
        api.listTenantProjects()
            .then(setTenantProjects)
            .catch((err) => setError(err.message));
    }, [configReady]);

    useEffect(() => {
        if (!configReady || !tenant || !project) return;
        setLoading(true);
        setError(null);
        setSelectedUser('');
        setUsers([]);
        setConversations([]);
        setSelectedConversationId('');
        setSelectedConversationIds([]);
        setManualConversationId('');
        setConversationDetails(null);
        setConversationFetch(null);
        api.listUsers(tenant, project, userSearch)
            .then(setUsers)
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }, [tenant, project, userSearch, configReady]);

    useEffect(() => {
        if (!selectedUser || !tenant || !project) return;
        setLoading(true);
        setError(null);
        setConversations([]);
        setSelectedConversationId('');
        setSelectedConversationIds([]);
        setManualConversationId('');
        setConversationDetails(null);
        setConversationFetch(null);
        api.listConversations(tenant, project, selectedUser)
            .then(setConversations)
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }, [selectedUser, tenant, project]);

    const loadConversation = async (conversationId: string) => {
        if (!selectedUser) return;
        setLoading(true);
        setError(null);
        setSelectedConversationId(conversationId);
        try {
            const [details, fetched] = await Promise.all([
                api.getConversationDetails(tenant, project, selectedUser, conversationId),
                api.fetchConversation(tenant, project, selectedUser, conversationId)
            ]);
            setConversationDetails(details);
            setConversationFetch(fetched);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoading(false);
        }
    };

    const downloadExcel = async () => {
        if (!selectedUser) return;
        setLoading(true);
        setError(null);
        try {
            const blob = await api.exportUserExcel(
                tenant,
                project,
                selectedUser,
                selectedConversationIds.length ? selectedConversationIds : undefined
            );
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `${selectedUser}_conversations.xlsx`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setLoading(false);
        }
    };

    const addConversationId = (conversationId: string) => {
        const trimmed = conversationId.trim();
        if (!trimmed) return;
        setSelectedConversationIds((prev) => {
            if (prev.includes(trimmed)) return prev;
            return [...prev, trimmed];
        });
    };

    const removeConversationId = (conversationId: string) => {
        setSelectedConversationIds((prev) => prev.filter((cid) => cid !== conversationId));
    };

    const toggleConversationId = (conversationId: string) => {
        setSelectedConversationIds((prev) => (
            prev.includes(conversationId)
                ? prev.filter((cid) => cid !== conversationId)
                : [...prev, conversationId]
        ));
    };

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
            <div className="max-w-7xl mx-auto px-6 py-10">
                <div className="flex items-center justify-between mb-8">
                    <div>
                        <h1 className="text-4xl font-semibold text-gray-900 tracking-tight">Conversation Browser</h1>
                        <p className="text-gray-600 mt-2">Inspect user conversations across tenant projects.</p>
                    </div>
                    <div className="text-sm text-gray-500">
                        {loading ? 'Loading…' : 'Ready'}
                    </div>
                </div>

                {error && (
                    <div className="mb-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-red-700 text-sm">
                        {error}
                    </div>
                )}

                <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-6">
                    <div className="space-y-6">
                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="text-sm font-semibold text-gray-900 mb-3">Scope</div>
                            <div className="space-y-3">
                                <label className="block text-xs font-semibold text-gray-600">Tenant / Project</label>
                                <select
                                    className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm"
                                    value={`${tenant}::${project}`}
                                    onChange={(e) => {
                                        const [t, p] = e.target.value.split('::');
                                        setTenant(t || '');
                                        setProject(p || '');
                                    }}
                                >
                                    {tenantProjectOptions.map((opt) => (
                                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                                    ))}
                                </select>
                                <div className="grid grid-cols-2 gap-2">
                                    <input
                                        className="rounded-xl border border-gray-200 px-3 py-2 text-xs"
                                        value={tenant}
                                        onChange={(e) => setTenant(e.target.value)}
                                        placeholder="Tenant"
                                    />
                                    <input
                                        className="rounded-xl border border-gray-200 px-3 py-2 text-xs"
                                        value={project}
                                        onChange={(e) => setProject(e.target.value)}
                                        placeholder="Project"
                                    />
                                </div>
                                <p className="text-xs text-gray-500">Schema: {tenant && project ? `${tenant}_${project}` : '—'}</p>
                            </div>
                        </div>

                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="flex items-center justify-between mb-3">
                                <div className="text-sm font-semibold text-gray-900">Users</div>
                                <button
                                    className="text-xs text-blue-600 font-semibold"
                                    onClick={() => api.listUsers(tenant, project, userSearch).then(setUsers)}
                                >
                                    Refresh
                                </button>
                            </div>
                            <input
                                className="w-full rounded-xl border border-gray-200 px-3 py-2 text-xs mb-3"
                                placeholder="Search users"
                                value={userSearch}
                                onChange={(e) => setUserSearch(e.target.value)}
                            />
                            <div className="max-h-72 overflow-auto space-y-2">
                                {users.map((user) => (
                                    <button
                                        key={user}
                                        className={`w-full text-left px-3 py-2 rounded-xl text-xs font-semibold transition ${selectedUser === user ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}
                                        onClick={() => setSelectedUser(user)}
                                    >
                                        {user}
                                    </button>
                                ))}
                                {!users.length && (
                                    <div className="text-xs text-gray-500">No users found.</div>
                                )}
                            </div>
                            <button
                                className="mt-4 w-full px-4 py-2 rounded-xl text-sm font-semibold bg-gray-900 text-white disabled:opacity-50"
                                onClick={downloadExcel}
                                disabled={!selectedUser}
                            >
                                Download Excel for User
                            </button>
                        </div>
                    </div>

                    <div className="space-y-6">
                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="flex items-center justify-between mb-3">
                                <div className="text-sm font-semibold text-gray-900">Conversations</div>
                                <div className="text-xs text-gray-500">{selectedUser || 'Select a user'}</div>
                            </div>
                            <div className="max-h-52 overflow-auto divide-y divide-gray-100">
                                {conversations.map((conv) => (
                                    <div
                                        key={conv.conversation_id}
                                        className={`flex items-center justify-between px-3 py-2 text-xs transition ${selectedConversationId === conv.conversation_id ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                                    >
                                        <button
                                            onClick={() => loadConversation(conv.conversation_id)}
                                            className="flex-1 text-left"
                                        >
                                            <div className="font-semibold text-gray-900">
                                                {conv.title || conv.conversation_id}
                                            </div>
                                            <div className="text-gray-500">
                                                {conv.last_activity_at || conv.started_at || '—'}
                                            </div>
                                        </button>
                                        <button
                                            className={`ml-3 px-2 py-1 rounded-lg border text-[10px] font-semibold ${selectedConversationIds.includes(conv.conversation_id) ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-200'}`}
                                            onClick={() => toggleConversationId(conv.conversation_id)}
                                        >
                                            {selectedConversationIds.includes(conv.conversation_id) ? 'Added' : 'Add'}
                                        </button>
                                    </div>
                                ))}
                                {!conversations.length && (
                                    <div className="px-3 py-4 text-xs text-gray-500">No conversations loaded.</div>
                                )}
                            </div>
                            <div className="mt-4 border-t border-gray-100 pt-4">
                                <div className="text-xs font-semibold text-gray-700 mb-2">Report selection</div>
                                <input
                                    className="w-full rounded-xl border border-gray-200 px-3 py-2 text-xs"
                                    placeholder="Paste conversation id and press Enter"
                                    value={manualConversationId}
                                    onChange={(e) => setManualConversationId(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter') {
                                            e.preventDefault();
                                            addConversationId(manualConversationId);
                                            setManualConversationId('');
                                        }
                                    }}
                                />
                                <div className="flex flex-wrap gap-2 mt-3">
                                    {selectedConversationIds.map((cid) => (
                                        <span
                                            key={cid}
                                            className="inline-flex items-center gap-2 px-2 py-1 rounded-full bg-blue-50 text-[11px] font-semibold text-blue-700 border border-blue-100"
                                        >
                                            <span className="truncate max-w-[180px]">{cid}</span>
                                            <button
                                                className="text-blue-700 hover:text-blue-900"
                                                onClick={() => removeConversationId(cid)}
                                            >
                                                x
                                            </button>
                                        </span>
                                    ))}
                                    {!selectedConversationIds.length && (
                                        <span className="text-[11px] text-gray-400">No conversations selected.</span>
                                    )}
                                </div>
                                <div className="text-[11px] text-gray-400 mt-2">
                                    {selectedConversationIds.length ? `${selectedConversationIds.length} selected` : 'Exporting with no selections includes all conversations.'}
                                </div>
                            </div>
                        </div>

                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="flex items-center justify-between mb-3">
                                <div className="text-sm font-semibold text-gray-900">Conversation JSON</div>
                                <div className="text-xs text-gray-500">{selectedConversationId || '—'}</div>
                            </div>
                            {conversationDetails && (
                                <div className="text-xs text-gray-500 mb-3">
                                    Turns: {conversationDetails.turns?.length || 0}
                                </div>
                            )}
                            <pre className="text-xs bg-gray-900 text-gray-100 rounded-xl p-4 max-h-[420px] overflow-auto">
                                {conversationFetch ? JSON.stringify(conversationFetch, null, 2) : 'Select a conversation to load JSON.'}
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
    root.render(<ConversationBrowserAdmin />);
}
