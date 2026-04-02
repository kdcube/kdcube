import { useEffect, useRef, useState } from 'react';

interface AppSettings {
    baseUrl: string;
    accessToken: string | null;
    idToken: string | null;
    idTokenHeader: string;
    defaultTenant: string;
    defaultProject: string;
    defaultAppBundleId: string;
}

interface WidgetPayload {
    ok?: boolean;
    error?: string;
    user_id?: string;
}

interface CanvasEntry {
    key?: string;
    label?: string;
    text?: string;
    updated_at?: string | null;
    author?: string;
    origin?: string;
    source?: string;
    evidence?: string;
    raw_value?: unknown;
}

interface PreferencesCanvasPayload {
    ok?: boolean;
    error?: string;
    user_id?: string;
    path?: string | null;
    document_format?: string;
    document_text?: string;
    entries?: CanvasEntry[];
    last_modified?: number | string | null;
    changed_keys?: string[];
    removed_keys?: string[];
}

interface ExecArtifact {
    path?: string;
    mime?: string;
    size_bytes?: number;
}

interface ExecOutputFile {
    type?: string;
    path?: string;
    filename?: string;
    mime?: string;
    text?: string;
}

interface ExecReportPayload {
    ok?: boolean;
    error?: unknown;
    report_text?: string;
    items?: ExecArtifact[];
    out_dyn?: Record<string, ExecOutputFile | unknown>;
    report_filename?: string;
    report_mime?: string;
    report_content_b64?: string;
}

interface ExcelExportPayload {
    ok?: boolean;
    error?: string;
    user_id?: string;
    filename?: string;
    mime?: string;
    content_b64?: string;
}

const INITIAL_DATA: WidgetPayload = __PREFERENCES_JSON__;

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}';

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
        if (!this.settings.accessToken || isTemplatePlaceholder(this.settings.accessToken)) {
            return null;
        }
        return this.settings.accessToken;
    }

    getIdToken(): string | null {
        if (!this.settings.idToken || isTemplatePlaceholder(this.settings.idToken)) {
            return null;
        }
        return this.settings.idToken;
    }

    getIdTokenHeader(): string {
        if (!this.settings.idTokenHeader || isTemplatePlaceholder(this.settings.idTokenHeader)) {
            return 'X-ID-Token';
        }
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
            ? 'versatile@2026-03-31-13-36'
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

function normalizeCanvasEntries(entries: CanvasEntry[] | null | undefined): CanvasEntry[] {
    return (entries || []).map((entry) => ({
        key: String(entry?.key || entry?.label || ''),
        label: String(entry?.label || entry?.key || ''),
        text: String(entry?.text || ''),
        updated_at: entry?.updated_at ?? null,
        author: entry?.author || 'assistant',
        origin: entry?.origin || '',
        source: entry?.source || '',
        evidence: entry?.evidence || '',
        raw_value: entry?.raw_value,
    }));
}

function normalizeCanvasPayload(payload: PreferencesCanvasPayload | null | undefined): PreferencesCanvasPayload {
    return {
        ok: payload?.ok ?? true,
        error: payload?.error,
        user_id: payload?.user_id ?? INITIAL_DATA.user_id ?? 'anonymous',
        path: payload?.path ?? null,
        document_format: payload?.document_format ?? 'entries',
        document_text: payload?.document_text ?? '{}\n',
        entries: normalizeCanvasEntries(payload?.entries),
        last_modified: payload?.last_modified ?? null,
        changed_keys: payload?.changed_keys ?? [],
        removed_keys: payload?.removed_keys ?? [],
    };
}

async function postBundleOperation<T>(operation: string, data: Record<string, unknown> = {}): Promise<T> {
    const tenant = settings.getTenant();
    const project = settings.getProject();
    const bundleId = settings.getBundleId();
    if (!tenant || !project) {
        throw new Error('Widget configuration is incomplete: tenant/project are not available.');
    }

    const url = `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/${bundleId}/operations/${operation}`;
    const response = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ data }),
    });

    if (!response.ok) {
        const detail = await response.text().catch(() => response.statusText);
        throw new Error(`${response.status}: ${detail}`);
    }

    const json = await response.json();
    return (json[operation] ?? json) as T;
}

