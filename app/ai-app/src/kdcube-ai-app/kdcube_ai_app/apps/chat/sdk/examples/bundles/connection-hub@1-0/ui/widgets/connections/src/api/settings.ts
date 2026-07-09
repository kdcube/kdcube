// Runtime settings for the Connections widget — resolves {baseUrl, tenant,
// project, bundleId} and auth headers. Mirrors the kdcube widget convention
// (memories): placeholders the platform may template at serve time, with a
// fallback to the URL route context, the cp-frontend-config endpoint, and a
// parent-window CONFIG_REQUEST bridge (used when embedded in the scene host).

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}';

const WIDGET_IDENTITY = 'CONNECTIONS_WIDGET';
const DEFAULT_WIDGET_ALIAS = 'connections_settings';

type RuntimeConfigPayload = {
  baseUrl?: string;
  accessToken?: string | null;
  idToken?: string | null;
  idTokenHeader?: string;
  idTokenHeaderName?: string;
  auth?: { idTokenHeaderName?: string };
  authContext?: {
    headers?: Record<string, unknown>;
  };
  defaultTenant?: string;
  defaultProject?: string;
  defaultAppBundleId?: string;
  tenant?: string;
  tenant_id?: string;
  project?: string;
  project_id?: string;
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
    authContextHeaders: {} as Record<string, string>,
  };

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
    // The ROUTE names this widget's own app (it is served from its bundle
    // URL); a host's defaultAppBundleId is the HOST's current app. In an
    // embedded scene the CONFIG_REQUEST relays to the outer host, whose
    // answer carries the OUTER bundle — letting it win re-points every hub
    // operation at a foreign bundle (empty hub). Route first, always.
    if (context.bundleId) return context.bundleId;
    return isPlaceholder(this.values.bundleId) ? '' : this.values.bundleId;
  }

  getWidgetAlias(): string {
    return context.widgetAlias || DEFAULT_WIDGET_ALIAS;
  }

  authHeaders(base?: HeadersInit): Headers {
    const headers = new Headers(base);
    Object.entries(this.values.authContextHeaders).forEach(([name, value]) => {
      if (name && value) headers.set(name, value);
    });
    if (this.values.accessToken && !isPlaceholder(this.values.accessToken)) {
      headers.set('Authorization', `Bearer ${this.values.accessToken}`);
    }
    if (this.values.idToken && !isPlaceholder(this.values.idToken)) {
      headers.set(isPlaceholder(this.values.idTokenHeader) ? 'X-ID-Token' : this.values.idTokenHeader, this.values.idToken);
    }
    return headers;
  }

  private needsRuntimeConfig(): boolean {
    return (
      isPlaceholder(this.values.baseUrl) ||
      isPlaceholder(this.values.tenant) ||
      isPlaceholder(this.values.project) ||
      isPlaceholder(this.values.bundleId) ||
      this.needsHostAuthContext()
    );
  }

  private needsHostAuthContext(): boolean {
    return window.parent !== window && Object.keys(this.values.authContextHeaders).length === 0;
  }

  private applyRuntimeConfig(config: RuntimeConfigPayload): boolean {
    const tenant = config.defaultTenant || config.tenant || config.tenant_id;
    const project = config.defaultProject || config.project || config.project_id;
    const idTokenHeader = config.idTokenHeader || config.idTokenHeaderName || config.auth?.idTokenHeaderName;
    const authContextHeaders: Record<string, string> = {};
    Object.entries(config.authContext?.headers || {}).forEach(([name, value]) => {
      if (!name || value === undefined || value === null || String(value) === '') return;
      authContextHeaders[name] = String(value);
    });
    this.values = {
      ...this.values,
      baseUrl: config.baseUrl || this.values.baseUrl,
      accessToken: config.accessToken ?? this.values.accessToken,
      idToken: config.idToken ?? this.values.idToken,
      idTokenHeader: idTokenHeader || this.values.idTokenHeader,
      tenant: tenant || this.values.tenant,
      project: project || this.values.project,
      bundleId: config.defaultAppBundleId || this.values.bundleId,
      authContextHeaders: Object.keys(authContextHeaders).length > 0
        ? authContextHeaders
        : this.values.authContextHeaders,
    };
    return Boolean(
      tenant ||
      project ||
      config.baseUrl ||
      config.accessToken !== undefined ||
      config.idToken !== undefined ||
      idTokenHeader ||
      config.defaultAppBundleId ||
      Object.keys(authContextHeaders).length > 0
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

  // Resolve runtime config before the first API call. No-op when the platform
  // already templated real values; otherwise tries cp-frontend-config, then
  // asks the parent window (scene host) via CONFIG_REQUEST.
  setupParentListener(): Promise<boolean> {
    if (!this.needsRuntimeConfig()) {
      return Promise.resolve(true);
    }

    let resolveReady: ((value: boolean) => void) | null = null;
    let resolved = false;
    const finish = (ready: boolean) => {
      if (resolved) return;
      resolved = true;
      resolveReady?.(ready);
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
            requestedFields: ['baseUrl', 'accessToken', 'idToken', 'idTokenHeader', 'defaultTenant', 'defaultProject', 'defaultAppBundleId', 'authContext'],
          },
        }, '*');
        window.setTimeout(() => finish(true), 3000);
      };
      this.loadFrontendConfig().then((loaded) => {
        if (loaded && !this.needsHostAuthContext()) {
          finish(true);
        } else {
          requestParentConfig();
        }
      });
    });
  }
}

export const settings = new Settings();
