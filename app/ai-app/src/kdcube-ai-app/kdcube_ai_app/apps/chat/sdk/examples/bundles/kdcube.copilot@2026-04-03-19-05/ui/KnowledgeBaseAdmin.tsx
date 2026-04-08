import { useEffect, useMemo, useRef, useState } from 'react';

type TurnKind = 'regular' | 'followup' | 'steer';
type AdminSection = 'chat' | 'settings' | 'workspace' | 'conversations';

interface AppSettings {
    baseUrl: string;
    accessToken: string | null;
    idToken: string | null;
    idTokenHeader: string;
    defaultTenant: string;
    defaultProject: string;
    defaultAppBundleId: string;
}

interface RepoConfig {
    id?: string;
    label?: string;
    source?: string;
    branch?: string;
    slot?: string;
}

interface SyncRepoStatus {
    repo_type?: string;
    slot?: string;
    repo_id?: string;
    label?: string;
    source?: string;
    branch?: string;
    local_path?: string;
    current_branch?: string;
    head?: string;
    dirty?: boolean;
    action?: string;
}

interface WidgetPayload {
    ok?: boolean;
    error?: string;
    user_id?: string;
    config?: {
        content_repos?: RepoConfig[];
        output_repo?: RepoConfig;
        last_sync?: {
            synced_at?: string;
            repo_statuses?: SyncRepoStatus[];
            workspace_root?: string;
        } | null;
    };
    secrets?: {
        has_git_pat?: boolean;
        has_anthropic_api_key?: boolean;
        has_claude_code_key?: boolean;
    };
    conversations?: ConversationSummary[];
    selected_conversation_id?: string | null;
    current_conversation?: ConversationDocument | null;
    workspace_root?: string | null;
}

interface ConversationSummary {
    conversation_id: string;
    title?: string;
    updated_at?: string;
    created_at?: string;
    message_count?: number;
    last_role?: string | null;
    last_preview?: string;
}

interface ConversationMessage {
    message_id?: string;
    role?: string;
    text?: string;
    created_at?: string;
    metadata?: Record<string, unknown>;
}

interface ConversationDocument {
    conversation_id: string;
    title?: string;
    created_at?: string;
    updated_at?: string;
    messages?: ConversationMessage[];
}

interface ProfilePayload {
    session_id?: string;
}

const INITIAL_DATA: WidgetPayload = __KNOWLEDGE_BASE_ADMIN_JSON__;

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}';
const STREAM_ID_HEADER_NAME = 'KDC-Stream-ID';

function isTemplatePlaceholder(value: string | null | undefined): boolean {
    return typeof value === 'string' && value.includes('{{') && value.includes('}}');
}

class SettingsManager {
    private settings: AppSettings = {
        baseUrl: PLACEHOLDER_BASE_URL,
        accessToken: PLACEHOLDER_ACCESS_TOKEN,
        idToken: PLACEHOLDER_ID_TOKEN,
        idTokenHeader: PLACEHOLDER_ID_TOKEN_HEADER,
        defaultTenant: PLACEHOLDER_TENANT,
        defaultProject: PLACEHOLDER_PROJECT,
        defaultAppBundleId: PLACEHOLDER_BUNDLE_ID,
    };

    private configReceivedCallback: (() => void) | null = null;

