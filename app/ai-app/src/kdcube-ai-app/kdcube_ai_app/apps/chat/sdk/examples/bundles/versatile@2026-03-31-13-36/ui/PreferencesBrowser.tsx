import { useEffect, useMemo, useState } from 'react';

interface AppSettings {
    baseUrl: string;
    accessToken: string | null;
    idToken: string | null;
    idTokenHeader: string;
    defaultTenant: string;
    defaultProject: string;
    defaultAppBundleId: string;
}

interface PreferenceValue {
    value?: unknown;
    updated_at?: string;
    source?: string;
    origin?: string;
    evidence?: string;
}

interface PreferenceEvent {
    captured_at?: string;
    key?: string;
    value?: unknown;
    source?: string;
    origin?: string;
    evidence?: string;
}

interface WidgetPayload {
    ok?: boolean;
    error?: string;
    user_id?: string;
    current?: Record<string, PreferenceValue>;
    recent?: PreferenceEvent[];
    matched_count?: number;
}

interface ExecArtifact {
    path?: string;
    mime?: string;
    size_bytes?: number;
}

interface ExecReportPayload {
    ok?: boolean;
    error?: unknown;
    report_text?: string;
    items?: ExecArtifact[];
    out_dyn?: Record<string, unknown>;
    recency?: number;
    keywords?: string;
}

interface ExecReportOptions {
    recency: number;
    kwords: string;
}

const INITIAL_DATA: WidgetPayload = __PREFERENCES_JSON__;

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}';

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
        if (this.settings.baseUrl === PLACEHOLDER_BASE_URL) {
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
        return this.settings.accessToken === PLACEHOLDER_ACCESS_TOKEN ? null : this.settings.accessToken;
    }

    getIdToken(): string | null {
        return this.settings.idToken === PLACEHOLDER_ID_TOKEN ? null : this.settings.idToken;
    }

    getIdTokenHeader(): string {
        return this.settings.idTokenHeader === PLACEHOLDER_ID_TOKEN_HEADER ? 'X-ID-Token' : this.settings.idTokenHeader;
    }

    getTenant(): string {
        return this.settings.defaultTenant === PLACEHOLDER_TENANT ? '' : this.settings.defaultTenant;
    }

    getProject(): string {
        return this.settings.defaultProject === PLACEHOLDER_PROJECT ? '' : this.settings.defaultProject;
    }

    getBundleId(): string {
        return this.settings.defaultAppBundleId === PLACEHOLDER_BUNDLE_ID
            ? 'versatile@2026-03-31-13-36'
            : this.settings.defaultAppBundleId;
    }

    hasPlaceholders(): boolean {
        return this.settings.baseUrl === PLACEHOLDER_BASE_URL;
    }

    update(partial: Partial<AppSettings>): void {
        this.settings = { ...this.settings, ...partial };
    }

    onConfigReceived(callback: () => void): void {
        this.configReceivedCallback = callback;
    }

    setupParentListener(): Promise<boolean> {
        const identity = 'BUNDLE_VERSATILE_PREFERENCES_WIDGET';

        window.addEventListener('message', (event: MessageEvent) => {
            if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') {
                return;
            }
            if (event.data.identity !== identity || !event.data.config) {
                return;
            }

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
    if (accessToken) {
        headers.set('Authorization', `Bearer ${accessToken}`);
    }
    if (idToken) {
        headers.set(settings.getIdTokenHeader(), idToken);
    }
    return headers;
}

function normalizePayload(payload: WidgetPayload | null | undefined): WidgetPayload {
    return {
        ok: payload?.ok ?? true,
        error: payload?.error,
        user_id: payload?.user_id ?? INITIAL_DATA.user_id ?? 'anonymous',
        current: payload?.current ?? INITIAL_DATA.current ?? {},
        recent: payload?.recent ?? INITIAL_DATA.recent ?? [],
        matched_count: payload?.matched_count ?? INITIAL_DATA.matched_count ?? 0,
    };
}

async function fetchPreferencesPayload(): Promise<WidgetPayload> {
    const tenant = settings.getTenant();
    const project = settings.getProject();
    const bundleId = settings.getBundleId();
    if (!tenant || !project) {
        throw new Error('Widget configuration is incomplete: tenant/project are not available.');
    }

    const url = `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/operations/preferences_widget_data`;
    const response = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ bundle_id: bundleId }),
    });

    if (!response.ok) {
        const detail = await response.text().catch(() => response.statusText);
        throw new Error(`${response.status}: ${detail}`);
    }

    const json = await response.json();
    return normalizePayload(json.preferences_widget_data ?? json);
}