async function fetchPreferencesCanvas(): Promise<PreferencesCanvasPayload> {
    const payload = await postBundleOperation<PreferencesCanvasPayload>('preferences_canvas_data');
    return normalizeCanvasPayload(payload);
}

async function savePreferencesCanvas(entries: CanvasEntry[]): Promise<PreferencesCanvasPayload> {
    const payload = await postBundleOperation<PreferencesCanvasPayload>('preferences_canvas_save', { entries });
    return normalizeCanvasPayload(payload);
}

async function fetchPreferencesExecReport(): Promise<ExecReportPayload> {
    return await postBundleOperation<ExecReportPayload>('preferences_exec_report', {
        recency: 10,
        kwords: '',
    });
}

async function exportPreferencesExcel(): Promise<ExcelExportPayload> {
    return await postBundleOperation<ExcelExportPayload>('preferences_canvas_export_excel');
}

async function importPreferencesExcel(contentB64: string): Promise<PreferencesCanvasPayload> {
    const payload = await postBundleOperation<PreferencesCanvasPayload>('preferences_canvas_import_excel', {
        content_b64: contentB64,
    });
    return normalizeCanvasPayload(payload);
}

function formatStamp(value: string | null | undefined): string {
    if (!value) {
        return 'time unknown';
    }
    try {
        return new Date(value).toLocaleString();
    } catch {
        return String(value);
    }
}

function tinyActionStyle(danger = false): Record<string, string | number> {
    return {
        border: 'none',
        background: 'transparent',
        padding: 0,
        cursor: 'pointer',
        color: danger ? '#a13e35' : '#385447',
        fontSize: '12px',
        lineHeight: 1.1,
    };
}

function pillStyle(kind: 'stamp' | 'label' | 'user' | 'assistant'): Record<string, string | number> {
    if (kind === 'label') {
        return {
            background: '#efe6d3',
            color: '#624d1f',
        };
    }
    if (kind === 'user') {
        return {
            background: '#ddeee0',
            color: '#214428',
        };
    }
    if (kind === 'assistant') {
        return {
            background: '#ebe6fb',
            color: '#4c4275',
        };
    }
    return {
        background: '#f4efe5',
        color: '#675a46',
    };
}

function entryKey(entry: CanvasEntry, index: number): string {
    return `${entry.key || entry.label || 'entry'}-${index}`;
}

function decodeBase64ToBlob(contentB64: string, mime: string): Blob {
    const bytes = atob(contentB64);
    const data = new Uint8Array(bytes.length);
    for (let index = 0; index < bytes.length; index += 1) {
        data[index] = bytes.charCodeAt(index);
    }
    return new Blob([data], { type: mime });
}

function downloadBlob(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

function findExecReportFile(payload: ExecReportPayload): ExecOutputFile | null {
    const values = Object.values(payload.out_dyn || {});
    for (const value of values) {
        if (!value || typeof value !== 'object') {
            continue;
        }
        const candidate = value as ExecOutputFile;
        if ((candidate.type || '') !== 'file') {
            continue;
        }
        if (typeof candidate.text !== 'string' || !candidate.text) {
            continue;
        }
        return candidate;
    }
    return null;
}

function downloadExecReport(payload: ExecReportPayload): void {
    if (payload.report_content_b64) {
        const blob = decodeBase64ToBlob(
            payload.report_content_b64,
            payload.report_mime || 'text/markdown;charset=utf-8',
        );
        downloadBlob(blob, payload.report_filename || 'preferences_exec_report.md');
        return;
    }
    const reportFile = findExecReportFile(payload);
    if (reportFile) {
        downloadBlob(
            new Blob([reportFile.text || ''], { type: reportFile.mime || 'text/markdown;charset=utf-8' }),
            reportFile.filename || 'preferences_exec_report.md',
        );
    }
}

async function fileToBase64(file: File): Promise<string> {
    const buffer = await file.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let index = 0; index < bytes.length; index += 1) {
        binary += String.fromCharCode(bytes[index]);
    }
    return btoa(binary);
}

