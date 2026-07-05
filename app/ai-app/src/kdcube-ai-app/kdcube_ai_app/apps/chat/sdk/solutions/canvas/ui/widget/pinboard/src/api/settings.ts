/**
 * CONFIG bridge for the standalone Pin Board widget.
 *
 * Mirrors the memories widget's settings: placeholders are baked into the
 * delivered bundle and replaced either by the server's
 * `/api/cp-frontend-config` or by a parent-frame `CONFIG_REQUEST` handshake
 * (identity `PINBOARD_WIDGET`). The resolved values feed the canvas host's
 * `RouteContext` — including the access / id tokens the Data Bus socket
 * needs to authenticate canvas patches.
 */

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}';

const WIDGET_IDENTITY = 'PINBOARD_WIDGET';
const DEFAULT_WIDGET_ALIAS = 'pinboard';

type RuntimeConfigPayload = {
  baseUrl?: string;
  accessToken?: string | null;
  idToken?: string | null;
  idTokenHeader?: string;
  idTokenHeaderName?: string;
  auth?: { idTokenHeaderName?: string };
  defaultTenant?: string;
  defaultProject?: string;
  defaultAppBundleId?: string;
  tenant?: string;
  tenant_id?: string;
  project?: string;
  project_id?: string;
  namespace_styles?: Record<string, unknown>;
  namespaceStyles?: Record<string, unknown>;
};

type NamespacePresentationConfigPayload = {
  namespace_styles?: Record<string, unknown>;
  namespaceStyles?: Record<string, unknown>;
};

function isPlaceholder(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.includes('{{') && value.includes('}}');
}

function routeContext() {
  const markers = ['/api/integrations/bundles/', '/api/integrations/static/'];
  const path = window.location.pathname;
  const marker = markers.find((candidate) => path.includes(candidate));
  const index = marker ? path.indexOf(marker) : -1;
  const query = new URLSearchParams(window.location.search);
  if (!marker || index < 0) {
    return {
      tenant: query.get('tenant') || '',
      project: query.get('project') || '',
      bundleId: query.get('bundle_id') || query.get('bundleId') || '',
      widgetAlias: query.get('widget') || DEFAULT_WIDGET_ALIAS,
    };
  }
  const parts = path.slice(index + marker.length).split('/').map((part) => decodeURIComponent(part));
  const widgetsIndex = parts.indexOf('widgets');
  return {
    tenant: parts[0] || query.get('tenant') || '',
    project: parts[1] || query.get('project') || '',
    bundleId: parts[2] || query.get('bundle_id') || query.get('bundleId') || '',
    widgetAlias: widgetsIndex >= 0 ? parts[widgetsIndex + 1] || DEFAULT_WIDGET_ALIAS : DEFAULT_WIDGET_ALIAS,
  };
}

const context = routeContext();

class Settings {
  private values = {
    baseUrl: PLACEHOLDER_BASE_URL,
    accessToken: PLACEHOLDER_ACCESS_TOKEN,
    idToken: PLACEHOLDER_ID_TOKEN,
    idTokenHeader: PLACEHOLDER_ID_TOKEN_HEADER,
    tenant: PLACEHOLDER_TENANT,
    project: PLACEHOLDER_PROJECT,
    bundleId: PLACEHOLDER_BUNDLE_ID,
    namespaceStyles: {} as Record<string, unknown>,
  };

  // Namespace styles arrive asynchronously (bundle fetch, host CONFIG_RESPONSE,
  // background retry). Subscribers — the React app — re-render cards when a
  // late payload lands instead of staying colorless until a reload.
  private stylesListeners = new Set<(styles: Record<string, unknown>) => void>();

  private stylesFetch: Promise<boolean> | null = null;

  getBaseUrl(): string {
    if (isPlaceholder(this.values.baseUrl)) return window.location.origin;
    const trimmed = this.values.baseUrl.replace(/\/+$/, '');
    return trimmed.endsWith('/api') ? trimmed.slice(0, -4) : trimmed;
  }

