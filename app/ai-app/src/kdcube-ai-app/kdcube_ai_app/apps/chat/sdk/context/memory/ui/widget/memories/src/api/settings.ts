const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}';

type RuntimeConfigPayload = {
  baseUrl?: string;
  accessToken?: string | null;
  idToken?: string | null;
  idTokenHeader?: string;
  idTokenHeaderName?: string;
  auth?: {
    idTokenHeaderName?: string;
  };
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
      widgetAlias: query.get('widget') || 'memories',
    };
  }
  const parts = path.slice(index + marker.length).split('/').map((part) => decodeURIComponent(part));
  const widgetsIndex = parts.indexOf('widgets');
  return {
    tenant: parts[0] || query.get('tenant') || '',
    project: parts[1] || query.get('project') || '',
    bundleId: parts[2] || query.get('bundle_id') || query.get('bundleId') || '',
    widgetAlias: widgetsIndex >= 0 ? parts[widgetsIndex + 1] || 'memories' : 'memories',
  };
}

function normalizeAuthContextHeaders(input?: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  if (!input || typeof input !== 'object') return out;
  Object.entries(input).forEach(([key, value]) => {
    const name = String(key || '').trim();
    if (!name || value === undefined || value === null) return;
    const text = String(value);
    if (!text) return;
    out[name] = text;
  });
  return out;
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
    // Host-supplied request auth context. The widget does not interpret these
    // headers; it only promotes them on its KDCube API calls.
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
    return isPlaceholder(this.values.bundleId) ? context.bundleId : this.values.bundleId;
  }

  getWidgetAlias(): string {
    return context.widgetAlias || 'memories';
  }

  authHeaders(base?: HeadersInit): Headers {
    const headers = new Headers(base);
    if (this.values.accessToken && !isPlaceholder(this.values.accessToken)) {
      headers.set('Authorization', `Bearer ${this.values.accessToken}`);
    }
    if (this.values.idToken && !isPlaceholder(this.values.idToken)) {
      headers.set(isPlaceholder(this.values.idTokenHeader) ? 'X-ID-Token' : this.values.idTokenHeader, this.values.idToken);
    }
    Object.entries(this.values.authContextHeaders).forEach(([name, value]) => {
      if (name && value) headers.set(name, value);
    });
    return headers;
  }

  private authContextFingerprint(): string {
    return Object.keys(this.values.authContextHeaders)
      .sort()
      .map((name) => `${name}:${this.values.authContextHeaders[name]}`)
      .join('\n');
  }

  private needsRuntimeConfig(): boolean {
    return (
      isPlaceholder(this.values.baseUrl) ||
      isPlaceholder(this.values.tenant) ||
      isPlaceholder(this.values.project) ||
      isPlaceholder(this.values.bundleId)
    );
  }

  private applyRuntimeConfig(config: RuntimeConfigPayload): boolean {
    const tenant = config.defaultTenant || config.tenant || config.tenant_id;
    const project = config.defaultProject || config.project || config.project_id;
    const idTokenHeader = config.idTokenHeader || config.idTokenHeaderName || config.auth?.idTokenHeaderName;
    const authContextHeaders = normalizeAuthContextHeaders(config.authContext?.headers);
    const hasAuthContext = Object.keys(authContextHeaders).length > 0;
    this.values = {
      ...this.values,
      baseUrl: config.baseUrl || this.values.baseUrl,
      accessToken: config.accessToken ?? this.values.accessToken,
      idToken: config.idToken ?? this.values.idToken,
      idTokenHeader: idTokenHeader || this.values.idTokenHeader,
      tenant: tenant || this.values.tenant,
      project: project || this.values.project,
      // The bundle the widget is SERVED from owns its operations endpoint, so a
      // known served bundleId wins over the host's `defaultAppBundleId` (the
      // host's "current app" hint). Only fall back to the hint when the served
      // bundle is unknown (placeholder) — otherwise an embedding host (e.g. the
      // versatile scene) would redirect this user-memories widget's calls to its
      // own bundle and get `memory_disabled`.
      bundleId: isPlaceholder(this.values.bundleId)
        ? (config.defaultAppBundleId || this.values.bundleId)
        : this.values.bundleId,
      authContextHeaders: hasAuthContext ? authContextHeaders : this.values.authContextHeaders,
    };
    return Boolean(
      tenant || project || config.baseUrl || config.accessToken !== undefined ||
      config.idToken !== undefined || idTokenHeader || config.defaultAppBundleId ||
      hasAuthContext,
    );
  }

  private isEmbedded(): boolean {
    try {
      return window.parent !== window;
    } catch {
      return true;
    }
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

  private authChangedCallback: (() => void) | null = null;
  private parentListenerReady: Promise<boolean> | null = null;

  // Re-run the caller's initial data load when the host-supplied auth context
  // changes after first render.
  onAuthContextChanged(callback: () => void): void {
    this.authChangedCallback = callback;
  }

  private requestParentConfig(): void {
    if (!this.isEmbedded()) return;
    try {
      window.parent.postMessage({
        type: 'CONFIG_REQUEST',
        data: {
          identity: 'MEMORIES_WIDGET',
          requestedFields: [
            'baseUrl',
            'accessToken',
            'idToken',
            'idTokenHeader',
            'defaultTenant',
            'defaultProject',
            'defaultAppBundleId',
            'authContext',
          ],
        },
      }, '*');
    } catch {
      // Parent may be opaque; widget falls back to route-derived config.
    }
  }

  setupParentListener(): Promise<boolean> {
    if (this.parentListenerReady) return this.parentListenerReady;
    const embedded = this.isEmbedded();
    let resolveReady: ((value: boolean) => void) | null = null;
    let resolved = false;
    const finish = (ready: boolean) => {
      if (resolved) return;
      resolved = true;
      resolveReady?.(ready);
    };

    // Persistent listener: the host may push a fresh CONFIG_RESPONSE later
    // (e.g. once a host acquires fresh auth material). Re-apply and signal when
    // the tokens or opaque authContext actually change so the caller can reload.
    window.addEventListener('message', (event: MessageEvent) => {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      // The standard host re-auth nudge: re-request config from the parent.
      if (data.type === 'kdcube-auth-changed') {
        this.requestParentConfig();
        return;
      }
      if (data.type !== 'CONN_RESPONSE' && data.type !== 'CONFIG_RESPONSE') return;
      if (data.identity !== 'MEMORIES_WIDGET' || !data.config) return;
      const before = `${this.values.accessToken}|${this.values.idToken}|${this.authContextFingerprint()}`;
      this.applyRuntimeConfig(data.config);
      const after = `${this.values.accessToken}|${this.values.idToken}|${this.authContextFingerprint()}`;
      finish(true);
      if (resolved && after !== before) this.authChangedCallback?.();
    });

    this.parentListenerReady = new Promise((resolve) => {
      resolveReady = resolve;
      // Even when route-derived tenant/project already suffice, an embedded
      // widget must still handshake so a host can deliver authContext.
      if (!this.needsRuntimeConfig() && !embedded) {
        finish(true);
        return;
      }
      // Embedded widgets prefer the host handshake (so they pick up any
      // host-only authContext); only standalone frames fall back to the
      // platform config endpoint.
      if (embedded) {
        this.requestParentConfig();
        window.setTimeout(() => finish(true), 3000);
        return;
      }
      this.loadFrontendConfig().then((loaded) => {
        finish(loaded || Boolean(this.getTenant() && this.getProject()));
      });
    });
    return this.parentListenerReady;
  }
}

export const settings = new Settings();