    getBaseUrl(): string {
        if (isTemplatePlaceholder(this.settings.baseUrl)) {
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
        if (!this.settings.accessToken || isTemplatePlaceholder(this.settings.accessToken)) return null;
        return this.settings.accessToken;
    }

    getIdToken(): string | null {
        if (!this.settings.idToken || isTemplatePlaceholder(this.settings.idToken)) return null;
        return this.settings.idToken;
    }

    getIdTokenHeader(): string {
        if (!this.settings.idTokenHeader || isTemplatePlaceholder(this.settings.idTokenHeader)) return 'X-ID-Token';
        return this.settings.idTokenHeader;
    }

    getTenant(): string {
        return isTemplatePlaceholder(this.settings.defaultTenant) ? '' : this.settings.defaultTenant;
    }

    getProject(): string {
        return isTemplatePlaceholder(this.settings.defaultProject) ? '' : this.settings.defaultProject;
    }

    getBundleId(): string {
        return !this.settings.defaultAppBundleId || isTemplatePlaceholder(this.settings.defaultAppBundleId)
            ? 'kdcube.copilot@2026-04-03-19-05'
            : this.settings.defaultAppBundleId;
    }

    hasPlaceholders(): boolean {
        return [
            this.settings.baseUrl,
            this.settings.accessToken,
            this.settings.idToken,
            this.settings.idTokenHeader,
            this.settings.defaultTenant,
            this.settings.defaultProject,
            this.settings.defaultAppBundleId,
        ].some((value) => isTemplatePlaceholder(value ?? undefined));
    }

    update(partial: Partial<AppSettings>): void {
        this.settings = { ...this.settings, ...partial };
    }

    onConfigReceived(callback: () => void): void {
        this.configReceivedCallback = callback;
    }

    setupParentListener(): Promise<boolean> {
        const identity = 'KDCUBE_COPILOT_KNOWLEDGE_BASE_ADMIN';
        window.addEventListener('message', (event: MessageEvent) => {
            if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') return;
            if (event.data.identity !== identity || !event.data.config) return;

            const config = event.data.config;
            const updates: Partial<AppSettings> = {};
            if (config.baseUrl) updates.baseUrl = config.baseUrl;
            if (config.accessToken !== undefined) updates.accessToken = config.accessToken;
            if (config.idToken !== undefined) updates.idToken = config.idToken;
            if (config.idTokenHeader) updates.idTokenHeader = config.idTokenHeader;
            if (config.defaultTenant) updates.defaultTenant = config.defaultTenant;
            if (config.defaultProject) updates.defaultProject = config.defaultProject;
            if (config.defaultAppBundleId) updates.defaultAppBundleId = config.defaultAppBundleId;
            if (Object.keys(updates).length > 0) {
                this.update(updates);
                this.configReceivedCallback?.();
            }
        });

        if (this.hasPlaceholders()) {
            window.parent.postMessage(
                {
                    type: 'CONFIG_REQUEST',
                    data: {
                        requestedFields: [
                            'baseUrl',
                            'accessToken',
                            'idToken',
                            'idTokenHeader',
                            'defaultTenant',
                            'defaultProject',
                            'defaultAppBundleId',
                        ],
                        identity,
                    },
                },
                '*',
            );

            return new Promise<boolean>((resolve) => {
                const timeout = setTimeout(() => resolve(false), 3000);
                const previous = this.configReceivedCallback;
                this.onConfigReceived(() => {
                    clearTimeout(timeout);
                    previous?.();
                    resolve(true);
                });
            });
        }

        return Promise.resolve(true);
    }
}

const settings = new SettingsManager();

function makeAuthHeaders(base?: HeadersInit): Headers {
    const headers = new Headers(base);
    const accessToken = settings.getAccessToken();
    const idToken = settings.getIdToken();
    if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
    if (idToken) headers.set(settings.getIdTokenHeader(), idToken);
    return headers;
}

function makeOperationUrl(operation: string): string {
    const root = settings.getBaseUrl();
    const tenant = encodeURIComponent(settings.getTenant());
    const project = encodeURIComponent(settings.getProject());
    const bundleId = encodeURIComponent(settings.getBundleId());
    return `${root}/api/integrations/bundles/${tenant}/${project}/${bundleId}/operations/${operation}`;
}

async function fetchProfile(): Promise<ProfilePayload> {
    const response = await fetch(`${settings.getBaseUrl()}/profile`, {
        method: 'GET',
        credentials: 'include',
        headers: makeAuthHeaders(),
    });
    if (!response.ok) throw new Error(`Profile request failed: ${response.status}`);
    return (await response.json()) as ProfilePayload;
}

async function postOperation<T>(operation: string, payload: Record<string, unknown>, streamId?: string | null): Promise<T> {
    const headers = makeAuthHeaders({ 'Content-Type': 'application/json' });
    if (streamId) headers.set(STREAM_ID_HEADER_NAME, streamId);
    const response = await fetch(makeOperationUrl(operation), {
        method: 'POST',
        credentials: 'include',
        headers,
        body: JSON.stringify({ data: payload }),
    });
    const text = await response.text();
    let parsed: unknown = {};
    try {
        parsed = text ? JSON.parse(text) : {};
    } catch {
        parsed = { raw: text };
    }
    if (!response.ok) {
        const detail = typeof parsed === 'object' && parsed && 'detail' in (parsed as Record<string, unknown>)
            ? String((parsed as Record<string, unknown>).detail)
            : text || response.statusText;
        throw new Error(detail);
    }
    if (parsed && typeof parsed === 'object' && operation in (parsed as Record<string, unknown>)) {
        return (parsed as Record<string, unknown>)[operation] as T;
    }
    return parsed as T;
}

function makeEmptyRepo(slot: string): RepoConfig {
    return { id: slot, label: '', source: '', branch: '', slot };
}

function normalizeContentRepos(input?: RepoConfig[]): RepoConfig[] {
    const source = Array.isArray(input) ? input : [];
    const result: RepoConfig[] = [];
    for (let i = 0; i < 3; i += 1) {
        result.push({ ...makeEmptyRepo(`content-${i + 1}`), ...(source[i] || {}) });
    }
    return result;
}

function formatTimestamp(value?: string): string {
    if (!value) return '—';
    try {
        return new Date(value).toLocaleString();
    } catch {
        return value;
    }
}

function shortText(value?: string, max = 120): string {
    const text = String(value || '');
    if (text.length <= max) return text;
    return `${text.slice(0, max - 1)}…`;
}

function sectionNavClass(active: boolean): string {
    return [
        'w-full rounded-2xl border px-3 py-3 text-left transition',
        active
            ? 'border-slate-900 bg-slate-900 text-white shadow-sm'
            : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50',
    ].join(' ');
}

function messageBubbleClass(role?: string): string {
    if (role === 'user') return 'bg-slate-900 text-white ml-12';
    if (role === 'assistant') return 'bg-white text-slate-900 mr-12 border border-slate-200';
    return 'bg-amber-50 text-amber-950 mr-12 border border-amber-200';
}

function App() {
    const [ready, setReady] = useState(false);
    const [sessionId, setSessionId] = useState<string>('');
    const [streamId, setStreamId] = useState<string>('');
    const eventSourceRef = useRef<EventSource | null>(null);
    const selectedConversationRef = useRef<string | null>(INITIAL_DATA.selected_conversation_id || null);

    const [data, setData] = useState<WidgetPayload>(INITIAL_DATA);
    const [selectedConversationId, setSelectedConversationId] = useState<string | null>(INITIAL_DATA.selected_conversation_id || null);
    const [currentConversation, setCurrentConversation] = useState<ConversationDocument | null>(INITIAL_DATA.current_conversation || null);
    const [composerText, setComposerText] = useState('');
    const [turnKind, setTurnKind] = useState<TurnKind>('regular');
    const [activeSection, setActiveSection] = useState<AdminSection>('chat');
    const [savingSettings, setSavingSettings] = useState(false);
    const [syncingWorkspace, setSyncingWorkspace] = useState(false);
    const [pushingOutputRepo, setPushingOutputRepo] = useState(false);
    const [resettingOutputRepo, setResettingOutputRepo] = useState(false);
    const [sending, setSending] = useState(false);
    const [statusText, setStatusText] = useState<string>('Ready');
    const [errorText, setErrorText] = useState<string>('');
    const [resetCommit, setResetCommit] = useState('');

    const [contentRepos, setContentRepos] = useState<RepoConfig[]>(normalizeContentRepos(INITIAL_DATA.config?.content_repos));
    const [outputRepo, setOutputRepo] = useState<RepoConfig>({ id: 'output', label: '', source: '', branch: '', ...(INITIAL_DATA.config?.output_repo || {}) });
    const [gitHttpUser, setGitHttpUser] = useState<string>('x-access-token');
    const [gitHttpToken, setGitHttpToken] = useState<string>('');
    const [anthropicApiKey, setAnthropicApiKey] = useState<string>('');
    const [claudeCodeKey, setClaudeCodeKey] = useState<string>('');

    const currentMessages = currentConversation?.messages || [];
    const syncStatuses = data.config?.last_sync?.repo_statuses || [];
    const workspaceRoot = data.config?.last_sync?.workspace_root || data.workspace_root || '';
    const outputRepoStatus = syncStatuses.find((item) => item.repo_type === 'output') || null;

    const currentConversationMap = useMemo(() => {
        const map = new Map<string, ConversationSummary>();
        for (const item of data.conversations || []) map.set(item.conversation_id, item);
        return map;
    }, [data.conversations]);

    useEffect(() => {
        selectedConversationRef.current = selectedConversationId;
    }, [selectedConversationId]);

    async function refreshWidgetData(nextConversationId?: string | null, nextStreamId?: string | null): Promise<void> {
        const payload = await postOperation<WidgetPayload>(
            'knowledge_base_admin_widget_data',
            nextConversationId ? { selected_conversation_id: nextConversationId } : {},
            nextStreamId || streamId || undefined,
        );
        setData(payload);
        selectedConversationRef.current = payload.selected_conversation_id || null;
        setSelectedConversationId(payload.selected_conversation_id || null);
        setCurrentConversation(payload.current_conversation || null);
        setContentRepos(normalizeContentRepos(payload.config?.content_repos));
        setOutputRepo({ id: 'output', label: '', source: '', branch: '', ...(payload.config?.output_repo || {}) });
    }

    async function loadConversation(conversationId: string): Promise<void> {
        setErrorText('');
        const payload = await postOperation<{ ok?: boolean; conversation?: ConversationDocument }>(
            'knowledge_base_admin_conversation_data',
            { conversation_id: conversationId },
            streamId || undefined,
        );
        if (payload.ok && payload.conversation) {
            selectedConversationRef.current = conversationId;
            setSelectedConversationId(conversationId);
            setCurrentConversation(payload.conversation);
            setActiveSection('chat');
        }
    }

    useEffect(() => {
        let cancelled = false;
        (async () => {
            await settings.setupParentListener();
            const profile = await fetchProfile();
            if (cancelled) return;
            const resolvedSessionId = String(profile.session_id || '');
            const resolvedStreamId = `kb-admin-${Math.random().toString(36).slice(2, 12)}`;
            setSessionId(resolvedSessionId);
            setStreamId(resolvedStreamId);

            const streamUrl = new URL(`${settings.getBaseUrl()}/sse/stream`);
            streamUrl.searchParams.set('stream_id', resolvedStreamId);
            if (resolvedSessionId) streamUrl.searchParams.set('user_session_id', resolvedSessionId);
            if (settings.getTenant()) streamUrl.searchParams.set('tenant', settings.getTenant());
            if (settings.getProject()) streamUrl.searchParams.set('project', settings.getProject());
            const accessToken = settings.getAccessToken();
            const idToken = settings.getIdToken();
            if (accessToken) streamUrl.searchParams.set('bearer_token', accessToken);
            if (idToken) streamUrl.searchParams.set('id_token', idToken);

            const es = new EventSource(streamUrl.toString(), { withCredentials: true });
            eventSourceRef.current = es;
            es.addEventListener('chat_delta', (event) => {
                try {
                    const envelope = JSON.parse((event as MessageEvent).data || '{}');
                    const convId = envelope?.conversation?.conversation_id;
                    const agent = envelope?.event?.agent;
                    if (!convId || convId !== selectedConversationRef.current || agent !== 'knowledge-base-admin') return;
                    const chunk = String(envelope?.delta?.text || '');
                    if (!chunk) return;
                    setCurrentConversation((prev) => {
                        if (!prev || prev.conversation_id !== convId) return prev;
                        const messages = [...(prev.messages || [])];
                        const last = messages[messages.length - 1];
                        if (!last || last.role !== 'assistant' || last.metadata?.pending !== true) return prev;
                        messages[messages.length - 1] = {
                            ...last,
                            text: `${String(last.text || '')}${chunk}`,
                        };
                        return { ...prev, messages };
                    });
                } catch {
                    /* noop */
                }
            });
            es.addEventListener('chat_step', (event) => {
                try {
                    const envelope = JSON.parse((event as MessageEvent).data || '{}');
                    const convId = envelope?.conversation?.conversation_id;
                    const step = envelope?.event?.step;
                    const status = envelope?.event?.status;
                    const title = envelope?.event?.title;
                    const agent = envelope?.event?.agent;
                    if (!convId || convId !== selectedConversationRef.current || agent !== 'knowledge-base-admin') return;
                    if (step === 'knowledge_base_admin.agent' || step === 'knowledge_base_admin.agent.stderr') {
                        setStatusText(title ? `${title} (${status})` : String(status || 'running'));
                        if (status === 'error') {
                            const err = envelope?.data?.error || envelope?.data?.line || 'Claude run failed';
                            setErrorText(String(err));
                        }
                    }
                } catch {
                    /* noop */
                }
            });
            es.addEventListener('chat_error', (event) => {
                try {
                    const envelope = JSON.parse((event as MessageEvent).data || '{}');
                    const convId = envelope?.conversation?.conversation_id;
                    if (!convId || convId !== selectedConversationRef.current) return;
                    const message = String(envelope?.data?.error || 'Claude run failed');
                    setStatusText('Claude run failed');
                    setErrorText(message);
                } catch {
                    /* noop */
                }
            });
            es.addEventListener('chat_complete', (event) => {
                try {
                    const envelope = JSON.parse((event as MessageEvent).data || '{}');
                    const convId = envelope?.conversation?.conversation_id;
                    if (!convId || convId !== selectedConversationRef.current) return;
                    setStatusText('Claude response completed');
                } catch {
                    /* noop */
                }
            });
            es.onerror = () => {
                setStatusText('Stream disconnected');
            };

            await refreshWidgetData(INITIAL_DATA.selected_conversation_id || null, resolvedStreamId);
            setReady(true);
        })().catch((err) => {
            setErrorText(err instanceof Error ? err.message : String(err));
            setStatusText('Failed to initialize widget');
        });

        return () => {
            cancelled = true;
            eventSourceRef.current?.close();
        };
    }, []);

    async function handleSaveSettings(): Promise<void> {
        setSavingSettings(true);
        setErrorText('');
        try {
            const payload = await postOperation<WidgetPayload>(
                'knowledge_base_admin_save_settings',
                {
                    content_repos: contentRepos,
                    output_repo: outputRepo,
                    git_http_user: gitHttpUser.trim() || undefined,
                    git_http_token: gitHttpToken.trim() || undefined,
                    anthropic_api_key: anthropicApiKey.trim() || undefined,
                    claude_code_key: claudeCodeKey.trim() || undefined,
                },
                streamId || undefined,
            );
            setData(payload);
            setContentRepos(normalizeContentRepos(payload.config?.content_repos));
            setOutputRepo({ id: 'output', label: '', source: '', branch: '', ...(payload.config?.output_repo || {}) });
            setGitHttpToken('');
            setAnthropicApiKey('');
            setClaudeCodeKey('');
            setStatusText('Settings saved');
            setActiveSection('workspace');
        } catch (err) {
            setErrorText(err instanceof Error ? err.message : String(err));
        } finally {
            setSavingSettings(false);
        }
    }

    async function handleSyncWorkspace(): Promise<void> {
        setSyncingWorkspace(true);
        setErrorText('');
        try {
            const payload = await postOperation<{ ok?: boolean; error?: string; repo_statuses?: SyncRepoStatus[]; workspace_root?: string }>(
                'knowledge_base_admin_sync_workspace',
                {},
                streamId || undefined,
            );
            if (!payload.ok) throw new Error(payload.error || 'Workspace sync failed');
            await refreshWidgetData(selectedConversationId);
            setStatusText('Workspace synced');
            setActiveSection('workspace');
        } catch (err) {
            setErrorText(err instanceof Error ? err.message : String(err));
        } finally {
            setSyncingWorkspace(false);
        }
    }

    async function handlePushOutputRepo(): Promise<void> {
        setPushingOutputRepo(true);
        setErrorText('');
        try {
            const payload = await postOperation<{ ok?: boolean; error?: string }>(
                'knowledge_base_admin_push_output_repo',
                {},
                streamId || undefined,
            );
            if (!payload.ok) throw new Error(payload.error || 'Output repo push failed');
            await refreshWidgetData(selectedConversationId);
            setStatusText('Output branch pushed');
            setActiveSection('workspace');
        } catch (err) {
            setErrorText(err instanceof Error ? err.message : String(err));
        } finally {
            setPushingOutputRepo(false);
        }
    }

    async function handleResetOutputRepo(): Promise<void> {
        const commit = resetCommit.trim();
        if (!commit) {
            setErrorText('Enter a commit, ref, or remote branch before resetting the output repo.');
            return;
        }
        setResettingOutputRepo(true);
        setErrorText('');
        try {
            const payload = await postOperation<{ ok?: boolean; error?: string }>(
                'knowledge_base_admin_reset_output_repo',
                { commit },
                streamId || undefined,
            );
            if (!payload.ok) throw new Error(payload.error || 'Output repo reset failed');
            await refreshWidgetData(selectedConversationId);
            setStatusText(`Output branch reset to ${shortText(commit, 16)}`);
            setResetCommit('');
            setActiveSection('workspace');
        } catch (err) {
            setErrorText(err instanceof Error ? err.message : String(err));
        } finally {
            setResettingOutputRepo(false);
        }
    }

    function handleNewConversation(): void {
        selectedConversationRef.current = null;
        setSelectedConversationId(null);
        setCurrentConversation(null);
        setStatusText('New conversation');
        setErrorText('');
        setActiveSection('chat');
    }

    async function handleSend(): Promise<void> {
        const text = composerText.trim();
        if (!text) return;

        const nextConversationId = selectedConversationId || `kb_admin_${Math.random().toString(36).slice(2, 14)}`;
        const userMessage: ConversationMessage = {
            message_id: `local-user-${Date.now()}`,
            role: 'user',
            text,
            created_at: new Date().toISOString(),
            metadata: { turn_kind: turnKind },
        };
        const pendingAssistant: ConversationMessage = {
            message_id: `local-assistant-${Date.now()}`,
            role: 'assistant',
            text: '',
            created_at: new Date().toISOString(),
            metadata: { pending: true, turn_kind: turnKind },
        };

        selectedConversationRef.current = nextConversationId;
        setSelectedConversationId(nextConversationId);
        setCurrentConversation((prev) => ({
            conversation_id: nextConversationId,
            title: prev?.title || shortText(text, 60),
            created_at: prev?.created_at || new Date().toISOString(),
            updated_at: new Date().toISOString(),
            messages: [...(prev?.messages || []), userMessage, pendingAssistant],
        }));
        setComposerText('');
        setSending(true);
        setStatusText(`Sending ${turnKind} turn`);
        setErrorText('');
        setActiveSection('chat');

        try {
            const payload = await postOperation<{
                ok?: boolean;
                error?: string;
                conversation_id?: string;
                conversation?: ConversationDocument;
            }>(
                'knowledge_base_admin_chat',
                {
                    conversation_id: nextConversationId,
                    message: text,
                    turn_kind: turnKind,
                },
                streamId || undefined,
            );
            if (!payload.ok) throw new Error(payload.error || 'Claude run failed');
            setSelectedConversationId(payload.conversation_id || nextConversationId);
            setCurrentConversation(payload.conversation || null);
            await refreshWidgetData(payload.conversation_id || nextConversationId);
            setStatusText('Claude response completed');
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            setErrorText(message);
            setCurrentConversation((prev) => {
                if (!prev || prev.conversation_id !== nextConversationId) return prev;
                const messages = [...(prev.messages || [])];
                const last = messages[messages.length - 1];
                if (last && last.role === 'assistant') {
                    messages[messages.length - 1] = {
                        ...last,
                        text: `Error: ${message}`,
                        metadata: { ...(last.metadata || {}), pending: false, error: true },
                    };
                }
                return { ...prev, messages };
            });
        } finally {
            setSending(false);
        }
    }

    return (
        <div className="flex h-full min-h-0 flex-col bg-[radial-gradient(circle_at_top_left,_#f8fafc,_#e2e8f0_45%,_#dbeafe_100%)] text-slate-900">
            <div className="border-b border-slate-200 bg-white/90 px-4 py-4 backdrop-blur">
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <div className="text-[11px] uppercase tracking-[0.28em] text-sky-600">Admin Widget</div>
                        <h1 className="mt-1 text-lg font-semibold tracking-tight">Knowledge Base Admin</h1>
                        <p className="mt-1 text-sm leading-6 text-slate-600">
                            Configure source repos, prepare the workspace, and steer Claude Code in a compact side-panel flow.
                        </p>
                    </div>
                    <button
                        className="shrink-0 rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-700 transition hover:bg-slate-50"
                        onClick={handleNewConversation}
                    >
                        New
                    </button>
                </div>

                <div className="mt-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-3 py-3 text-sm text-slate-700">
                    <div className="font-medium">{statusText}</div>
                    {errorText && <div className="mt-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">{errorText}</div>}
                </div>

            </div>

            <div className="flex min-h-0 flex-1">
                <aside className="hidden w-44 shrink-0 border-r border-slate-200 bg-white/70 p-3 lg:block">
                    <div className="space-y-2">
                        <button className={sectionNavClass(activeSection === 'chat')} onClick={() => setActiveSection('chat')}>
                            <div className="text-[11px] uppercase tracking-[0.22em] opacity-70">Claude</div>
                            <div className="mt-1 text-sm font-semibold">Chat</div>
                        </button>
                        <button className={sectionNavClass(activeSection === 'settings')} onClick={() => setActiveSection('settings')}>
                            <div className="text-[11px] uppercase tracking-[0.22em] opacity-70">Config</div>
                            <div className="mt-1 text-sm font-semibold">Settings</div>
                        </button>
                        <button className={sectionNavClass(activeSection === 'workspace')} onClick={() => setActiveSection('workspace')}>
                            <div className="text-[11px] uppercase tracking-[0.22em] opacity-70">Git</div>
                            <div className="mt-1 text-sm font-semibold">Workspace</div>
                        </button>
                        <button className={sectionNavClass(activeSection === 'conversations')} onClick={() => setActiveSection('conversations')}>
                            <div className="text-[11px] uppercase tracking-[0.22em] opacity-70">State</div>
                            <div className="mt-1 text-sm font-semibold">History</div>
                        </button>
                    </div>

                    <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-3 text-xs leading-6 text-slate-600">
                        <div className="font-semibold text-slate-900">Session</div>
                        <div className="mt-1 break-all">{shortText(sessionId, 24) || '—'}</div>
                        <div className="mt-3 font-semibold text-slate-900">Stream</div>
                        <div className="mt-1 break-all">{shortText(streamId, 24) || '—'}</div>
                    </div>
                </aside>

                <div className="flex min-h-0 flex-1 flex-col">
                    <div className="border-b border-slate-200 bg-white/80 px-4 py-3 lg:hidden">
                        <div className="grid grid-cols-2 gap-2">
                            <button className={sectionNavClass(activeSection === 'chat')} onClick={() => setActiveSection('chat')}>
                                <div className="text-[11px] uppercase tracking-[0.22em] opacity-70">Claude</div>
                                <div className="mt-1 text-sm font-semibold">Chat</div>
                            </button>
                            <button className={sectionNavClass(activeSection === 'settings')} onClick={() => setActiveSection('settings')}>
                                <div className="text-[11px] uppercase tracking-[0.22em] opacity-70">Config</div>
                                <div className="mt-1 text-sm font-semibold">Settings</div>
                            </button>
                            <button className={sectionNavClass(activeSection === 'workspace')} onClick={() => setActiveSection('workspace')}>
                                <div className="text-[11px] uppercase tracking-[0.22em] opacity-70">Git</div>
                                <div className="mt-1 text-sm font-semibold">Workspace</div>
                            </button>
                            <button className={sectionNavClass(activeSection === 'conversations')} onClick={() => setActiveSection('conversations')}>
                                <div className="text-[11px] uppercase tracking-[0.22em] opacity-70">State</div>
                                <div className="mt-1 text-sm font-semibold">History</div>
                            </button>
                        </div>
                    </div>

                    {activeSection === 'chat' && (
                        <>
                        <div className="border-b border-slate-200 bg-white px-4 py-4">
                            <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Active conversation</div>
                            <div className="mt-2 text-base font-semibold">
                                {currentConversationMap.get(selectedConversationId || '')?.title || currentConversation?.title || 'New conversation'}
                            </div>
                            <div className="mt-3 flex flex-col gap-2">
                                <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Turn kind</div>
                                <select
                                    className="rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm"
                                    value={turnKind}
                                    onChange={(e) => setTurnKind(e.target.value as TurnKind)}
                                >
                                    <option value="regular">regular</option>
                                    <option value="followup">followup</option>
                                    <option value="steer">steer</option>
                                </select>
                            </div>
                        </div>

                        <div className="flex-1 overflow-y-auto px-4 py-4">
                            <div className="space-y-3">
                                {!currentMessages.length && (
                                    <div className="rounded-[24px] border border-dashed border-slate-300 bg-white/80 p-6 text-center text-sm leading-7 text-slate-600">
                                        Start a conversation to let Claude Code inspect the connected repos and plan or build the knowledge base.
                                    </div>
                                )}
                                {currentMessages.map((message) => (
                                    <div key={message.message_id || `${message.role}-${message.created_at}`} className={`rounded-[24px] px-4 py-4 shadow-sm ${messageBubbleClass(message.role)}`}>
                                        <div className="mb-2 flex items-center justify-between gap-3 text-[11px] uppercase tracking-[0.22em] text-slate-500">
                                            <span>{message.role || 'assistant'}</span>
                                            <span>{formatTimestamp(message.created_at)}</span>
                                        </div>
                                        <div className="whitespace-pre-wrap text-sm leading-7">{message.text || (message.metadata?.pending ? 'Streaming…' : '')}</div>
                                    </div>
                                ))}
                            </div>
                        </div>

                        <div className="border-t border-slate-200 bg-white px-4 py-4">
                            <textarea
                                className="min-h-[132px] w-full rounded-[24px] border border-slate-200 bg-white px-4 py-4 text-sm leading-7 text-slate-900 shadow-inner focus:border-sky-400 focus:outline-none"
                                value={composerText}
                                onChange={(e) => setComposerText(e.target.value)}
                                placeholder="Ask Claude Code to inspect the source repos, create a wiki structure, sync docs into the output repo, or continue an existing knowledge-base conversation."
                            />
                            <div className="mt-4 flex flex-col gap-3">
                                <button
                                    className="rounded-full bg-sky-500 px-5 py-3 text-sm font-semibold text-white transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-60"
                                    onClick={handleSend}
                                    disabled={!ready || sending || !composerText.trim()}
                                >
                                    {sending ? 'Sending…' : 'Send to Claude'}
                                </button>
                            </div>
                        </div>
                        </>
                    )}

                    {activeSection === 'settings' && (
                        <div className="flex-1 overflow-y-auto px-4 py-4">
                            <div className="space-y-4">
                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.24em] text-slate-500">Saved secrets</div>
                                <div className="mt-3 grid gap-3">
                                    <div className="flex items-center justify-between text-sm">
                                        <span>Git PAT</span>
                                        <span className={`rounded-full px-2 py-1 text-xs font-semibold ${data.secrets?.has_git_pat ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}>
                                            {data.secrets?.has_git_pat ? 'present' : 'missing'}
                                        </span>
                                    </div>
                                    <div className="flex items-center justify-between text-sm">
                                        <span>Anthropic API key</span>
                                        <span className={`rounded-full px-2 py-1 text-xs font-semibold ${data.secrets?.has_anthropic_api_key ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}>
                                            {data.secrets?.has_anthropic_api_key ? 'present' : 'missing'}
                                        </span>
                                    </div>
                                    <div className="flex items-center justify-between text-sm">
                                        <span>Claude Code key</span>
                                        <span className={`rounded-full px-2 py-1 text-xs font-semibold ${data.secrets?.has_claude_code_key ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}>
                                            {data.secrets?.has_claude_code_key ? 'present' : 'missing'}
                                        </span>
                                    </div>
                                </div>
                            </div>

                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="mb-3 text-sm font-semibold text-slate-900">Git credentials</div>
                                <div className="grid gap-3">
                                    <label className="grid gap-1">
                                        <span className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">HTTP user</span>
                                        <input className="rounded-xl border border-slate-200 px-3 py-2 text-sm" value={gitHttpUser} onChange={(e) => setGitHttpUser(e.target.value)} placeholder="x-access-token" />
                                    </label>
                                    <label className="grid gap-1">
                                        <span className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">Git PAT</span>
                                        <input className="rounded-xl border border-slate-200 px-3 py-2 text-sm" type="password" value={gitHttpToken} onChange={(e) => setGitHttpToken(e.target.value)} placeholder="leave blank to keep current" />
                                    </label>
                                </div>
                            </div>

                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="mb-3 text-sm font-semibold text-slate-900">Claude credentials</div>
                                <div className="grid gap-3">
                                    <label className="grid gap-1">
                                        <span className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">Anthropic API key</span>
                                        <input className="rounded-xl border border-slate-200 px-3 py-2 text-sm" type="password" value={anthropicApiKey} onChange={(e) => setAnthropicApiKey(e.target.value)} placeholder="leave blank to keep current" />
                                    </label>
                                    <label className="grid gap-1">
                                        <span className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">Claude Code key</span>
                                        <input className="rounded-xl border border-slate-200 px-3 py-2 text-sm" type="password" value={claudeCodeKey} onChange={(e) => setClaudeCodeKey(e.target.value)} placeholder="optional" />
                                    </label>
                                </div>
                            </div>

                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="mb-3 text-sm font-semibold text-slate-900">Source repositories</div>
                                <div className="space-y-3">
                                    {contentRepos.map((repo, idx) => (
                                        <div key={repo.slot || idx} className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
                                            <div className="mb-2 text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">Content repo {idx + 1}</div>
                                            <div className="grid gap-2">
                                                <input className="rounded-xl border border-slate-200 px-3 py-2 text-sm" value={repo.label || ''} onChange={(e) => setContentRepos((prev) => prev.map((item, itemIdx) => itemIdx === idx ? { ...item, label: e.target.value } : item))} placeholder="Label" />
                                                <input className="rounded-xl border border-slate-200 px-3 py-2 text-sm" value={repo.source || ''} onChange={(e) => setContentRepos((prev) => prev.map((item, itemIdx) => itemIdx === idx ? { ...item, source: e.target.value } : item))} placeholder="https://github.com/org/repo.git" />
                                                <input className="rounded-xl border border-slate-200 px-3 py-2 text-sm" value={repo.branch || ''} onChange={(e) => setContentRepos((prev) => prev.map((item, itemIdx) => itemIdx === idx ? { ...item, branch: e.target.value } : item))} placeholder="branch (optional)" />
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="mb-3 text-sm font-semibold text-slate-900">Output repository</div>
                                <div className="grid gap-2">
                                    <input className="rounded-xl border border-slate-200 px-3 py-2 text-sm" value={outputRepo.label || ''} onChange={(e) => setOutputRepo((prev) => ({ ...prev, label: e.target.value }))} placeholder="Label" />
                                    <input className="rounded-xl border border-slate-200 px-3 py-2 text-sm" value={outputRepo.source || ''} onChange={(e) => setOutputRepo((prev) => ({ ...prev, source: e.target.value }))} placeholder="https://github.com/org/output-repo.git" />
                                    <input className="rounded-xl border border-slate-200 px-3 py-2 text-sm" value={outputRepo.branch || ''} onChange={(e) => setOutputRepo((prev) => ({ ...prev, branch: e.target.value }))} placeholder="branch (optional)" />
                                </div>
                            </div>

                            <button
                                className="w-full rounded-full bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
                                onClick={handleSaveSettings}
                                disabled={!ready || savingSettings}
                            >
                                {savingSettings ? 'Saving…' : 'Save settings'}
                            </button>
                            </div>
                        </div>
                    )}

                    {activeSection === 'workspace' && (
                        <div className="flex-1 overflow-y-auto px-4 py-4">
                            <div className="space-y-4">
                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.24em] text-slate-500">Workspace root</div>
                                <div className="mt-2 break-all text-sm text-slate-700">{workspaceRoot || 'No workspace prepared yet.'}</div>
                                <button
                                    className="mt-4 w-full rounded-full bg-sky-500 px-4 py-3 text-sm font-semibold text-white transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-60"
                                    onClick={handleSyncWorkspace}
                                    disabled={!ready || syncingWorkspace}
                                >
                                    {syncingWorkspace ? 'Syncing…' : 'Sync workspace'}
                                </button>
                            </div>

                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.24em] text-slate-500">Output repo controls</div>
                                {outputRepoStatus ? (
                                    <>
                                        <div className="mt-2 text-sm font-semibold text-slate-900">
                                            {(outputRepoStatus.label || 'Output repo')} · {outputRepoStatus.current_branch || outputRepoStatus.branch || 'default'}
                                        </div>
                                        <div className="mt-1 text-xs leading-6 text-slate-600">
                                            HEAD {shortText(outputRepoStatus.head, 12)} {outputRepoStatus.dirty ? '· dirty working tree' : '· clean'}
                                        </div>
                                        <div className="mt-4 grid gap-3">
                                            <button
                                                className="w-full rounded-full bg-slate-900 px-4 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
                                                onClick={handlePushOutputRepo}
                                                disabled={!ready || pushingOutputRepo || resettingOutputRepo}
                                            >
                                                {pushingOutputRepo ? 'Pushing…' : 'Push output branch'}
                                            </button>
                                            <div className="grid gap-2">
                                                <input
                                                    className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
                                                    value={resetCommit}
                                                    onChange={(e) => setResetCommit(e.target.value)}
                                                    placeholder="commit hash or ref (for example origin/main)"
                                                />
                                                <button
                                                    className="w-full rounded-full border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-800 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
                                                    onClick={handleResetOutputRepo}
                                                    disabled={!ready || resettingOutputRepo || pushingOutputRepo}
                                                >
                                                    {resettingOutputRepo ? 'Resetting…' : 'Reset output branch'}
                                                </button>
                                            </div>
                                        </div>
                                    </>
                                ) : (
                                    <div className="mt-2 text-sm text-slate-600">
                                        Sync the workspace first to prepare the output repo branch and enable push/reset controls.
                                    </div>
                                )}
                            </div>

                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.24em] text-slate-500">Last sync</div>
                                <div className="mt-2 text-sm text-slate-700">{formatTimestamp(data.config?.last_sync?.synced_at)}</div>
                            </div>

                            <div className="space-y-3">
                                {syncStatuses.length > 0 ? syncStatuses.map((item) => (
                                    <div key={item.slot} className="rounded-2xl border border-slate-200 bg-white p-4">
                                        <div className="flex items-center justify-between gap-3">
                                            <div className="text-sm font-semibold text-slate-900">{item.label || item.slot}</div>
                                            <div className="rounded-full bg-slate-100 px-2 py-1 text-[11px] uppercase tracking-[0.22em] text-slate-700">
                                                {item.action || 'present'}
                                            </div>
                                        </div>
                                        <div className="mt-3 text-xs leading-6 text-slate-600">
                                            {(item.current_branch || item.branch || 'default')} · {shortText(item.head, 10)} {item.dirty ? '· dirty' : ''}
                                        </div>
                                        <div className="mt-1 break-all text-[11px] leading-5 text-slate-500">{item.local_path || '—'}</div>
                                    </div>
                                )) : (
                                    <div className="rounded-2xl border border-dashed border-slate-300 bg-white/80 p-6 text-center text-sm text-slate-600">
                                        Sync the workspace to clone and inspect the configured repositories.
                                    </div>
                                )}
                            </div>
                            </div>
                        </div>
                    )}

                    {activeSection === 'conversations' && (
                        <div className="flex-1 overflow-y-auto px-4 py-4">
                            <div className="space-y-3">
                            {(data.conversations || []).map((conversation) => (
                                <button
                                    key={conversation.conversation_id}
                                    className={`w-full rounded-2xl border p-4 text-left transition ${
                                        conversation.conversation_id === selectedConversationId
                                            ? 'border-sky-300 bg-sky-50'
                                            : 'border-slate-200 bg-white hover:border-slate-300'
                                    }`}
                                    onClick={() => loadConversation(conversation.conversation_id)}
                                >
                                    <div className="text-sm font-semibold text-slate-900">{conversation.title || 'Untitled conversation'}</div>
                                    <div className="mt-1 text-xs text-slate-500">{formatTimestamp(conversation.updated_at)}</div>
                                    <div className="mt-3 text-sm leading-6 text-slate-600">{shortText(conversation.last_preview, 120)}</div>
                                </button>
                            ))}
                            {!data.conversations?.length && (
                                <div className="rounded-2xl border border-dashed border-slate-300 bg-white/80 p-6 text-center text-sm text-slate-600">
                                    No stored conversations yet.
                                </div>
                            )}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

App;