async function fetchPreferencesExecReport(reportOptions: ExecReportOptions): Promise<ExecReportPayload> {
    const tenant = settings.getTenant();
    const project = settings.getProject();
    const bundleId = settings.getBundleId();
    if (!tenant || !project) {
        throw new Error('Widget configuration is incomplete: tenant/project are not available.');
    }

    const url = `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/operations/preferences_exec_report`;
    const response = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
            bundle_id: bundleId,
            data: {
                recency: reportOptions.recency,
                kwords: reportOptions.kwords,
            },
        }),
    });

    if (!response.ok) {
        const detail = await response.text().catch(() => response.statusText);
        throw new Error(`${response.status}: ${detail}`);
    }

    const json = await response.json();
    return (json.preferences_exec_report ?? json) as ExecReportPayload;
}

function PreferencesBrowser() {
    const [ready, setReady] = useState(false);
    const [query, setQuery] = useState('');
    const [data, setData] = useState<WidgetPayload>(() => normalizePayload(INITIAL_DATA));
    const [loading, setLoading] = useState(false);
    const [reportLoading, setReportLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [lastSync, setLastSync] = useState<string | null>(null);
    const [report, setReport] = useState<ExecReportPayload | null>(null);
    const [reportRecency, setReportRecency] = useState('10');

    async function refreshPreferences() {
        setLoading(true);
        setError(null);
        try {
            const payload = await fetchPreferencesPayload();
            setData(payload);
            setLastSync(new Date().toISOString());
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setLoading(false);
        }
    }

    async function runExecReport() {
        setReportLoading(true);
        setError(null);
        try {
            const payload = await fetchPreferencesExecReport({
                recency: Math.max(1, Number.parseInt(reportRecency || '10', 10) || 10),
                kwords: query.trim(),
            });
            setReport(payload);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setReportLoading(false);
        }
    }

    useEffect(() => {
        settings.setupParentListener().then(() => {
            setReady(true);
            refreshPreferences();
        });
    }, []);

    const currentEntries = useMemo(
        () => Object.entries(data.current || {}),
        [data.current],
    );
    const recent = data.recent || [];
    const filterValue = query.trim().toLowerCase();

    const visibleCurrent = useMemo(
        () => currentEntries.filter(([key, value]) => {
            const haystack = `${key} ${value?.value || ''}`.toLowerCase();
            return !filterValue || haystack.includes(filterValue);
        }),
        [currentEntries, filterValue],
    );

    const visibleRecent = useMemo(
        () => recent.filter((item) => {
            const haystack = `${item.key || ''} ${item.value || ''} ${item.evidence || ''}`.toLowerCase();
            return !filterValue || haystack.includes(filterValue);
        }),
        [recent, filterValue],
    );

    if (!ready) {
        return (
            <div style={{ padding: '24px', fontFamily: 'ui-sans-serif, system-ui, sans-serif', color: '#53605a' }}>
                Loading widget configuration…
            </div>
        );
    }

    return (
        <div style={{
            fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            minHeight: "100vh",
            margin: 0,
            background: "linear-gradient(160deg, #f6f4ee 0%, #f3efe2 45%, #e8f0ed 100%)",
            color: "#18231d",
            padding: "24px",
            boxSizing: "border-box",
        }}>
            <div style={{
                maxWidth: "1100px",
                margin: "0 auto",
                display: "grid",
                gap: "18px",
            }}>
                <section style={{
                    background: "rgba(255,255,255,0.78)",
                    border: "1px solid rgba(24,35,29,0.12)",
                    borderRadius: "24px",
                    padding: "24px",
                    boxShadow: "0 24px 64px rgba(24,35,29,0.08)",
                }}>
                    <div style={{ display: "flex", gap: "12px", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
                        <div>
                            <div style={{ fontSize: "13px", textTransform: "uppercase", letterSpacing: "0.12em", opacity: 0.6 }}>
                                Versatile Bundle Widget
                            </div>
                            <h1 style={{ margin: "8px 0 6px", fontSize: "32px", lineHeight: 1.1 }}>
                                Preference Browser
                            </h1>
                            <p style={{ margin: 0, maxWidth: "720px", opacity: 0.8 }}>
                                This widget refreshes bundle-backed preferences through the integrations operations API for
                                <strong> {data.user_id}</strong>.
                            </p>
                            <p style={{ margin: "10px 0 0", fontSize: "13px", opacity: 0.68 }}>
                                Operation: <code>POST /api/integrations/bundles/&lt;tenant&gt;/&lt;project&gt;/operations/preferences_widget_data</code>
                            </p>
                        </div>
                        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
                            <input
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder="Filter by key, value, or evidence"
                                style={{
                                    minWidth: "260px",
                                    padding: "12px 16px",
                                    borderRadius: "999px",
                                    border: "1px solid rgba(24,35,29,0.18)",
                                    outline: "none",
                                    fontSize: "14px",
                                    background: "rgba(255,255,255,0.92)",
                                }}
                            />
                            <button
                                type="button"
                                onClick={refreshPreferences}
                                disabled={loading}
                                style={{
                                    padding: "12px 18px",
                                    borderRadius: "999px",
                                    border: "none",
                                    background: loading ? "#6d7f75" : "#18231d",
                                    color: "#fff",
                                    cursor: loading ? "default" : "pointer",
                                    fontSize: "14px",
                                }}
                            >
                                {loading ? 'Refreshing…' : 'Refresh'}
                            </button>
                            <input
                                type="number"
                                min="1"
                                step="1"
                                value={reportRecency}
                                onChange={(event) => setReportRecency(event.target.value)}
                                aria-label="Exec report recency"
                                style={{
                                    width: "100px",
                                    padding: "12px 14px",
                                    borderRadius: "999px",
                                    border: "1px solid rgba(24,35,29,0.18)",
                                    outline: "none",
                                    fontSize: "14px",
                                    background: "rgba(255,255,255,0.92)",
                                }}
                            />
                            <button
                                type="button"
                                onClick={runExecReport}
                                disabled={reportLoading}
                                style={{
                                    padding: "12px 18px",
                                    borderRadius: "999px",
                                    border: "1px solid rgba(24,35,29,0.14)",
                                    background: reportLoading ? "#d9ded7" : "#f7faf7",
                                    color: "#18231d",
                                    cursor: reportLoading ? "default" : "pointer",
                                    fontSize: "14px",
                                }}
                            >
                                {reportLoading ? 'Running report…' : 'Run Exec Report'}
                            </button>
                        </div>
                    </div>
                    {lastSync ? (
                        <div style={{ marginTop: "12px", fontSize: "12px", opacity: 0.62 }}>
                            Last sync: {lastSync}
                        </div>
                    ) : null}
                    {error ? (
                        <div style={{
                            marginTop: "14px",
                            padding: "12px 14px",
                            borderRadius: "16px",
                            background: "rgba(140, 29, 48, 0.08)",
                            border: "1px solid rgba(140, 29, 48, 0.14)",
                            color: "#7a1730",
                            fontSize: "14px",
                        }}>
                            {error}
                        </div>
                    ) : null}
                </section>

                {report ? (
                    <section style={{
                        background: "rgba(255,255,255,0.84)",
                        border: "1px solid rgba(24,35,29,0.12)",
                        borderRadius: "24px",
                        padding: "22px",
                    }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
                            <div>
                                <h2 style={{ margin: "0 0 8px" }}>Exec report</h2>
                                <p style={{ margin: 0, opacity: 0.72 }}>
                                    Generated through <code>POST /api/integrations/bundles/&lt;tenant&gt;/&lt;project&gt;/operations/preferences_exec_report</code>
                                </p>
                                <p style={{ margin: "8px 0 0", opacity: 0.64, fontSize: "13px" }}>
                                    Sent data: <code>{'{"recency": ..., "kwords": ...}'}</code>
                                </p>
                                <p style={{ margin: "8px 0 0", opacity: 0.64, fontSize: "13px" }}>
                                    Applied: recency=<code>{report.recency ?? 'n/a'}</code>, keywords=<code>{report.keywords || '(none)'}</code>
                                </p>
                            </div>
                            <div style={{ fontSize: "13px", opacity: 0.68 }}>
                                Status: {report.ok ? 'ok' : 'error'}
                            </div>
                        </div>
                        {report.error ? (
                            <div style={{
                                marginTop: "14px",
                                padding: "12px 14px",
                                borderRadius: "16px",
                                background: "rgba(140, 29, 48, 0.08)",
                                border: "1px solid rgba(140, 29, 48, 0.14)",
                                color: "#7a1730",
                                fontSize: "14px",
                                whiteSpace: "pre-wrap",
                            }}>
                                {typeof report.error === 'string' ? report.error : JSON.stringify(report.error, null, 2)}
                            </div>
                        ) : null}
                        {report.report_text ? (
                            <pre style={{
                                marginTop: "16px",
                                padding: "16px",
                                borderRadius: "18px",
                                background: "#f6f8f6",
                                border: "1px solid rgba(24,35,29,0.08)",
                                overflowX: "auto",
                                whiteSpace: "pre-wrap",
                            }}>
                                {report.report_text}
                            </pre>
                        ) : null}
                        {report.items && report.items.length > 0 ? (
                            <div style={{ marginTop: "16px" }}>
                                <div style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.08em", opacity: 0.58 }}>
                                    Artifacts
                                </div>
                                <ul style={{ margin: "10px 0 0", paddingLeft: "18px" }}>
                                    {report.items.map((item, index) => (
                                        <li key={`${item.path || 'artifact'}-${index}`} style={{ marginBottom: "6px" }}>
                                            <code>{item.path || '(unknown path)'}</code>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ) : null}
                    </section>
                ) : null}

                <section style={{ display: "grid", gridTemplateColumns: "1.1fr 1fr", gap: "18px" }}>
                    <div style={{
                        background: "rgba(255,255,255,0.86)",
                        border: "1px solid rgba(24,35,29,0.12)",
                        borderRadius: "24px",
                        padding: "22px",
                    }}>
                        <h2 style={{ marginTop: 0 }}>Current snapshot</h2>
                        {visibleCurrent.length === 0 ? (
                            <p style={{ opacity: 0.72 }}>No current preferences matched the filter.</p>
                        ) : (
                            <div style={{ display: "grid", gap: "12px" }}>
                                {visibleCurrent.map(([key, value]) => (
                                    <div key={key} style={{
                                        padding: "14px 16px",
                                        borderRadius: "18px",
                                        background: "#f8faf8",
                                        border: "1px solid rgba(24,35,29,0.08)",
                                    }}>
                                        <div style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.08em", opacity: 0.6 }}>
                                            {key}
                                        </div>
                                        <div style={{ fontSize: "18px", marginTop: "4px" }}>{String(value?.value ?? '')}</div>
                                        <div style={{ fontSize: "12px", marginTop: "8px", opacity: 0.65 }}>
                                            {value?.origin || 'unknown'} • {value?.updated_at || 'unknown time'}
                                        </div>
                                        {value?.evidence ? (
                                            <div style={{ marginTop: "10px", fontSize: "13px", opacity: 0.78 }}>
                                                {value.evidence}
                                            </div>
                                        ) : null}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    <div style={{
                        background: "rgba(255,255,255,0.86)",
                        border: "1px solid rgba(24,35,29,0.12)",
                        borderRadius: "24px",
                        padding: "22px",
                    }}>
                        <h2 style={{ marginTop: 0 }}>Recent observations</h2>
                        {visibleRecent.length === 0 ? (
                            <p style={{ opacity: 0.72 }}>No recent observations matched the filter.</p>
                        ) : (
                            <div style={{ display: "grid", gap: "12px" }}>
                                {visibleRecent.map((item, index) => (
                                    <div key={`${item.captured_at}-${index}`} style={{
                                        padding: "14px 16px",
                                        borderRadius: "18px",
                                        background: "#f4f7f5",
                                        border: "1px solid rgba(24,35,29,0.08)",
                                    }}>
                                        <div style={{ fontWeight: 600 }}>
                                            {item.key}: {String(item.value)}
                                        </div>
                                        <div style={{ fontSize: "12px", marginTop: "6px", opacity: 0.72 }}>
                                            {item.origin} • {item.source} • {item.captured_at}
                                        </div>
                                        {item.evidence ? (
                                            <div style={{ marginTop: "10px", fontSize: "13px", opacity: 0.8 }}>
                                                {item.evidence}
                                            </div>
                                        ) : null}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </section>
            </div>
        </div>
    );
}

export default PreferencesBrowser;