function PreferencesBrowser() {
    const importInputRef = useRef<HTMLInputElement | null>(null);
    const [ready, setReady] = useState(false);
    const [canvas, setCanvas] = useState<PreferencesCanvasPayload>(() => normalizeCanvasPayload({
        user_id: INITIAL_DATA.user_id,
        entries: [],
    }));
    const [entries, setEntries] = useState<CanvasEntry[]>([]);
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [reportLoading, setReportLoading] = useState(false);
    const [excelBusy, setExcelBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [lastSync, setLastSync] = useState<string | null>(null);
    const [editingIndex, setEditingIndex] = useState<number | null>(null);
    const [draftLabel, setDraftLabel] = useState('');
    const [draftText, setDraftText] = useState('');
    const [newLabel, setNewLabel] = useState('');
    const [newText, setNewText] = useState('');
    const [report, setReport] = useState<ExecReportPayload | null>(null);

    async function loadCanvas() {
        setLoading(true);
        setError(null);
        try {
            const payload = await fetchPreferencesCanvas();
            setCanvas(payload);
            setEntries(normalizeCanvasEntries(payload.entries));
            setLastSync(new Date().toISOString());
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setLoading(false);
        }
    }

    async function persistEntries(nextEntries: CanvasEntry[]): Promise<boolean> {
        setSaving(true);
        setError(null);
        try {
            const payload = await savePreferencesCanvas(nextEntries);
            setCanvas(payload);
            setEntries(normalizeCanvasEntries(payload.entries));
            setEditingIndex(null);
            setDraftLabel('');
            setDraftText('');
            setLastSync(new Date().toISOString());
            return true;
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
            return false;
        } finally {
            setSaving(false);
        }
    }

    function beginEdit(index: number) {
        const entry = entries[index];
        setEditingIndex(index);
        setDraftLabel(String(entry?.label || ''));
        setDraftText(String(entry?.text || ''));
        setError(null);
    }

    async function applyEdit(index: number) {
        const label = draftLabel.trim();
        const text = draftText.trim();
        if (!label || !text) {
            setError('Both label and text are required.');
            return;
        }
        const nextEntries = entries.map((entry, currentIndex) => (
            currentIndex === index
                ? {
                    ...entry,
                    label,
                    text,
                    author: 'user',
                }
                : entry
        ));
        await persistEntries(nextEntries);
    }

    async function removeEntry(index: number) {
        await persistEntries(entries.filter((_, currentIndex) => currentIndex !== index));
    }

    async function addEntry() {
        const label = newLabel.trim();
        const text = newText.trim();
        if (!label || !text) {
            setError('Both label and text are required.');
            return;
        }
        const saved = await persistEntries(entries.concat([
            {
                key: '',
                label,
                text,
                author: 'user',
                origin: 'user',
                source: 'preferences_canvas',
            },
        ]));
        if (saved) {
            setNewLabel('');
            setNewText('');
        }
    }

    async function runExecReport() {
        setReportLoading(true);
        setError(null);
        try {
            const payload = await fetchPreferencesExecReport();
            setReport(payload);
            downloadExecReport(payload);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setReportLoading(false);
        }
    }

    async function downloadExcel() {
        setExcelBusy(true);
        setError(null);
        try {
            const payload = await exportPreferencesExcel();
            if (!payload.ok || !payload.content_b64) {
                throw new Error(payload.error || 'Excel export failed.');
            }
            const blob = decodeBase64ToBlob(
                payload.content_b64,
                payload.mime || 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            );
            downloadBlob(blob, payload.filename || 'preferences.xlsx');
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setExcelBusy(false);
        }
    }

    async function onImportFileSelected(event: React.ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        if (!file) {
            return;
        }
        setExcelBusy(true);
        setError(null);
        try {
            const contentB64 = await fileToBase64(file);
            const payload = await importPreferencesExcel(contentB64);
            setCanvas(payload);
            setEntries(normalizeCanvasEntries(payload.entries));
            setEditingIndex(null);
            setDraftLabel('');
            setDraftText('');
            setLastSync(new Date().toISOString());
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            event.target.value = '';
            setExcelBusy(false);
        }
    }

    useEffect(() => {
        settings.setupParentListener().then(() => {
            setReady(true);
            void loadCanvas();
        });
    }, []);

    if (!ready) {
        return (
            <div style={{ padding: '24px', color: '#52605a', fontFamily: 'ui-sans-serif, system-ui, sans-serif' }}>
                Loading widget configuration...
            </div>
        );
    }

    const userId = canvas.user_id || INITIAL_DATA.user_id || 'anonymous';

    return (
        <div
            style={{
                minHeight: '100vh',
                margin: 0,
                padding: '16px',
                boxSizing: 'border-box',
                background: '#efe6d6',
                color: '#1e251f',
                fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            }}
        >
            <div style={{ maxWidth: '880px', margin: '0 auto' }}>
                <section
                    style={{
                        position: 'relative',
                        overflow: 'hidden',
                        background: '#fffdf7',
                        border: '1px solid rgba(76, 58, 34, 0.14)',
                        borderRadius: '20px',
                        boxShadow: '0 18px 45px rgba(81, 59, 26, 0.08)',
                    }}
                >
                    <div
                        style={{
                            position: 'absolute',
                            inset: 0,
                            backgroundImage:
                                'repeating-linear-gradient(to bottom, transparent 0, transparent 39px, rgba(76, 124, 200, 0.13) 39px, rgba(76, 124, 200, 0.13) 40px)',
                            pointerEvents: 'none',
                        }}
                    />
                    <div
                        style={{
                            position: 'absolute',
                            top: 0,
                            bottom: 0,
                            left: '28px',
                            width: '2px',
                            background: 'rgba(190, 72, 72, 0.34)',
                            pointerEvents: 'none',
                        }}
                    />
                    <div style={{ position: 'relative', padding: '16px 18px 18px 42px' }}>
                        <div
                            style={{
                                display: 'flex',
                                alignItems: 'flex-start',
                                justifyContent: 'space-between',
                                gap: '12px',
                                flexWrap: 'wrap',
                                marginBottom: '10px',
                            }}
                        >
                            <div>
                                <div
                                    style={{
                                        fontSize: '11px',
                                        textTransform: 'uppercase',
                                        letterSpacing: '0.14em',
                                        opacity: 0.56,
                                        marginBottom: '4px',
                                    }}
                                >
                                    Collaborative preferences
                                </div>
                                <div style={{ fontSize: '24px', lineHeight: 1.1, marginBottom: '4px' }}>
                                    Preferences notebook
                                </div>
                                <div style={{ fontSize: '13px', opacity: 0.74, maxWidth: '540px' }}>
                                    Each line is one preference note. Assistant-captured notes stay read-only except for
                                    label and text. If you edit a line, it is rewritten as a fresh user note.
                                </div>
                            </div>
                            <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
                                <span style={{ fontSize: '12px', opacity: 0.66 }}>
                                    {entries.length} lines for <strong>{userId}</strong>
                                </span>
                                <button
                                    type="button"
                                    onClick={() => void downloadExcel()}
                                    disabled={excelBusy}
                                    style={tinyActionStyle()}
                                >
                                    {excelBusy ? 'Working...' : 'Excel'}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => importInputRef.current?.click()}
                                    disabled={excelBusy}
                                    style={tinyActionStyle()}
                                >
                                    Import
                                </button>
                                <button
                                    type="button"
                                    onClick={() => void runExecReport()}
                                    disabled={reportLoading}
                                    style={tinyActionStyle()}
                                >
                                    {reportLoading ? 'Running report...' : 'Report'}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => void loadCanvas()}
                                    disabled={loading || saving}
                                    style={tinyActionStyle()}
                                >
                                    {loading ? 'Refreshing...' : 'Refresh'}
                                </button>
                            </div>
                        </div>

                        <input
                            ref={importInputRef}
                            type="file"
                            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            onChange={(event) => void onImportFileSelected(event)}
                            style={{ display: 'none' }}
                        />

                        {error ? (
                            <div
                                style={{
                                    marginBottom: '8px',
                                    padding: '8px 10px',
                                    borderRadius: '10px',
                                    border: '1px solid rgba(150, 49, 39, 0.18)',
                                    background: 'rgba(170, 58, 45, 0.08)',
                                    color: '#8b2e25',
                                    fontSize: '13px',
                                }}
                            >
                                {error}
                            </div>
                        ) : null}

                        {lastSync ? (
                            <div style={{ marginBottom: '8px', fontSize: '12px', opacity: 0.58 }}>
                                last sync {formatStamp(lastSync)}
                            </div>
                        ) : null}

                        <div style={{ display: 'grid', gap: '2px' }}>
                            {entries.length === 0 ? (
                                <div style={{ minHeight: '40px', display: 'flex', alignItems: 'center', fontSize: '14px', opacity: 0.62 }}>
                                    No saved lines yet. Add the first preference below.
                                </div>
                            ) : null}

                            {entries.map((entry, index) => (
                                <div
                                    key={entryKey(entry, index)}
                                    style={{
                                        minHeight: '40px',
                                        display: 'flex',
                                        alignItems: 'flex-start',
                                        gap: '10px',
                                        padding: '7px 0',
                                        flexWrap: 'wrap',
                                    }}
                                >
                                    {editingIndex === index ? (
                                        <>
                                            <input
                                                value={draftLabel}
                                                onChange={(event) => setDraftLabel(event.target.value)}
                                                placeholder="label"
                                                style={{
                                                    width: '150px',
                                                    border: 'none',
                                                    borderBottom: '1px solid rgba(71, 57, 37, 0.25)',
                                                    background: 'transparent',
                                                    padding: '4px 0',
                                                    fontSize: '13px',
                                                    outline: 'none',
                                                }}
                                            />
                                            <textarea
                                                value={draftText}
                                                onChange={(event) => setDraftText(event.target.value)}
                                                rows={1}
                                                placeholder="text"
                                                style={{
                                                    flex: '1 1 260px',
                                                    border: 'none',
                                                    borderBottom: '1px solid rgba(71, 57, 37, 0.25)',
                                                    background: 'transparent',
                                                    padding: '4px 0',
                                                    fontSize: '16px',
                                                    lineHeight: 1.5,
                                                    fontFamily: '"Bradley Hand", "Segoe Print", "Comic Sans MS", cursive',
                                                    outline: 'none',
                                                    resize: 'vertical',
                                                }}
                                            />
                                            <div style={{ marginLeft: 'auto', display: 'flex', gap: '10px' }}>
                                                <button type="button" onClick={() => void applyEdit(index)} style={tinyActionStyle()}>
                                                    Save
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => setEditingIndex(null)}
                                                    style={tinyActionStyle()}
                                                >
                                                    Cancel
                                                </button>
                                            </div>
                                        </>
                                    ) : (
                                        <>
                                            <span style={{ ...pillStyle('stamp'), borderRadius: '999px', padding: '3px 8px', fontSize: '11px' }}>
                                                {formatStamp(entry.updated_at)}
                                            </span>
                                            <span
                                                style={{
                                                    ...pillStyle(entry.author === 'user' ? 'user' : 'assistant'),
                                                    borderRadius: '999px',
                                                    padding: '3px 8px',
                                                    fontSize: '11px',
                                                }}
                                            >
                                                {entry.author || 'assistant'}
                                            </span>
                                            <span style={{ ...pillStyle('label'), borderRadius: '999px', padding: '3px 8px', fontSize: '11px' }}>
                                                {entry.label || 'note'}
                                            </span>
                                            <div
                                                style={{
                                                    flex: '1 1 260px',
                                                    fontSize: '18px',
                                                    lineHeight: 1.5,
                                                    fontFamily: '"Bradley Hand", "Segoe Print", "Comic Sans MS", cursive',
                                                    whiteSpace: 'pre-wrap',
                                                    color: '#32453a',
                                                    paddingTop: '1px',
                                                }}
                                            >
                                                {entry.text}
                                            </div>
                                            <div style={{ marginLeft: 'auto', display: 'flex', gap: '10px' }}>
                                                <button type="button" onClick={() => beginEdit(index)} style={tinyActionStyle()}>
                                                    Edit
                                                </button>
                                                <button type="button" onClick={() => void removeEntry(index)} style={tinyActionStyle(true)}>
                                                    Delete
                                                </button>
                                            </div>
                                        </>
                                    )}
                                </div>
                            ))}

                            <div
                                style={{
                                    minHeight: '40px',
                                    display: 'flex',
                                    alignItems: 'flex-start',
                                    gap: '10px',
                                    padding: '7px 0',
                                    flexWrap: 'wrap',
                                }}
                            >
                                <span style={{ ...pillStyle('user'), borderRadius: '999px', padding: '3px 8px', fontSize: '11px' }}>
                                    user
                                </span>
                                <input
                                    value={newLabel}
                                    onChange={(event) => setNewLabel(event.target.value)}
                                    placeholder="label"
                                    style={{
                                        width: '150px',
                                        border: 'none',
                                        borderBottom: '1px solid rgba(71, 57, 37, 0.25)',
                                        background: 'transparent',
                                        padding: '4px 0',
                                        fontSize: '13px',
                                        outline: 'none',
                                    }}
                                />
                                <textarea
                                    value={newText}
                                    onChange={(event) => setNewText(event.target.value)}
                                    rows={1}
                                    placeholder="write a new preference line"
                                    style={{
                                        flex: '1 1 260px',
                                        border: 'none',
                                        borderBottom: '1px solid rgba(71, 57, 37, 0.25)',
                                        background: 'transparent',
                                        padding: '4px 0',
                                        fontSize: '16px',
                                        lineHeight: 1.5,
                                        fontFamily: '"Bradley Hand", "Segoe Print", "Comic Sans MS", cursive',
                                        outline: 'none',
                                        resize: 'vertical',
                                    }}
                                />
                                <div style={{ marginLeft: 'auto', display: 'flex', gap: '10px' }}>
                                    <button
                                        type="button"
                                        onClick={() => void addEntry()}
                                        disabled={saving}
                                        style={tinyActionStyle()}
                                    >
                                        {saving ? 'Saving...' : 'Add line'}
                                    </button>
                                </div>
                            </div>
                        </div>

                        {report ? (
                            <div
                                style={{
                                    marginTop: '12px',
                                    paddingTop: '10px',
                                    borderTop: '1px dashed rgba(74, 59, 34, 0.18)',
                                }}
                            >
                                <div style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.12em', opacity: 0.54 }}>
                                    Exec report
                                </div>
                                {report.report_text ? (
                                    <pre
                                        style={{
                                            margin: '8px 0 0',
                                            whiteSpace: 'pre-wrap',
                                            fontSize: '12px',
                                            lineHeight: 1.5,
                                            fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
                                            color: '#3f463f',
                                            background: 'rgba(255, 255, 255, 0.55)',
                                            borderRadius: '10px',
                                            padding: '10px 12px',
                                            overflowX: 'auto',
                                        }}
                                    >
                                        {report.report_text}
                                    </pre>
                                ) : (
                                    <div style={{ marginTop: '6px', fontSize: '13px', opacity: 0.68 }}>
                                        Report completed with no inline text.
                                    </div>
                                )}
                            </div>
                        ) : null}
                    </div>
                </section>
            </div>
        </div>
    );
}

export default PreferencesBrowser;