  getTenant(): string {
    return isPlaceholder(this.values.tenant) ? context.tenant : this.values.tenant;
  }

  getProject(): string {
    return isPlaceholder(this.values.project) ? context.project : this.values.project;
  }

  getBundleId(): string {
    return isPlaceholder(this.values.bundleId) ? context.bundleId : this.values.bundleId;
  }

  getWidgetAlias(): string {
    return context.widgetAlias || DEFAULT_WIDGET_ALIAS;
  }

  getAccessToken(): string | null {
    return isPlaceholder(this.values.accessToken) ? null : (this.values.accessToken || null);
  }

  getIdToken(): string | null {
    return isPlaceholder(this.values.idToken) ? null : (this.values.idToken || null);
  }

  getNamespaceStyles(): Record<string, unknown> {
    return { ...this.values.namespaceStyles };
  }

  hasNamespaceStyles(): boolean {
    return Object.keys(this.values.namespaceStyles).length > 0;
  }

  subscribeNamespaceStyles(listener: (styles: Record<string, unknown>) => void): () => void {
    this.stylesListeners.add(listener);
    return () => { this.stylesListeners.delete(listener); };
  }

  private notifyNamespaceStyles(): void {
    const snapshot = this.getNamespaceStyles();
    this.stylesListeners.forEach((listener) => listener(snapshot));
  }

  private applyRuntimeConfig(config: RuntimeConfigPayload): boolean {
    const tenant = config.defaultTenant || config.tenant || config.tenant_id;
    const project = config.defaultProject || config.project || config.project_id;
    const idTokenHeader = config.idTokenHeader || config.idTokenHeaderName || config.auth?.idTokenHeaderName;
    const namespaceStyles = config.namespace_styles || config.namespaceStyles;
    this.values = {
      ...this.values,
      baseUrl: config.baseUrl || this.values.baseUrl,
      accessToken: config.accessToken ?? this.values.accessToken,
      idToken: config.idToken ?? this.values.idToken,
      idTokenHeader: idTokenHeader || this.values.idTokenHeader,
      tenant: tenant || this.values.tenant,
      project: project || this.values.project,
      bundleId: config.defaultAppBundleId || this.values.bundleId,
      namespaceStyles: namespaceStyles || this.values.namespaceStyles,
    };
    if (namespaceStyles) this.notifyNamespaceStyles();
    return Boolean(tenant || project || config.baseUrl || config.accessToken !== undefined || config.idToken !== undefined || idTokenHeader || config.defaultAppBundleId || namespaceStyles);
  }

  private needsRuntimeConfig(): boolean {
    return (
      isPlaceholder(this.values.baseUrl) ||
      isPlaceholder(this.values.tenant) ||
      isPlaceholder(this.values.project) ||
      isPlaceholder(this.values.bundleId)
    );
  }

  private async loadFrontendConfig(): Promise<boolean> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 1000);
    try {
      const response = await fetch(`${this.getBaseUrl()}/api/cp-frontend-config`, {
        credentials: 'include',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
      if (!response.ok) return false;
      return this.applyRuntimeConfig(await response.json());
    } catch {
      return false;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  /** Single-flight wrapper: concurrent callers share one in-flight fetch. */
  private loadNamespaceStyles(): Promise<boolean> {
    if (this.stylesFetch) return this.stylesFetch;
    this.stylesFetch = this.fetchNamespaceStyles()
      .catch(() => false)
      .finally(() => { this.stylesFetch = null; });
    return this.stylesFetch;
  }

  /**
   * Retry ladder for a failed startup fetch. The presentation-config call is
   * flaky on a deployed runtime (slow dispatch, transient aborts); giving up
   * after one attempt left the board colorless for the whole session.
   */
  private async retryNamespaceStyles(): Promise<void> {
    for (const delayMs of [1500, 4000]) {
      await new Promise((resolve) => window.setTimeout(resolve, delayMs));
      if (this.hasNamespaceStyles()) return;
      if (await this.loadNamespaceStyles()) return;
    }
  }

  /**
   * Fetch-if-empty, safe to call opportunistically (e.g. on board switch):
   * no-op once styles are present, deduped while a fetch is in flight.
   */
  ensureNamespaceStyles(): void {
    if (this.hasNamespaceStyles()) return;
    void this.loadNamespaceStyles();
  }

  private async fetchNamespaceStyles(): Promise<boolean> {
    const tenant = this.getTenant();
    const project = this.getProject();
    const bundleId = this.getBundleId();
    if (!tenant || !project || !bundleId) return false;
    const controller = new AbortController();
    // The presentation-config call dispatches through the bundle operations
    // bridge and on a deployed runtime routinely takes well over a second; a
    // 1.2s abort dropped the styles there and left every pin card colorless
    // (the chat widget's identical fetch already carries this longer window).
    const timeout = window.setTimeout(() => controller.abort(), 6000);
    try {
      const alias = 'namespace_presentation_config';
      const response = await fetch(
        `${this.getBaseUrl()}/api/integrations/bundles/${encodeURIComponent(tenant)}/${encodeURIComponent(project)}/${encodeURIComponent(bundleId)}/public/${alias}`,
        {
          method: 'POST',
          credentials: 'include',
          cache: 'no-store',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            ...(this.getAccessToken() ? { Authorization: `Bearer ${this.getAccessToken()}` } : {}),
            ...(this.getIdToken() ? { [this.getIdTokenHeader()]: this.getIdToken() as string } : {}),
          },
          body: JSON.stringify({ data: {} }),
          signal: controller.signal,
        },
      );
      if (!response.ok) return false;
      const payload = await response.json().catch(() => null) as Record<string, unknown> | null;
      const body = payload && typeof payload === 'object' && alias in payload
        ? payload[alias] as NamespacePresentationConfigPayload
        : payload as NamespacePresentationConfigPayload | null;
      if (!body || typeof body !== 'object') return false;
      const styles = body.namespace_styles || body.namespaceStyles;
      if (!styles || typeof styles !== 'object') return false;
      this.values = { ...this.values, namespaceStyles: styles };
      this.notifyNamespaceStyles();
      return true;
    } catch {
      return false;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  /**
   * First styles attempt is awaited (boot renders colored when it succeeds);
   * on failure boot proceeds and retries run in the background, re-coloring
   * live via the styles subscription.
   */
  private bootstrapNamespaceStyles(): Promise<void> {
    return this.loadNamespaceStyles().then((ok) => {
      if (!ok && !this.hasNamespaceStyles()) void this.retryNamespaceStyles();
    });
  }

  setupParentListener(): Promise<boolean> {
    if (!this.needsRuntimeConfig()) {
      return this.bootstrapNamespaceStyles().then(() => true);
    }

    let resolveReady: ((value: boolean) => void) | null = null;
    let resolved = false;
    const finish = (ready: boolean) => {
      if (resolved) return;
      resolved = true;
      this.bootstrapNamespaceStyles().finally(() => resolveReady?.(ready));
    };

    window.addEventListener('message', (event: MessageEvent) => {
      if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') return;
      if (event.data.identity !== WIDGET_IDENTITY || !event.data.config) return;
      this.applyRuntimeConfig(event.data.config);
      finish(true);
    });
    return new Promise((resolve) => {
      resolveReady = resolve;
      const requestParentConfig = () => {
        window.parent.postMessage({
          type: 'CONFIG_REQUEST',
          data: {
            identity: WIDGET_IDENTITY,
            requestedFields: ['baseUrl', 'accessToken', 'idToken', 'idTokenHeader', 'defaultTenant', 'defaultProject', 'defaultAppBundleId', 'namespaceStyles', 'namespace_styles'],
          },
        }, '*');
        window.setTimeout(() => finish(true), 3000);
      };
      this.loadFrontendConfig().then((loaded) => {
        if (loaded) {
          finish(true);
        } else {
          requestParentConfig();
        }
      });
    });
  }
}

export const settings = new Settings();
